"""Integration tests for newly wired API endpoints: capabilities, security audit, hybrid vector search, and multipart upload."""
from __future__ import annotations


def test_system_capabilities_and_security_audit(env, owner_headers, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("OTTERNEST_MUSE_BASE_URL", "https://muse.example.com")
    monkeypatch.setenv("OTTERNEST_MUSE_TOKEN", "muse-secret-token")

    cap = env.get("/api/v1/system/capabilities", headers=owner_headers)
    assert cap.status_code == 200
    data = cap.json()
    assert data["executors"]["gemini_worker"]["available"] is True
    assert data["executors"]["muse_adapter"]["available"] is True
    assert data["vector_search"]["enabled"] is True
    assert data["vector_search"]["mode"] == "hybrid"
    assert data["storage"]["multipart_enabled"] is True
    assert data["kms"]["enabled"] is True

    audit = env.get("/api/v1/system/security-audit", headers=owner_headers)
    assert audit.status_code == 200
    audit_data = audit.json()
    assert audit_data["status"] in {"pass", "warning", "critical"}
    assert "findings" in audit_data
    assert len(audit_data["findings"]) >= 3


def test_hybrid_vector_search_on_memories(env, owner_headers):
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "VectorProj"}).json()["id"]
    r1 = env.post(
        f"/api/v1/projects/{pid}/memories",
        headers=owner_headers,
        json={"kind": "fact", "title": "PostgreSQL Tuning", "content": "Set shared_buffers to 25% RAM", "source": "ops", "sensitivity": "internal"},
    )
    assert r1.status_code == 201
    r2 = env.post(
        f"/api/v1/projects/{pid}/memories",
        headers=owner_headers,
        json={"kind": "experience", "title": "React UI Build", "content": "Run npm run build to compile vite bundle", "source": "dev", "sensitivity": "internal"},
    )
    assert r2.status_code == 201

    res = env.get(f"/api/v1/projects/{pid}/memories", params={"q": "database shared_buffers PostgreSQL"}, headers=owner_headers)
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    assert items[0]["title"] == "PostgreSQL Tuning"
    assert "score" in items[0]


def test_multipart_artifact_upload_and_download(env, owner_headers):
    pid = env.post("/api/v1/projects", headers=owner_headers, json={"name": "MultipartProj"}).json()["id"]
    gid = env.post(f"/api/v1/projects/{pid}/goals", headers=owner_headers, json={"title": "G1", "success_criteria": "OK"}).json()["id"]
    tid = env.post(
        f"/api/v1/projects/{pid}/tasks",
        headers=owner_headers,
        json={"goal_id": gid, "title": "Upload large bundle", "instruction": "upload", "acceptance": "checksum verified", "risk": "R0"},
    ).json()["id"]

    init_res = env.post(
        f"/api/v1/tasks/{tid}/artifacts/multipart/init",
        headers=owner_headers,
        json={"filename": "dataset.bin", "sensitivity": "internal"},
    )
    assert init_res.status_code == 201
    upload_id = init_res.json()["upload_id"]

    part1 = env.put(
        f"/api/v1/tasks/{tid}/artifacts/multipart/{upload_id}/parts/1",
        headers=owner_headers,
        content=b"hello-",
    )
    assert part1.status_code == 200
    part2 = env.put(
        f"/api/v1/tasks/{tid}/artifacts/multipart/{upload_id}/parts/2",
        headers=owner_headers,
        content=b"world!",
    )
    assert part2.status_code == 200

    comp_res = env.post(
        f"/api/v1/tasks/{tid}/artifacts/multipart/{upload_id}/complete",
        params={"filename": "dataset.bin", "sensitivity": "internal"},
        headers=owner_headers,
        json={"parts": [part1.json(), part2.json()]},
    )
    assert comp_res.status_code == 201
    artifact = comp_res.json()
    assert artifact["size_bytes"] == 12

    dl = env.get(f"/api/v1/artifacts/{artifact['id']}/content", headers=owner_headers)
    assert dl.status_code == 200
    assert dl.content == b"hello-world!"
