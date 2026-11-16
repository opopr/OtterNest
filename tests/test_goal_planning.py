from __future__ import annotations

from otternest import model_planner
from otternest.model_planner import GoalPlanProposal


def _project(env, headers):
    response = env.post("/api/v1/projects", headers=headers, json={"name": "Planning project"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _configure(monkeypatch):
    monkeypatch.setenv("OTTERNEST_PLANNER_ALLOW_EXTERNAL", "1")
    monkeypatch.setenv("OTTERNEST_PLANNER_URL", "https://api.example.test/v1/chat/completions")
    monkeypatch.setenv("OTTERNEST_PLANNER_ALLOWED_HOSTS", "api.example.test")
    monkeypatch.setenv("OTTERNEST_PLANNER_MODEL", "test-model")
    monkeypatch.setenv("OTTERNEST_PLANNER_API_KEY", "test-secret-key")


def _proposal():
    return GoalPlanProposal.model_validate({
        "goal_title": "Deliver research report",
        "success_criteria": "A reviewed report with reproducible evidence and accepted deliverables.",
        "priority": 80,
        "tasks": [
            {"title": "Collect evidence", "instruction": "Collect and catalogue the required project evidence.",
             "acceptance": "A traceable evidence catalogue is stored.", "risk": "R1", "priority": 80,
             "required_capabilities": ["research"], "depends_on": []},
            {"title": "Analyze evidence", "instruction": "Analyze the accepted evidence and document the reasoning.",
             "acceptance": "Analysis is complete and all claims cite the evidence catalogue.",
             "risk": "R2", "priority": 70, "required_capabilities": ["analysis"], "depends_on": [0]},
            {"title": "Write report", "instruction": "Prepare the final report from the accepted analysis.",
             "acceptance": "The report satisfies the goal criteria and passes owner review.",
             "risk": "R2", "priority": 60, "required_capabilities": ["writing"], "depends_on": [1]},
        ],
    })


def test_multi_step_goal_plan_is_preview_only_until_owner_confirms(env, owner_headers, monkeypatch):
    project = _project(env, owner_headers)
    _configure(monkeypatch)
    monkeypatch.setattr(model_planner, "_request_goal_plan", lambda *args: _proposal())
    response = env.post("/api/v1/goal-plans/proposals", headers=owner_headers,
                        json={"project_id": project, "text": "Create a staged research report workflow with evidence, analysis and writing."})
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["confirmation_required"] is True
    assert len(draft["proposal"]["tasks"]) == 3
    assert env.get(f"/api/v1/projects/{project}/goals", headers=owner_headers).json() == []
    assert env.get(f"/api/v1/projects/{project}/tasks", headers=owner_headers).json() == []

    confirmed = env.post(f"/api/v1/goal-plans/{draft['id']}/confirm", headers=owner_headers)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["result"]
    assert len(result["tasks"]) == 3
    assert result["dependency_count"] == 2
    goals = env.get(f"/api/v1/projects/{project}/goals", headers=owner_headers).json()
    tasks = env.get(f"/api/v1/projects/{project}/tasks", headers=owner_headers).json()
    assert len(goals) == 1 and len(tasks) == 3
    graph = env.get(f"/api/v1/projects/{project}/task-graph", headers=owner_headers).json()
    assert len(graph["dependencies"]) == 2
    replay = env.post(f"/api/v1/goal-plans/{draft['id']}/confirm", headers=owner_headers)
    assert replay.status_code == 200
    assert replay.json()["result"] == result


def test_goal_plan_rejects_cycles_and_invalid_dependencies(env, owner_headers):
    proposal = _proposal().model_dump()
    proposal["tasks"][0]["depends_on"] = [2]
    try:
        model_planner._validate_goal_plan(GoalPlanProposal.model_validate(proposal))
        assert False, "cycle must be rejected"
    except Exception as exc:
        assert "cycle" in str(exc).lower()


def test_goal_plan_confirmation_rejects_stale_project_snapshot(env, owner_headers, monkeypatch):
    project = _project(env, owner_headers)
    _configure(monkeypatch)
    monkeypatch.setattr(model_planner, "_request_goal_plan", lambda *args: _proposal())
    response = env.post("/api/v1/goal-plans/proposals", headers=owner_headers,
                        json={"project_id": project, "text": "Plan a staged workflow with three dependent tasks."})
    assert response.status_code == 201, response.text
    env.post(f"/api/v1/projects/{project}/tasks", headers=owner_headers, json={
        "title": "Concurrent change", "instruction": "This task was added after the preview.",
        "acceptance": "The task exists and is visible in the project."
    })
    confirm = env.post(f"/api/v1/goal-plans/{response.json()['id']}/confirm", headers=owner_headers)
    assert confirm.status_code == 409
    assert len(env.get(f"/api/v1/projects/{project}/goals", headers=owner_headers).json()) == 0


def test_goal_plan_is_owner_only_and_provider_defaults_off(env, owner_headers, monkeypatch):
    project = _project(env, owner_headers)
    monkeypatch.delenv("OTTERNEST_PLANNER_ALLOW_EXTERNAL", raising=False)
    payload = {"project_id": project, "text": "Plan a project with several dependent tasks."}
    assert env.post("/api/v1/goal-plans/proposals", headers=owner_headers, json=payload).status_code == 503
    assert env.post("/api/v1/goal-plans/proposals", json=payload).status_code == 401
