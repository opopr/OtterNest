"""Project charter budgets, epoch-fenced checkpoints, lease revocation, and operational briefings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from otternest.mcp_bridge import MCPBridgeError, OtterNestMCPClient


def _project(client, owner, name="Charter Project"):
    r = client.post("/api/v1/projects", headers=owner, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _agent(client, owner, name="Worker", *, mode="polling", caps=None):
    r = client.post(
        "/api/v1/agents",
        headers=owner,
        json={
            "name": name,
            "platform": "generic",
            "execution_mode": mode,
            "capabilities": caps or [],
        },
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    if caps:
        v = client.put(
            f"/api/v1/agents/{agent['id']}/verified-capabilities",
            headers=owner,
            json={"capabilities": caps},
        )
        assert v.status_code == 200, v.text
    return agent, {"Authorization": f"Bearer {agent['token']}"}


def _enroll(client, owner, project_id, agent_id, role="worker"):
    r = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=owner,
        json={"agent_id": agent_id, "role": role},
    )
    assert r.status_code == 201, r.text


def _task(client, owner, project_id, title="Task", risk="R1", caps=None):
    r = client.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=owner,
        json={
            "title": title,
            "instruction": "Execute cleanly",
            "acceptance": "Verified output",
            "risk": risk,
            "required_capabilities": caps or [],
        },
    )
    return r


def test_charter_validation_total_budget_and_a2a_cap(env, owner_headers):
    p = _project(env, owner_headers)
    manager, manager_auth = _agent(env, owner_headers, "Manager")
    _enroll(env, owner_headers, p, manager["id"], role="manager")

    # Non-owner cannot mutate charter; invalid bounds rejected.
    assert (
        env.put(
            f"/api/v1/projects/{p}/charter",
            headers=manager_auth,
            json={"max_concurrent_tasks": 1, "max_total_tasks": 2},
        ).status_code
        == 403
    )
    assert (
        env.put(
            f"/api/v1/projects/{p}/charter",
            headers=owner_headers,
            json={"max_concurrent_tasks": 5, "max_total_tasks": 3},
        ).status_code
        == 422
    )

    put = env.put(
        f"/api/v1/projects/{p}/charter",
        headers=owner_headers,
        json={
            "scope_boundary": "Only staging data",
            "stop_conditions": "Stop on schema mismatch",
            "max_concurrent_tasks": 1,
            "max_total_tasks": 2,
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["configured"] is True

    t1 = _task(env, owner_headers, p, "One")
    assert t1.status_code == 201
    t2 = _task(env, owner_headers, p, "Two")
    assert t2.status_code == 201

    # Third non-cancelled task blocked via REST and A2A message/send.
    t3 = _task(env, owner_headers, p, "Three")
    assert t3.status_code == 409

    a2a_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-budget-1",
                "parts": [{"kind": "text", "text": "Extra task"}],
            },
            "metadata": {"projectId": p, "title": "A2A Extra", "risk": "R1"},
        },
    }
    a2a_resp = env.post("/a2a", headers=manager_auth, json=a2a_body)
    assert a2a_resp.status_code == 200
    assert a2a_resp.json()["error"]["code"] == -32004

    # Cancelling one task frees capacity for one new A2A task, and idempotent replay keeps working at budget cap.
    assert env.post(f"/api/v1/tasks/{t2.json()['id']}/cancel", headers=owner_headers).status_code == 200
    ok_a2a = env.post("/a2a", headers=manager_auth, json=a2a_body)
    assert ok_a2a.status_code == 200
    assert "result" in ok_a2a.json()
    replay_a2a = env.post("/a2a", headers=manager_auth, json=a2a_body)
    assert replay_a2a.status_code == 200
    assert replay_a2a.json()["result"]["id"] == ok_a2a.json()["result"]["id"]


def test_charter_concurrency_budget_enforced_in_claim_and_scheduler(env, owner_headers):
    p = _project(env, owner_headers)
    a1, auth1 = _agent(env, owner_headers, "A1")
    a2, auth2 = _agent(env, owner_headers, "A2")
    _enroll(env, owner_headers, p, a1["id"])
    _enroll(env, owner_headers, p, a2["id"])

    # Register live runtimes for both polling agents.
    rt1 = env.post(f"/api/v1/agents/{a1['id']}/runtimes", headers=owner_headers, json={"label": "r1", "environment": "vm"}).json()
    rt2 = env.post(f"/api/v1/agents/{a2['id']}/runtimes", headers=owner_headers, json={"label": "r2", "environment": "vm"}).json()
    assert env.post(f"/api/v1/agent/runtimes/{rt1['id']}/heartbeat", headers=auth1, json={"status": "idle"}).status_code == 200
    assert env.post(f"/api/v1/agent/runtimes/{rt2['id']}/heartbeat", headers=auth2, json={"status": "idle"}).status_code == 200

    assert (
        env.put(
            f"/api/v1/projects/{p}/charter",
            headers=owner_headers,
            json={"max_concurrent_tasks": 1, "max_total_tasks": 10},
        ).status_code
        == 200
    )

    t1 = _task(env, owner_headers, p, "First").json()
    t2 = _task(env, owner_headers, p, "Second").json()

    # Scheduler can route at most 1 task due to max_concurrent_tasks=1.
    sched = env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={"limit": 10})
    assert sched.status_code == 200
    assert sched.json()["count"] == 1

    # Second schedule call routes 0 more tasks because one ready task is already targeted.
    sched2 = env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={"limit": 10})
    assert sched2.status_code == 200
    assert sched2.json()["count"] == 0

    # Agent claiming t1 (matching target or not targeted): t1 was targeted by scheduler.
    routed_agent = sched.json()["routes"][0]["agent_id"]
    first_auth = auth1 if routed_agent == a1["id"] else auth2
    second_auth = auth2 if routed_agent == a1["id"] else auth1
    assert env.post(f"/api/v1/tasks/{t1['id']}/claim", headers=first_auth, json={}).status_code == 200

    # Claiming t2 fails due to concurrent running budget == 1.
    second_claim = env.post(f"/api/v1/tasks/{t2['id']}/claim", headers=second_auth, json={})
    assert second_claim.status_code == 409


def test_project_pause_halts_new_work_without_falsely_stopping_remote(env, owner_headers):
    p = _project(env, owner_headers)
    a, auth = _agent(env, owner_headers, "Worker")
    _enroll(env, owner_headers, p, a["id"])
    t_running = _task(env, owner_headers, p, "Running").json()
    t_ready = _task(env, owner_headers, p, "Pending").json()

    claim = env.post(f"/api/v1/tasks/{t_running['id']}/claim", headers=auth, json={})
    assert claim.status_code == 200

    assert env.post(f"/api/v1/projects/{p}/status", headers=auth, json={"status": "paused", "reason": "No auth"}).status_code == 403
    assert env.post(f"/api/v1/projects/{p}/status", headers=owner_headers, json={"status": "paused", "reason": "no"}).status_code == 422

    paused = env.post(
        f"/api/v1/projects/{p}/status",
        headers=owner_headers,
        json={"status": "paused", "reason": "Pause scheduling while verifying outputs"},
    )
    assert paused.status_code == 200, paused.text
    body = paused.json()
    assert body["status"] == "paused"
    assert body["in_flight_running_tasks"] == 1
    assert body["remote_processes_stopped"] is False

    # New creations, queue polling, scheduling, and claims are halted while paused.
    assert _task(env, owner_headers, p, "Blocked by pause").status_code == 409
    assert env.get("/api/v1/agent/queue", headers=auth).json() == []
    assert env.post(f"/api/v1/tasks/{t_ready['id']}/claim", headers=auth, json={}).status_code == 409
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).status_code == 409

    # Resume back to active restores work.
    resumed = env.post(
        f"/api/v1/projects/{p}/status",
        headers=owner_headers,
        json={"status": "active", "reason": "Verification complete, resume operations"},
    )
    assert resumed.status_code == 200
    assert env.post(f"/api/v1/tasks/{t_ready['id']}/claim", headers=auth, json={}).status_code == 200

    archived = env.post(
        f"/api/v1/projects/{p}/status",
        headers=owner_headers,
        json={"status": "archived", "reason": "Project archived after completion"},
    )
    assert archived.status_code == 200
    assert _task(env, owner_headers, p, "After archive").status_code == 409


def test_checkpoints_verification_handoff_context_and_mcp_client(env, owner_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path))
    p = _project(env, owner_headers)
    other_p = _project(env, owner_headers, "Other")
    a1, auth1 = _agent(env, owner_headers, "Primary")
    a2, auth2 = _agent(env, owner_headers, "Backup")
    _enroll(env, owner_headers, p, a1["id"])
    _enroll(env, owner_headers, p, a2["id"])

    other_task = _task(env, owner_headers, other_p, "Other task").json()
    other_art = env.post(
        f"/api/v1/tasks/{other_task['id']}/artifacts",
        headers={**owner_headers, "X-Artifact-Name": "other.txt"},
        content=b"foreign",
    ).json()

    goal = env.post(
        f"/api/v1/projects/{p}/goals",
        headers=owner_headers,
        json={"title": "Resumable Work", "success_criteria": "Checkpointed"},
    ).json()
    task = env.post(
        f"/api/v1/projects/{p}/tasks",
        headers=owner_headers,
        json={
            "title": "Long migration",
            "goal_id": goal["id"],
            "instruction": "Migrate carefully",
            "acceptance": "All rows migrated",
            "risk": "R1",
        },
    ).json()

    claimed = env.post(f"/api/v1/tasks/{task['id']}/claim", headers=auth1, json={}).json()
    epoch = claimed["lease_epoch"]

    art = env.post(
        f"/api/v1/tasks/{task['id']}/artifacts?epoch={epoch}",
        headers={**auth1, "X-Artifact-Name": "stage1.json"},
        content=b'{"step":1}',
    ).json()

    # Reject wrong actor, owner, stale epoch, and foreign artifacts.
    assert (
        env.post(
            f"/api/v1/tasks/{task['id']}/checkpoints",
            headers=owner_headers,
            json={"epoch": epoch, "summary": "Owner cannot checkpoint"},
        ).status_code
        == 403
    )
    assert (
        env.post(
            f"/api/v1/tasks/{task['id']}/checkpoints",
            headers=auth2,
            json={"epoch": epoch, "summary": "Foreign agent"},
        ).status_code
        == 409
    )
    assert (
        env.post(
            f"/api/v1/tasks/{task['id']}/checkpoints",
            headers=auth1,
            json={"epoch": epoch + 1, "summary": "Wrong epoch"},
        ).status_code
        == 409
    )
    assert (
        env.post(
            f"/api/v1/tasks/{task['id']}/checkpoints",
            headers=auth1,
            json={"epoch": epoch, "summary": "Bad art", "artifact_ids": [other_art["id"]]},
        ).status_code
        == 422
    )

    cp1 = env.post(
        f"/api/v1/tasks/{task['id']}/checkpoints",
        headers=auth1,
        json={
            "epoch": epoch,
            "summary": "Stage 1 schema prepared",
            "next_steps": "Run stage 2 backfill",
            "artifact_ids": [art["id"]],
        },
    )
    assert cp1.status_code == 201, cp1.text
    cp1_data = cp1.json()
    assert cp1_data["step_number"] == 1
    assert cp1_data["verification_status"] == "self_reported"
    assert cp1_data["verified_by_owner"] is False

    cp2 = env.post(
        f"/api/v1/tasks/{task['id']}/checkpoints",
        headers=auth1,
        json={"epoch": epoch, "summary": "Stage 2 started", "next_steps": "Verify counts"},
    )
    assert cp2.status_code == 201
    assert cp2.json()["step_number"] == 2

    # Owner verifies cp1 and rejects cp2 (rejection requires >=5 chars note).
    assert (
        env.post(
            f"/api/v1/checkpoints/{cp1_data['id']}/verify",
            headers=auth1,
            json={"status": "owner_verified"},
        ).status_code
        == 403
    )
    assert (
        env.post(
            f"/api/v1/checkpoints/{cp2.json()['id']}/verify",
            headers=owner_headers,
            json={"status": "rejected", "note": "no"},
        ).status_code
        == 422
    )
    v1 = env.post(
        f"/api/v1/checkpoints/{cp1_data['id']}/verify",
        headers=owner_headers,
        json={"status": "owner_verified", "note": "Checked stage1.json checksum"},
    )
    assert v1.status_code == 200
    assert v1.json()["verified_by_owner"] is True
    v2 = env.post(
        f"/api/v1/checkpoints/{cp2.json()['id']}/verify",
        headers=owner_headers,
        json={"status": "rejected", "note": "Missing artifact proof"},
    )
    assert v2.status_code == 200
    assert v2.json()["verification_status"] == "rejected"

    # Handoff & task_context reflect verified checkpoint separately from self-reported.
    handoff = env.get(f"/api/v1/tasks/{task['id']}/handoff", headers=auth2)
    assert handoff.status_code == 200, handoff.text
    h_json = handoff.json()
    assert h_json["latest_verified_checkpoint"]["id"] == cp1_data["id"]
    assert h_json["latest_self_reported_checkpoint"] is None
    assert len(h_json["artifacts"]) == 1
    assert "only latest_verified_checkpoint" in h_json["handoff_boundary"].lower()

    ctx = env.get(f"/api/v1/tasks/{task['id']}/context", headers=auth1)
    assert ctx.status_code == 200
    assert ctx.json()["latest_verified_checkpoint"]["id"] == cp1_data["id"]

    # MCP client wrappers work cleanly.
    def serve_mcp(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/checkpoints") and request.method == "POST":
            return httpx.Response(201, json={"id": "cp-x", "step_number": 3})
        if request.url.path.endswith("/handoff") and request.method == "GET":
            return httpx.Response(200, json={"task": {"id": "t-1"}})
        return httpx.Response(404, json={"detail": "missing"})

    with httpx.Client(transport=httpx.MockTransport(serve_mcp)) as _:
        bridge = OtterNestMCPClient(
            "https://otter.example", a1["token"], transport=httpx.MockTransport(serve_mcp)
        )
        try:
            with pytest.raises(MCPBridgeError):
                bridge.record_checkpoint("t-1", 1, "   ")
            assert bridge.record_checkpoint("t-1", 1, "progress")["step_number"] == 3
            assert bridge.task_handoff("t-1")["task"]["id"] == "t-1"
        finally:
            bridge.close()


def test_revoke_lease_fences_old_worker_and_protects_r3_and_a2a(env, owner_headers, monkeypatch):
    import json
    from sqlalchemy.orm import sessionmaker
    from otternest.a2a_outbound import dispatch_once
    from otternest.app import app
    from otternest.db import get_session

    p = _project(env, owner_headers)
    a1, auth1 = _agent(env, owner_headers, "Worker1", caps=["python"])
    a2, auth2 = _agent(env, owner_headers, "Worker2", caps=["python"])
    a3, _ = _agent(env, owner_headers, "Unqualified", caps=[])
    _enroll(env, owner_headers, p, a1["id"])
    _enroll(env, owner_headers, p, a2["id"])
    _enroll(env, owner_headers, p, a3["id"])

    t_r1 = _task(env, owner_headers, p, "R1 handoff", risk="R1", caps=["python"]).json()
    c1 = env.post(f"/api/v1/tasks/{t_r1['id']}/claim", headers=auth1, json={}).json()
    old_epoch = c1["lease_epoch"]

    # Reject unqualified handoff target.
    assert (
        env.post(
            f"/api/v1/tasks/{t_r1['id']}/revoke-lease",
            headers=owner_headers,
            json={"reason": "Worker stalled", "target_agent_id": a3["id"]},
        ).status_code
        == 422
    )

    revoked = env.post(
        f"/api/v1/tasks/{t_r1['id']}/revoke-lease",
        headers=owner_headers,
        json={"reason": "Worker stalled; hand off to Worker2", "target_agent_id": a2["id"]},
    )
    assert revoked.status_code == 200, revoked.text
    r_json = revoked.json()
    assert r_json["status"] == "ready"
    assert r_json["fenced_epoch"] == old_epoch
    assert r_json["lease_epoch"] == old_epoch + 1
    assert r_json["target_agent_id"] == a2["id"]
    assert r_json["remote_execution_stopped_verified"] is False

    # Old worker is completely fenced from heartbeat, checkpoint, and submit.
    assert env.post(f"/api/v1/tasks/{t_r1['id']}/heartbeat", headers=auth1, json={"epoch": old_epoch}).status_code == 409
    assert (
        env.post(
            f"/api/v1/tasks/{t_r1['id']}/checkpoints",
            headers=auth1,
            json={"epoch": old_epoch, "summary": "Zombie checkpoint"},
        ).status_code
        == 409
    )
    assert (
        env.post(
            f"/api/v1/tasks/{t_r1['id']}/submit",
            headers=auth1,
            json={"epoch": old_epoch, "summary": "Zombie submit"},
        ).status_code
        == 409
    )
    # New worker claims cleanly at incremented epoch.
    c2 = env.post(f"/api/v1/tasks/{t_r1['id']}/claim", headers=auth2, json={})
    assert c2.status_code == 200
    assert c2.json()["lease_epoch"] == old_epoch + 2

    # R3 task lease revocation forces blocked + approval_granted=False.
    t_r3 = _task(env, owner_headers, p, "R3 handoff", risk="R3").json()
    assert env.post(f"/api/v1/tasks/{t_r3['id']}/authorize", headers=owner_headers).status_code == 200
    assert env.post(f"/api/v1/tasks/{t_r3['id']}/claim", headers=auth1, json={}).status_code == 200
    rev_r3 = env.post(
        f"/api/v1/tasks/{t_r3['id']}/revoke-lease",
        headers=owner_headers,
        json={"reason": "High risk worker disconnected"},
    )
    assert rev_r3.status_code == 200
    assert rev_r3.json()["status"] == "blocked"
    assert rev_r3.json()["approval_granted"] is False
    assert env.post(f"/api/v1/tasks/{t_r3['id']}/claim", headers=auth2, json={}).status_code == 409

    # Active external A2A delegation blocks local revoke-lease.
    monkeypatch.setenv("OTTERNEST_A2A_ALLOWED_HOSTS", "peer.example.org")
    monkeypatch.setattr(
        "otternest.a2a_outbound.socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("93.184.215.14", 443))],
    )
    remote_agent, _ = _agent(env, owner_headers, "RemotePeer", mode="remote")
    _enroll(env, owner_headers, p, remote_agent["id"])
    ep = env.post(
        f"/api/v1/projects/{p}/a2a/remotes",
        headers=owner_headers,
        json={"agent_id": remote_agent["id"], "url": "https://peer.example.org/a2a"},
    ).json()
    monkeypatch.setenv(ep["secret_env"], "remote-secret-token-12345")
    t_a2a = _task(env, owner_headers, p, "A2A running task", risk="R1").json()
    assert (
        env.post(
            f"/api/v1/tasks/{t_a2a['id']}/a2a/delegate",
            headers=owner_headers,
            json={"endpoint_id": ep["id"]},
        ).status_code
        == 201
    )

    gen = app.dependency_overrides[get_session]()
    try:
        db_session = next(gen)
        factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    finally:
        gen.close()

    def serve_a2a(request: httpx.Request) -> httpx.Response:
        obj = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": obj["id"], "result": {"kind": "task", "id": "rem-1", "status": {"state": "working"}}},
        )

    with httpx.Client(transport=httpx.MockTransport(serve_a2a), trust_env=False) as client:
        assert dispatch_once(factory, client=client)["sent"] == 1

    assert (
        env.post(
            f"/api/v1/tasks/{t_a2a['id']}/revoke-lease",
            headers=owner_headers,
            json={"reason": "Attempt local revoke during A2A"},
        ).status_code
        == 409
    )


def test_truthful_project_briefing_distinguishes_active_and_expired_leases(env, owner_headers, monkeypatch):
    p = _project(env, owner_headers)
    a, auth = _agent(env, owner_headers, "Worker")
    _enroll(env, owner_headers, p, a["id"])

    t_active = _task(env, owner_headers, p, "Active work").json()
    t_expired = _task(env, owner_headers, p, "Expired work").json()
    _task(env, owner_headers, p, "Needs R3 approval", risk="R3")

    assert env.post(f"/api/v1/tasks/{t_active['id']}/claim", headers=auth, json={"lease_seconds": 600}).status_code == 200
    c_exp = env.post(f"/api/v1/tasks/{t_expired['id']}/claim", headers=auth, json={"lease_seconds": 30}).json()

    cp = env.post(
        f"/api/v1/tasks/{t_active['id']}/checkpoints",
        headers=auth,
        json={"epoch": 1, "summary": "Verified milestone"},
    ).json()
    assert (
        env.post(
            f"/api/v1/checkpoints/{cp['id']}/verify",
            headers=owner_headers,
            json={"status": "owner_verified"},
        ).status_code
        == 200
    )
    assert (
        env.post(
            f"/api/v1/projects/{p}/decisions",
            headers=owner_headers,
            json={"question": "Q?", "resolution": "R", "rationale": "Because"},
        ).status_code
        == 201
    )

    # Advance briefing clock by 120 seconds so the 30s lease is expired while the 600s lease remains active.
    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    monkeypatch.setattr("otternest.continuity.now", lambda: future)

    briefing = env.get(f"/api/v1/projects/{p}/briefing", headers=owner_headers)
    assert briefing.status_code == 200, briefing.text
    b = briefing.json()
    assert b["task_counts"]["running_lease_active"] == 1
    assert b["task_counts"]["running_lease_expired_unverified"] == 1
    assert b["task_counts"]["waiting_approval"] == 1
    assert b["pending_owner_actions"]["tasks_waiting_approval"] == 1
    assert b["pending_owner_actions"]["tasks_lease_expired_unverified"] == 1
    assert b["pending_owner_actions"]["decisions_proposed"] == 1
    assert len(b["recent_verified_checkpoints"]) == 1
    assert b["recent_verified_checkpoints"][0]["id"] == cp["id"]
    assert "not verified remote process execution" in b["honesty_notice"]
    assert c_exp["lease_epoch"] == 1
