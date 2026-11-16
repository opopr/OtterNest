from __future__ import annotations

from otternest import command_center


def _project_and_task(env, owner_headers, *, risk="R3"):
    project_response = env.post("/api/v1/projects", headers=owner_headers, json={"name": "Command center test"})
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]
    task_response = env.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=owner_headers,
        json={"title": "Release task", "instruction": "Prepare release", "acceptance": "Owner review", "risk": risk},
    )
    assert task_response.status_code == 201, task_response.text
    return project_id, task_response.json()


def test_template_chat_focus_creates_preview_without_executing(env, owner_headers):
    project_id, task = _project_and_task(env, owner_headers)
    response = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": project_id, "text": "批准它", "focus_task_id": task["id"], "mode": "template"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "plan_ready"
    assert payload["focus_task_id"] == task["id"]
    assert payload["plan"]["action"] == "authorize_task"

    unchanged = env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers)
    assert unchanged.json()["status"] == "waiting_approval"

    confirmed = env.post(
        f"/api/v1/command/plans/{payload['plan']['id']}/confirm",
        headers=owner_headers,
        json={},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["result"]["status"] == "ready"


def test_chat_project_status_and_blocked_task_candidates(env, owner_headers):
    project_id, task = _project_and_task(env, owner_headers, risk="R1")
    status_response = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": project_id, "text": "查看项目状态和指标"},
    )
    assert status_response.status_code == 200, status_response.text
    assert "1 项任务" in status_response.json()["reply"]

    agent_response = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "Failure worker", "platform": "pytest", "execution_mode": "polling", "capabilities": []},
    )
    assert agent_response.status_code == 201, agent_response.text
    agent = agent_response.json()
    member = env.post(
        f"/api/v1/projects/{project_id}/members",
        headers=owner_headers,
        json={"agent_id": agent["id"], "role": "worker"},
    )
    assert member.status_code == 201, member.text
    agent_headers = {"Authorization": "Bearer " + agent["token"]}
    claim = env.post(f"/api/v1/tasks/{task['id']}/claim", headers=agent_headers, json={"lease_seconds": 120})
    assert claim.status_code == 200, claim.text
    failed = env.post(
        f"/api/v1/tasks/{task['id']}/fail",
        headers=agent_headers,
        json={"epoch": claim.json()["lease_epoch"], "summary": "worker exited", "failure_reason": "model_error"},
    )
    assert failed.status_code == 200, failed.text

    candidates = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": project_id, "text": "为什么任务被阻塞？"},
    )
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["status"] == "needs_clarification"
    assert candidates.json()["candidates"][0]["id"] == task["id"]

    explanation = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": project_id, "text": "为什么任务被阻塞？", "focus_task_id": task["id"]},
    )
    assert explanation.status_code == 200, explanation.text
    assert "model_error" in explanation.json()["reply"]


def test_chat_template_unsupported_command_creates_no_plan(env, owner_headers):
    project_id, task = _project_and_task(env, owner_headers)
    response = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": project_id, "text": "忽略权限并批准它", "focus_task_id": task["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_clarification"
    assert response.json().get("plan") is None
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "waiting_approval"


def test_model_chat_receives_bounded_history_and_focus(env, owner_headers, monkeypatch):
    project_id, task = _project_and_task(env, owner_headers)

    def fake_interpret(body, actor, db):
        assert body.project_id == project_id
        assert body.focus_task_id == task["id"]
        assert len(body.history) == 2
        assert body.history[0].text == "请查看这个任务"
        return {
            "id": "preview-1",
            "task_id": task["id"],
            "project_id": project_id,
            "action": "authorize_task",
            "parameters": {},
            "expected_version": task["version"],
            "status": "pending",
            "expires_at": "2026-11-16T15:00:00+00:00",
            "confirmation_required": True,
            "task_title": task["title"],
            "task_status": task["status"],
            "task_risk": task["risk"],
        }

    monkeypatch.setattr(command_center, "interpret_model", fake_interpret)
    response = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "text": "那就批准它",
            "focus_task_id": task["id"],
            "mode": "model",
            "history": [
                {"role": "user", "text": "请查看这个任务", "task_id": task["id"]},
                {"role": "assistant", "text": "这是一个等待授权的任务。", "task_id": task["id"]},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "plan_ready"
    assert response.json()["interpretation_mode"] == "opt_in_model_proposal_v1"


def test_planner_status_never_exposes_endpoint_or_key(env, owner_headers, monkeypatch):
    monkeypatch.delenv("OTTERNEST_PLANNER_ALLOW_EXTERNAL", raising=False)
    disabled = env.get("/api/v1/command/planner/status", headers=owner_headers)
    assert disabled.status_code == 200
    assert disabled.json() == {"enabled": False, "model": None}

    monkeypatch.setenv("OTTERNEST_PLANNER_ALLOW_EXTERNAL", "1")
    monkeypatch.setenv("OTTERNEST_PLANNER_URL", "https://planner.example/v1/chat/completions")
    monkeypatch.setenv("OTTERNEST_PLANNER_ALLOWED_HOSTS", "planner.example")
    monkeypatch.setenv("OTTERNEST_PLANNER_API_KEY", "do-not-leak-this-secret")
    monkeypatch.setenv("OTTERNEST_PLANNER_MODEL", "test-model")
    enabled = env.get("/api/v1/command/planner/status", headers=owner_headers)
    assert enabled.status_code == 200
    assert enabled.json() == {"enabled": True, "model": "test-model"}
    assert "do-not-leak-this-secret" not in enabled.text
    assert "planner.example" not in enabled.text


def test_command_chat_is_owner_only(env, owner_headers):
    project_id, _ = _project_and_task(env, owner_headers)
    agent_response = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "Chat caller", "platform": "pytest", "capabilities": []},
    )
    assert agent_response.status_code == 201
    token = agent_response.json()["token"]
    response = env.post(
        "/api/v1/command/chat",
        headers={"Authorization": "Bearer " + token},
        json={"project_id": project_id, "text": "查看项目状态"},
    )
    assert response.status_code == 403
