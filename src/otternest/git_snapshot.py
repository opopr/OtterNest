"""Immutable Git source snapshots and bounded, owner-reviewed text patches.

No task-controlled Git URL, hooks, credentials, checkout, or Git push. Remote
fetch is limited to explicitly configured PUBLIC github.com owner/repository
and an operator-pinned branch-head commit. Offline local repositories must be
operator-trusted. The Codex workspace never receives a .git directory.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import os
import re
import selectors
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TREE_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_FILES = 1200
MAX_PATCH_BYTES = 5 * 1024 * 1024
GITHUB_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}\Z")
REF_NAME = re.compile(r"refs/heads/[a-zA-Z0-9][a-zA-Z0-9_./-]{0,119}\Z")
COMMIT_NAME = re.compile(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\Z")
PATCH_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\Z")


class SnapshotError(ValueError):
    """Safe, non-provider diagnostic for failed source preparation."""


@dataclass(frozen=True)
class SnapshotSpec:
    ref: str
    commit: str
    github_repo: str | None = None
    local_repo: Path | None = None

    def validate(self) -> None:
        if (self.github_repo is None) == (self.local_repo is None):
            raise SnapshotError("Choose exactly one Git source")
        if not COMMIT_NAME.fullmatch(self.commit):
            raise SnapshotError("Snapshot requires a complete Git commit SHA")
        if (not REF_NAME.fullmatch(self.ref) or ".." in self.ref or
                "//" in self.ref or self.ref.endswith(("/", ".", ".lock"))):
            raise SnapshotError("Git reference must be a safe explicit heads ref")
        if self.github_repo is not None:
            name = self.github_repo
            if (not GITHUB_NAME.fullmatch(name) or ".." in name or
                    name.endswith(("/", ".git")) or name.split("/")[0].endswith(".")):
                raise SnapshotError("GitHub source must be owner/repository, not a URL")
        else:
            assert self.local_repo is not None
            if not self.local_repo.is_absolute() or not self.local_repo.is_dir():
                raise SnapshotError("Local Git source must be an existing absolute directory")


@dataclass(frozen=True)
class Snapshot:
    origin: str
    ref: str
    commit: str
    files: dict[str, bytes]


def _git_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    return env


def _git_command(binary: str, *args: str) -> list[str]:
    return [binary, "-c", "credential.helper=",
            "-c", "core.hooksPath=/dev/null",
            "-c", "protocol.file.allow=never",
            "-c", "http.followRedirects=false", *args]


def _git_run(command: list[str], *, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_git_env(), timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SnapshotError("Git source command failed or timed out") from None
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise SnapshotError("Git source command was rejected")
    return result.stdout.decode("ascii", errors="replace").strip()


def _bounded_archive(command: list[str]) -> io.BufferedRandom:
    """Stream archive to a temporary file; interrupt before disk-size blowup."""
    archive = tempfile.TemporaryFile()
    proc = None
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_git_env(), start_new_session=True,
        )
        assert proc.stdout is not None
        total = 0
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise SnapshotError("Git archive exceeds bounded size")
            archive.write(chunk)
        if proc.wait(timeout=10) != 0:
            raise SnapshotError("Git archive failed")
        archive.seek(0)
        return archive
    except (OSError, subprocess.TimeoutExpired) as exc:
        archive.close()
        raise SnapshotError("Git archive is not accessible") from exc
    except Exception:
        archive.close()
        raise
    finally:
        if proc is not None:
            try:
                if hasattr(os, "killpg"):
                    import signal
                    os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass


def _check_path(name: str) -> str:
    if (not name or name.startswith("/") or "\\" in name or "\x00" in name or
            ":" in name.split("/")[0]):
        raise SnapshotError("Unsafe Git archive path")
    path = PurePosixPath(name)
    parts = path.parts
    if not parts or any(part in {"", ".", "..", ".git"} for part in parts):
        raise SnapshotError("Unsafe Git archive path")
    return "/".join(parts)


def _read_tree(root: Path, *, excluded: frozenset[str] = frozenset()) -> dict[str, bytes]:
    files = {}
    size = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        for directory in dirs:
            child = Path(current) / directory
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise SnapshotError("Workspace contains a symlink or special directory")
            relative = child.relative_to(root).as_posix()
            _check_path(relative)
        for name in names:
            child = Path(current) / name
            relative = _check_path(child.relative_to(root).as_posix())
            if relative in excluded:
                raise SnapshotError("Reserved output artifact already exists in worktree")
            info = child.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SnapshotError("Workspace contains links or special files")
            if info.st_size > MAX_FILE_BYTES:
                raise SnapshotError("Workspace file exceeds snapshot safety limit")
            size += info.st_size
            if size > MAX_TREE_BYTES or len(files) >= MAX_FILES:
                raise SnapshotError("Workspace exceeds file or total-size limit")
            with child.open("rb") as file:
                files[relative] = file.read(MAX_FILE_BYTES + 1)
                if len(files[relative]) > MAX_FILE_BYTES:
                    raise SnapshotError("Workspace file exceeds snapshot safety limit")
    return files


def import_snapshot(spec: SnapshotSpec, workspace: Path) -> Snapshot:
    """Fetch, verify the exact branch-head SHA, and extract only regular files."""
    spec.validate()
    if not workspace.is_dir() or any(workspace.iterdir()):
        raise SnapshotError("Git snapshot requires an empty dedicated task workspace")
    git = shutil.which("git")
    if not git:
        raise SnapshotError("Trusted system Git executable not installed")
    source = spec.local_repo
    with tempfile.TemporaryDirectory(prefix="otternest-git-fetch-") as scratch:
        if spec.github_repo is not None:
            git_dir = str(Path(scratch) / "git-repository")
            _git_run(_git_command(git, "init", "--bare", "--quiet", git_dir))
            url = "https://github.com/" + spec.github_repo + ".git"
            # Fail closed on moved branches; don't fetch arbitrary task URLs,
            # pull requests, tags, unknown protocols, or local file remotes.
            _git_run(_git_command(
                git, "--git-dir", git_dir, "fetch", "--depth=1",
                "--no-tags", "--quiet", url, spec.ref,
            ))
            prefix = ["--git-dir", git_dir]
            origin = "github.com/" + spec.github_repo
        else:
            assert source is not None
            prefix = ["-C", str(source.resolve(strict=True))]
            origin = "local:" + str(source.resolve(strict=True))
        actual = _git_run(_git_command(git, *prefix, "rev-parse", "--verify",
                                      (("FETCH_HEAD" if spec.github_repo else spec.ref) + "^{commit}")))
        if actual.lower() != spec.commit.lower():
            raise SnapshotError("Git branch head does not match the pinned commit")
        archive = _bounded_archive(_git_command(
            git, *prefix, "archive", "--format=tar", actual,
        ))
        try:
            files: dict[str, bytes] = {}
            total = 0
            with tarfile.open(fileobj=archive, mode="r:") as tar:
                for member in tar:
                    name = _check_path(member.name.rstrip("/"))
                    if member.isdir():
                        (workspace / name).mkdir(mode=0o700, parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        raise SnapshotError("Git snapshot contains links or nonregular entries")
                    if name in files or len(files) >= MAX_FILES or member.size > MAX_FILE_BYTES:
                        raise SnapshotError("Git snapshot exceeds per-file or file-count limits")
                    total += member.size
                    if total > MAX_TREE_BYTES:
                        raise SnapshotError("Git snapshot exceeds total file limit")
                    handle = tar.extractfile(member)
                    if handle is None:
                        raise SnapshotError("Git archive file cannot be read")
                    data = handle.read(MAX_FILE_BYTES + 1)
                    if len(data) != member.size:
                        raise SnapshotError("Git archive file content is inconsistent")
                    destination = workspace / name
                    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    if destination.exists():
                        raise SnapshotError("Git archive has duplicate paths")
                    with destination.open("xb") as output:
                        output.write(data)
                    destination.chmod(0o700 if member.mode & 0o111 else 0o600)
                    files[name] = data
            return Snapshot(origin, spec.ref, actual.lower(), files)
        finally:
            archive.close()


def compare_snapshot(snapshot: Snapshot, workspace: Path, *,
                     reserved: frozenset[str] = frozenset()) -> dict[str, tuple[bytes | None, bytes | None]]:
    files = _read_tree(workspace, excluded=reserved)
    return {
        path: (snapshot.files.get(path), files.get(path))
        for path in sorted(snapshot.files.keys() | files.keys())
        if snapshot.files.get(path) != files.get(path)
    }


def make_patch(snapshot: Snapshot, workspace: Path, *, reserved: frozenset[str]) -> tuple[bytes, int]:
    """Text-only unified patch, anchored to source SHA, no auto-apply or Git push."""
    differences = compare_snapshot(snapshot, workspace, reserved=reserved)
    if not differences:
        raise SnapshotError("Codex reported success but produced no source changes")
    lines: list[str] = []
    for path, (before, after) in differences.items():
        if (not PATCH_NAME.fullmatch(Path(path).name) or
                any(not PATCH_NAME.fullmatch(x) for x in PurePosixPath(path).parts)):
            raise SnapshotError("Changed filename is not patch-safe ASCII")
        try:
            old = before.decode("utf-8") if before is not None else ""
            new = after.decode("utf-8") if after is not None else ""
        except UnicodeDecodeError:
            raise SnapshotError("Binary file modifications cannot be represented as text patch") from None
        if (old and not old.endswith("\n")) or (new and not new.endswith("\n")):
            raise SnapshotError("Modified text files must end with a newline")
        from_name = "a/" + path if before is not None else "/dev/null"
        to_name = "b/" + path if after is not None else "/dev/null"
        piece = list(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=from_name, tofile=to_name, n=3,
        ))
        if not piece:
            continue
        lines.append(f"diff --git a/{path} b/{path}\n")
        if before is None:
            lines.append("new file mode 100644\n")
        elif after is None:
            lines.append("deleted file mode 100644\n")
        lines.extend(piece)
        if sum(len(line.encode("utf-8")) for line in lines) > MAX_PATCH_BYTES:
            raise SnapshotError("Generated patch exceeds 5 MiB artifact limit")
    patch = "".join(lines).encode("utf-8")
    if not patch or len(patch) > MAX_PATCH_BYTES:
        raise SnapshotError("Generated patch is empty or exceeds artifact limit")
    return patch, len(differences)
