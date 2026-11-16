"""OtterNest Muse Cloud machine / agent execution adapter.

Bridges OtterNest Runner with a Muse Cloud computer or agent instance.
Dispatches authorized tasks to Muse, monitors execution progress,
verifies artifact outputs, and formats an owner-reviewable result manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_INPUT_BYTES = 300_000
MAX_ARTIFACT_DOWNLOAD_BYTES = 10 * 1024 * 1024


class MuseAdapterError(RuntimeError):
    """Failure message safe for audit logs and task summaries."""


def validated_muse_endpoint(endpoint: str, *, allow_local_http: bool = False) -> str:
    """Ensure Muse endpoint is a valid, secure URL without embedded credentials."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise MuseAdapterError("Invalid Muse endpoint URL") from None

    localhost = host in {"127.0.0.1", "localhost", "::1"}
    if not host or parsed.username or parsed.password or parsed.fragment:
        raise MuseAdapterError("Muse endpoint must not contain embedded userinfo or fragments")
    if parsed.scheme != "https" and not (allow_local_http and localhost and parsed.scheme == "http"):
        raise MuseAdapterError("HTTPS Muse endpoint required (local HTTP needs explicit opt-in)")
    if port == 0:
        raise MuseAdapterError("Invalid Muse endpoint port")
    return endpoint.rstrip("/")


def dispatch_to_muse(
    packet: dict,
    *,
    endpoint: str,
    api_key: str,
    workspace_dir: str | None = None,
    timeout_seconds: float = 300,
    poll_interval: float = 2.0,
    allow_local_http: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Dispatch task to Muse Cloud machine, await completion, and format worker manifest."""
    base_url = validated_muse_endpoint(endpoint, allow_local_http=allow_local_http)
    if not api_key or not api_key.strip():
        raise MuseAdapterError("Muse API key is required")
    if not 10 <= timeout_seconds <= 3600:
        raise MuseAdapterError("Timeout must be between 10 and 3600 seconds")

    task = packet.get("task")
    if not isinstance(task, dict) or not task.get("id"):
        raise MuseAdapterError("Invalid task context packet: missing task.id")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OtterNest-Task-Id": str(task["id"]),
    }

    with httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=15.0,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        # 1. Health & readiness probe
        try:
            health = client.get("/v1/health")
            if not health.is_success:
                raise MuseAdapterError(f"Muse machine health check failed with status {health.status_code}")
        except MuseAdapterError:
            raise
        except Exception as exc:
            raise MuseAdapterError(f"Failed to connect to Muse endpoint: {exc}") from None

        # 2. Dispatch task run
        payload = {
            "task_id": task["id"],
            "title": task.get("title", ""),
            "instruction": task.get("instruction", ""),
            "acceptance": task.get("acceptance", ""),
            "risk": task.get("risk", "R1"),
            "context": packet,
        }
        try:
            resp = client.post("/v1/tasks/run", json=payload)
            if not resp.is_success:
                raise MuseAdapterError(f"Muse task dispatch rejected: HTTP {resp.status_code}")
            dispatch_info = resp.json()
        except MuseAdapterError:
            raise
        except Exception as exc:
            raise MuseAdapterError(f"Muse task submission failed: {exc}") from None

        muse_run_id = dispatch_info.get("run_id") or task["id"]

        # 3. Poll for completion
        deadline = time.monotonic() + timeout_seconds
        last_status = "pending"
        result_data: dict[str, Any] = {}

        while time.monotonic() < deadline:
            try:
                poll_resp = client.get(f"/v1/tasks/runs/{muse_run_id}")
                if poll_resp.is_success:
                    result_data = poll_resp.json()
                    last_status = result_data.get("status", "running")
                    if last_status in {"completed", "failed", "cancelled"}:
                        break
            except Exception:
                pass
            time.sleep(poll_interval)

        if last_status != "completed":
            raise MuseAdapterError(f"Muse task did not complete successfully (status: {last_status})")

        summary = result_data.get("summary", "").strip()
        if not summary:
            summary = f"Muse Cloud completed task execution for {task.get('title')}."

        artifacts_meta = result_data.get("artifacts", [])
        manifest_artifacts = []

        # 4. If artifacts declared and workspace provided, download and verify checksums
        if workspace_dir and artifacts_meta:
            os.makedirs(workspace_dir, exist_ok=True)
            for art in artifacts_meta:
                name = art.get("name")
                download_url = art.get("download_path")
                expected_sha = art.get("sha256")
                if not name or not download_url:
                    continue
                # Sanitize filename
                safe_name = os.path.basename(name)
                dest_path = os.path.join(workspace_dir, safe_name)

                art_resp = client.get(download_url)
                if not art_resp.is_success:
                    raise MuseAdapterError(f"Failed to download Muse artifact: {safe_name}")
                content = art_resp.content
                if len(content) > MAX_ARTIFACT_DOWNLOAD_BYTES:
                    raise MuseAdapterError(f"Muse artifact {safe_name} exceeds download size limit")

                if expected_sha:
                    actual_sha = hashlib.sha256(content).hexdigest()
                    if actual_sha != expected_sha:
                        raise MuseAdapterError(f"Checksum mismatch for artifact {safe_name}")

                with open(dest_path, "wb") as f:
                    f.write(content)

                manifest_artifacts.append({"path": safe_name})

        manifest: dict[str, Any] = {
            "summary": summary,
        }
        if manifest_artifacts:
            manifest["artifacts"] = manifest_artifacts
            manifest["checkpoint"] = {
                "summary": f"Muse Cloud completed task with {len(manifest_artifacts)} artifact(s)",
                "next_steps": "Inspect artifacts and verify output against acceptance criteria",
            }

        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest Muse Cloud machine task worker")
    parser.add_argument("--endpoint", required=True, help="Muse Cloud machine HTTPS API endpoint")
    parser.add_argument("--api-key-env", default="OTTERNEST_MUSE_API_KEY",
                        help="Environment variable containing Muse machine API key")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--allow-local-http", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        print(f"OtterNest Muse worker: {args.api_key_env} is not set", file=sys.stderr)
        sys.exit(1)

    raw_input = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw_input) > MAX_INPUT_BYTES:
        print("OtterNest Muse worker: context exceeds input limit", file=sys.stderr)
        sys.exit(1)
    try:
        packet = json.loads(raw_input.decode("utf-8"))
    except Exception:
        print("OtterNest Muse worker: invalid JSON input", file=sys.stderr)
        sys.exit(1)

    try:
        manifest = dispatch_to_muse(
            packet,
            endpoint=args.endpoint,
            api_key=api_key,
            workspace_dir=os.getcwd(),
            timeout_seconds=args.timeout,
            allow_local_http=args.allow_local_http,
        )
    except MuseAdapterError as exc:
        print(f"OtterNest Muse worker error: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(json.dumps(manifest, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
