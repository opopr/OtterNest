"""Crash recovery is operator-driven; active cgroups are never killed implicitly."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from otternest import cgroup_recovery, runner
from otternest.cgroup_recovery import CgroupOwnerLock, TaskScope, clean_scopes, scan_scopes
from otternest.cgroups import CgroupError, CgroupPolicy
from otternest.runner import Runner, RunnerFailure


def _make_scope(parent: Path, name: str, populated: bool) -> Path:
    scope = parent / name
    scope.mkdir()
    (scope / "cgroup.events").write_text(f"populated {int(populated)}\nfrozen 0\n")
    (scope / "cgroup.kill").write_text("")
    return scope


def test_lock_prevents_second_runner_on_same_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(cgroup_recovery, "LOCK_ROOT", tmp_path)
    parent = Path("/sys/fs/cgroup/a-private-service")
    first = CgroupOwnerLock(parent).acquire()
    try:
        with pytest.raises(CgroupError, match="in use"):
            CgroupOwnerLock(parent).acquire()
    finally:
        first.close()
    with CgroupOwnerLock(parent):
        pass


def test_reject_symlink_or_world_readable_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(cgroup_recovery, "LOCK_ROOT", tmp_path)
    parent = Path("/sys/fs/cgroup/private-parent")
    lock = CgroupOwnerLock(parent)
    lock.path.symlink_to(tmp_path / "victim")
    with pytest.raises(CgroupError):
        lock.acquire()
    lock.path.unlink()
    lock.path.write_text("")
    lock.path.chmod(0o644)
    with pytest.raises(CgroupError, match="cannot be secured"):
        lock.acquire()


def test_scan_filters_unknown_entries_and_reports_populated(tmp_path):
    _make_scope(tmp_path, "otternest-task-abcd1234", False)
    _make_scope(tmp_path, "otternest-task-dcba5678", True)
    (tmp_path / "cgroup.controllers").write_text("cpu memory pids")
    assert scan_scopes(tmp_path) == (
        TaskScope("otternest-task-abcd1234", False),
        TaskScope("otternest-task-dcba5678", True),
    )


def test_scan_rejects_malformed_unknown_and_symlink_scopes(tmp_path):
    fake = _make_scope(tmp_path, "otternest-task-abcd1234", False)
    (fake / "cgroup.events").write_text("foo 1\n")
    with pytest.raises(CgroupError, match="population"):
        scan_scopes(tmp_path)
    (fake / "cgroup.events").write_text("populated 0\n")
    (tmp_path / "otternest-task-unexpected-name").mkdir()
    with pytest.raises(CgroupError, match="Unexpected"):
        scan_scopes(tmp_path)


def test_scan_rejects_links_even_if_target_would_be_empty(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "cgroup.events").write_text("populated 0\n")
    (tmp_path / "otternest-task-zzzzzzzz").symlink_to(external, target_is_directory=True)
    with pytest.raises(CgroupError, match="real cgroup"):
        scan_scopes(tmp_path)


def _simulate_cgroup_file_removal(monkeypatch, root: Path):
    original = Path.rmdir
    def remove_kernel_pseudofiles(self):
        if self.parent == root and self.name.startswith("otternest-task-"):
            for name in ("cgroup.events", "cgroup.kill"):
                (self / name).unlink()
        return original(self)
    monkeypatch.setattr(Path, "rmdir", remove_kernel_pseudofiles)


def test_recover_removes_only_empty_scopes_by_default(monkeypatch, tmp_path):
    _simulate_cgroup_file_removal(monkeypatch, tmp_path)
    empty = _make_scope(tmp_path, "otternest-task-aaaaaaaa", False)
    populated = _make_scope(tmp_path, "otternest-task-bbbbbbbb", True)
    assert clean_scopes(tmp_path) == {
        "found": 2, "removed": [empty.name], "retained_populated": [populated.name],
    }
    assert not empty.exists()
    assert populated.exists()
    assert (populated / "cgroup.kill").read_text() == ""


def test_explicit_termination_kills_populated_scope(monkeypatch, tmp_path):
    _simulate_cgroup_file_removal(monkeypatch, tmp_path)
    active = _make_scope(tmp_path, "otternest-task-12345678", True)
    written = []
    original = Path.write_text
    def record_write(self, contents, *args, **kwargs):
        if self == active / "cgroup.kill":
            written.append(contents)
        return original(self, contents, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", record_write)
    result = clean_scopes(tmp_path, terminate_populated=True)
    assert result["removed"] == [active.name]
    assert written == ["1"]


def test_runner_refuses_stale_scopes_and_releases_guard(monkeypatch, tmp_path):
    events = []
    class FakeLock:
        def __init__(self, parent):
            events.append("lock-created")
        def acquire(self):
            events.append("locked")
            return self
        def close(self):
            events.append("released")
    monkeypatch.setattr(runner, "build_bwrap_command", lambda cmd, work: ["/usr/bin/true"])
    monkeypatch.setattr(runner, "apply_linux_resource_limits", lambda cmd, **kw: cmd)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kw:
                        type("Completed", (), {"returncode": 0})())
    monkeypatch.setattr(runner, "validate_parent", lambda policy: policy.parent)
    monkeypatch.setattr(runner, "CgroupOwnerLock", FakeLock)
    monkeypatch.setattr(runner, "scan_scopes", lambda parent:
                        (TaskScope("otternest-task-abcd1234", True),))
    with pytest.raises(RunnerFailure, match="otternest-cgroup-recover"):
        Runner("http://127.0.0.1:8000", "on_agent_example", ["true"],
               workspace_root=str(tmp_path), sandbox="bwrap",
               cgroup_parent="/sys/fs/cgroup/private-task",
               memory_max_mib=256, cpu_max_percent=100, pids_max=32)
    assert events == ["lock-created", "locked", "released"]


def test_cli_preview_does_not_mutate_orphans(monkeypatch, tmp_path, capsys):
    parent = tmp_path / "delegated"
    parent.mkdir()
    scope = _make_scope(parent, "otternest-task-abcdefgh", True)
    monkeypatch.setattr(cgroup_recovery, "LOCK_ROOT", tmp_path)
    monkeypatch.setattr(cgroup_recovery, "validate_parent", lambda policy: parent)
    monkeypatch.setattr(sys, "argv", ["otternest-cgroup-recover",
                                      "--parent", str(parent)])
    cgroup_recovery.main()
    record = json.loads(capsys.readouterr().out)
    assert record["mode"] == "preview_only"
    assert record["populated"] == [scope.name]
    assert scope.exists()


def test_cli_requires_apply_for_terminating_populated(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["otternest-cgroup-recover",
                                      "--parent", str(tmp_path),
                                      "--terminate-populated"])
    with pytest.raises(SystemExit) as exc:
        cgroup_recovery.main()
    assert exc.value.code == 2
