"""Tests for updating member role, removing project members, and deleting tasks."""
from __future__ import annotations


def test_update_member_role_and_remove_member(env, owner_headers):
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "MemberMgmtProj"}).json()["id"]
    agent_id = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "muse-test", "platform": "local", "execution_mode": "manual", "capabilities": []},
    ).json()["id"]

    added = env.post(
        f"/api/v1/projects/{pid}/members",
        headers=owner_headers,
        json={"agent_id": agent_id, "role": "worker"},
    )
    assert added.status_code == 201
    assert added.json()["role"] == "worker"

    # Change role worker -> manager
    updated = env.patch(
        f"/api/v1/projects/{pid}/members/{agent_id}",
        headers=owner_headers,
        json={"role": "manager"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "manager"

    members = env.get(f"/api/v1/projects/{pid}/members", headers=owner_headers).json()
    assert len(members) == 1
    assert members[0]["role"] == "manager"

    # A project must retain a manager when changing roles or removing members.
    assert env.patch(f"/api/v1/projects/{pid}/members/{agent_id}", headers=owner_headers, json={"role": "worker"}).status_code == 409
    assert env.delete(f"/api/v1/projects/{pid}/members/{agent_id}", headers=owner_headers).status_code == 409

    backup_id = env.post(
        "/api/v1/agents", headers=owner_headers,
        json={"name": "backup-manager", "platform": "local", "execution_mode": "manual", "capabilities": []},
    ).json()["id"]
    assert env.post(f"/api/v1/projects/{pid}/members", headers=owner_headers,
                    json={"agent_id": backup_id, "role": "manager"}).status_code == 201

    # Remove member after another manager takes the role.
    removed = env.delete(f"/api/v1/projects/{pid}/members/{agent_id}", headers=owner_headers)
    assert removed.status_code == 200
    assert removed.json()["removed"] is True

    members_after = env.get(f"/api/v1/projects/{pid}/members", headers=owner_headers).json()
    assert len(members_after) == 1
    assert members_after[0]["agent_id"] == backup_id


def test_delete_task_and_guard_running_task(env, owner_headers):
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "TaskDeleteProj"}).json()["id"]
    t1 = env.post(
        f"/api/v1/projects/{pid}/tasks",
        headers=owner_headers,
        json={"title": "Task 1", "instruction": "step 1", "acceptance": "ok", "risk": "R0"},
    ).json()["id"]
    t2 = env.post(
        f"/api/v1/projects/{pid}/tasks",
        headers=owner_headers,
        json={"title": "Task 2", "instruction": "step 2", "acceptance": "ok", "risk": "R0", "dependency_ids": [t1]},
    ).json()["id"]

    # Delete t2 (which has a dependency on t1)
    del_t2 = env.delete(f"/api/v1/tasks/{t2}", headers=owner_headers)
    assert del_t2.status_code == 200
    assert del_t2.json()["deleted"] is True
    assert env.get(f"/api/v1/tasks/{t2}", headers=owner_headers).status_code == 404

    # Claim t1 by an agent and verify running task cannot be deleted
    agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "runner-1", "platform": "local", "execution_mode": "polling", "capabilities": []},
    ).json()
    env.post(
        f"/api/v1/projects/{pid}/members",
        headers=owner_headers,
        json={"agent_id": agent["id"], "role": "worker"},
    )
    claim_res = env.post(
        f"/api/v1/tasks/{t1}/claim",
        headers={"Authorization": f"Bearer {agent['token']}"},
        json={"lease_seconds": 60},
    )
    assert claim_res.status_code == 200

    # Running task cannot be deleted (409) and member holding running task cannot be removed (409)
    assert env.delete(f"/api/v1/tasks/{t1}", headers=owner_headers).status_code == 409
    assert env.delete(f"/api/v1/projects/{pid}/members/{agent['id']}", headers=owner_headers).status_code == 409

    # Cancel task, then delete it cleanly
    assert env.post(f"/api/v1/tasks/{t1}/cancel", headers=owner_headers).status_code == 200
    assert env.delete(f"/api/v1/tasks/{t1}", headers=owner_headers).status_code == 200
    assert env.get(f"/api/v1/tasks/{t1}", headers=owner_headers).status_code == 404
