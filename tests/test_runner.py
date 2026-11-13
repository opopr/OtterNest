"""The runner must not expose central credentials or use shell interpolation."""
import os
import sys

import pytest

from otternest.runner import Runner, RunnerFailure, execute_command, worker_environment


def test_runner_credentials_not_inherited(monkeypatch):
    monkeypatch.setenv("OTTERNEST_AGENT_TOKEN", "on_agent_secret")
    monkeypatch.setenv("OTTERNEST_OWNER_TOKEN", "top-secret-owner")
    monkeypatch.setenv("OTTERNEST_DATABASE_URL", "postgresql://secret")
    env = worker_environment()
    assert "OTTERNEST_AGENT_TOKEN" not in env
    assert "OTTERNEST_OWNER_TOKEN" not in env
    assert "OTTERNEST_DATABASE_URL" not in env
    with pytest.raises(RunnerFailure):
        worker_environment(("OTTERNEST_AGENT_TOKEN",))


def test_local_command_receives_task_context_but_no_agent_token(monkeypatch):
    monkeypatch.setenv("OTTERNEST_AGENT_TOKEN", "on_agent_secret")
    script = "import os,sys,json; data=json.loads(sys.stdin.read()); print(json.dumps({'summary': data['task']['title'] + ':' + str(os.getenv('OTTERNEST_AGENT_TOKEN'))}))"
    summary = execute_command([sys.executable, "-c", script], {"task": {"title": "Hello"}}, 30, lambda: None)
    assert summary == "Hello:None"


def test_runner_rejects_unsafe_server_protocol():
    with pytest.raises(RunnerFailure):
        Runner("http://example.com", "on_agent_secret", ["echo", "hello"])


def test_runner_end_to_end_with_generic_command(monkeypatch):
    import httpx

    events = []

    def handler(request):
        path = request.url.path
        events.append(path)
        if path == "/api/v1/agent/queue":
            return httpx.Response(200, json=[{"id": "t1", "risk": "R1"}])
        if path == "/api/v1/tasks/t1/claim":
            return httpx.Response(200, json={"lease_epoch": 1})
        if path == "/api/v1/tasks/t1/context":
            return httpx.Response(200, json={"task": {"title": "Simple"}, "memories": []})
        if path == "/api/v1/tasks/t1/submit":
            body = __import__("json").loads(request.content)
            assert body["summary"] == "Simple passed"
            return httpx.Response(200, json={"status": "submitted"})
        raise AssertionError("Unexpected request " + path)

    runner = Runner("http://127.0.0.1:8000", "on_agent_secret", [sys.executable, "-c", "import sys,json; print(json.loads(sys.stdin.read())['task']['title'] + ' passed')"])
    runner.client.close()
    runner.client = httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler))
    try:
        assert runner.run_one() is True
    finally:
        runner.close()
    assert events == ["/api/v1/agent/queue", "/api/v1/tasks/t1/claim", "/api/v1/tasks/t1/context", "/api/v1/tasks/t1/submit"]
