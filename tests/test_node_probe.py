"""Probe authorization, endpoint policy and heartbeat transport tests."""
import json

import httpx
import pytest

from otternest import node_probe


def test_probe_enforces_dedicated_runtime_credential_and_tls():
    valid = ("https://control.example.com", "runtime-1", "on_monitor_secret", 30)
    node_probe.validate_endpoint(*valid)
    for values in [
        ("http://control.example.com", *valid[1:]),
        ("https://user:pass@control.example.com", *valid[1:]),
        ("https://control.example.com/?token=x", *valid[1:]),
        ("https://control.example.com", "", *valid[2:]),
        ("https://control.example.com", "runtime-1", "on_agent_token", 30),
        ("https://control.example.com", "runtime-1", "on_monitor_secret", 0),
    ]:
        with pytest.raises(ValueError):
            node_probe.validate_endpoint(*values)
    node_probe.validate_endpoint("http://127.0.0.1:8000", *valid[1:])


def test_probe_posts_only_runtime_heartbeat_with_measured_fields(monkeypatch):
    seen = []
    monkeypatch.setattr(node_probe, "collect_host_health", lambda: {
        "cpu_percent": "12.5", "telemetry_scope": "collector_os_view"
    })
    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "idle"})
    with httpx.Client(base_url="https://control.example.com", transport=httpx.MockTransport(handler)) as client:
        node_probe.send_probe(client, "runtime-1")
    assert seen == [(
        "POST", "/api/v1/agent/runtimes/runtime-1/heartbeat",
        {"status": "monitoring", "health": {"cpu_percent": "12.5", "telemetry_scope": "collector_os_view"}}
    )]


def test_probe_does_not_ignore_failed_requests(monkeypatch):
    monkeypatch.setattr(node_probe, "collect_host_health", lambda: {})
    with httpx.Client(base_url="https://control.example.com", transport=httpx.MockTransport(
        lambda request: httpx.Response(403, json={"detail": "forbidden"})
    )) as client:
        with pytest.raises(httpx.HTTPStatusError):
            node_probe.send_probe(client, "runtime-1")
