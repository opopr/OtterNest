"""Mock Codex CLI working on an immutable Git snapshot, with owner review."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from otternest.codex_worker import CodexWorkerError, run_codex
from otternest.runner import Runner, execute_command


PACKET = {
    "task": {
        "id": "task-id", "title": "修改源码", "instruction": "修改指定程序并产出补丁",
        "acceptance": "审查 patch 后由所有者验收", "risk": "R1",
    },
    "memories": [{"content": "malicious ref should never change git source"}],
}


FAKE_CODEX = '''#!/usr/bin/env python3
import io, json, pathlib, sys, os
if hasattr(sys.stdin, "buffer"):
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
args = sys.argv[1:]
assert args[0] == "exec"
assert "--json" in args
assert "--skip-git-repo-check" in args
assert args[-1] == "-"
assert "--dangerously-bypass-approvals-and-sandbox" not in args
assert "OTTERNEST_OWNER_TOKEN" not in os.environ
assert "OTTERNEST_AGENT_TOKEN" not in os.environ
assert "OTTERNEST_DATABASE_URL" not in os.environ
request = sys.stdin.read()
assert "TASK REQUEST" in request
assert "source_snapshot" in request
assert "base_commit" not in request
cwd = pathlib.Path.cwd()
assert (cwd / "src" / "app.py").read_text() == "print('base')\\n"
assert not (cwd / ".git").exists()
mode = args[args.index("--sandbox")+1]
if mode == "workspace-write":
    (cwd / "src" / "app.py").write_text("print('edited')\\n")
    (cwd / "new.txt").write_text("new artifact\\n")
last = pathlib.Path(args[args.index("--output-last-message") + 1])
last.write_text("Codex completed mocked code change, awaiting owner review.")
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "turn.completed"}))
'''


def make_repo(tmp_path):
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('base')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    return repo, sha


def make_cli(tmp_path):
    cli = tmp_path / ("fake-codex.py" if sys.platform == "win32" else "fake-codex")
    cli.write_text(FAKE_CODEX)
    cli.chmod(0o755)
    return cli


def kwargs(repo, sha):
    return {"local_repo": repo, "repo_ref": "refs/heads/main", "repo_commit": sha}


def test_read_only_pinned_repo_can_analyze_without_changing_files(tmp_path):
    repo, sha = make_repo(tmp_path)
    cli = make_cli(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    result = run_codex(PACKET, workspace=work, cli=str(cli), **kwargs(repo, sha))
    assert result["summary"].startswith("Codex completed")
    assert "artifacts" not in result
    assert (work / "src" / "app.py").read_text() == "print('base')\n"
    assert not (work / ".git").exists()


def test_worktree_patch_and_provenance_are_review_only(tmp_path):
    repo, sha = make_repo(tmp_path)
    cli = make_cli(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    result = run_codex(
        PACKET, workspace=work, cli=str(cli),
        mode="workspace-write", enable_workspace_write=True,
        **kwargs(repo, sha),
    )
    names = [item["path"] for item in result["artifacts"]]
    assert names == ["changes.patch", "otternest-provenance.json"]
    assert result["checkpoint"]["summary"].startswith("Codex CLI")
    provenance = json.loads((work / "otternest-provenance.json").read_text())
    patch = (work / "changes.patch").read_bytes()
    assert provenance["base_commit"] == sha
    assert provenance["origin"].endswith(str(repo))
    assert provenance["patch_sha256"] == hashlib.sha256(patch).hexdigest()
    assert provenance["auto_applied"] is False
    assert provenance["changed_files"] == 2
    assert b"src/app.py" in patch
    assert subprocess.run(
        ["git", "-C", str(repo), "apply", "--check", str(work / "changes.patch")],
        check=False,
    ).returncode == 0
    assert (repo / "src" / "app.py").read_text() == "print('base')\n"


def test_repository_source_cannot_override_commit_or_privileges(tmp_path):
    repo, sha = make_repo(tmp_path)
    cli = make_cli(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(CodexWorkerError, match="pinned"):
        run_codex(
            PACKET, workspace=work, cli=str(cli),
            repo_ref="refs/heads/main", repo_commit="f" * 40,
            local_repo=repo,
        )
    assert not any(work.iterdir())
    with pytest.raises(CodexWorkerError, match="requires source"):
        run_codex(PACKET, workspace=work, cli=str(cli), repo_commit=sha)


def test_missing_write_approval_denies_snapshotted_codex(tmp_path):
    repo, sha = make_repo(tmp_path)
    cli = make_cli(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(CodexWorkerError, match="explicit"):
        run_codex(
            PACKET, workspace=work, cli=str(cli), mode="workspace-write",
            **kwargs(repo, sha),
        )


def test_runner_exec_returns_real_patch_artifacts_without_original_changes(tmp_path):
    repo, sha = make_repo(tmp_path)
    cli = make_cli(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    result = execute_command([
        sys.executable, "-m", "otternest.codex_worker",
        "--cli", str(cli), "--mode", "workspace-write",
        "--enable-workspace-write",
        "--local-repo", str(repo), "--repo-ref", "refs/heads/main",
        "--repo-commit", sha,
    ], PACKET, 30, lambda: None, workspace=work, structured=True)
    assert [x.name for x in result.artifacts] == [
        "changes.patch", "otternest-provenance.json",
    ]
    assert result.checkpoint_summary is not None
    assert (repo / "src" / "app.py").read_text() == "print('base')\n"


def test_full_asgi_codex_snapshot_patch_upload_owner_review(env, owner_headers,
                                                            monkeypatch, tmp_path):
    monkeypatch.setenv("OTTERNEST_ARTIFACT_DIR", str(tmp_path / "stored"))
    repo, sha = make_repo(tmp_path)
    cli = make_cli(tmp_path)
    project = env.post("/api/v1/projects", headers=owner_headers,
                       json={"name": "Pinned patch integration"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    agent = env.post("/api/v1/agents", headers=owner_headers,
                     json={"name": "Mock Codex Snapshot", "platform": "codex-cli",
                           "execution_mode": "polling", "capabilities": []})
    assert agent.status_code == 201, agent.text
    agent_data = agent.json()
    assert env.post(f"/api/v1/projects/{project_id}/members", headers=owner_headers,
                    json={"agent_id": agent_data["id"], "role": "worker"}).status_code == 201
    task = env.post(f"/api/v1/projects/{project_id}/tasks", headers=owner_headers,
                    json={"title": "修改源码", "instruction": "修改指定程序并产出补丁",
                          "acceptance": "审查 patch 后验收"})
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]
    root = tmp_path / "workspaces"
    root.mkdir()
    cmd = [
        sys.executable, "-m", "otternest.codex_worker", "--cli", str(cli),
        "--mode", "workspace-write", "--enable-workspace-write",
        "--local-repo", str(repo), "--repo-ref", "refs/heads/main",
        "--repo-commit", sha,
    ]
    worker = Runner("http://127.0.0.1:8000", agent_data["token"], cmd,
                    timeout=30, workspace_root=str(root))

    def handler(request):
        response = env.request(
            request.method, request.url.path, params=dict(request.url.params),
            headers=dict(request.headers), content=request.content,
        )
        return httpx.Response(response.status_code, content=response.content,
                              headers=dict(response.headers))

    worker.client.close()
    worker.client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer " + agent_data["token"]},
    )
    try:
        assert worker.run_one() is True
    finally:
        worker.close()
    assert not list(root.iterdir())
    current = env.get(f"/api/v1/tasks/{task_id}", headers=owner_headers).json()
    assert current["status"] == "submitted"
    artifacts = env.get(f"/api/v1/tasks/{task_id}/artifacts",
                        headers=owner_headers).json()
    assert {a["name"] for a in artifacts} == {
        "changes.patch", "otternest-provenance.json",
    }
    by_name = {a["name"]: a for a in artifacts}
    for a in artifacts:
        assert a["review_status"] == "pending"
    patch = env.get(f"/api/v1/artifacts/{by_name['changes.patch']['id']}/content",
                    headers=owner_headers)
    assert patch.status_code == 200
    assert b"src/app.py" in patch.content
    provenance = env.get(
        f"/api/v1/artifacts/{by_name['otternest-provenance.json']['id']}/content",
        headers=owner_headers,
    )
    assert json.loads(provenance.content)["base_commit"] == sha
    assert (repo / "src" / "app.py").read_text() == "print('base')\n"
    checkpoints = env.get(f"/api/v1/tasks/{task_id}/checkpoints",
                          headers=owner_headers).json()
    assert len(checkpoints) == 1
    assert checkpoints[0]["verification_status"] == "self_reported"
    for artifact in artifacts:
        assert env.post(
            f"/api/v1/artifacts/{artifact['id']}/review", headers=owner_headers,
            json={"accepted": True, "note": "source SHA and diff reviewed"},
        ).status_code == 200
    assert env.post(
        f"/api/v1/checkpoints/{checkpoints[0]['id']}/verify",
        headers=owner_headers,
        json={"status": "owner_verified", "note": "reviewed"},
    ).status_code == 200
    accepted = env.post(
        f"/api/v1/tasks/{task_id}/review", headers=owner_headers,
        json={"accepted": True, "reason": "owner reviewed pinned patch"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"
