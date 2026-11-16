from __future__ import annotations

import json
import sys

import httpx
import pytest

from otternest.runner import Runner, RunnerFailure


def _create_agent_runtime_and_task(env, owner_headers):
    project_response = env.post("/api/v1/projects", headers=owner_headers, json={"name": "Runtime scoped execution"})
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]

    agent_response = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "Runtime worker", "platform": "local", "execution_mode": "polling", "capabilities": []},
    )
    assert agent_response.status_code == 201, agent_response.text
    agent = agent_response.json()

    member_response = env.post(
        f"/api/v1/projects/{project_id}/members",
        headers=owner_headers,
        json={"agent_id": agent["id"], "role": "worker"},
    )
    assert member_response.status_code == 201, member_response.text

    task_response = env.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=owner_headers,
        json={
            "title": "Runtime token execution",
            "instruction": "Execute a small deterministic task",
            "acceptance": "Runner submits the result for owner review",
        },
    )
    assert task_response.status_code == 201, task_response.text

    runtime_response = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes",
        headers=owner_headers,
        json={"label": "runtime-primary", "environment": "pytest"},
    )
    assert runtime_response.status_code == 201, runtime_response.text
    runtime_id = runtime_response.json()["id"]

    second_runtime_response = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes",
        headers=owner_headers,
        json={"label": "runtime-secondary", "environment": "pytest"},
    )
    assert second_runtime_response.status_code == 201, second_runtime_response.text
    second_runtime_id = second_runtime_response.json()["id"]

    token_response = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes/{runtime_id}/token",
        headers=owner_headers,
    )
    assert token_response.status_code == 200, token_response.text

    return project_id, task_response.json()["id"], runtime_id, second_runtime_id, token_response.json()["token"]


def _asgi_runner(env, token, runtime_id):
    runner = Runner(
        "http://127.0.0.1:8000",
        token,
        [
            sys.executable,
            "-c",
            "import json,sys; task=json.load(sys.stdin)['task']; print(json.dumps({'summary':'executed: '+task['title']}))",
        ],
        timeout=30,
        runtime_id=runtime_id,
    )
    heartbeat_states = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer " + token
        if request.url.path.endswith("/heartbeat") and "/agent/runtimes/" in request.url.path:
            heartbeat_states.append(json.loads(request.content)["status"])
        response = env.request(
            request.method,
            request.url.path,
            params=dict(request.url.params),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    runner.client.close()
    runner.client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer " + token},
    )
    return runner, heartbeat_states


def test_runtime_token_is_bound_to_one_runtime(env, owner_headers):
    _, _, runtime_id, second_runtime_id, token = _create_agent_runtime_and_task(env, owner_headers)

    wrong_runtime = env.post(
        f"/api/v1/agent/runtimes/{second_runtime_id}/heartbeat",
        headers={"Authorization": "Bearer " + token},
        json={"status": "idle"},
    )
    assert wrong_runtime.status_code == 403

    correct_runtime = env.post(
        f"/api/v1/agent/runtimes/{runtime_id}/heartbeat",
        headers={"Authorization": "Bearer " + token},
        json={"status": "idle"},
    )
    assert correct_runtime.status_code == 200


def test_runner_executes_with_runtime_token_and_reports_heartbeats(env, owner_headers):
    _, task_id, runtime_id, _, token = _create_agent_runtime_and_task(env, owner_headers)
    runner, heartbeat_states = _asgi_runner(env, token, runtime_id)
    try:
        assert runner.run_one() is True
    finally:
        runner.close()

    assert heartbeat_states[0] == "idle"
    assert "busy" in heartbeat_states
    assert heartbeat_states[-1] == "idle"

    task = env.get(f"/api/v1/tasks/{task_id}", headers=owner_headers)
    assert task.status_code == 200
    assert task.json()["status"] == "submitted"
    assert task.json()["result_summary"] == "executed: Runtime token execution"

    runtimes = env.get("/api/v1/runtimes", headers=owner_headers)
    assert runtimes.status_code == 200
    runtime = next(row for row in runtimes.json() if row["id"] == runtime_id)
    assert runtime["status"] == "idle"
    assert runtime["online"] is True


def test_runner_does_not_pass_runtime_token_to_worker(monkeypatch):
    from otternest.runner import worker_environment

    monkeypatch.setenv("OTTERNEST_RUNTIME_TOKEN", "on_runtime_private")
    with pytest.raises(RunnerFailure, match="control-plane credentials"):
        worker_environment(("OTTERNEST_RUNTIME_TOKEN",))
