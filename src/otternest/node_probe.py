"""Read-only remote node heartbeat probe; never claims or executes tasks."""
from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urlsplit

import httpx

from .host_metrics import collect_host_health


def validate_endpoint(url: str, runtime_id: str, token: str, interval: int) -> None:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Server URL must not contain credentials, query, or fragment")
    if not (parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    )):
        raise ValueError("HTTPS required except localhost development")
    if not parsed.hostname:
        raise ValueError("Server hostname required")
    if not runtime_id.strip():
        raise ValueError("OTTERNEST_RUNTIME_ID required")
    if not token.startswith("on_runtime_"):
        raise ValueError("Dedicated OTTERNEST_RUNTIME_TOKEN required")
    if not 10 <= interval <= 120:
        raise ValueError("Interval must be 10..120 seconds")


def send_probe(client: httpx.Client, runtime_id: str) -> None:
    """Self-report Runtime liveness. Cannot prove physical host attestation."""
    response = client.post(
        "/api/v1/agent/runtimes/" + runtime_id + "/heartbeat",
        json={"status": "monitoring", "health": collect_host_health()},
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest telemetry-only remote node probe")
    parser.add_argument("--server", default=os.environ.get("OTTERNEST_SERVER_URL", ""))
    parser.add_argument("--runtime-id", default=os.environ.get("OTTERNEST_RUNTIME_ID", ""))
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("OTTERNEST_RUNTIME_TOKEN", "")
    try:
        validate_endpoint(args.server, args.runtime_id, token, args.interval)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        with httpx.Client(
            base_url=args.server.rstrip("/"),
            headers={"Authorization": "Bearer " + token},
            timeout=10,
            follow_redirects=False,
        ) as client:
            while True:
                send_probe(client, args.runtime_id)
                if args.once:
                    return
                time.sleep(args.interval)
    except httpx.HTTPError as exc:
        # Avoid embedding response URLs, body or credentials in logs.
        print("OtterNest node probe transport/API error: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
