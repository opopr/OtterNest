"""Tests for Agent editing, primary token rotation, and member task creation."""
from __future__ import annotations


def test_agent_update_and_token_rotation(env, owner_headers):
    # 1. Create an agent
    r = env.post("/api/v1/agents", json={
        "name": "Test Worker Agent",
        "platform": "local",
        "execution_mode": "manual",
        "capabilities": ["python", "filesystem"]
    }, headers=owner_headers)
    assert r.status_code == 201
    data = r.json()
    agent_id = data["id"]
    old_token = data["token"]
    assert old_token.startswith("on_agent_")

    # 2. Update agent details
    r_patch = env.patch(f"/api/v1/agents/{agent_id}", json={
        "name": "Renamed Worker Agent",
        "platform": "cloud",
        "execution_mode": "polling",
        "capabilities": ["python", "browser"]
    }, headers=owner_headers)
    assert r_patch.status_code == 200
    updated = r_patch.json()
    assert updated["name"] == "Renamed Worker Agent"
    assert updated["platform"] == "cloud"
    assert updated["execution_mode"] == "polling"
    assert set(updated["declared_capabilities"]) == {"python", "browser"}

    # 3. Rotate agent token
    r_token = env.post(f"/api/v1/agents/{agent_id}/token", headers=owner_headers)
    assert r_token.status_code == 200
    token_data = r_token.json()
    assert token_data["id"] == agent_id
    new_token = token_data["token"]
    assert new_token.startswith("on_agent_")
    assert new_token != old_token

    # Verify old token invalid and new token valid
    assert env.get("/api/v1/projects", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert env.get("/api/v1/projects", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200

    # 4. Create project and verify task creation by member agent
    r_p = env.post("/api/v1/projects", json={"name": "Task Creation Test"}, headers=owner_headers)
    assert r_p.status_code == 201
    project_id = r_p.json()["id"]

    # Add agent as member
    r_m = env.post(f"/api/v1/projects/{project_id}/members", json={
        "agent_id": agent_id,
        "role": "worker"
    }, headers=owner_headers)
    assert r_m.status_code == 201

    # Task creation by agent using its new token
    agent_headers = {"Authorization": f"Bearer {new_token}"}
    r_task = env.post(f"/api/v1/projects/{project_id}/tasks", json={
        "title": "Agent Created Task",
        "instruction": "Do something useful",
        "acceptance": "Output valid result",
        "target_agent_id": agent_id
    }, headers=agent_headers)
    assert r_task.status_code == 201
    task_data = r_task.json()
    assert task_data["title"] == "Agent Created Task"
    assert task_data["target_agent_id"] == agent_id
