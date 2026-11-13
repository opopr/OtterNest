"""Tests for external side-effect idempotency/reconciliation, memory/skill lifecycle, cross-project shares, and export."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from otternest.mcp_bridge import MCPBridgeError, OtterNestMCPClient


def _project(client, owner, name="Lifecycle Project"):
    r = client.post("/api/v1/projects", headers=owner, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _agent(client, owner, name="Worker"):
    r = client.post(
        "/api/v1/agents",
        headers=owner,
        json={"name": name, "platform": "generic", "execution_mode": "polling", "capabilities": []},
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    return agent, {"Authorization": f"Bearer {agent['token']}"}


def _enroll(client, owner, project_id, agent_id, role="worker"):
    r = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=owner,
        json={"agent_id": agent_id, "role": role},
    )
    assert r.status_code == 201, r.text


def _task(client, owner, project_id, title="Task", risk="R1"):
    r = client.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=owner,
        json={
            "title": title,
            "instruction": "Execute step safely",
            "acceptance": "Reconciled output",
            "risk": risk,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_side_effect_idempotency_gates_and_reconciliation(env, owner_headers):
    p = _project(env, owner_headers, "Side Effect Project")
    a1, auth1 = _agent(env, owner_headers, "Worker1")
    a2, auth2 = _agent(env, owner_headers, "Worker2")
    _enroll(env, owner_headers, p, a1["id"])
    _enroll(env, owner_headers, p, a2["id"])
    t = _task(env, owner_headers, p, "External Action Task")

    c1 = env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth1, json={})
    assert c1.status_code == 200
    epoch1 = c1.json()["lease_epoch"]

    # Register idempotency key before invoking external side effect.
    se = env.post(
        f"/api/v1/tasks/{t['id']}/side-effects",
        headers=auth1,
        json={
            "epoch": epoch1,
            "idempotency_key": "github:release:v1.0.0",
            "target_system": "github.com",
            "description": "Publish release v1.0.0 tag",
        },
    )
    assert se.status_code == 201, se.text
    se_data = se.json()
    assert se_data["status"] == "pending"
    assert se_data["owner_verified"] is False
    assert se_data["reversible_by_control_plane"] is False

    # Same key cannot be registered a second time while pending/unknown/executed.
    dup = env.post(
        f"/api/v1/tasks/{t['id']}/side-effects",
        headers=auth1,
        json={
            "epoch": epoch1,
            "idempotency_key": "github:release:v1.0.0",
            "target_system": "github.com",
            "description": "Duplicate attempt",
        },
    )
    assert dup.status_code == 409

    # Worker reports unknown outcome after ambiguous network error; cannot re-report.
    rep = env.post(
        f"/api/v1/side-effects/{se_data['id']}/report",
        headers=auth1,
        json={"epoch": epoch1, "status": "unknown", "evidence_summary": "HTTP POST timed out after 30s"},
    )
    assert rep.status_code == 200
    assert rep.json()["status"] == "unknown"
    assert (
        env.post(
            f"/api/v1/side-effects/{se_data['id']}/report",
            headers=auth1,
            json={"epoch": epoch1, "status": "reported_executed", "evidence_summary": "Retry report"},
        ).status_code
        == 409
    )

    # Revoke lease: briefing flags unreconciled side effect and claim is blocked until reconciled.
    rev = env.post(
        f"/api/v1/tasks/{t['id']}/revoke-lease",
        headers=owner_headers,
        json={"reason": "Worker lost connectivity during release"},
    )
    assert rev.status_code == 200
    briefing = env.get(f"/api/v1/projects/{p}/briefing", headers=owner_headers).json()
    assert briefing["pending_owner_actions"]["side_effects_unreconciled"] == 1
    assert env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth2, json={}).status_code == 409

    # Non-owner cannot reconcile.
    assert (
        env.post(
            f"/api/v1/side-effects/{se_data['id']}/reconcile",
            headers=auth2,
            json={"status": "verified_not_executed", "note": "Checked target system"},
        ).status_code
        == 403
    )

    # Owner verifies action never happened -> key can be reopened on the next epoch.
    rec1 = env.post(
        f"/api/v1/side-effects/{se_data['id']}/reconcile",
        headers=owner_headers,
        json={"status": "verified_not_executed", "note": "Confirmed tag absent on GitHub"},
    )
    assert rec1.status_code == 200
    assert rec1.json()["owner_verified"] is True
    assert env.get(f"/api/v1/projects/{p}/briefing", headers=owner_headers).json()["pending_owner_actions"]["side_effects_unreconciled"] == 0

    c2 = env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth2, json={})
    assert c2.status_code == 200
    epoch2 = c2.json()["lease_epoch"]
    reopened = env.post(
        f"/api/v1/tasks/{t['id']}/side-effects",
        headers=auth2,
        json={
            "epoch": epoch2,
            "idempotency_key": "github:release:v1.0.0",
            "target_system": "github.com",
            "description": "Retry release publish after owner confirmed absent",
        },
    )
    assert reopened.status_code == 201
    assert reopened.json()["id"] == se_data["id"]
    assert reopened.json()["status"] == "pending"

    rep2 = env.post(
        f"/api/v1/side-effects/{se_data['id']}/report",
        headers=auth2,
        json={"epoch": epoch2, "status": "reported_executed", "evidence_summary": "Created release id 987"},
    )
    assert rep2.status_code == 200
    assert rep2.json()["status"] == "reported_executed"

    # Register a second side effect that remains pending when worker fails the task; unblock must stay locked.
    se2 = env.post(
        f"/api/v1/tasks/{t['id']}/side-effects",
        headers=auth2,
        json={
            "epoch": epoch2,
            "idempotency_key": "slack:notify:v1.0.0",
            "target_system": "slack.com",
            "description": "Post release announcement",
        },
    )
    assert se2.status_code == 201
    se2_id = se2.json()["id"]

    fail = env.post(
        f"/api/v1/tasks/{t['id']}/fail",
        headers=auth2,
        json={"epoch": epoch2, "summary": "Crash before Slack confirmation"},
    )
    assert fail.status_code == 200
    assert fail.json()["status"] == "blocked"
    assert env.post(f"/api/v1/tasks/{t['id']}/unblock", headers=owner_headers).status_code == 409

    rec2 = env.post(
        f"/api/v1/side-effects/{se2_id}/reconcile",
        headers=owner_headers,
        json={"status": "verified_executed", "note": "Verified Slack message timestamp in channel"},
    )
    assert rec2.status_code == 200
    unblock = env.post(f"/api/v1/tasks/{t['id']}/unblock", headers=owner_headers)
    assert unblock.status_code == 200
    assert unblock.json()["status"] == "ready"

    c3 = env.post(f"/api/v1/tasks/{t['id']}/claim", headers=auth2, json={})
    assert c3.status_code == 200
    epoch3 = c3.json()["lease_epoch"]
    # Already-executed keys remain permanently blocked across subsequent epochs.
    for key in ("github:release:v1.0.0", "slack:notify:v1.0.0"):
        assert (
            env.post(
                f"/api/v1/tasks/{t['id']}/side-effects",
                headers=auth2,
                json={"epoch": epoch3, "idempotency_key": key, "target_system": "x", "description": "Retry"},
            ).status_code
            == 409
        )

    handoff = env.get(f"/api/v1/tasks/{t['id']}/handoff", headers=owner_headers)
    assert handoff.status_code == 200
    assert len(handoff.json()["side_effects"]) == 2
    listed = env.get(f"/api/v1/tasks/{t['id']}/side-effects", headers=auth2)
    assert listed.status_code == 200
    assert [row["idempotency_key"] for row in listed.json()] == ["github:release:v1.0.0", "slack:notify:v1.0.0"]

    # Verify OtterNestMCPClient helpers + input validation.
    seen = []

    def serve_mcp(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/side-effects"):
            return httpx.Response(201, json={"id": "se-1", "status": "pending"})
        return httpx.Response(200, json={"id": "se-1", "status": "reported_executed"})

    mcp = OtterNestMCPClient("https://otter.example", a1["token"], transport=httpx.MockTransport(serve_mcp))
    try:
        assert mcp.register_side_effect("task-1", 2, "key-1", "stripe", "Charge invoice")["id"] == "se-1"
        assert mcp.report_side_effect("se-1", 2, "reported_executed", "Charge ch_123")["status"] == "reported_executed"
        with pytest.raises(MCPBridgeError):
            mcp.register_side_effect("task-1", 2, "   ", "stripe", "Empty key")
        with pytest.raises(MCPBridgeError):
            mcp.report_side_effect("se-1", 2, "verified_executed", "Agent cannot self-verify")
    finally:
        mcp.close()
    assert seen[0][0] == "POST" and seen[0][1] == "/api/v1/tasks/task-1/side-effects"
    assert seen[1][0] == "POST" and seen[1][1] == "/api/v1/side-effects/se-1/report"


def test_memory_revision_archival_and_skill_evaluation_lifecycle(env, owner_headers):
    p = _project(env, owner_headers, "Knowledge Lifecycle")
    other_p = _project(env, owner_headers, "Foreign Project")
    worker, w_auth = _agent(env, owner_headers, "Worker")
    outsider, o_auth = _agent(env, owner_headers, "Outsider")
    _enroll(env, owner_headers, p, worker["id"])
    t = _task(env, owner_headers, p, "Context Task")
    assert env.post(f"/api/v1/tasks/{t['id']}/claim", headers=w_auth, json={}).status_code == 200
    t_other = _task(env, owner_headers, other_p, "Other Task")

    # Propose & publish memory.
    m = env.post(
        f"/api/v1/projects/{p}/memories",
        headers=w_auth,
        json={"kind": "fact", "title": "API Rate Limit", "content": "100 rpm", "source": "docs/v1"},
    )
    assert m.status_code == 201
    m_id = m.json()["id"]
    assert env.post(f"/api/v1/memories/{m_id}/publish", headers=owner_headers).status_code == 200

    # Non-owner cannot revise or archive.
    assert (
        env.post(
            f"/api/v1/memories/{m_id}/revise",
            headers=w_auth,
            json={"title": "API Limit", "content": "200 rpm", "source": "docs/v2", "reason": "Unauthorized edit"},
        ).status_code
        == 403
    )
    assert (
        env.post(
            f"/api/v1/memories/{m_id}/archive",
            headers=w_auth,
            json={"reason": "Unauthorized archive"},
        ).status_code
        == 403
    )

    rev = env.post(
        f"/api/v1/memories/{m_id}/revise",
        headers=owner_headers,
        json={"title": "API Rate Limit", "content": "250 rpm burst", "source": "docs/v2", "reason": "Updated quota tier"},
    )
    assert rev.status_code == 200
    assert rev.json()["version"] == 3
    ctx = env.get(f"/api/v1/tasks/{t['id']}/context", headers=w_auth).json()
    assert [row["content"] for row in ctx["memories"]] == ["250 rpm burst"]

    arch = env.post(
        f"/api/v1/memories/{m_id}/archive",
        headers=owner_headers,
        json={"reason": "Superseded by gateway v3 docs"},
    )
    assert arch.status_code == 200
    assert arch.json()["status"] == "archived"
    assert arch.json()["version"] == 4

    # Archived memory disappears from agent context & list, remains visible to owner, and blocks revise/re-archive.
    ctx_after = env.get(f"/api/v1/tasks/{t['id']}/context", headers=w_auth).json()
    assert ctx_after["memories"] == []
    assert env.get(f"/api/v1/projects/{p}/memories", headers=w_auth).json() == []
    assert len(env.get(f"/api/v1/projects/{p}/memories", headers=owner_headers).json()) == 1
    assert (
        env.post(
            f"/api/v1/memories/{m_id}/revise",
            headers=owner_headers,
            json={"title": "X", "content": "Y", "source": "Z", "reason": "Should fail"},
        ).status_code
        == 409
    )
    assert env.post(f"/api/v1/memories/{m_id}/archive", headers=owner_headers, json={"reason": "Again"}).status_code == 409

    # Skill proposal, publish, evaluation, and archival.
    sk = env.post(
        f"/api/v1/projects/{p}/skills",
        headers=w_auth,
        json={"name": "deploy-check", "version": "1.0.0", "content": "Run health probes", "source": "runbook"},
    )
    assert sk.status_code == 201
    sk_id = sk.json()["id"]
    assert env.post(f"/api/v1/skills/{sk_id}/publish", headers=owner_headers).status_code == 200

    # Foreign project task rejected; outsider rejected.
    assert (
        env.post(
            f"/api/v1/skills/{sk_id}/evaluations",
            headers=w_auth,
            json={"verdict": "pass", "notes": "Worked well", "task_id": t_other["id"]},
        ).status_code
        == 422
    )
    assert (
        env.post(
            f"/api/v1/skills/{sk_id}/evaluations",
            headers=o_auth,
            json={"verdict": "fail", "notes": "Outsider cannot evaluate"},
        ).status_code
        in (403, 404)
    )

    ev = env.post(
        f"/api/v1/skills/{sk_id}/evaluations",
        headers=w_auth,
        json={"verdict": "pass", "notes": "Caught missing DB migration cleanly", "task_id": t["id"]},
    )
    assert ev.status_code == 201
    assert env.get(f"/api/v1/skills/{sk_id}/evaluations", headers=w_auth).json()[0]["verdict"] == "pass"
    assert env.get(f"/api/v1/skills/{sk_id}/evaluations", headers=o_auth).status_code in (403, 404)

    assert env.post(f"/api/v1/skills/{sk_id}/archive", headers=w_auth, json={"reason": "Only owner"}).status_code == 403
    sk_arch = env.post(
        f"/api/v1/skills/{sk_id}/archive",
        headers=owner_headers,
        json={"reason": "Replaced by v2 probe checklist"},
    )
    assert sk_arch.status_code == 200
    assert sk_arch.json()["status"] == "archived"
    assert env.get(f"/api/v1/projects/{p}/skills", headers=w_auth).json() == []


def test_cross_project_knowledge_share_and_portable_export(env, owner_headers):
    p1 = _project(env, owner_headers, "Source Project")
    p2 = _project(env, owner_headers, "Target Project")
    a_both, auth_both = _agent(env, owner_headers, "BridgeAgent")
    a_p1, auth_p1 = _agent(env, owner_headers, "P1Only")
    _enroll(env, owner_headers, p1, a_both["id"])
    _enroll(env, owner_headers, p2, a_both["id"])
    _enroll(env, owner_headers, p1, a_p1["id"])

    m_pub = env.post(
        f"/api/v1/projects/{p1}/memories",
        headers=auth_p1,
        json={"kind": "experience", "title": "Retry Jitter", "content": "Use full jitter", "source": "incident-42"},
    ).json()
    assert env.post(f"/api/v1/memories/{m_pub['id']}/publish", headers=owner_headers).status_code == 200
    m_draft = env.post(
        f"/api/v1/projects/{p1}/memories",
        headers=auth_p1,
        json={"kind": "fact", "title": "Draft Secret?", "content": "Unreviewed draft", "source": "scratch"},
    ).json()
    sk_pub = env.post(
        f"/api/v1/projects/{p1}/skills",
        headers=auth_p1,
        json={"name": "pg-lock-order", "version": "1.0.0", "content": "Lock Project before Task", "source": "postmortem"},
    ).json()
    assert env.post(f"/api/v1/skills/{sk_pub['id']}/publish", headers=owner_headers).status_code == 200

    # Isolation checks: single-project member cannot propose to p2; unpublished & same-project rejected.
    assert (
        env.post(
            f"/api/v1/projects/{p1}/knowledge-shares",
            headers=auth_p1,
            json={"target_project_id": p2, "item_kind": "memory", "source_item_id": m_pub["id"], "rationale": "Share across"},
        ).status_code
        in (403, 404)
    )
    assert (
        env.post(
            f"/api/v1/projects/{p1}/knowledge-shares",
            headers=auth_both,
            json={"target_project_id": p1, "item_kind": "memory", "source_item_id": m_pub["id"], "rationale": "Same project"},
        ).status_code
        == 422
    )
    assert (
        env.post(
            f"/api/v1/projects/{p1}/knowledge-shares",
            headers=auth_both,
            json={"target_project_id": p2, "item_kind": "memory", "source_item_id": m_draft["id"], "rationale": "Draft item"},
        ).status_code
        == 409
    )

    sh_mem = env.post(
        f"/api/v1/projects/{p1}/knowledge-shares",
        headers=auth_both,
        json={"target_project_id": p2, "item_kind": "memory", "source_item_id": m_pub["id"], "rationale": "Reusable across teams"},
    )
    assert sh_mem.status_code == 201
    sh_skill = env.post(
        f"/api/v1/projects/{p1}/knowledge-shares",
        headers=auth_both,
        json={"target_project_id": p2, "item_kind": "skill", "source_item_id": sk_pub["id"], "rationale": "Share DB discipline"},
    )
    assert sh_skill.status_code == 201

    # Pending proposals do not leak into p2 yet; non-owner cannot resolve.
    assert env.get(f"/api/v1/projects/{p2}/memories", headers=auth_both).json() == []
    assert (
        env.post(
            f"/api/v1/knowledge-shares/{sh_mem.json()['id']}/resolve",
            headers=auth_both,
            json={"approved": True, "note": "Self approve attempt"},
        ).status_code
        == 403
    )

    # Approve memory share -> cloned with provenance into p2.
    res_mem = env.post(
        f"/api/v1/knowledge-shares/{sh_mem.json()['id']}/resolve",
        headers=owner_headers,
        json={"approved": True, "note": "Verified safe generic retry advice"},
    )
    assert res_mem.status_code == 200
    assert res_mem.json()["status"] == "approved"
    promoted_id = res_mem.json()["promoted_item_id"]
    p2_memories = env.get(f"/api/v1/projects/{p2}/memories", headers=auth_both).json()
    assert len(p2_memories) == 1
    assert p2_memories[0]["id"] == promoted_id
    assert p2_memories[0]["source"].startswith(f"cross_project:{p1}:memory:{m_pub['id']}")

    # Reject skill share -> p2 skills untouched.
    res_sk = env.post(
        f"/api/v1/knowledge-shares/{sh_skill.json()['id']}/resolve",
        headers=owner_headers,
        json={"approved": False, "note": "Keep DB skill scoped to source repo"},
    )
    assert res_sk.status_code == 200
    assert res_sk.json()["status"] == "rejected"
    assert res_sk.json()["promoted_item_id"] is None
    assert env.get(f"/api/v1/projects/{p2}/skills", headers=auth_both).json() == []

    # Portable project export: owner-only, includes full lifecycle state, valid digest, zero secrets.
    assert env.get(f"/api/v1/projects/{p2}/export", headers=auth_both).status_code == 403
    exp = env.get(f"/api/v1/projects/{p2}/export", headers=owner_headers)
    assert exp.status_code == 200, exp.text
    bundle = exp.json()
    assert bundle["schema_version"] == "otternest.export.v1"
    assert bundle["secrets_included"] is False
    assert len(bundle["memories"]) == 1
    assert len(bundle["cross_project_shares"]) == 2
    assert "charter" in bundle and "side_effects" in bundle and "skill_evaluations" in bundle

    bundle_copy = {
        k: v
        for k, v in bundle.items()
        if k not in ("schema_version", "exported_at", "secrets_included", "sha256_digest")
    }
    expected_digest = hashlib.sha256(
        json.dumps(bundle_copy, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert bundle["sha256_digest"] == expected_digest

    raw_export = json.dumps(bundle, ensure_ascii=False)
    assert "on_agent_" not in raw_export
    assert "token_hash" not in raw_export
