"""Regression coverage for versioned artifacts and safe project portability."""
from __future__ import annotations

from copy import deepcopy


def _project_with_task(env, owner_headers):
    project = env.post(
        "/api/v1/projects",
        headers=owner_headers,
        json={"name": "回归测试项目", "description": "artifact and restore coverage"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    task = env.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=owner_headers,
        json={
            "title": "交付成果",
            "instruction": "创建可验收的成果",
            "acceptance": "文件可下载且校验和匹配",
        },
    )
    assert task.status_code == 201, task.text
    return project_id, task.json()["id"]


def _upload(env, owner_headers, task_id, data):
    response = env.post(
        f"/api/v1/tasks/{task_id}/artifacts",
        content=data,
        headers={**owner_headers, "X-Artifact-Name": "report.txt"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_artifact_versions_are_immutable_and_review_is_final(env, owner_headers, monkeypatch, tmp_path):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    project_id, task_id = _project_with_task(env, owner_headers)
    first = _upload(env, owner_headers, task_id, b"first version")
    second = _upload(env, owner_headers, task_id, b"second version")

    assert first["version"] == 1
    assert second["version"] == 2
    assert first["id"] != second["id"]
    assert first["family_id"] == second["family_id"]
    assert first["checksum"] != second["checksum"]
    assert first["review_status"] == second["review_status"] == "pending"

    listed = env.get(f"/api/v1/tasks/{task_id}/artifacts", headers=owner_headers)
    assert listed.status_code == 200
    assert [(a["id"], a["version"]) for a in listed.json()] == [
        (first["id"], 1), (second["id"], 2)
    ]
    for artifact, expected in ((first, b"first version"), (second, b"second version")):
        downloaded = env.get(f"/api/v1/artifacts/{artifact['id']}/content", headers=owner_headers)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == expected

    accepted = env.post(
        f"/api/v1/artifacts/{first['id']}/review",
        headers=owner_headers,
        json={"accepted": True, "note": "验收通过"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["review_status"] == "accepted"
    assert env.post(
        f"/api/v1/artifacts/{first['id']}/review",
        headers=owner_headers,
        json={"accepted": False, "note": "不能覆盖之前的验收"},
    ).status_code == 409

    assert env.post(
        f"/api/v1/artifacts/{second['id']}/review",
        headers=owner_headers,
        json={"accepted": False, "note": "short"},
    ).status_code == 422
    rejected = env.post(
        f"/api/v1/artifacts/{second['id']}/review",
        headers=owner_headers,
        json={"accepted": False, "note": "文件格式不符合验收约定"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"

    events = env.get(f"/api/v1/projects/{project_id}/events", headers=owner_headers)
    assert events.status_code == 200
    kinds = [event["kind"] for event in events.json()]
    assert "artifact.accepted" in kinds
    assert "artifact.rejected" in kinds


def test_artifact_tampering_blocks_download_and_acceptance(env, owner_headers, monkeypatch, tmp_path):
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(artifact_root))
    project_id, task_id = _project_with_task(env, owner_headers)
    artifact = _upload(env, owner_headers, task_id, b"trusted bytes")
    path = artifact_root / project_id / artifact["id"]
    assert path.read_bytes() == b"trusted bytes"
    path.write_bytes(b"altered bytes")

    fetched = env.get(f"/api/v1/artifacts/{artifact['id']}/content", headers=owner_headers)
    assert fetched.status_code == 409
    review = env.post(
        f"/api/v1/artifacts/{artifact['id']}/review",
        headers=owner_headers,
        json={"accepted": True, "note": "尝试验收"},
    )
    assert review.status_code == 409
    listed = env.get(f"/api/v1/tasks/{task_id}/artifacts", headers=owner_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["review_status"] == "pending"


def test_export_preview_and_restore_do_not_trust_unverified_bundle(
    env, owner_headers, monkeypatch, tmp_path
):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    project_id, task_id = _project_with_task(env, owner_headers)
    original_artifact = _upload(env, owner_headers, task_id, b"do not include bytes")
    approved = env.post(
        f"/api/v1/artifacts/{original_artifact['id']}/review",
        headers=owner_headers,
        json={"accepted": True, "note": "原项目已验收"},
    )
    assert approved.status_code == 200

    exported = env.get(f"/api/v1/projects/{project_id}/export", headers=owner_headers)
    assert exported.status_code == 200, exported.text
    bundle = exported.json()
    assert bundle["schema_version"] == "otternest.export.v1"
    assert bundle["secrets_included"] is False

    projects_before = env.get("/api/v1/projects", headers=owner_headers).json()
    preview = env.post(
        "/api/v1/projects/import/preview", headers=owner_headers, json={"bundle": bundle}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"]["artifacts"] == 1
    assert env.get("/api/v1/projects", headers=owner_headers).json() == projects_before

    damaged = deepcopy(bundle)
    damaged["project"]["name"] = "tampered after export"
    assert env.post(
        "/api/v1/projects/import/preview", headers=owner_headers, json={"bundle": damaged}
    ).status_code == 422
    assert env.post(
        "/api/v1/projects/import",
        headers=owner_headers,
        json={"bundle": bundle, "confirm_restore": False},
    ).status_code == 422
    assert env.get("/api/v1/projects", headers=owner_headers).json() == projects_before

    restored = env.post(
        "/api/v1/projects/import",
        headers=owner_headers,
        json={"bundle": bundle, "confirm_restore": True, "new_name": "已恢复项目"},
    )
    assert restored.status_code == 201, restored.text
    result = restored.json()
    assert result["status"] == "restored_paused"
    new_project_id = result["project_id"]
    assert new_project_id != project_id
    assert result["artifacts_metadata_only"] == 1

    new_project = next(
        item for item in env.get("/api/v1/projects", headers=owner_headers).json()
        if item["id"] == new_project_id
    )
    assert new_project["status"] == "paused"
    new_tasks = env.get(f"/api/v1/projects/{new_project_id}/tasks", headers=owner_headers)
    assert new_tasks.status_code == 200
    assert len(new_tasks.json()) == 1
    assert new_tasks.json()[0]["id"] != task_id
    assert new_tasks.json()[0]["status"] == "blocked"

    new_task_id = new_tasks.json()[0]["id"]
    new_artifacts = env.get(f"/api/v1/tasks/{new_task_id}/artifacts", headers=owner_headers)
    assert new_artifacts.status_code == 200
    assert len(new_artifacts.json()) == 1
    imported_artifact = new_artifacts.json()[0]
    assert imported_artifact["id"] != original_artifact["id"]
    assert imported_artifact["checksum"] == original_artifact["checksum"]
    assert imported_artifact["review_status"] == "pending"
    assert env.get(
        f"/api/v1/artifacts/{imported_artifact['id']}/content", headers=owner_headers
    ).status_code == 410
