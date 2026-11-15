"""Generic execution worker: connects to the OtterNest task API, not any AI vendor.

The configured executable receives a JSON Task Context Packet on stdin. It may
print JSON {"summary": "..."} or plain text to stdout. The user is responsible
for running that executable under an appropriately isolated OS identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from .sandbox import SandboxError, build_bwrap_command, apply_linux_resource_limits
from .cgroups import CgroupError, CgroupPolicy, TaskCgroup, validate_parent


class RunnerFailure(Exception):
    pass


MAX_ARTIFACTS_PER_TASK = 8
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 20 * 1024 * 1024


def _stop_worker_tree(proc: subprocess.Popen) -> None:
    """Reap the leader and signal its private process group on every exit.

    The host session is never signaled. In bwrap mode, killing the namespace
    leader also tears down processes inside its PID namespace. A TRUSTED
    worker which deliberately escapes its process group is not contained.
    """
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        # Always send KILL to remaining descendants, even when the leader has
        # already exited successfully. A leader-only terminate leaks children.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            proc.kill()
    elif proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
    proc.wait()



@dataclass(frozen=True)
class WorkerArtifact:
    path: Path
    name: str


@dataclass(frozen=True)
class WorkerResult:
    summary: str
    artifacts: tuple[WorkerArtifact, ...] = ()
    checkpoint_summary: str | None = None
    checkpoint_next_steps: str = ""


def parse_worker_output(output: str, workspace: Path | None = None) -> WorkerResult:
    """Parse an optional manifest without trusting output paths or arbitrary URLs.

    Legacy plain text / {"summary": "..."} output remains supported.
    Artifact files must be inside an operator-supplied per-task workspace.
    """
    try:
        document = json.loads(output)
    except (ValueError, TypeError):
        document = None
    if not isinstance(document, dict) or not isinstance(document.get("summary"), str):
        if isinstance(document, dict) and ("artifacts" in document or "checkpoint" in document):
            raise RunnerFailure("Worker manifest requires a textual summary")
        return WorkerResult(summary=output)
    summary = document["summary"].strip()
    if not summary or len(summary) > 20000:
        raise RunnerFailure("Worker result summary is empty or too large")

    artifacts_data = document.get("artifacts", [])
    if not isinstance(artifacts_data, list) or len(artifacts_data) > MAX_ARTIFACTS_PER_TASK:
        raise RunnerFailure("Worker manifest has too many artifacts")
    if artifacts_data and workspace is None:
        raise RunnerFailure("Artifact manifest requires --workspace-root")
    root = workspace.resolve(strict=True) if workspace is not None else None
    artifacts: list[WorkerArtifact] = []
    names: set[str] = set()
    source_paths: set[Path] = set()
    for item in artifacts_data:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RunnerFailure("Worker artifact needs a relative path")
        relative = item["path"]
        parts = PurePosixPath(relative).parts
        if (not relative or relative.startswith("/") or "\\" in relative or
                not parts or any(p in {".", ".."} for p in parts) or ":" in parts[0]):
            raise RunnerFailure("Worker artifact path must be a safe relative POSIX path")
        name = item.get("name", parts[-1])
        if (not isinstance(name, str) or not 1 <= len(name) <= 240 or
                not name.isascii() or any(not 32 <= ord(c) <= 126 for c in name) or
                "/" in name or "\\" in name or name in names):
            raise RunnerFailure("Artifact names must be unique, printable ASCII filenames")
        assert root is not None
        candidate = root.joinpath(*parts)
        # Reject links even when their targets remain inside the workspace.
        if any((root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
            raise RunnerFailure("Artifact manifest cannot contain symlinks")
        try:
            resolved = candidate.resolve(strict=True)
            details = resolved.stat()
        except (OSError, ValueError, RuntimeError):
            raise RunnerFailure("Artifact manifest references a missing or invalid file") from None
        if not resolved.is_relative_to(root) or not stat.S_ISREG(details.st_mode):
            raise RunnerFailure("Artifact must be a regular workspace file")
        if details.st_nlink != 1:
            raise RunnerFailure("Artifact hard links are not allowed")
        if details.st_size > MAX_ARTIFACT_BYTES:
            raise RunnerFailure("Artifact exceeds 5 MiB")
        if resolved in source_paths:
            raise RunnerFailure("Duplicate artifact source path")
        names.add(name)
        source_paths.add(resolved)
        artifacts.append(WorkerArtifact(resolved, name))

    checkpoint = document.get("checkpoint")
    checkpoint_summary = None
    checkpoint_next_steps = ""
    if checkpoint is not None:
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("summary"), str):
            raise RunnerFailure("Checkpoint must contain a summary")
        checkpoint_summary = checkpoint["summary"].strip()
        checkpoint_next_steps = checkpoint.get("next_steps", "")
        if (not checkpoint_summary or len(checkpoint_summary) > 10000 or
                not isinstance(checkpoint_next_steps, str) or len(checkpoint_next_steps) > 6000):
            raise RunnerFailure("Checkpoint content is invalid")
    return WorkerResult(summary, tuple(artifacts), checkpoint_summary, checkpoint_next_steps)


def worker_environment(pass_env: tuple[str, ...] = ()) -> dict[str, str]:
    """No owner/Agent control-plane secrets are inherited by child by default."""
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT", "WINDIR"}
    allowed.update(pass_env)
    forbidden = {"OTTERNEST_OWNER_TOKEN", "OTTERNEST_AGENT_TOKEN", "OTTERNEST_DATABASE_URL"}
    if forbidden.intersection(pass_env):
        raise RunnerFailure("Refusing to expose OtterNest control-plane credentials to child")
    return {k: v for k, v in os.environ.items() if k in allowed and k not in forbidden}


def execute_command(command: list[str], packet: dict, timeout_seconds: int,
                    heartbeat, pass_env: tuple[str, ...] = (), *,
                    workspace: Path | None = None, structured: bool = False,
                    sandbox: str = "none", cpu_seconds: int | None = None,
                    address_space_mib: int | None = None,
                    cgroup_policy: CgroupPolicy | None = None) -> str | WorkerResult:
    """Explicit executable, no shell; output bounded before returning to server."""
    if not command:
        raise RunnerFailure("No execution command configured")
    if sandbox not in {"none", "bwrap"}:
        raise RunnerFailure("Unsupported execution isolation mode")
    if sandbox == "bwrap" and pass_env:
        raise RunnerFailure("Sandboxed code execution does not pass through host credentials")
    if sandbox != "bwrap" and (cpu_seconds is not None or address_space_mib is not None
                               or cgroup_policy is not None):
        raise RunnerFailure("Resource limits require --sandbox bwrap")
    try:
        launch_command = build_bwrap_command(command, workspace) if sandbox == "bwrap" else command
        launch_command = apply_linux_resource_limits(
            launch_command, cpu_seconds=cpu_seconds, address_space_mib=address_space_mib,
        )
    except SandboxError as exc:
        raise RunnerFailure(str(exc)) from None
    content = json.dumps(packet, ensure_ascii=False).encode("utf-8")
    if len(content) > 300_000:
        raise RunnerFailure("Context too large for execution")
    # A PIPE write may block forever if a child does not read stdin. Stage the
    # bounded packet in an unnamed temporary file before starting the process,
    # so the execution timeout and lease heartbeats cover all child behavior.
    try:
        scope_manager = TaskCgroup(cgroup_policy) if cgroup_policy else nullcontext(None)
        with scope_manager as scope:
            fds: tuple[int, ...] = ()
            if scope is not None:
                launch_command, fds = scope.command(launch_command)
            with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                stdin.write(content)
                stdin.seek(0)
                with subprocess.Popen(launch_command, shell=False, stdin=stdin, cwd=workspace,
                                      stdout=stdout, stderr=stderr, env=worker_environment(pass_env),
                                      start_new_session=(os.name == "posix"), pass_fds=fds) as proc:
                    try:
                        started = time.monotonic()
                        next_heartbeat = started + 30
                        while proc.poll() is None:
                            if time.monotonic() - started >= timeout_seconds:
                                raise RunnerFailure("Execution exceeded configured timeout")
                            if time.monotonic() >= next_heartbeat:
                                heartbeat()  # abort on revoked or expired lease
                                next_heartbeat = time.monotonic() + 30
                            time.sleep(0.1)
                    finally:
                        _stop_worker_tree(proc)
                    stdout.seek(0)
                    data = stdout.read(20001)
                    if proc.returncode:
                        raise RunnerFailure(f"Configured executable exited with status {proc.returncode}")
                    if len(data) > 20000:
                        raise RunnerFailure("Executable output exceeded 20000 bytes")
                    output = data.decode("utf-8", errors="replace").strip()
                    if not output:
                        raise RunnerFailure("Executable produced no result")
                    result = parse_worker_output(output, workspace)
                    return result if structured else result.summary
    except CgroupError as exc:
        raise RunnerFailure(str(exc)) from None


class Runner:
    def __init__(self, server: str, token: str, command: list[str], timeout: int = 600,
                 allow_r3: bool = False, pass_env: tuple[str, ...] = (),
                 runtime_id: str | None = None, workspace_root: str | None = None,
                 sandbox: str = "none", cpu_seconds: int | None = None,
                 address_space_mib: int | None = None):
        if not token.startswith("on_agent_"):
            raise RunnerFailure("OTTERNEST_AGENT_TOKEN required (not the owner token)")
        if not server.startswith("https://") and not (server.startswith("http://127.0.0.1:") or server.startswith("http://localhost:")):
            raise RunnerFailure("HTTPS required except explicit localhost development")
        if timeout < 30 or timeout > 3600:
            raise RunnerFailure("Timeout must be between 30 and 3600 seconds")
        self.client = httpx.Client(base_url=server.rstrip("/"), headers={"Authorization": "Bearer " + token},
                                   follow_redirects=False, timeout=15)
        self.command = command
        self.timeout = timeout
        self.allow_r3 = allow_r3
        self.pass_env = pass_env
        self.runtime_id = runtime_id
        if workspace_root is not None:
            configured_root = Path(workspace_root)
            if not configured_root.is_absolute() or not configured_root.is_dir():
                raise RunnerFailure("--workspace-root must be an existing absolute directory")
            self.workspace_root = configured_root.resolve(strict=True)
        else:
            self.workspace_root = None
        if sandbox not in {"none", "bwrap"}:
            raise RunnerFailure("Unsupported execution isolation mode")
        if sandbox != "bwrap" and (cpu_seconds is not None or address_space_mib is not None):
            raise RunnerFailure("Resource limits require --sandbox bwrap")
        if sandbox == "bwrap":
            if self.workspace_root is None:
                raise RunnerFailure("--sandbox bwrap requires --workspace-root")
            if self.pass_env:
                raise RunnerFailure("Sandboxed code execution does not pass through host credentials")
            # Check namespace support BEFORE taking a task lease. WSL2 and some
            # Linux hosts disable unprivileged user namespaces even with bwrap.
            with tempfile.TemporaryDirectory(prefix="otternest-sandbox-probe-",
                                             dir=self.workspace_root) as probe_dir:
                try:
                    # Validate the configured executable before leasing a task,
                    # but NEVER execute its program during preflight.
                    build_bwrap_command(self.command, Path(probe_dir))
                    probe = build_bwrap_command(["true"], Path(probe_dir))
                    probe = apply_linux_resource_limits(
                        probe, cpu_seconds=cpu_seconds,
                        address_space_mib=address_space_mib,
                    )
                    verified = subprocess.run(
                        probe, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=worker_environment(),
                        timeout=5, check=False,
                    )
                except (SandboxError, OSError, subprocess.TimeoutExpired) as exc:
                    raise RunnerFailure("bubblewrap isolation preflight failed: " +
                                        (str(exc) if isinstance(exc, SandboxError) else type(exc).__name__)) from None
                if verified.returncode != 0:
                    raise RunnerFailure("bubblewrap isolation preflight failed; task was not claimed")
        self.sandbox = sandbox
        self.cpu_seconds = cpu_seconds
        self.address_space_mib = address_space_mib

    def upload_artifacts(self, task_id: str, epoch: int,
                         artifacts: tuple[WorkerArtifact, ...], heartbeat) -> list[str]:
        """Upload only verified private-workspace files under the active lease."""
        uploaded: list[str] = []
        total_bytes = 0
        for artifact in artifacts:
            # O_NOFOLLOW protects the final component against link replacement;
            # the directory must also be isolated with OS-level permissions.
            fd = os.open(artifact.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise RunnerFailure("Artifact file was replaced or hard-linked")
                if info.st_size > MAX_ARTIFACT_BYTES:
                    raise RunnerFailure("Artifact exceeds 5 MiB")
                content = file.read(MAX_ARTIFACT_BYTES + 1)
            if len(content) > MAX_ARTIFACT_BYTES:
                raise RunnerFailure("Artifact exceeds 5 MiB")
            total_bytes += len(content)
            if total_bytes > MAX_TOTAL_ARTIFACT_BYTES:
                raise RunnerFailure("Artifact uploads exceed 20 MiB task limit")
            heartbeat()
            response = self.client.post(
                f"/api/v1/tasks/{task_id}/artifacts",
                params={"epoch": epoch},
                headers={"X-Artifact-Name": artifact.name, "Content-Type": "application/octet-stream"},
                content=content,
            )
            response.raise_for_status()
            record = response.json()
            if (not isinstance(record, dict) or not isinstance(record.get("id"), str) or
                    record.get("checksum") != hashlib.sha256(content).hexdigest()):
                raise RunnerFailure("Server artifact acknowledgement is invalid")
            uploaded.append(record["id"])
        return uploaded

    def api(self, method: str, path: str, payload=None):
        response = self.client.request(method, "/api/v1" + path, json=payload)
        response.raise_for_status()
        return response.json()

    def runtime_heartbeat(self, status: str = "idle") -> None:
        """Opt-in heartbeat for a pre-registered runtime (self-reported status)."""
        if self.runtime_id:
            self.api("POST", f"/agent/runtimes/{self.runtime_id}/heartbeat", {"status": status})

    def run_one(self) -> bool:
        self.runtime_heartbeat("idle")
        queue = self.api("GET", "/agent/queue")
        for item in queue:
            if item["risk"] == "R3" and not self.allow_r3:
                continue
            task_id = item["id"]
            try:
                claim = self.api("POST", f"/tasks/{task_id}/claim", {"lease_seconds": 900})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:  # another worker got it
                    continue
                raise
            epoch = claim["lease_epoch"]
            try:
                self.runtime_heartbeat("busy")
                packet = self.api("GET", f"/tasks/{task_id}/context")
                def heartbeat():
                    self.api("POST", f"/tasks/{task_id}/heartbeat", {"epoch": epoch})
                    self.runtime_heartbeat("busy")
                workspace_manager = (
                    tempfile.TemporaryDirectory(prefix="otternest-task-", dir=self.workspace_root)
                    if self.workspace_root is not None else nullcontext(None)
                )
                with workspace_manager as workspace_dir:
                    result = execute_command(
                        self.command, packet, self.timeout, heartbeat, self.pass_env,
                        workspace=Path(workspace_dir) if workspace_dir else None,
                        structured=True, sandbox=self.sandbox,
                        cpu_seconds=self.cpu_seconds,
                        address_space_mib=self.address_space_mib,
                    )
                    assert isinstance(result, WorkerResult)
                    artifact_ids = self.upload_artifacts(task_id, epoch, result.artifacts, heartbeat)
                    if artifact_ids or result.checkpoint_summary is not None:
                        heartbeat()
                        self.api("POST", f"/tasks/{task_id}/checkpoints", {
                            "epoch": epoch,
                            "summary": result.checkpoint_summary or "Runner uploaded task artifacts for owner review",
                            "next_steps": result.checkpoint_next_steps,
                            "artifact_ids": artifact_ids,
                        })
                # The task remains submitted, not accepted, until a human reviews.
                self.api("POST", f"/tasks/{task_id}/submit", {"epoch": epoch, "summary": result.summary})
            except Exception as exc:
                # Don't leak stderr, environment or traceback into an Agent-visible
                # task summary. Worker still needs local operator diagnostics.
                description = str(exc) if isinstance(exc, RunnerFailure) else type(exc).__name__
                try:
                    self.api("POST", f"/tasks/{task_id}/fail", {"epoch": epoch, "summary": description[:20000]})
                except httpx.HTTPError:
                    pass  # lease may have been revoked; no unsafe overwrite
                print(f"Task failed: {task_id}: {description}", file=sys.stderr)
                try:
                    self.runtime_heartbeat("idle")
                except httpx.HTTPError:
                    pass
                return True
            print(f"Task submitted for review: {task_id}")
            self.runtime_heartbeat("idle")
            return True
        return False

    def close(self):
        self.client.close()


def main():
    parser = argparse.ArgumentParser(description="OtterNest generic remote task runner")
    parser.add_argument("--server", required=True)
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--runtime-id", default=os.environ.get("OTTERNEST_RUNTIME_ID", ""),
                        help="Pre-registered Agent runtime ID for periodic heartbeat")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--workspace-root", default=None,
                        help="Existing absolute private directory for opt-in artifact manifests")
    parser.add_argument("--sandbox", choices=("none", "bwrap"), default="none",
                        help="Opt-in Linux bubblewrap isolation for local code workers (fails closed)")
    parser.add_argument("--cpu-seconds", type=int, default=None,
                        help="Optional sandbox CPU-time RLIMIT (1..3600 seconds), requires prlimit")
    parser.add_argument("--address-space-mib", type=int, default=None,
                        help="Optional sandbox virtual-address-space limit (64..16384 MiB), not RSS")
    parser.add_argument("--allow-r3", action="store_true", help="Explicitly allow owner-authorized high-risk tasks")
    parser.add_argument("--pass-env", action="append", default=[], help="Explicitly expose one environment variable to the child")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- /path/to/your-agent-command [args]")
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if args.interval < 1:
        parser.error("--interval must be positive")
    try:
        runner = Runner(args.server, os.environ.get("OTTERNEST_AGENT_TOKEN", ""), command,
                        timeout=args.timeout, allow_r3=args.allow_r3, pass_env=tuple(args.pass_env),
                        runtime_id=args.runtime_id or None,
                        workspace_root=args.workspace_root, sandbox=args.sandbox,
                        cpu_seconds=args.cpu_seconds,
                        address_space_mib=args.address_space_mib)
        try:
            while True:
                worked = runner.run_one()
                if args.once:
                    break
                if not worked:
                    # Ensure liveness remains fresh even with long idle poll intervals.
                    remaining = args.interval
                    while remaining > 0:
                        delay = min(remaining, 60)
                        time.sleep(delay)
                        remaining -= delay
                        if remaining > 0:
                            runner.runtime_heartbeat("idle")
        finally:
            runner.close()
    except (RunnerFailure, httpx.HTTPError) as exc:
        print(f"OtterNest runner error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
