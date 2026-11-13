"""Generic execution worker: connects to the OtterNest task API, not any AI vendor.

The configured executable receives a JSON Task Context Packet on stdin. It may
print JSON {"summary": "..."} or plain text to stdout. The user is responsible
for running that executable under an appropriately isolated OS identity.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


class RunnerFailure(Exception):
    pass


def worker_environment(pass_env: tuple[str, ...] = ()) -> dict[str, str]:
    """No owner/Agent control-plane secrets are inherited by child by default."""
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT", "WINDIR"}
    allowed.update(pass_env)
    forbidden = {"OTTERNEST_OWNER_TOKEN", "OTTERNEST_AGENT_TOKEN", "OTTERNEST_DATABASE_URL"}
    if forbidden.intersection(pass_env):
        raise RunnerFailure("Refusing to expose OtterNest control-plane credentials to child")
    return {k: v for k, v in os.environ.items() if k in allowed and k not in forbidden}


def execute_command(command: list[str], packet: dict, timeout_seconds: int,
                    heartbeat, pass_env: tuple[str, ...] = ()) -> str:
    """Explicit executable, no shell; output bounded before returning to server."""
    if not command:
        raise RunnerFailure("No execution command configured")
    content = json.dumps(packet, ensure_ascii=False).encode("utf-8")
    if len(content) > 300_000:
        raise RunnerFailure("Context too large for execution")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        with subprocess.Popen(command, shell=False, stdin=subprocess.PIPE,
                              stdout=stdout, stderr=stderr, env=worker_environment(pass_env)) as proc:
            try:
                assert proc.stdin is not None
                proc.stdin.write(content)
                proc.stdin.close()
                started = time.monotonic()
                next_heartbeat = started + 30
                while proc.poll() is None:
                    if time.monotonic() - started >= timeout_seconds:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        raise RunnerFailure("Execution exceeded configured timeout")
                    if time.monotonic() >= next_heartbeat:
                        heartbeat()  # abort on revoked or expired lease
                        next_heartbeat = time.monotonic() + 30
                    time.sleep(0.1)
            except Exception:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                raise
            stdout.seek(0)
            data = stdout.read(20000)
            if proc.returncode:
                raise RunnerFailure(f"Configured executable exited with status {proc.returncode}")
            output = data.decode("utf-8", errors="replace").strip()
            if not output:
                raise RunnerFailure("Executable produced no result")
            try:
                parsed = json.loads(output)
                if isinstance(parsed, dict) and isinstance(parsed.get("summary"), str):
                    output = parsed["summary"]
            except (ValueError, TypeError):
                pass
            return output[:20000]


class Runner:
    def __init__(self, server: str, token: str, command: list[str], timeout: int = 600,
                 allow_r3: bool = False, pass_env: tuple[str, ...] = (),
                 runtime_id: str | None = None):
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
                summary = execute_command(self.command, packet, self.timeout, heartbeat, self.pass_env)
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
            self.api("POST", f"/tasks/{task_id}/submit", {"epoch": epoch, "summary": summary})
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
                        runtime_id=args.runtime_id or None)
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
