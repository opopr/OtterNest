"""Delegated cgroup v2 budgets: validation, preflight and worker lifecycle."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from otternest import cgroups, runner
from otternest.cgroups import CgroupError, CgroupPolicy, TaskCgroup
from otternest.runner import Runner, RunnerFailure, execute_command


def policy(parent=Path("/sys/fs/cgroup/otternest-runner"), **overrides):
    values = {"memory_max_mib": 256, "cpu_max_percent": 100, "pids_max": 32}
    values.update(overrides)
    return CgroupPolicy(parent, **values)


@pytest.mark.parametrize("key,value", [
    ("memory_max_mib", 63), ("memory_max_mib", True),
    ("cpu_max_percent", 0), ("cpu_max_percent", 401),
    ("pids_max", 7), ("pids_max", 1025), ("pids_max", 16.0),
])
def test_reject_invalid_cgroup_budgets(key, value):
    with pytest.raises(CgroupError):
        policy(**{key: value})


def test_refuse_non_delegated_cgroup_paths_and_missing_controllers(tmp_path, monkeypatch):
    with pytest.raises(CgroupError):
        cgroups.validate_parent(policy(tmp_path))
    # The canonical mount is required; a fake test directory may never be
    # accepted as a real cgroup, regardless of its local contents.


def test_runner_requires_all_flags_and_bwrap(tmp_path):
    with pytest.raises(RunnerFailure, match="all four"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
               cgroup_parent="/sys/fs/cgroup/task")
    with pytest.raises(RunnerFailure, match="all four"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
               sandbox="bwrap", workspace_root=str(tmp_path),
               cgroup_parent="/sys/fs/cgroup/task", memory_max_mib=256)
    with pytest.raises(RunnerFailure, match="Invalid cgroup"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
               sandbox="bwrap", workspace_root=str(tmp_path),
               cgroup_parent="/sys/fs/cgroup/task", memory_max_mib=16,
               cpu_max_percent=100, pids_max=32)


def test_fixed_bootstrap_attaches_before_any_worker_code():
    script = cgroups._ATTACH_SCRIPT
    assert script.index("os.write") < script.index("os.execvpe")
    assert "os.close(fd)" in script


def test_scope_configures_three_kernel_controllers_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(cgroups, "validate_parent", lambda _: tmp_path)
    original_mkdtemp = cgroups.tempfile.mkdtemp
    scope_dirs = []

    def fake_mkdtemp(**kwargs):
        name = original_mkdtemp(**kwargs)
        dir = Path(name)
        scope_dirs.append(dir)
        (dir / "cgroup.kill").write_text("")
        (dir / "cgroup.procs").write_text("")
        return name

    monkeypatch.setattr(cgroups.tempfile, "mkdtemp", fake_mkdtemp)
    original_rmdir = Path.rmdir

    def fake_rmdir(self):
        if self in scope_dirs:
            for child in self.iterdir():
                child.unlink()
        return original_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", fake_rmdir)
    with TaskCgroup(policy(tmp_path)) as scope:
        assert scope.path is not None
        assert (scope.path / "memory.max").read_text() == str(256 * 1024 * 1024)
        assert (scope.path / "cpu.max").read_text() == "100000 100000"
        assert (scope.path / "pids.max").read_text() == "32"
        cmd, fds = scope.command(["/usr/bin/true"])
        assert cmd[:4] == [sys.executable, "-I", "-S", "-c"]
        assert cmd[-1] == "/usr/bin/true"
        assert fds and fds[0] >= 0
    assert not scope_dirs[0].exists()


def test_preflight_probes_membership_before_claim(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(runner, "build_bwrap_command", lambda c, d: ["/usr/bin/true"])
    monkeypatch.setattr(runner, "apply_linux_resource_limits", lambda cmd, **kw: cmd)
    monkeypatch.setattr(runner, "validate_parent", lambda _: events.append("validate"))
    class FakeScope:
        def __init__(self, p): events.append("create")
        def __enter__(self): events.append("enter"); return self
        def command(self, cmd): events.append("attach"); return cmd, ()
        def __exit__(self, *_): events.append("cleanup")
    monkeypatch.setattr(runner, "TaskCgroup", FakeScope)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw:
                        (events.append("probe") or SimpleNamespace(returncode=0)))
    instance = Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
                      sandbox="bwrap", workspace_root=str(tmp_path),
                      cgroup_parent="/sys/fs/cgroup/otternest", memory_max_mib=256,
                      cpu_max_percent=100, pids_max=32)
    try:
        assert events == ["probe", "validate", "create", "enter", "attach", "probe", "cleanup"]
        assert instance.cgroup_policy.pids_max == 32
    finally:
        instance.close()


def test_cgroup_preflight_failure_refuses_start(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "build_bwrap_command", lambda c, d: ["/usr/bin/true"])
    monkeypatch.setattr(runner, "apply_linux_resource_limits", lambda cmd, **kw: cmd)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(runner, "validate_parent", lambda p:
                        (_ for _ in ()).throw(CgroupError("missing controllers")))
    with pytest.raises(RunnerFailure, match="preflight failed"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
               sandbox="bwrap", workspace_root=str(tmp_path),
               cgroup_parent="/sys/fs/cgroup/otternest", memory_max_mib=256,
               cpu_max_percent=100, pids_max=32)


def test_execute_command_passes_only_attached_launcher_to_popen(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(runner, "build_bwrap_command", lambda c, d: c)
    class FakeScope:
        def __init__(self, p): events.append("create")
        def __enter__(self): events.append("enter"); return self
        def command(self, cmd): events.append("attach"); return cmd, ()
        def __exit__(self, *_): events.append("cleanup")
    monkeypatch.setattr(runner, "TaskCgroup", FakeScope)
    result = execute_command(
        [sys.executable, "-c", "print('sandbox result')"],
        {"task": {"id": "test"}}, 30, lambda: None, workspace=tmp_path,
        sandbox="bwrap", cgroup_policy=policy(),
    )
    assert result == "sandbox result"
    assert events == ["create", "enter", "attach", "cleanup"]


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux cgroup v2")
def test_real_cgroup_only_when_explicitly_delegated(monkeypatch):
    """Manual on a delegated cgroup host, never silently skip CI enforcement."""
    parent = os.getenv("OTTERNEST_LIVE_CGROUP_PARENT")
    if not parent:
        pytest.skip("set OTTERNEST_LIVE_CGROUP_PARENT on an isolated delegated Linux host")
    with TaskCgroup(policy(Path(parent))) as scope:
        command, fds = scope.command([
            sys.executable, "-I", "-c",
            "import pathlib;print(pathlib.Path('/proc/self/cgroup').read_text())",
        ])
        import subprocess
        done = subprocess.run(command, pass_fds=fds, check=True,
                              capture_output=True, text=True, timeout=5)
        assert scope.path.name in done.stdout
