"""Process-tree cleanup and opt-in Linux process resource guardrails."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from otternest import runner, sandbox
from otternest.runner import Runner, RunnerFailure, execute_command
from otternest.sandbox import SandboxError, apply_linux_resource_limits

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process resource limits require Linux")


def test_limits_require_sandbox_before_subprocess_or_task_claim(tmp_path):
    with pytest.raises(RunnerFailure, match="Resource limits require"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"], cpu_seconds=10)
    with pytest.raises(RunnerFailure, match="Resource limits require"):
        execute_command([sys.executable, "-c", "print('oops')"], {}, 30,
                        lambda: None, address_space_mib=128)
    assert not list(tmp_path.iterdir())


def test_requested_limits_validate_exact_integer_ranges():
    with pytest.raises(SandboxError, match="CPU"):
        apply_linux_resource_limits(["true"], cpu_seconds=True)
    with pytest.raises(SandboxError, match="CPU"):
        apply_linux_resource_limits(["true"], cpu_seconds=3601)
    with pytest.raises(SandboxError, match="Address-space"):
        apply_linux_resource_limits(["true"], address_space_mib=32)
    with pytest.raises(SandboxError, match="Address-space"):
        apply_linux_resource_limits(["true"], address_space_mib=32.0)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only prlimit")
def test_limit_command_applies_to_entire_sandbox_tree():
    if not shutil.which("prlimit"):
        pytest.skip("util-linux prlimit is unavailable")
    command = apply_linux_resource_limits(
        ["/usr/bin/bwrap", "--unshare-all", "--", "/usr/bin/true"],
        cpu_seconds=9, address_space_mib=512,
    )
    assert Path(command[0]) == Path(shutil.which("prlimit")).resolve()
    assert "--cpu=9" in command
    assert "--as=536870912" in command
    assert command[-4:] == ["--unshare-all", "--", "/usr/bin/true"] or (
        command[-4:] == ["/usr/bin/bwrap", "--unshare-all", "--", "/usr/bin/true"]
    )
    assert command[command.index("--") + 1] == "/usr/bin/bwrap"


def test_missing_prlimit_refuses_limits_not_silently_ignores(monkeypatch):
    original = shutil.which
    monkeypatch.setattr(sandbox.shutil, "which",
                        lambda name: None if name == "prlimit" else original(name))
    with pytest.raises(SandboxError, match="prlimit"):
        apply_linux_resource_limits(["echo"], cpu_seconds=15)


def test_preflight_applies_limits_before_claim(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "build_bwrap_command",
                        lambda command, work: ["bwrap", *command])

    def wrapped(cmd, **kw):
        calls.append((cmd, kw))
        return ["prlimit", "--cpu=3", "--", *cmd]

    monkeypatch.setattr(runner, "apply_linux_resource_limits", wrapped)
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *args, **kw: SimpleNamespace(returncode=0))
    instance = Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
                      sandbox="bwrap", workspace_root=str(tmp_path),
                      cpu_seconds=3, address_space_mib=256)
    try:
        assert instance.cpu_seconds == 3
        assert instance.address_space_mib == 256
        assert calls == [(["bwrap", "true"],
                          {"cpu_seconds": 3, "address_space_mib": 256})]
    finally:
        instance.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc assertion")
@pytest.mark.parametrize("leader_exits", [False, True])
def test_worker_kills_descendants_on_timeout_and_after_success(tmp_path, leader_exits):
    # Use a child which sleeps after its leader ends, to catch process leaks.
    script = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
        "pathlib.Path('child.pid').write_text(str(p.pid)); "
        + ("print('complete')" if leader_exits else "time.sleep(60)")
    )
    if leader_exits:
        assert execute_command([sys.executable, "-c", script], {}, 2,
                               lambda: None, workspace=tmp_path) == "complete"
    else:
        with pytest.raises(RunnerFailure, match="timeout"):
            execute_command([sys.executable, "-c", script], {}, 1,
                            lambda: None, workspace=tmp_path)
    child = int((tmp_path / "child.pid").read_text())

    def still_running():
        try:
            # A zombie is defunct, not a running child; init reaps it later.
            return Path(f"/proc/{child}/stat").read_text().split(") ")[1][0] != "Z"
        except FileNotFoundError:
            return False

    deadline = time.monotonic() + 3
    while still_running() and time.monotonic() < deadline:
        time.sleep(.05)
    assert not still_running(), "a worker descendant escaped process-group cleanup"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only enforcement")
def test_cpu_guardrail_inherited_by_children():
    if not shutil.which("prlimit"):
        pytest.skip("prlimit unavailable")
    # Running a small process in a CPU-time-limited scope should fail once it
    # consumes the quota. This tests kernel enforcement without bwrap.
    cmd = apply_linux_resource_limits(
        [sys.executable, "-c", "while True: pass"], cpu_seconds=1,
    )
    result = subprocess.run(cmd, capture_output=True, timeout=8)
    assert result.returncode != 0
