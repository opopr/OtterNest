from __future__ import annotations


def create_project(env, headers, name="Website"):
    r = env.post("/api/v1/projects", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def create_agent(env, headers, name="Coder"):
    r = env.post("/api/v1/agents", headers=headers, json={"name": name, "platform": "generic", "execution_mode": "polling"})
    assert r.status_code == 201, r.text
    return r.json()


def enroll(env, headers, project_id, agent_id):
    r = env.post(f"/api/v1/projects/{project_id}/members", headers=headers, json={"agent_id": agent_id})
    assert r.status_code == 201, r.text


def create_task(env, headers, project_id, risk="R1"):
    r = env.post(f"/api/v1/projects/{project_id}/tasks", headers=headers,
                 json={"title": "Build page", "instruction": "Implement and test", "acceptance": "Tests pass", "risk": risk})
    assert r.status_code == 201, r.text
    return r.json()


def test_authenticated_full_task_workflow(env, owner_headers):
    assert env.get("/health/live").json()["status"] == "alive"
    assert env.get("/api/v1/projects").status_code == 401
    project = create_project(env, owner_headers)
    goal = env.post(f"/api/v1/projects/{project}/goals", headers=owner_headers, json={"title": "Site", "success_criteria": "Shippable"})
    assert goal.status_code == 201
    agent = create_agent(env, owner_headers)
    assert agent["token"].startswith("on_agent_")
    agent_auth = {"Authorization": f"Bearer {agent['token']}"}
    enroll(env, owner_headers, project, agent["id"])
    assert env.get("/api/v1/projects", headers=agent_auth).status_code == 200
    task = create_task(env, owner_headers, project)
    claim = env.post(f"/api/v1/tasks/{task['id']}/claim", headers=agent_auth, json={"lease_seconds": 90})
    assert claim.status_code == 200, claim.text
    assert claim.json()["lease_epoch"] == 1
    assert env.post(f"/api/v1/tasks/{task['id']}/claim", headers=agent_auth, json={}).status_code == 409
    hb = env.post(f"/api/v1/tasks/{task['id']}/heartbeat", headers=agent_auth, json={"epoch": 1})
    assert hb.status_code == 200
    submit = env.post(f"/api/v1/tasks/{task['id']}/submit", headers=agent_auth,
                      json={"epoch": 1, "summary": "Tests passed; artifact submitted"})
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "submitted"
    assert env.post(f"/api/v1/tasks/{task['id']}/submit", headers=agent_auth,
                    json={"epoch": 1, "summary": "stale"}).status_code == 409
    review = env.post(f"/api/v1/tasks/{task['id']}/review", headers=owner_headers,
                      json={"accepted": True, "reason": "Accepted"})
    assert review.json()["status"] == "completed"
    history = env.get(f"/api/v1/projects/{project}/events", headers=owner_headers)
    assert history.status_code == 200
    assert "task.claimed" in [e["kind"] for e in history.json()]
    assert "task.accepted" in [e["kind"] for e in history.json()]


def test_cross_project_access_and_owner_only_controls(env, owner_headers):
    p1 = create_project(env, owner_headers, "Private A")
    p2 = create_project(env, owner_headers, "Private B")
    a = create_agent(env, owner_headers)
    enroll(env, owner_headers, p1, a["id"])
    agent_auth = {"Authorization": f"Bearer {a['token']}"}
    t = create_task(env, owner_headers, p2)
    assert env.get(f"/api/v1/projects/{p2}/tasks", headers=agent_auth).status_code == 404
    assert env.get(f"/api/v1/tasks/{t['id']}", headers=agent_auth).status_code == 404
    assert env.post(f"/api/v1/tasks/{t['id']}/claim", headers=agent_auth, json={}).status_code == 404
    assert env.post("/api/v1/projects", headers=agent_auth, json={"name": "unauthorized"}).status_code == 403
    assert env.post(f"/api/v1/projects/{p1}/members", headers=agent_auth, json={"agent_id": a["id"]}).status_code == 403
    assert len(env.get("/api/v1/projects", headers=agent_auth).json()) == 1
    env.post(f"/api/v1/agents/{a['id']}/disable", headers=owner_headers)
    assert env.get("/api/v1/projects", headers=agent_auth).status_code == 401


def test_r3_approval_and_fencing_after_cancel(env, owner_headers):
    p = create_project(env, owner_headers)
    a = create_agent(env, owner_headers)
    enroll(env, owner_headers, p, a["id"])
    auth = {"Authorization": f"Bearer {a['token']}"}
    t = create_task(env, owner_headers, p, "R3")
    assert t["status"] == "waiting_approval"
    assert env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth, json={}).status_code == 409
    assert env.post(f"/api/v1/tasks/{t['id']}/authorize", headers=auth).status_code == 403
    assert env.post(f"/api/v1/tasks/{t['id']}/authorize", headers=owner_headers).status_code == 200
    c = env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth, json={}).json()
    assert c["lease_epoch"] == 1
    assert env.post(f"/api/v1/tasks/{t['id']}/cancel", headers=owner_headers).status_code == 200
    assert env.post(f"/api/v1/tasks/{t['id']}/submit", headers=auth,
                    json={"epoch": 1, "summary": "should fail"}).status_code == 409


def test_knowledge_publish_and_decision_approval(env, owner_headers):
    p = create_project(env, owner_headers)
    a = create_agent(env, owner_headers)
    enroll(env, owner_headers, p, a["id"])
    auth = {"Authorization": f"Bearer {a['token']}"}
    m = env.post(f"/api/v1/projects/{p}/memories", headers=auth,
                 json={"title": "Test result", "content": "All good", "source": "fixture-test"}).json()
    assert env.get(f"/api/v1/projects/{p}/memories", headers=auth).json() == []
    assert len(env.get(f"/api/v1/projects/{p}/memories", headers=owner_headers).json()) == 1
    assert env.post(f"/api/v1/memories/{m['id']}/publish", headers=owner_headers).status_code == 200
    assert len(env.get(f"/api/v1/projects/{p}/memories", headers=auth).json()) == 1
    d = env.post(f"/api/v1/projects/{p}/decisions", headers=auth,
                 json={"question": "Use PG?", "resolution": "Yes", "rationale": "Transactions"}).json()
    assert env.get(f"/api/v1/projects/{p}/decisions", headers=auth).json() == []
    assert env.post(f"/api/v1/decisions/{d['id']}/approve", headers=owner_headers).status_code == 200
    assert len(env.get(f"/api/v1/projects/{p}/decisions", headers=auth).json()) == 1


def test_goal_must_match_project_and_lease_epoch(env, owner_headers):
    a = create_project(env, owner_headers, "A")
    b = create_project(env, owner_headers, "B")
    g = env.post(f"/api/v1/projects/{a}/goals", headers=owner_headers,
                 json={"title": "A goal", "success_criteria": "Done"}).json()
    invalid = env.post(f"/api/v1/projects/{b}/tasks", headers=owner_headers,
                       json={"title": "X", "instruction": "Y", "acceptance": "Z", "goal_id": g["id"]})
    assert invalid.status_code == 422
    agent = create_agent(env, owner_headers)
    enroll(env, owner_headers, b, agent["id"])
    auth = {"Authorization": f"Bearer {agent['token']}"}
    t = create_task(env, owner_headers, b)
    env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth, json={})
    wrong = env.post(f"/api/v1/tasks/{t['id']}/submit", headers=auth,
                     json={"epoch": 99, "summary": "bad"})
    assert wrong.status_code == 409


def test_artifact_integrity_and_project_event_isolation(env, owner_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path))
    a = create_project(env, owner_headers, "A")
    b = create_project(env, owner_headers, "B")
    agent = create_agent(env, owner_headers)
    enroll(env, owner_headers, a, agent["id"])
    agent_auth = {"Authorization": f"Bearer {agent['token']}"}
    task = create_task(env, owner_headers, a)
    claimed = env.post(f"/api/v1/tasks/{task['id']}/claim", headers=agent_auth, json={}).json()
    auth = {**agent_auth, "X-Artifact-Name": "report.txt"}
    invalid = env.post(f"/api/v1/tasks/{task['id']}/artifacts?epoch=999", headers=auth, content=b"test")
    assert invalid.status_code == 409
    resp = env.post(f"/api/v1/tasks/{task['id']}/artifacts?epoch={claimed['lease_epoch']}", headers=auth, content=b"test")
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "report.txt"
    assert len(list(tmp_path.rglob("*"))) >= 2
    files = env.get(f"/api/v1/tasks/{task['id']}/artifacts", headers=agent_auth).json()
    assert len(files) == 1
    assert files[0]["checksum"] == "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    assert env.get(f"/api/v1/projects/{b}/events", headers=agent_auth).status_code == 404
    kinds = [e["kind"] for e in env.get(f"/api/v1/projects/{a}/events", headers=agent_auth).json()]
    assert "artifact.submitted" in kinds


def test_agent_cannot_read_unpublished_decision_or_register_itself(env, owner_headers):
    a = create_project(env, owner_headers)
    bot = create_agent(env, owner_headers)
    enroll(env, owner_headers, a, bot["id"])
    auth = {"Authorization": "Bearer " + bot["token"]}
    assert env.post("/api/v1/agents", headers=auth, json={"name": "bad", "platform": "other"}).status_code == 403
    rec = env.post(f"/api/v1/projects/{a}/decisions", headers=auth,
                   json={"question": "Question", "resolution": "Maybe", "rationale": "Opinion"})
    assert rec.status_code == 201
    assert not env.get(f"/api/v1/projects/{a}/decisions", headers=auth).json()
    assert env.get("/api/v1/agents", headers=auth).status_code == 403
