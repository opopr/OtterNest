"""Opt-in Linux bubblewrap command construction for isolated code workers.

Fail closed: unlike the trusted generic Runner, sandboxed commands only see a
read-only system runtime and their private writable /work. They have no host
home, control-plane secrets, or network namespace. This is not a substitute for
a hardened VM, cgroup limits, or a security review of untrusted code.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


class SandboxError(ValueError):
    """A requested isolation boundary cannot be guaranteed."""


def _trusted_system_binary(value: str) -> str:
    """Resolve commands to installed system programs, never a host home script."""
    candidate = value if os.path.isabs(value) else shutil.which(value)
    if not candidate:
        raise SandboxError("Sandbox executable was not found")
    try:
        resolved = Path(candidate).resolve(strict=True)
        mode = resolved.stat()
    except (OSError, RuntimeError):
        raise SandboxError("Sandbox executable is not accessible") from None
    if (not resolved.is_relative_to(Path("/usr")) or
            not stat.S_ISREG(mode.st_mode) or not os.access(resolved, os.X_OK)):
        raise SandboxError("Sandbox executable must be an installed program under /usr")
    return str(resolved)


def _bubblewrap_binary() -> str:
    if sys.platform != "linux":
        raise SandboxError("bubblewrap isolation is available only on Linux")
    binary = shutil.which("bwrap")
    if not binary:
        raise SandboxError("bubblewrap is not installed; refusing unisolated execution")
    resolved = _trusted_system_binary(binary)
    info = os.stat(resolved)
    if info.st_uid != 0 or (info.st_mode & 0o022):
        raise SandboxError("bubblewrap must be root-owned and not group/world writable")
    return resolved


def build_bwrap_command(command: list[str], workspace: Path | None) -> list[str]:
    """Build a non-networked, private-mount/PID/IPC sandbox.

    Only the private task directory is writable from the host. The system
    runtime is read-only. Operators should use /usr/bin/python3 or a reviewed
    tool installed in /usr; scripts and input data may live under /work.
    """
    if not command:
        raise SandboxError("Sandbox requires a configured executable")
    if workspace is None:
        raise SandboxError("Sandbox requires --workspace-root")
    try:
        root = workspace.resolve(strict=True)
        info = root.stat()
    except (OSError, RuntimeError):
        raise SandboxError("Sandbox workspace is missing") from None
    if not root.is_dir() or info.st_uid != os.getuid() or (info.st_mode & 0o077):
        raise SandboxError("Sandbox workspace must be a private directory owned by the runner")

    bwrap = _bubblewrap_binary()
    executable = _trusted_system_binary(command[0])
    argv = [
        bwrap,
        "--unshare-all",                 # Includes network, PID, IPC and user namespaces.
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--clearenv",
        "--setenv", "HOME", "/work",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "PYTHONNOUSERSITE", "1",
        "--ro-bind", "/usr", "/usr",
    ]
    # Support both merged-/usr and older distributions without mounting /etc,
    # /home, /root, /run, /var or any other host tree.
    for path in ("/bin", "/sbin", "/lib", "/lib64"):
        source = Path(path)
        if source.is_symlink():
            argv += ["--symlink", os.readlink(source), path]
        elif source.is_dir():
            argv += ["--ro-bind", path, path]
    argv += [
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", str(root), "/work",
        "--chdir", "/work",
        "--",
        executable, *command[1:],
    ]
    return argv


def apply_linux_resource_limits(
    command: list[str], *, cpu_seconds: int | None = None,
    address_space_mib: int | None = None,
) -> list[str]:
    """Set inherited RLIMIT_CPU and RLIMIT_AS with util-linux prlimit.

    Address space is VIRTUAL memory, not resident memory; these limits are
    additional guardrails, NOT cgroup memory.max / pids.max guarantees.
    """
    if cpu_seconds is None and address_space_mib is None:
        return command
    if sys.platform != "linux":
        raise SandboxError("Resource limits require Linux")
    if cpu_seconds is not None and (type(cpu_seconds) is not int or not 1 <= cpu_seconds <= 3600):
        raise SandboxError("CPU limit must be 1..3600 seconds")
    if address_space_mib is not None and (
        type(address_space_mib) is not int or not 64 <= address_space_mib <= 16384
    ):
        raise SandboxError("Address-space limit must be 64..16384 MiB")
    binary = shutil.which("prlimit")
    if not binary:
        raise SandboxError("util-linux prlimit is required for requested resource limits")
    executable = _trusted_system_binary(binary)
    info = os.stat(executable)
    if info.st_uid != 0 or (info.st_mode & 0o022):
        raise SandboxError("prlimit must be root-owned and not group/world writable")
    options = []
    if cpu_seconds is not None:
        options.append(f"--cpu={cpu_seconds}")
    if address_space_mib is not None:
        options.append(f"--as={address_space_mib * 1024 * 1024}")
    return [executable, *options, "--", *command]
