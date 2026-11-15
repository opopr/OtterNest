"""Opt-in delegated Linux cgroup v2 budgets for sandboxed task *trees*.

Requires an operator-owned, delegated cgroup2 parent with cpu/memory/pids
controllers enabled in cgroup.subtree_control. Never writes to ancestor limits.
The child enters the cgroup BEFORE exec of bubblewrap or any task code.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


class CgroupError(ValueError):
    """Fail closed if any requested task-level resource boundary is absent."""


@dataclass(frozen=True)
class CgroupPolicy:
    parent: Path
    memory_max_mib: int
    cpu_max_percent: int
    pids_max: int

    def __post_init__(self):
        for value, lower, upper, name in (
            (self.memory_max_mib, 64, 16384, "memory"),
            (self.cpu_max_percent, 10, 400, "CPU"),
            (self.pids_max, 8, 1024, "PIDs"),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise CgroupError(f"Invalid {name} task limit")


def validate_parent(policy: CgroupPolicy) -> Path:
    """Accept only the actual delegated cgroup2 tree, not an arbitrary path."""
    if sys.platform != "linux":
        raise CgroupError("cgroup v2 task limits require Linux")
    raw = policy.parent
    if not raw.is_absolute() or not raw.is_relative_to(Path("/sys/fs/cgroup")):
        raise CgroupError("cgroup parent must be absolute and under /sys/fs/cgroup")
    try:
        parent = raw.resolve(strict=True)
        info = parent.stat()
        # Detect the cgroup2 mount, including when running under a namespace.
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
        has_mount = any(
            " - cgroup2 " in line and len(line.split(" - ", 1)[0].split()) >= 5
            and line.split(" - ", 1)[0].split()[4] == "/sys/fs/cgroup"
            for line in lines
        )
        enabled = set((parent / "cgroup.subtree_control").read_text().split())
    except (OSError, ValueError, RuntimeError):
        raise CgroupError("cgroup parent is not an accessible delegated cgroup v2 directory") from None
    if not has_mount or raw != parent or not stat.S_ISDIR(info.st_mode):
        raise CgroupError("cgroup parent is not on the required cgroup v2 mount")
    if info.st_uid != os.getuid() or (info.st_mode & 0o022) or not os.access(parent, os.W_OK | os.X_OK):
        raise CgroupError("cgroup parent must be writable and owned by the worker identity")
    if not {"cpu", "memory", "pids"}.issubset(enabled):
        raise CgroupError("cgroup parent needs delegated cpu, memory and pids subtree controllers")
    return parent


# Trusted Python executes only this fixed bootstrap. It cannot run the user
# command before membership is successfully written to cgroup.procs.
_ATTACH_SCRIPT = (
    "import os,sys;"
    "fd=int(sys.argv[1]);"
    "assert os.write(fd,str(os.getpid()).encode())>0;"
    "os.close(fd);"
    "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
)


class TaskCgroup:
    """Own a fresh cgroup. Always attempt cgroup.kill + rmdir after the task."""

    def __init__(self, policy: CgroupPolicy):
        self.policy = policy
        self.path: Path | None = None
        self._procs_fd: int | None = None

    def __enter__(self) -> "TaskCgroup":
        parent = validate_parent(self.policy)
        try:
            path = Path(tempfile.mkdtemp(prefix="otternest-task-", dir=parent))
            self.path = path
            for name, value in (
                ("memory.max", str(self.policy.memory_max_mib * 1024 * 1024)),
                ("cpu.max", f"{self.policy.cpu_max_percent * 1000} 100000"),
                ("pids.max", str(self.policy.pids_max)),
            ):
                (path / name).write_text(value)
            if not (path / "cgroup.kill").exists():
                raise CgroupError("Kernel cgroup.kill support is required")
            # Prevent a child from using a different path after parent checks;
            # the fd is passed by Popen and explicitly closed before exec.
            self._procs_fd = os.open(
                path / "cgroup.procs", os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            )
        except (OSError, CgroupError) as exc:
            self.close()
            raise CgroupError("Unable to create configured cgroup task scope") from exc
        return self

    def command(self, executable: list[str]) -> tuple[list[str], tuple[int, ...]]:
        if not executable or self._procs_fd is None:
            raise CgroupError("Task cgroup has not been initialized")
        return (
            [sys.executable, "-I", "-S", "-c", _ATTACH_SCRIPT,
             str(self._procs_fd), *executable],
            (self._procs_fd,),
        )

    def close(self) -> None:
        if self._procs_fd is not None:
            os.close(self._procs_fd)
            self._procs_fd = None
        if self.path is None:
            return
        path, self.path = self.path, None
        try:
            (path / "cgroup.kill").write_text("1")
        except OSError:
            pass
        for attempt in range(20):
            try:
                path.rmdir()
                return
            except OSError:
                if attempt < 19:
                    time.sleep(0.1)
        raise CgroupError("Task cgroup is not empty or cannot be removed; inspect delegated subtree")

    def __exit__(self, *_exc) -> None:
        self.close()
