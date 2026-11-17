"""Tests for Agent disable/enable, soft-delete recycle bin, restore, and permanent deletion."""
from __future__ import annotations


def test_agent_disable_enable_and_recycle_bin_lifecycle(env, owner_headers):
    created = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "temp-bot", "platform": "local", "execution_mode": "manual", "capabilities": ["python"]},
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]
    agent_token = created.json()["token"]
    agent_headers = {"Authorization": f"Bearer {agent_token}"}

    # 1. Disable and re-enable
    assert env.post(f"/api/v1/agents/{agent_id}/disable", headers=owner_headers).status_code == 200
    assert env.get("/api/v1/projects", headers=agent_headers).status_code == 401
    assert env.post(f"/api/v1/agents/{agent_id}/enable", headers=owner_headers).status_code == 200
    assert env.get("/api/v1/projects", headers=agent_headers).status_code == 200

    # 2. Soft-delete into recycle bin
    deleted = env.delete(f"/api/v1/agents/{agent_id}", headers=owner_headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"] is not None
    assert env.get("/api/v1/projects", headers=agent_headers).status_code == 401

    active_ids = [a["id"] for a in env.get("/api/v1/agents", headers=owner_headers).json()]
    assert agent_id not in active_ids

    all_agents = env.get("/api/v1/agents", params={"include_deleted": "true"}, headers=owner_headers).json()
    assert any(a["id"] == agent_id and a["deleted_at"] is not None for a in all_agents)

    # 3. Restore from recycle bin
    restored = env.post(f"/api/v1/agents/{agent_id}/restore", headers=owner_headers)
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert restored.json()["enabled"] is True
    assert env.get("/api/v1/projects", headers=agent_headers).status_code == 200

    # 4. Permanent delete requires recycle bin first
    assert env.delete(f"/api/v1/agents/{agent_id}/permanent", headers=owner_headers).status_code == 409

    # Move back to recycle bin and purge permanently
    assert env.delete(f"/api/v1/agents/{agent_id}", headers=owner_headers).status_code == 200
    purged = env.delete(f"/api/v1/agents/{agent_id}/permanent", headers=owner_headers)
    assert purged.status_code == 200
    assert purged.json()["permanently_deleted"] is True

    all_after = env.get("/api/v1/agents", params={"include_deleted": "true"}, headers=owner_headers).json()
    assert all(a["id"] != agent_id for a in all_after)


def test_cannot_delete_agent_with_running_task(env, owner_headers):
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "LockProj"}).json()["id"]
    agent_id = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "busy-bot", "platform": "local", "execution_mode": "polling", "capabilities": []},
    ).json()["id"]
    env.post(
        f"/api/v1/projects/{pid}/members",
        headers=owner_headers,
        json={"agent_id": agent_id, "role": "worker"},
    )
    tid = env.post(
        f"/api/v1/projects/{pid}/tasks",
        headers=owner_headers,
        json={"title": "Running job", "instruction": "do work", "acceptance": "done", "risk": "R0"},
    ).json()["id"]
    # Claim task by agent
    claim_res = env.post(
        f"/api/v1/tasks/{tid}/claim",
        headers=owner_headers,
        json={"agent_id": agent_id, "lease_seconds": 60},
    )
    if claim_res.status_code == 200:
        assert env.delete(f"/api/v1/agents/{agent_id}", headers=owner_headers).status_code == 409
