"""End-to-end artifact handback and handoff for the opt-in generic Runner."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from otternest.runner import (
    MAX_ARTIFACT_BYTES,
    Runner,
    RunnerFailure,
    WorkerResult,
    parse_worker_output,
)


def _manifest(summary: str = "完成", artifacts=None, checkpoint=None) -> str:
    payload = {"summary": summary, "artifacts": artifacts or []}
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint
    return json.dumps(payload, ensure_ascii=False)


def test_legacy_plain_text_and_json_results_remain_compatible():
    assert parse_worker_output("plain result").summary == "plain result"
    assert parse_worker_output('{"summary": "old protocol"}').summary == "old protocol"
    assert parse_worker_output(_manifest()).artifacts == ()


@pytest.mark.parametrize("path", [
    "../secret.txt",
    "/etc/passwd",
    "nested/../../outside",
    "C:/Windows/system.txt",
    "subdir\\secret.txt",
])
def test_manifest_rejects_unsafe_paths(tmp_path, path):
    with pytest.raises(RunnerFailure):
        parse_worker_output(_manifest(artifacts=[{"path": path, "name": "safe.txt"}]), tmp_path)


def test_manifest_requires_workspace_and_rejects_links(tmp_path):
    (tmp_path / "inside.txt").write_text("hello")
    with pytest.raises(RunnerFailure, match="workspace"):
        parse_worker_output(_manifest(artifacts=[{"path": "inside.txt"}]))
    outside = tmp_path.parent / ("outside-" + tmp_path.name + ".txt")
    outside.write_text("outside")
    try:
        (tmp_path / "linked.txt").symlink_to(outside)
        with pytest.raises(RunnerFailure):
            parse_worker_output(_manifest(artifacts=[{"path": "linked.txt"}]), tmp_path)
    finally:
        outside.unlink()


def test_manifest_rejects_hardlinks_oversize_and_bad_names(tmp_path):
    (tmp_path / "big.txt").write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(RunnerFailure, match="5 MiB"):
        parse_worker_output(_manifest(artifacts=[{"path": "big.txt"}]), tmp_path)
    (tmp_path / "first.txt").write_text("safe")
    os.link(tmp_path / "first.txt", tmp_path / "second.txt")
    with pytest.raises(RunnerFailure, match="hard links"):
        parse_worker_output(_manifest(artifacts=[{"path": "second.txt"}]), tmp_path)
    with pytest.raises(RunnerFailure, match="ASCII"):
        parse_worker_output(_manifest(artifacts=[{"path": "first.txt", "name": "成果.txt"}]), tmp_path)
    with pytest.raises(RunnerFailure, match="unique"):
        parse_worker_output(_manifest(artifacts=[
            {"path": "first.txt", "name": "duplicate.txt"},
            {"path": "first.txt", "name": "duplicate.txt"},
        ]), tmp_path)


def test_manifest_rejects_invalid_checkpoint(tmp_path):
    with pytest.raises(RunnerFailure):
        parse_worker_output(_manifest(checkpoint={"summary": "", "next_steps": "next"}), tmp_path)
    with pytest.raises(RunnerFailure):
        parse_worker_output(_manifest(checkpoint={"summary": "a", "next_steps": "x" * 6001}), tmp_path)


def _setup_owner_project_and_agent(env, owner_headers):
    p = env.post("/api/v1/projects", headers=owner_headers,
                 json={"name": "成果回传测试"})
    assert p.status_code == 201, p.text
    project_id = p.json()["id"]
    a = env.post("/api/v1/agents", headers=owner_headers,
                 json={"name": "Workspace Worker", "platform": "trusted-local",
                       "execution_mode": "polling", "capabilities": []})
    assert a.status_code == 201, a.text
    agent = a.json()
    m = env.post(f"/api/v1/projects/{project_id}/members", headers=owner_headers,
                 json={"agent_id": agent["id"], "role": "worker"})
    assert m.status_code == 201, m.text
    task = env.post(f"/api/v1/projects/{project_id}/tasks", headers=owner_headers,
                    json={"title": "交付文件", "instruction": "生成一份简单文本成果",
                          "acceptance": "文件可以下载且与内容一致"})
    assert task.status_code == 201, task.text
    return project_id, task.json()["id"], agent["token"]


def _asgi_runner(env, token, child, workspace_root=None):
    runner = Runner("http://127.0.0.1:8000", token, child,
                    timeout=30, workspace_root=workspace_root)

    def handler(request: httpx.Request) -> httpx.Response:
        response = env.request(
            request.method,
            request.url.path,
            params=dict(request.url.params),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    runner.client.close()
    runner.client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer " + token},
    )
    return runner


def test_runner_uploads_artifact_records_checkpoint_and_awaits_owner(env, owner_headers, monkeypatch, tmp_path):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path / "stored"))
    project_id, task_id, token = _setup_owner_project_and_agent(env, owner_headers)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    child = [
        sys.executable, "-c",
        "import json, pathlib; "
        "pathlib.Path('report.txt').write_text('trusted output', encoding='utf8'); "
        "print(json.dumps({'summary':'已完成草案，请所有者检查',"
        "'artifacts':[{'path':'report.txt','name':'report.txt'}],"
        "'checkpoint':{'summary':'文件已经生成','next_steps':'核验成果'}}))"
    ]
    runner = _asgi_runner(env, token, child, workspace_root=str(workspace_root))
    try:
        assert runner.run_one() is True
    finally:
        runner.close()
    assert list(workspace_root.iterdir()) == []

    task = env.get(f"/api/v1/tasks/{task_id}", headers=owner_headers)
    assert task.status_code == 200
    assert task.json()["status"] == "submitted"
    assert task.json()["result_summary"] == "已完成草案，请所有者检查"

    files = env.get(f"/api/v1/tasks/{task_id}/artifacts", headers=owner_headers)
    assert files.status_code == 200
    assert len(files.json()) == 1
    record = files.json()[0]
    assert record["name"] == "report.txt"
    assert record["review_status"] == "pending"
    downloaded = env.get(f"/api/v1/artifacts/{record['id']}/content", headers=owner_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"trusted output"

    checkpoints = env.get(f"/api/v1/tasks/{task_id}/checkpoints", headers=owner_headers)
    assert checkpoints.status_code == 200
    assert len(checkpoints.json()) == 1
    checkpoint = checkpoints.json()[0]
    assert checkpoint["artifact_ids"] == [record["id"]]
    assert checkpoint["verification_status"] == "self_reported"
    assert checkpoint["summary"] == "文件已经生成"

    owner_review = env.post(
        f"/api/v1/artifacts/{record['id']}/review",
        headers=owner_headers,
        json={"accepted": True, "note": "下载校验一致"},
    )
    assert owner_review.status_code == 200

    verified = env.post(
        f"/api/v1/checkpoints/{checkpoint['id']}/verify",
        headers=owner_headers,
        json={"status": "owner_verified", "note": "内容可信"},
    )
    assert verified.status_code == 200
    handoff = env.get(f"/api/v1/tasks/{task_id}/handoff", headers=owner_headers)
    assert handoff.status_code == 200
    handoff_text = json.dumps(handoff.json(), ensure_ascii=False)
    assert record["id"] in handoff_text

    accepted = env.post(
        f"/api/v1/tasks/{task_id}/review",
        headers=owner_headers,
        json={"accepted": True, "reason": "通过文件和检查点复核"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"
    events = env.get(f"/api/v1/projects/{project_id}/events", headers=owner_headers)
    kinds = [ev["kind"] for ev in events.json()]
    assert "artifact.submitted" in kinds
    assert "task.checkpoint_recorded" in kinds
    assert "task.submitted" in kinds


def test_runner_blocks_task_if_manifest_has_artifacts_without_workspace(env, owner_headers):
    _, task_id, token = _setup_owner_project_and_agent(env, owner_headers)
    child = [
        sys.executable, "-c",
        "import json; print(json.dumps({'summary':'should fail',"
        "'artifacts':[{'path':'nonexistent.txt'}]}))",
    ]
    runner = _asgi_runner(env, token, child)
    try:
        assert runner.run_one() is True
    finally:
        runner.close()
    task = env.get(f"/api/v1/tasks/{task_id}", headers=owner_headers)
    assert task.json()["status"] == "blocked"
    assert env.get(f"/api/v1/tasks/{task_id}/artifacts", headers=owner_headers).json() == []


def test_runner_rejects_nonabsolute_workspace(tmp_path):
    with pytest.raises(RunnerFailure, match="existing absolute"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["echo", "hello"], workspace_root="relative")
