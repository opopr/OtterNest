"""Model proposal, scoped evidence and automatic debate contract tests."""
import uuid

from otternest import model_planner
from otternest.model_planner import ModelProposal


def _project(client, owner):
    r = client.post("/api/v1/projects", headers=owner, json={"name": "Governed project"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _task(client, owner, project):
    r = client.post(f"/api/v1/projects/{project}/tasks", headers=owner,
                    json={"title": "Review plan", "instruction": "Work carefully",
                          "acceptance": "Reviewed", "risk": "R3"})
    assert r.status_code == 201, r.text
    return r.json()


def _agent(client, owner, project, name):
    r = client.post("/api/v1/agents", headers=owner,
                    json={"name": name, "platform": "test"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert client.post(f"/api/v1/projects/{project}/members", headers=owner,
                       json={"agent_id": a["id"], "role": "reviewer"}).status_code == 201
    return a["id"], {"Authorization": f"Bearer {a['token']}"}


def _decision(client, owner, project):
    r = client.post(f"/api/v1/projects/{project}/decisions", headers=owner,
                    json={"question": "What should we do?", "resolution": "Option A",
                          "rationale": "Initial reasoning"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _review(client, headers, decision, stance):
    return client.post(f"/api/v1/decisions/{decision}/reviews", headers=headers,
                       json={"stance": stance, "reasoning": "Independent evidence and reasons."})


def _configure(monkeypatch):
    monkeypatch.setenv("OTTERNEST_PLANNER_ALLOW_EXTERNAL", "1")
    monkeypatch.setenv("OTTERNEST_PLANNER_URL", "https://api.example.test/v1/chat/completions")
    monkeypatch.setenv("OTTERNEST_PLANNER_ALLOWED_HOSTS", "api.example.test")
    monkeypatch.setenv("OTTERNEST_PLANNER_MODEL", "test-model")
    monkeypatch.setenv("OTTERNEST_PLANNER_API_KEY", "test-secret-key")


def test_model_defaults_off_and_agents_cannot_use_it(env, owner_headers, monkeypatch):
    p = _project(env, owner_headers)
    _task(env, owner_headers, p)
    _, auth = _agent(env, owner_headers, p, "No privilege")
    body = {"project_id": p, "text": "请批准这项任务"}
    monkeypatch.delenv("OTTERNEST_PLANNER_ALLOW_EXTERNAL", raising=False)
    assert env.post("/api/v1/command/interpret-model", headers=owner_headers, json=body).status_code == 503
    assert env.post("/api/v1/command/interpret-model", headers=auth, json=body).status_code == 403


def test_model_can_only_preview_known_task_and_owner_must_confirm(env, owner_headers, monkeypatch):
    p = _project(env, owner_headers)
    t = _task(env, owner_headers, p)
    _configure(monkeypatch)
    observed = {}
    def stub(url, key, model, text, tasks, agents):
        observed["tasks"] = tasks
        return ModelProposal(action="authorize_task", task_id=t["id"])
    monkeypatch.setattr(model_planner, "_request_proposal", stub)
    result = env.post("/api/v1/command/interpret-model", headers=owner_headers,
                      json={"project_id": p, "text": "批准此任务，但需要我确认"})
    assert result.status_code == 201, result.text
    preview = result.json()
    assert preview["confirmation_required"] is True
    assert preview["interpretation"]["requires_explicit_confirmation"] is True
    assert list(observed["tasks"][0]) == ["id", "title", "status", "risk", "version"]
    assert env.get(f"/api/v1/tasks/{t['id']}", headers=owner_headers).json()["status"] == "waiting_approval"
    assert env.post(f"/api/v1/command/plans/{preview['id']}/confirm",
                    headers=owner_headers).status_code == 200


def test_model_rejects_foreign_task_and_unexpected_parameters(env, owner_headers, monkeypatch):
    p = _project(env, owner_headers)
    good = _task(env, owner_headers, p)
    foreign = _task(env, owner_headers, _project(env, owner_headers))
    _configure(monkeypatch)
    endpoint = "/api/v1/command/interpret-model"
    body = {"project_id": p, "text": "请取消一个任务"}
    monkeypatch.setattr(model_planner, "_request_proposal",
                        lambda *args: ModelProposal(action="cancel_task", task_id=foreign["id"]))
    assert env.post(endpoint, headers=owner_headers, json=body).status_code == 422
    monkeypatch.setattr(model_planner, "_request_proposal",
                        lambda *args: ModelProposal(action="cancel_task", task_id=good["id"], accepted=True))
    assert env.post(endpoint, headers=owner_headers, json=body).status_code == 422
    monkeypatch.setenv("OTTERNEST_PLANNER_URL", "http://127.0.0.1:4444/chat")
    assert env.post(endpoint, headers=owner_headers, json=body).status_code == 503


def test_evidence_is_provenance_not_factual_truth(env, owner_headers):
    p = _project(env, owner_headers)
    outsider = _project(env, owner_headers)
    decision = _decision(env, owner_headers, p)
    def add(prj):
        r = env.post(f"/api/v1/projects/{prj}/memories", headers=owner_headers,
                     json={"title": "Source", "content": "Unverified content", "source": "test"})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    good = add(p)
    bad = add(outsider)
    refs = [f"memory:{good}", f"memory:{bad}", f"memory:{uuid.uuid4()}", "https://example.org", "artifact:1"]
    url = f"/api/v1/decisions/{decision}/evidence/inspect"
    response = env.post(url, headers=owner_headers, json={"references": refs})
    assert response.status_code == 200
    assert response.json()["checks"][0]["status"] == "unresolved"
    assert env.post(f"/api/v1/memories/{good}/publish", headers=owner_headers).status_code == 200
    result = env.post(url, headers=owner_headers, json={"references": refs}).json()
    assert [x["status"] for x in result["checks"]] == [
        "linked", "unresolved", "unresolved", "unsupported", "unsupported"]
    assert all(not x["factual_claim_verified"] for x in result["checks"])


def test_auto_deliberation_waits_for_full_roster_and_never_auto_approves(env, owner_headers):
    p = _project(env, owner_headers)
    did = _decision(env, owner_headers, p)
    a, ha = _agent(env, owner_headers, p, "A")
    b, hb = _agent(env, owner_headers, p, "B")
    c, hc = _agent(env, owner_headers, p, "C")
    url = f"/api/v1/decisions/{did}"
    policy = env.post(url + "/deliberation/auto-policy", headers=owner_headers,
                      json={"reviewer_ids": [a, b, c]})
    assert policy.status_code == 201, policy.text
    assert policy.json()["owner_approval_required"] is True
    assert _review(env, ha, did, "support").status_code == 201
    assert _review(env, hb, did, "oppose").status_code == 201
    assert env.get(url + "/deliberation", headers=owner_headers).status_code == 404
    assert env.post(url + "/approve", headers=owner_headers).status_code == 409
    third = _review(env, hc, did, "support")
    assert third.status_code == 201, third.text
    state = env.get(url + "/deliberation", headers=owner_headers)
    assert state.status_code == 200 and state.json()["status"] == "open"
    opinions = state.json()["first_round"]
    for headers, target_id in [(ha, opinions[1]["id"]), (hb, opinions[2]["id"]), (hc, opinions[0]["id"])]:
        result = env.post(url + "/deliberation/rebuttals", headers=headers, json={
            "responds_to_review_id": target_id, "stance": "support",
            "reasoning": "I independently considered the other position."})
        assert result.status_code == 201, result.text
    assert env.get(url + "/deliberation", headers=owner_headers).json()["status"] == "closed"
    assert env.post(url + "/resolve", headers=owner_headers,
                    json={"owner_note": "I considered dissent and approve after review."}).status_code == 200
    events = env.get(f"/api/v1/projects/{p}/events", headers=owner_headers).json()
    assert [e["kind"] for e in events].count("decision.second_round_opened") == 1
    assert [e["kind"] for e in events].count("decision.second_round_closed") == 1


def test_unanimous_auto_review_records_completion_without_approval(env, owner_headers):
    p = _project(env, owner_headers)
    did = _decision(env, owner_headers, p)
    a, ha = _agent(env, owner_headers, p, "A")
    b, hb = _agent(env, owner_headers, p, "B")
    url = f"/api/v1/decisions/{did}"
    assert env.post(url + "/deliberation/auto-policy", headers=owner_headers,
                    json={"reviewer_ids": [a, b]}).status_code == 201
    assert _review(env, ha, did, "support").status_code == 201
    assert _review(env, hb, did, "support").status_code == 201
    assert env.get(url + "/deliberation", headers=owner_headers).status_code == 404
    events = env.get(f"/api/v1/projects/{p}/events", headers=owner_headers).json()
    assert [e["kind"] for e in events].count("decision.first_round_completed") == 1
    assert env.post(url + "/approve", headers=owner_headers).status_code == 200
