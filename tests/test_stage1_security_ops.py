from __future__ import annotations

import io
import tarfile
from datetime import timedelta

from otternest.models import Membership, now


def test_runtime_independent_credential_and_health(env, owner_headers):
    agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "worker-1", "platform": "local", "execution_mode": "polling", "capabilities": ["code"]},
    ).json()
    rt1 = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes",
        headers=owner_headers,
        json={"label": "box-1", "environment": "linux"},
    ).json()
    rt2 = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes",
        headers=owner_headers,
        json={"label": "box-2", "environment": "windows"},
    ).json()

    tok = env.post(f"/api/v1/agents/{agent['id']}/runtimes/{rt1['id']}/token", headers=owner_headers).json()
    assert tok["token"].startswith("on_runtime_")
    rt_headers = {"Authorization": f"Bearer {tok['token']}"}

    # Runtime token cannot heartbeat a different runtime
    assert env.post(f"/api/v1/agent/runtimes/{rt2['id']}/heartbeat", headers=rt_headers, json={"status": "idle"}).status_code == 403

    hb = env.post(
        f"/api/v1/agent/runtimes/{rt1['id']}/heartbeat",
        headers=rt_headers,
        json={"status": "idle", "health": {"cpu": "ok", "disk_free_gb": 42}},
    )
    assert hb.status_code == 200
    assert hb.json()["last_health"]["cpu"] == "ok"

    runtimes = env.get(f"/api/v1/agents/{agent['id']}/runtimes", headers=owner_headers).json()
    rt1_row = [r for r in runtimes if r["id"] == rt1["id"]][0]
    assert rt1_row["has_token"] is True
    assert rt1_row["online"] is True


def test_abac_sensitivity_and_task_grants(env, owner_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path))
    proj = env.post("/api/v1/projects", headers=owner_headers, json={"name": "SecProj"}).json()
    agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "sec-agent", "platform": "local", "execution_mode": "polling", "capabilities": []},
    ).json()
    ah = {"Authorization": f"Bearer {agent['token']}"}

    env.post(
        f"/api/v1/projects/{proj['id']}/members",
        headers=owner_headers,
        json={"agent_id": agent["id"], "role": "worker", "max_sensitivity": "internal"},
    )

    # Confidential and restricted memories are hidden from internal-clearance agent
    m_pub = env.post(
        f"/api/v1/projects/{proj['id']}/memories",
        headers=owner_headers,
        json={"kind": "fact", "title": "Public", "content": "open doc", "source": "manual", "sensitivity": "public"},
    ).json()
    m_conf = env.post(
        f"/api/v1/projects/{proj['id']}/memories",
        headers=owner_headers,
        json={"kind": "fact", "title": "Secret", "content": "conf doc", "source": "manual", "sensitivity": "confidential"},
    ).json()
    env.post(f"/api/v1/memories/{m_pub['id']}/publish", headers=owner_headers)
    env.post(f"/api/v1/memories/{m_conf['id']}/publish", headers=owner_headers)

    listed = env.get(f"/api/v1/projects/{proj['id']}/memories", headers=ah).json()
    assert [m["id"] for m in listed] == [m_pub["id"]]

    # Restricted task cannot be claimed without an active TaskGrant
    t_rest = env.post(
        f"/api/v1/projects/{proj['id']}/tasks",
        headers=owner_headers,
        json={"title": "Restricted Work", "instruction": "do", "acceptance": "ok", "sensitivity": "restricted"},
    ).json()
    assert env.post(f"/api/v1/tasks/{t_rest['id']}/claim", headers=ah, json={"lease_seconds": 60}).status_code == 403

    grant = env.post(
        f"/api/v1/tasks/{t_rest['id']}/grants",
        headers=owner_headers,
        json={"agent_id": agent["id"], "granted_scopes": ["read_confidential"], "max_sensitivity": "restricted", "expires_in_hours": 4},
    )
    assert grant.status_code == 201
    claimed = env.post(f"/api/v1/tasks/{t_rest['id']}/claim", headers=ah, json={"lease_seconds": 60})
    assert claimed.status_code == 200
    epoch = claimed.json()["lease_epoch"]

    # With active restricted grant, task_context includes confidential memory
    ctx = env.get(f"/api/v1/tasks/{t_rest['id']}/context", headers=ah).json()
    assert {m["id"] for m in ctx["memories"]} == {m_pub["id"], m_conf["id"]}

    # Owner uploads a restricted artifact; agent can download while grant active, blocked after revoke
    art = env.post(
        f"/api/v1/tasks/{t_rest['id']}/artifacts",
        headers={**owner_headers, "X-Artifact-Name": "top.txt", "X-Artifact-Sensitivity": "restricted"},
        content=b"secret-payload",
    ).json()
    assert env.get(f"/api/v1/artifacts/{art['id']}/content", headers=ah).status_code == 200
    env.post(f"/api/v1/tasks/{t_rest['id']}/grants/{grant.json()['id']}/revoke", headers=owner_headers)
    assert env.get(f"/api/v1/artifacts/{art['id']}/content", headers=ah).status_code == 403
    assert epoch >= 1


def test_system_protection_modes_and_artifact_archive_gc(env, owner_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path))
    proj = env.post("/api/v1/projects", headers=owner_headers, json={"name": "OpsProj"}).json()
    agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "ops-agent", "platform": "local", "execution_mode": "polling", "capabilities": []},
    ).json()
    env.post(f"/api/v1/projects/{proj['id']}/members", headers=owner_headers, json={"agent_id": agent["id"], "role": "worker"})
    rt = env.post(f"/api/v1/agents/{agent['id']}/runtimes", headers=owner_headers, json={"label": "r1", "environment": "local"}).json()
    ah = {"Authorization": f"Bearer {agent['token']}"}
    env.post(f"/api/v1/agent/runtimes/{rt['id']}/heartbeat", headers=ah, json={"status": "idle"})

    task = env.post(
        f"/api/v1/projects/{proj['id']}/tasks",
        headers=owner_headers,
        json={"title": "Packaged Work", "instruction": "build", "acceptance": "done"},
    ).json()

    # Degraded mode pauses scheduling, allows existing claim/owner writes
    assert env.put("/api/v1/system/mode", headers=owner_headers, json={"mode": "degraded", "reason": "upstream outage check"}).status_code == 200
    assert env.post(f"/api/v1/projects/{proj['id']}/schedule", headers=owner_headers, json={"limit": 10}).status_code == 503

    # Readonly protect blocks task creation & agent claim
    assert env.put("/api/v1/system/mode", headers=owner_headers, json={"mode": "readonly_protect", "reason": "incident freeze"}).status_code == 200
    assert env.post(f"/api/v1/tasks/{task['id']}/claim", headers=ah, json={"lease_seconds": 60}).status_code == 503
    assert env.post(
        f"/api/v1/projects/{proj['id']}/tasks",
        headers=owner_headers,
        json={"title": "Blocked", "instruction": "x", "acceptance": "y"},
    ).status_code == 503

    # Restore normal mode
    assert env.put("/api/v1/system/mode", headers=owner_headers, json={"mode": "normal", "reason": "recovered"}).status_code == 200

    # Upload 3 versions of an artifact: v1 rejected, v2 unaccepted superseded, v3 accepted
    v1 = env.post(f"/api/v1/tasks/{task['id']}/artifacts", headers={**owner_headers, "X-Artifact-Name": "report.md"}, content=b"v1").json()
    v2 = env.post(f"/api/v1/tasks/{task['id']}/artifacts", headers={**owner_headers, "X-Artifact-Name": "report.md"}, content=b"v2").json()
    v3 = env.post(f"/api/v1/tasks/{task['id']}/artifacts", headers={**owner_headers, "X-Artifact-Name": "report.md"}, content=b"v3").json()
    env.post(f"/api/v1/artifacts/{v1['id']}/review", headers=owner_headers, json={"accepted": False, "note": "rejected old draft"})
    env.post(f"/api/v1/artifacts/{v3['id']}/review", headers=owner_headers, json={"accepted": True, "note": "final accepted"})

    # Export binary archive, delete local v3 file to simulate disaster, then restore
    arch = env.get(f"/api/v1/projects/{proj['id']}/artifacts/archive", headers=owner_headers)
    assert arch.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(arch.content), mode="r:gz") as tf:
        assert "manifest.json" in tf.getnames()

    v3_path = tmp_path / proj["id"] / v3["id"]
    v3_path.unlink()
    assert env.get(f"/api/v1/artifacts/{v3['id']}/content", headers=owner_headers).status_code == 410

    res = env.post(f"/api/v1/projects/{proj['id']}/artifacts/restore", headers=owner_headers, content=arch.content)
    assert res.status_code == 200
    assert v3["id"] in res.json()["restored_ids"]
    assert env.get(f"/api/v1/artifacts/{v3['id']}/content", headers=owner_headers).content == b"v3"

    # GC dry-run & execute: keep_versions=1 should remove v1 (rejected) and v2 (superseded) while preserving accepted v3
    gc_dry = env.post(f"/api/v1/projects/{proj['id']}/artifacts/gc", headers=owner_headers, json={"dry_run": True, "keep_versions": 1, "include_rejected": True}).json()
    assert v1["id"] in gc_dry["candidate_ids"]
    assert v3["id"] not in gc_dry["candidate_ids"]

    gc_run = env.post(f"/api/v1/projects/{proj['id']}/artifacts/gc", headers=owner_headers, json={"dry_run": False, "keep_versions": 1, "include_rejected": True}).json()
    assert v1["id"] in gc_run["candidate_ids"]
    assert env.get(f"/api/v1/artifacts/{v1['id']}/content", headers=owner_headers).status_code == 410
    assert env.get(f"/api/v1/artifacts/{v3['id']}/content", headers=owner_headers).content == b"v3"
