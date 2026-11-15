"""Crash-recovery inspection for operator-delegated cgroup v2 task scopes.

An exclusive, process-lifetime flock serializes modern Runners and the
operator recovery command for ONE canonical cgroup parent. Recovery never
kills a populated cgroup unless the operator explicitly opts in. It cannot
distinguish a surviving worker of an older uncooperative Runner version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .cgroups import CgroupError, CgroupPolicy, validate_parent

if sys.platform == "linux":
    import fcntl
else:
    fcntl = None

SCOPE_PATTERN = re.compile(r"otternest-task-[a-z0-9_]{8}\Z")
MAX_SCOPES = 256
LOCK_ROOT = Path("/tmp")


@dataclass(frozen=True)
class TaskScope:
    name: str
    populated: bool


class CgroupOwnerLock:
    """Nonblocking, private advisory lock per delegated parent.

    A distinct lock file is used for each parent and Linux UID; the parent
    itself must be dedicated to one Runner service. The lock must be held for
    the entire Runner lifetime, not only during preflight.
    """

    def __init__(self, parent: Path):
        if fcntl is None:
            raise CgroupError("cgroup recovery requires Linux flock")
        fingerprint = hashlib.sha256(os.fsencode(parent)).hexdigest()[:32]
        self.path = LOCK_ROOT / f"otternest-cgroup-{os.getuid()}-{fingerprint}.lock"
        self._fd: int | None = None

    def acquire(self) -> "CgroupOwnerLock":
        if self._fd is not None:
            raise CgroupError("cgroup ownership lock already acquired")
        fd = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(self.path, flags, 0o600)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    info.st_nlink != 1 or (info.st_mode & 0o077)):
                raise CgroupError("Insecure cgroup ownership lock file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd = fd
            fd = None
            return self
        except (OSError, CgroupError) as exc:
            raise CgroupError("Cgroup parent is in use or lock cannot be secured") from exc
        finally:
            if fd is not None:
                os.close(fd)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "CgroupOwnerLock":
        return self.acquire()

    def __exit__(self, *_exc):
        self.close()


def scan_scopes(parent: Path) -> tuple[TaskScope, ...]:
    """Read only exact OtterNest-generated children; never follow symlinks."""
    scopes = []
    try:
        children = sorted(parent.iterdir(), key=lambda p: p.name)
        for child in children:
            if not child.name.startswith("otternest-task-"):
                continue
            if len(scopes) >= MAX_SCOPES:
                raise CgroupError("Too many orphan scopes; operator inspection required")
            if not SCOPE_PATTERN.fullmatch(child.name):
                raise CgroupError("Unexpected OtterNest scope name; refusing recovery")
            details = child.lstat()
            if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
                raise CgroupError("Orphan scope is not an owned real cgroup directory")
            event_lines = (child / "cgroup.events").read_text().splitlines()
            populated = [line.split()[1] for line in event_lines
                         if len(line.split()) == 2 and line.split()[0] == "populated"]
            if len(populated) != 1 or populated[0] not in {"0", "1"}:
                raise CgroupError("Cannot establish orphan cgroup population state")
            scopes.append(TaskScope(child.name, populated[0] == "1"))
    except CgroupError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise CgroupError("Unable to inspect delegated cgroup children") from exc
    return tuple(scopes)


def clean_scopes(parent: Path, *, terminate_populated: bool = False) -> dict:
    """Call only while CgroupOwnerLock is held by an authorized operator.

    Unpopulated cgroups may be deleted. Populated cgroups are untouched by
    default; terminating one requires a separate explicit CLI flag.
    """
    found = scan_scopes(parent)
    removed: list[str] = []
    retained: list[str] = []
    for scope in found:
        child = parent / scope.name
        # Re-check immediately before destructive operations. An older Runner
        # without the new flock can still race; run recovery only after it stops.
        current = next((x for x in scan_scopes(parent) if x.name == scope.name), None)
        if current is None:
            continue
        if current.populated and not terminate_populated:
            retained.append(scope.name)
            continue
        if current.populated:
            try:
                (child / "cgroup.kill").write_text("1")
            except OSError as exc:
                raise CgroupError("Failed to terminate orphan scope") from exc
        for attempt in range(20):
            try:
                child.rmdir()
                removed.append(scope.name)
                break
            except OSError as exc:
                if attempt == 19:
                    raise CgroupError("Orphan cgroup could not be removed; operator inspection required") from exc
                time.sleep(0.1)
    return {"found": len(found), "removed": removed, "retained_populated": retained}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly clean delegated OtterNest cgroup v2 task scopes"
    )
    parser.add_argument("--parent", required=True, help="Dedicated delegated cgroup2 parent")
    parser.add_argument("--apply", action="store_true", help="Remove empty orphan task scopes")
    parser.add_argument("--terminate-populated", action="store_true",
                        help="DANGEROUS: also kill remaining processes in orphan scopes")
    args = parser.parse_args()
    if args.terminate_populated and not args.apply:
        parser.error("--terminate-populated requires --apply")
    # Limits exist only for policy validation; this command does not alter quotas.
    policy = CgroupPolicy(Path(args.parent), memory_max_mib=64,
                          cpu_max_percent=100, pids_max=8)
    try:
        parent = validate_parent(policy)
        with CgroupOwnerLock(parent):
            if args.apply:
                result = clean_scopes(parent, terminate_populated=args.terminate_populated)
            else:
                scopes = scan_scopes(parent)
                result = {
                    "found": len(scopes),
                    "empty": [scope.name for scope in scopes if not scope.populated],
                    "populated": [scope.name for scope in scopes if scope.populated],
                    "mode": "preview_only",
                }
    except CgroupError as exc:
        print("OtterNest cgroup recovery error: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
