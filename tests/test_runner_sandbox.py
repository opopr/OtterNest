"""Fail-closed Linux sandbox policy for the optional code-execution Runner."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from otternest import runner, sandbox
from otternest.runner import Runner, RunnerFailure, execute_command
from otternest.sandbox import SandboxError, build_bwrap_command


def test_builder_requires_private_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "_bubblewrap_binary", lambda: "/usr/bin/bwrap")
    with pytest.raises(SandboxError, match="workspace"):
        build_bwrap_command(["true"], None)
    tmp_path.chmod(0o755)
    try:
        with pytest.raises(SandboxError, match="private"):
            build_bwrap_command(["true"], tmp_path)
    finally:
        tmp_path.chmod(0o700)


def test_builder_no_host_home_no_network_and_read_only_system(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "_bubblewrap_binary", lambda: "/usr/bin/bwrap")
    args = build_bwrap_command(["true", "--some-argument"], tmp_path)
    assert args[0] == "/usr/bin/bwrap"
    for flag in ("--unshare-all", "--die-with-parent", "--new-session",
                 "--clearenv", "--cap-drop", "--ro-bind", "--proc",
                 "--dev", "--tmpfs", "--bind", "--chdir"):
        assert flag in args
    assert args[args.index("--ro-bind") + 1:args.index("--ro-bind") + 3] == ["/usr", "/usr"]
    assert args[args.index("--bind") + 1:args.index("--bind") + 3] == [
        str(tmp_path.resolve()), "/work"
    ]
    assert args[args.index("--chdir") + 1] == "/work"
    assert args[-2:] == ["true", "--some-argument"] or args[-2:] == ["/usr/bin/true", "--some-argument"]
    assert "--share-net" not in args
    assert "--bind" not in args[:args.index("--ro-bind")]
    assert "/home" not in args and "/root" not in args and "/etc" not in args
    assert args[args.index("--setenv") + 1:args.index("--setenv") + 3] == ["HOME", "/work"]


def test_builder_rejects_untrusted_executable_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "_bubblewrap_binary", lambda: "/usr/bin/bwrap")
    script = tmp_path / "run-me"
    script.write_text("#!/bin/sh\necho unsafe\n")
    script.chmod(0o755)
    with pytest.raises(SandboxError, match="under /usr"):
        build_bwrap_command([str(script)], tmp_path)


def test_bwrap_missing_fails_closed(monkeypatch, tmp_path):
    real_which = shutil.which
    monkeypatch.setattr(sandbox.shutil, "which", lambda x: None if x == "bwrap" else real_which(x))
    with pytest.raises(SandboxError, match="not installed"):
        build_bwrap_command(["true"], tmp_path)
    with pytest.raises(RunnerFailure, match="not installed"):
        execute_command(["true"], {"task": {"title": "unsafe"}}, 30,
                        lambda: None, workspace=tmp_path, sandbox="bwrap")


def test_non_linux_is_rejected_before_spawning(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    with pytest.raises(SandboxError, match="Linux"):
        sandbox._bubblewrap_binary()


def test_runner_requires_workspace_and_no_credentials(tmp_path):
    with pytest.raises(RunnerFailure, match="workspace"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"], sandbox="bwrap")
    with pytest.raises(RunnerFailure, match="credentials"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
               sandbox="bwrap", workspace_root=str(tmp_path), pass_env=("EXTERNAL_API_KEY",))


def test_preflight_rejects_unusable_kernel_before_task_claim(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "build_bwrap_command", lambda command, work: ["/usr/bin/false"])
    seen = []

    def failing_run(*args, **kwargs):
        seen.append(args[0])
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner.subprocess, "run", failing_run)
    with pytest.raises(RunnerFailure, match="preflight failed"):
        Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
               sandbox="bwrap", workspace_root=str(tmp_path))
    assert seen == [["/usr/bin/false"]]
    assert list(tmp_path.iterdir()) == []


def test_runner_preflight_passes_and_sandbox_mode_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "build_bwrap_command", lambda command, work: ["/usr/bin/true"])
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    instance = Runner("http://127.0.0.1:8000", "on_agent_test", ["true"],
                      sandbox="bwrap", workspace_root=str(tmp_path))
    try:
        assert instance.sandbox == "bwrap"
    finally:
        instance.close()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only integration")
def test_live_bwrap_isolation_only_when_explicitly_enabled(tmp_path):
    """Optional host test: actual namespace support is outside normal CI."""
    if os.environ.get("OTTERNEST_LIVE_SANDBOX_TEST") != "1":
        pytest.skip("set OTTERNEST_LIVE_SANDBOX_TEST=1 on an isolated Linux test host")
    child = [
        "/usr/bin/python3", "-c",
        "import os,socket; "
        "assert not os.path.exists('/etc/passwd'); "
        "assert not os.path.exists('/home'); "
        "assert os.path.isdir('/work'); "
        "s=socket.socket(); "
        "assert s.connect_ex(('127.0.0.1', 9)) != 0; "
        "print('sandbox ok')",
    ]
    cmd = build_bwrap_command(child, tmp_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "sandbox ok" in result.stdout
