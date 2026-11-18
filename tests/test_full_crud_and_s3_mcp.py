"""Integration tests for full CRUD (Project, Goal, Task, Webhook, A2A Remote, Workflow Rule), S3 SigV4, and MCP search."""
from __future__ import annotations

import hashlib
import httpx

from otternest.mcp_bridge import OtterNestMCPClient
from otternest.storage import S3StorageBackend


def test_project_goal_task_patch_and_delete(env, owner_headers):
    # Create & update project
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "Alpha", "description": "v1", "priority": 20}).json()["id"]
    p_upd = env.patch(f"/api/v1/projects/{pid}", headers=owner_headers, json={"name": "Alpha-2", "priority": 80})
    assert p_upd.status_code == 200
    assert p_upd.json()["name"] == "Alpha-2"
    assert p_upd.json()["priority"] == 80

    # Create & update goal
    gid = env.post(f"/api/v1/projects/{pid}/goals", headers=owner_headers, json={"title": "G1", "success_criteria": "C1"}).json()["id"]
    g_upd = env.patch(f"/api/v1/goals/{gid}", headers=owner_headers, json={"title": "G1-Updated", "priority": 90})
    assert g_upd.status_code == 200
    assert g_upd.json()["title"] == "G1-Updated"

    # Create & update task
    tid = env.post(
        f"/api/v1/projects/{pid}/tasks",
        headers=owner_headers,
        json={"goal_id": gid, "title": "T1", "instruction": "do", "acceptance": "ok", "risk": "R1"},
    ).json()["id"]
    t_upd = env.patch(f"/api/v1/tasks/{tid}", headers=owner_headers, json={"title": "T1-Edited", "risk": "R3", "priority": 99})
    assert t_upd.status_code == 200
    assert t_upd.json()["title"] == "T1-Edited"
    assert t_upd.json()["status"] == "waiting_approval"

    # Delete goal (task should remain with goal_id=None)
    assert env.delete(f"/api/v1/goals/{gid}", headers=owner_headers).status_code == 200
    t_after = env.get(f"/api/v1/tasks/{tid}", headers=owner_headers).json()
    assert t_after["goal_id"] is None

    # Delete project (cascades remaining task cleanly)
    assert env.delete(f"/api/v1/projects/{pid}", headers=owner_headers).status_code == 200
    assert all(p["id"] != pid for p in env.get("/api/v1/projects", headers=owner_headers).json())


def test_webhook_a2a_remote_and_workflow_rule_delete(env, owner_headers, monkeypatch):
    monkeypatch.setenv("OTTERNEST_WEBHOOK_ALLOWED_HOSTS", "hooks.example.com")
    monkeypatch.setenv("OTTERNEST_WEBHOOK_SIGNING_KEY", "test-webhook-signing-key-longer-than-32-chars")
    monkeypatch.setenv("OTTERNEST_A2A_ALLOWED_HOSTS", "agent.example.com")
    monkeypatch.setattr("otternest.webhooks.socket.getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("93.184.215.14", 443))])
    monkeypatch.setattr("otternest.a2a_outbound.socket.getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("93.184.215.14", 443))])
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "CleanupProj"}).json()["id"]

    # Webhook delete
    wh_id = env.post(
        f"/api/v1/projects/{pid}/webhooks",
        headers=owner_headers,
        json={"url": "https://hooks.example.com/wh", "event_prefix": "task."},
    ).json()["id"]
    assert env.delete(f"/api/v1/webhooks/{wh_id}", headers=owner_headers).status_code == 200
    assert env.get(f"/api/v1/projects/{pid}/webhooks", headers=owner_headers).json() == []

    # A2A remote delete
    agent_id = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "remote-peer", "platform": "external", "execution_mode": "remote", "capabilities": []},
    ).json()["id"]
    env.post(f"/api/v1/projects/{pid}/members", headers=owner_headers, json={"agent_id": agent_id, "role": "worker"})
    rem_id = env.post(
        f"/api/v1/projects/{pid}/a2a/remotes",
        headers=owner_headers,
        json={"agent_id": agent_id, "url": "https://agent.example.com/a2a"},
    ).json()["id"]
    assert env.delete(f"/api/v1/a2a/remotes/{rem_id}", headers=owner_headers).status_code == 200
    assert env.get(f"/api/v1/projects/{pid}/a2a/remotes", headers=owner_headers).json() == []

    # Workflow rule delete
    rule_id = env.post(
        f"/api/v1/projects/{pid}/workflow-rules",
        headers=owner_headers,
        json={"name": "AutoNotify", "event_prefix": "task.completed", "action_kind": "notify_inbox"},
    ).json()["id"]
    assert env.delete(f"/api/v1/workflow-rules/{rule_id}", headers=owner_headers).status_code == 200
    assert env.get(f"/api/v1/projects/{pid}/workflow-rules", headers=owner_headers).json() == []


def test_s3_sigv4_requests_and_mcp_memory_query(monkeypatch):
    calls = []

    def fake_put(url, content=b"", headers=None, timeout=60):
        calls.append(("PUT", url, headers))
        return httpx.Response(200, headers={"etag": '"abc123"'})

    def fake_get(url, headers=None, timeout=60):
        calls.append(("GET", url, headers))
        return httpx.Response(200, content=b"s3-payload")

    def fake_post(url, content=b"", headers=None, timeout=60):
        calls.append(("POST", url, headers))
        if "uploads=" in url:
            return httpx.Response(200, text="<InitiateMultipartUploadResult><UploadId>up-99</UploadId></InitiateMultipartUploadResult>")
        return httpx.Response(200, text="<CompleteMultipartUploadResult/>")

    monkeypatch.setattr(httpx, "put", fake_put)
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)

    s3 = S3StorageBackend(bucket="bkt", endpoint_url="https://s3.example.com", access_key="AKIA", secret_key="SECRET")
    uri = s3.save("p1", "a1", b"s3-payload")
    assert uri == "s3://bkt/p1/a1"
    assert s3.load(uri) == b"s3-payload"
    assert any("AWS4-HMAC-SHA256" in c[2]["Authorization"] for c in calls)

    uid = s3.initiate_multipart("p1", "a2", "big.bin")
    assert uid == "up-99"
    p1 = s3.upload_part(uid, 1, b"part1")
    assert p1["etag"] == "abc123"
    m_uri, m_sha, m_size = s3.complete_multipart(uid, [p1])
    assert m_uri == "s3://bkt/p1/a2"
    assert m_size == 5
    assert m_sha == hashlib.sha256(p1["sha256"].encode("utf-8")).hexdigest()

    # Verify MCP memory query passes ?q=
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": "m1", "title": "Hit"}])

    mcp = OtterNestMCPClient("http://127.0.0.1:8000", "on_agent_testtoken", transport=httpx.MockTransport(handler))
    res = mcp.list_memories("proj-1", query="vector search")
    assert res[0]["id"] == "m1"
    assert "q=vector+search" in captured["url"] or "q=vector%20search" in captured["url"]
    mcp.close()


def test_s3_artifact_gc_and_delete_error_handling(env, owner_headers, monkeypatch):
    monkeypatch.setenv("OTTERNEST_STORAGE_TYPE", "s3")
    monkeypatch.setenv("OTTERNEST_S3_BUCKET", "test-bucket")
    monkeypatch.setenv("OTTERNEST_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setenv("OTTERNEST_S3_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("OTTERNEST_S3_SECRET_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setattr("otternest.storage._DEFAULT_BACKEND", None)

    put_urls = []
    delete_urls = []
    stored_objects = {}

    def fake_put(url, content=b"", headers=None, timeout=60):
        put_urls.append(url)
        stored_objects[url] = content
        return httpx.Response(200, headers={"etag": '"mock-etag"'})

    def fake_get(url, headers=None, timeout=60):
        if url not in stored_objects:
            return httpx.Response(404)
        return httpx.Response(200, content=stored_objects[url])

    monkeypatch.setattr(httpx, "put", fake_put)
    monkeypatch.setattr(httpx, "get", fake_get)

    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "S3GCProj"}).json()["id"]
    tid = env.post(
        f"/api/v1/projects/{pid}/tasks",
        headers=owner_headers,
        json={"title": "Upload S3 Artifacts", "instruction": "do", "acceptance": "ok"},
    ).json()["id"]

    v1 = env.post(
        f"/api/v1/tasks/{tid}/artifacts",
        headers={**owner_headers, "X-Artifact-Name": "report.md"},
        content=b"rejected-s3-payload",
    ).json()
    v2 = env.post(
        f"/api/v1/tasks/{tid}/artifacts",
        headers={**owner_headers, "X-Artifact-Name": "report.md"},
        content=b"accepted-s3-payload",
    ).json()
    assert len(put_urls) == 2
    assert f"/{pid}/{v1['id']}" in put_urls[0]
    assert f"/{pid}/{v2['id']}" in put_urls[1]

    assert env.post(
        f"/api/v1/artifacts/{v1['id']}/review",
        headers=owner_headers,
        json={"accepted": False, "note": "rejected s3 draft"},
    ).status_code == 200
    assert env.post(
        f"/api/v1/artifacts/{v2['id']}/review",
        headers=owner_headers,
        json={"accepted": True, "note": "accepted s3 final"},
    ).status_code == 200

    # Dry-run GC identifies v1 and reports reclaimable bytes
    gc_dry = env.post(
        f"/api/v1/projects/{pid}/artifacts/gc",
        headers=owner_headers,
        json={"dry_run": True, "keep_versions": 1, "include_rejected": True},
    )
    assert gc_dry.status_code == 200
    dry_data = gc_dry.json()
    assert v1["id"] in dry_data["candidate_ids"]
    assert v2["id"] not in dry_data["candidate_ids"]
    assert dry_data["reclaimable_bytes"] > 0

    # Failed S3 DELETE (500) records artifact in failed_ids and keeps metadata for retry
    def fake_delete_fail(url, headers=None, timeout=30):
        delete_urls.append(("FAIL", url))
        return httpx.Response(500)

    monkeypatch.setattr(httpx, "delete", fake_delete_fail)
    gc_fail = env.post(
        f"/api/v1/projects/{pid}/artifacts/gc",
        headers=owner_headers,
        json={"dry_run": False, "keep_versions": 1, "include_rejected": True},
    )
    assert gc_fail.status_code == 200
    fail_data = gc_fail.json()
    assert v1["id"] in fail_data["failed_ids"]
    assert v1["id"] not in fail_data["deleted_ids"]
    assert fail_data["reclaimed_bytes"] == 0

    # Successful S3 DELETE (204) deletes candidate and reports reclaimed bytes
    def fake_delete_ok(url, headers=None, timeout=30):
        delete_urls.append(("OK", url))
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "delete", fake_delete_ok)
    gc_ok = env.post(
        f"/api/v1/projects/{pid}/artifacts/gc",
        headers=owner_headers,
        json={"dry_run": False, "keep_versions": 1, "include_rejected": True},
    )
    assert gc_ok.status_code == 200
    ok_data = gc_ok.json()
    assert v1["id"] in ok_data["deleted_ids"]
    assert ok_data["failed_ids"] == []
    assert ok_data["reclaimed_bytes"] > 0
    assert any(status == "OK" and f"/{pid}/{v1['id']}" in url for status, url in delete_urls)
