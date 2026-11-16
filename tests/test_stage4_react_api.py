from __future__ import annotations

from urllib.parse import quote


def _project(env, owner_headers, name="Stage4 Project"):
    response = env.post("/api/v1/projects", headers=owner_headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _agent(env, owner_headers, name="Skill Author"):
    response = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": name, "platform": "test", "execution_mode": "manual", "capabilities": []},
    )
    assert response.status_code == 201, response.text
    row = response.json()
    return row, {"Authorization": "Bearer " + row["token"]}


def test_skill_proposal_owner_publication_and_member_visibility(env, owner_headers):
    project_id = _project(env, owner_headers)
    agent, agent_headers = _agent(env, owner_headers)
    enrolled = env.post(
        f"/api/v1/projects/{project_id}/members",
        headers=owner_headers,
        json={"agent_id": agent["id"], "role": "worker"},
    )
    assert enrolled.status_code == 201, enrolled.text

    draft = {
        "name": "release-checklist",
        "version": "1.0.0",
        "content": "1. Verify the release candidate. 2. Check the rollback path.",
        "source": "Owner-reviewed release process proposal",
    }
    proposed = env.post(
        f"/api/v1/projects/{project_id}/skills",
        headers=agent_headers,
        json=draft,
    )
    assert proposed.status_code == 201, proposed.text
    skill = proposed.json()
    assert skill["status"] == "proposed"

    # Proposed Skills are visible to the owner, but never exposed to project workers.
    owner_rows = env.get(f"/api/v1/projects/{project_id}/skills", headers=owner_headers)
    agent_rows = env.get(f"/api/v1/projects/{project_id}/skills", headers=agent_headers)
    assert owner_rows.status_code == 200
    assert [row["id"] for row in owner_rows.json()] == [skill["id"]]
    assert agent_rows.status_code == 200
    assert agent_rows.json() == []

    duplicate = env.post(
        f"/api/v1/projects/{project_id}/skills",
        headers=agent_headers,
        json=draft,
    )
    assert duplicate.status_code == 409

    published = env.post(
        f"/api/v1/skills/{skill['id']}/publish",
        headers=owner_headers,
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    visible = env.get(f"/api/v1/projects/{project_id}/skills", headers=agent_headers)
    assert visible.status_code == 200
    assert [row["id"] for row in visible.json()] == [skill["id"]]

    evaluation = env.post(
        f"/api/v1/skills/{skill['id']}/evaluations",
        headers=agent_headers,
        json={"verdict": "pass", "notes": "Validated against the release checklist."},
    )
    assert evaluation.status_code == 201, evaluation.text
    assert evaluation.json()["verdict"] == "pass"

    archived = env.post(
        f"/api/v1/skills/{skill['id']}/archive",
        headers=owner_headers,
        json={"reason": "Superseded by the next approved release process."},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert env.get(f"/api/v1/projects/{project_id}/skills", headers=agent_headers).json() == []


def test_artifact_upload_supports_utf8_filename_header(env, owner_headers):
    project_id = _project(env, owner_headers, "UTF8 Artifact Project")
    task_response = env.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=owner_headers,
        json={
            "title": "Upload UTF-8 artifact",
            "instruction": "Upload a report",
            "acceptance": "The report checksum matches",
            "risk": "R1",
        },
    )
    assert task_response.status_code == 201, task_response.text
    task_id = task_response.json()["id"]
    filename = "成果-最终版.txt"
    upload_headers = {
        **owner_headers,
        "X-Artifact-Name-Encoded": quote(filename, safe=""),
        "X-Artifact-Sensitivity": "internal",
    }
    payload = "OtterNest UTF-8 artifact".encode("utf-8")
    uploaded = env.post(
        f"/api/v1/tasks/{task_id}/artifacts",
        headers=upload_headers,
        content=payload,
    )
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()
    assert artifact["name"] == filename
    assert artifact["size_bytes"] == len(payload)

    downloaded = env.get(f"/api/v1/artifacts/{artifact['id']}/content", headers=owner_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == payload
