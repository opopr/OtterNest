"""Pinned, detached Git snapshots and owner-reviewable text patches."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from otternest.git_snapshot import (
    SnapshotError, SnapshotSpec, import_snapshot, compare_snapshot, make_patch,
)


def git(*args, cwd=None):
    command = ["git"]
    if cwd is not None:
        command += ["-C", str(cwd)]
    command += list(args)
    result = subprocess.run(command, capture_output=True, check=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / "source-repo"
    git("init", "-q", "-b", "main", str(repo))
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
    (repo / "README.md").write_text("A codebase\n", encoding="utf-8")
    (repo / "media").mkdir()
    (repo / "media" / "logo.bin").write_bytes(b"\xff\x00\x11")
    git("add", ".", cwd=repo)
    git("-c", "user.email=ci@example.invalid", "-c",
        "user.name=CI", "commit", "-qm", "initial", cwd=repo)
    commit = git("rev-parse", "HEAD", cwd=repo)
    return repo, commit


def spec(repo, commit):
    return SnapshotSpec(local_repo=repo, ref="refs/heads/main", commit=commit)


def test_import_exact_local_commit_without_git_or_credentials(source_repo, tmp_path):
    repo, commit = source_repo
    workspace = tmp_path / "work"
    workspace.mkdir()
    baseline = import_snapshot(spec(repo, commit), workspace)
    assert baseline.commit == commit
    assert baseline.ref == "refs/heads/main"
    assert baseline.origin.startswith("local:")
    assert (workspace / "src" / "app.py").read_text() == "print('base')\n"
    assert (workspace / "media" / "logo.bin").read_bytes() == b"\xff\x00\x11"
    assert not (workspace / ".git").exists()
    assert compare_snapshot(baseline, workspace) == {}


def test_generated_patch_applies_to_exact_base_commit(source_repo, tmp_path):
    repo, commit = source_repo
    work = tmp_path / "work"
    work.mkdir()
    base = import_snapshot(spec(repo, commit), work)
    (work / "src" / "app.py").write_text("print('updated')\n", encoding="utf-8")
    (work / "src" / "new.py").write_text("value = 42\n", encoding="utf-8")
    (work / "README.md").unlink()
    patch, changed = make_patch(base, work, reserved=frozenset({"changes.patch"}))
    assert changed == 3
    assert b"diff --git a/src/app.py b/src/app.py" in patch
    assert b"new file mode 100644" in patch
    assert b"deleted file mode 100644" in patch
    assert b"git push" not in patch
    patchfile = tmp_path / "review.patch"
    patchfile.write_bytes(patch)
    # Git apply is a *verification*, never an automatic write to the source.
    git("apply", "--check", str(patchfile), cwd=repo)
    assert (repo / "src" / "app.py").read_text() == "print('base')\n"
    git("apply", str(patchfile), cwd=repo)
    assert (repo / "src" / "app.py").read_text() == "print('updated')\n"
    assert (repo / "src" / "new.py").read_text() == "value = 42\n"
    assert not (repo / "README.md").exists()


def test_fail_if_branch_moves_after_operator_pin(source_repo, tmp_path):
    repo, original = source_repo
    (repo / "README.md").write_text("modified branch\n", encoding="utf-8")
    git("add", ".", cwd=repo)
    git("-c", "user.email=ci@example.invalid", "-c",
        "user.name=CI", "commit", "-qm", "second", cwd=repo)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SnapshotError, match="pinned"):
        import_snapshot(spec(repo, original), work)
    assert not any(work.iterdir())


@pytest.mark.parametrize("ref", [
    "main", "refs/tags/v1", "refs/heads/-rf", "refs/heads/main..dev",
    "refs/heads/main.lock", "refs/heads//main",
])
def test_reject_unsafe_ref(source_repo, ref):
    repo, commit = source_repo
    with pytest.raises(SnapshotError, match="reference"):
        SnapshotSpec(ref=ref, commit=commit, local_repo=repo).validate()


@pytest.mark.parametrize("github", [
    "https://github.com/owner/repo", "other.com/owner/repo",
    "owner/../repo", "-owner/repo", "owner/repo.git", "owner/repo?token=secret",
])
def test_public_github_source_requires_owner_repo_not_task_url(github):
    with pytest.raises(SnapshotError):
        SnapshotSpec(github_repo=github, ref="refs/heads/main",
                     commit="a" * 40).validate()


def test_reject_short_sha_and_multiple_source(source_repo):
    repo, commit = source_repo
    with pytest.raises(SnapshotError, match="complete"):
        SnapshotSpec(local_repo=repo, ref="refs/heads/main", commit=commit[:10]).validate()
    with pytest.raises(SnapshotError, match="exactly one"):
        SnapshotSpec(local_repo=repo, github_repo="owner/repo",
                     ref="refs/heads/main", commit=commit).validate()


def test_existing_files_rejected_before_import(source_repo, tmp_path):
    repo, commit = source_repo
    work = tmp_path / "work"
    work.mkdir()
    (work / "codex-auth.json").write_text("unrelated private file")
    with pytest.raises(SnapshotError, match="empty"):
        import_snapshot(spec(repo, commit), work)
    assert (work / "codex-auth.json").read_text() == "unrelated private file"


def test_snapshot_rejects_symlink_in_source(source_repo, tmp_path):
    repo, _ = source_repo
    (repo / "src" / "link").symlink_to("app.py")
    git("add", ".", cwd=repo)
    git("-c", "user.email=ci@example.invalid", "-c",
        "user.name=CI", "commit", "-qm", "link", cwd=repo)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SnapshotError, match="links"):
        import_snapshot(spec(repo, git("rev-parse", "HEAD", cwd=repo)), work)


def test_patch_rejects_symlinks_binary_modification_and_unsafe_paths(source_repo, tmp_path):
    repo, commit = source_repo
    work = tmp_path / "work"
    work.mkdir()
    base = import_snapshot(spec(repo, commit), work)
    (work / "src" / "link").symlink_to("app.py")
    with pytest.raises(SnapshotError, match="links"):
        make_patch(base, work, reserved=frozenset({"changes.patch"}))
    (work / "src" / "link").unlink()
    (work / "media" / "logo.bin").write_bytes(b"\xff\x00\x12")
    with pytest.raises(SnapshotError, match="Binary"):
        make_patch(base, work, reserved=frozenset({"changes.patch"}))
    (work / "media" / "logo.bin").write_bytes(b"\xff\x00\x11")
    (work / "src" / "spaces in name.py").write_text("new\n")
    with pytest.raises(SnapshotError, match="patch-safe"):
        make_patch(base, work, reserved=frozenset({"changes.patch"}))


def test_patch_rejects_reserved_artifact_tampering(source_repo, tmp_path):
    repo, commit = source_repo
    work = tmp_path / "work"
    work.mkdir()
    base = import_snapshot(spec(repo, commit), work)
    (work / "changes.patch").write_text("pretend codex patch\n")
    with pytest.raises(SnapshotError, match="Reserved"):
        make_patch(base, work, reserved=frozenset({"changes.patch"}))


def test_no_source_change_is_not_a_fake_code_patch(source_repo, tmp_path):
    repo, commit = source_repo
    work = tmp_path / "work"
    work.mkdir()
    base = import_snapshot(spec(repo, commit), work)
    with pytest.raises(SnapshotError, match="no source changes"):
        make_patch(base, work, reserved=frozenset({"changes.patch"}))


def test_no_git_network_environment_or_secrets_leak(monkeypatch):
    from otternest.git_snapshot import _git_env, _git_command
    monkeypatch.setenv("OTTERNEST_OWNER_TOKEN", "private-owner")
    monkeypatch.setenv("GIT_ASKPASS", "dangerous-provider")
    env = _git_env()
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "OTTERNEST_OWNER_TOKEN" not in env
    assert "GIT_ASKPASS" not in env
    command = _git_command("git", "fetch", "--depth=1", "https://github.com/o/r.git",
                           "refs/heads/main")
    assert "credential.helper=" in command
    assert "http.followRedirects=false" in command
