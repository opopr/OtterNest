"""Outbound A2A only sends after explicit owner consent, and never blindly resends."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from otternest.app import app
from otternest.db import get_session
from otternest.a2a_outbound import dispatch_once
from otternest.models import A2ADelegation, Task, now

HOST = "verified-agent.example.org"
URL = f"https://{HOST}/a2a"


def database_factory():
    # Uses the same database engine as TestClient's dependency override.
    gen = app.dependency_overrides[get_session]()
    try:
        session = next(gen)
        return sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    finally:
        gen.close()


@pytest.fixture(autouse=True)
def network(monkeypatch):
    monkeypatch.setenv("OTTERNEST_A2A_ALLOWED_HOSTS", HOST)
    monkeypatch.setattr("otternest.a2a_outbound.socket.getaddrinfo",
                        lambda *a, **kw: [(None, None, None, None, ("93.184.215.14", 443))])


def setup_remote(env, owner_headers, monkeypatch, *, capabilities=("research",), risk="R1"):
    project = env.post("/api/v1/projects", headers=owner_headers, json={"name": "Outbound"}).json()["id"]
    created = env.post("/api/v1/agents", headers=owner_headers, json={
        "name": "remote-researcher", "platform": "test", "execution_mode": "remote",
        "capabilities": list(capabilities),
    }).json()
    assert env.post(f"/api/v1/projects/{project}/members", headers=owner_headers,
                    json={"agent_id": created["id"], "role": "worker"}).status_code == 201
    assert env.put(f"/api/v1/agents/{created['id']}/verified-capabilities", headers=owner_headers,
                   json={"capabilities": list(capabilities)}).status_code == 200
    endpoint = env.post(f"/api/v1/projects/{project}/a2a/remotes", headers=owner_headers,
                        json={"url": URL, "agent_id": created["id"]})
    assert endpoint.status_code == 201, endpoint.text
    peer = endpoint.json()
    assert "token" not in json.dumps(peer).lower() or "secret_env" in peer
    monkeypatch.setenv(peer["secret_env"], "example-remote-bearer-token-12345")
    task = env.post(f"/api/v1/projects/{project}/tasks", headers=owner_headers,
                    json={"title": "Research", "instruction": "Write a sourced summary", "acceptance": "Sources",
                          "required_capabilities": list(capabilities), "risk": risk}).json()
    return project, created, peer, task


def delegate(env, owner_headers, task, endpoint):
    return env.post(f"/api/v1/tasks/{task['id']}/a2a/delegate", headers=owner_headers,
                    json={"endpoint_id": endpoint['id']})


def mock_client(handler):
    def serve(request):
        payload = json.loads(request.content)
        assert request.url == URL
        assert request.headers.get("authorization") == "Bearer example-remote-bearer-token-12345"
        return handler(payload)
    return httpx.Client(transport=httpx.MockTransport(serve), trust_env=False)


def a2a_result(request, *, state="working", artifacts=None, remote_id="remote-47"):
    task = {"kind": "task", "id": remote_id, "status": {"state": state}}
    if artifacts is not None:
        task["artifacts"] = artifacts
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": request["id"], "result": task})


def test_outbound_dispatch_remote_complete_requires_owner_review(env, owner_headers, monkeypatch):
    project, remote, peer, task = setup_remote(env, owner_headers, monkeypatch)
    queued = delegate(env, owner_headers, task, peer)
    assert queued.status_code == 201, queued.text
    delegation = queued.json()
    assert delegation["status"] == "pending"
    # Cannot be claimed locally even using the registered peer's agent token.
    assert env.post(f"/api/v1/tasks/{task['id']}/claim", headers={"Authorization": f"Bearer {remote['token']}"},
                    json={}).status_code == 409
    calls = []
    def handler(req):
        calls.append(req)
        if req["method"] == "message/send":
            assert isinstance(req["params"]["message"]["messageId"], str)
            assert len(req["params"]["message"]["messageId"]) >= 24
            assert req["params"]["message"]["parts"][0]["text"] == "Write a sourced summary"
            return a2a_result(req)
        assert req["method"] == "tasks/get" and req["params"] == {"id": "remote-47"}
        return a2a_result(req, state="completed", artifacts=[{
            "artifactId": "a", "parts": [{"kind": "text", "text": "Verified research report"}],
        }])
    with mock_client(handler) as client:
        first = dispatch_once(database_factory(), client=client)
        assert first["sent"] == 1
        live = env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()
        assert live["status"] == "running" and live["assigned_agent_id"] == remote["id"]
        with database_factory()() as db:
            row = db.get(A2ADelegation, delegation["id"])
            row.next_poll_at = now() - timedelta(seconds=1)
            db.commit()
        second = dispatch_once(database_factory(), client=client)
        assert second["polled"] == 1
    assert len(calls) == 2
    pending = env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()
    assert pending["status"] == "submitted" and pending["result_summary"] == "Verified research report"
    reviewed = env.post(f"/api/v1/tasks/{task['id']}/review", headers=owner_headers,
                        json={"accepted": True})
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "completed"
    assert env.get(f"/api/v1/a2a/delegations/{delegation['id']}", headers=owner_headers).json()["status"] == "submitted"


def test_outbound_unknown_send_is_not_retried(env, owner_headers, monkeypatch):
    _, remote, peer, task = setup_remote(env, owner_headers, monkeypatch)
    delegation = delegate(env, owner_headers, task, peer).json()
    attempts = []
    def lose_response(req):
        attempts.append(req)
        raise httpx.ReadTimeout("request was transmitted but reply was lost")
    with mock_client(lose_response) as client:
        result = dispatch_once(database_factory(), client=client)
        assert result["unknown"] == 1
        assert dispatch_once(database_factory(), client=client)["sent"] == 0
    assert len(attempts) == 1
    detail = env.get(f"/api/v1/a2a/delegations/{delegation['id']}", headers=owner_headers).json()
    assert detail["status"] == "unknown" and detail["remote_task_id"] is None
    assert env.post(f"/api/v1/tasks/{task['id']}/claim", headers={"Authorization": f"Bearer {remote['token']}"},
                    json={}).status_code == 409
    assert env.post(f"/api/v1/a2a/delegations/{delegation['id']}/cancel", headers=owner_headers).status_code == 409
    recovered = env.post(f"/api/v1/a2a/delegations/{delegation['id']}/reconcile", headers=owner_headers,
                         json={"remote_task_id": "verified-remote-id"})
    assert recovered.status_code == 200 and recovered.json()["status"] == "remote"


def test_remote_safe_cancel_requires_actual_remote_confirmation(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    delegation = delegate(env, owner_headers, task, peer).json()
    methods = []
    def handler(req):
        methods.append(req["method"])
        return a2a_result(req, state="canceled" if req["method"] == "tasks/cancel" else "working")
    with mock_client(handler) as client:
        dispatch_once(database_factory(), client=client)
        cancel = env.post(f"/api/v1/a2a/delegations/{delegation['id']}/cancel", headers=owner_headers)
        assert cancel.status_code == 200 and cancel.json()["status"] == "cancel_requested"
        assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "running"
        assert env.post(f"/api/v1/tasks/{task['id']}/cancel", headers=owner_headers).status_code == 409
        result = dispatch_once(database_factory(), client=client)
        assert result["cancelled"] == 1
    assert methods == ["message/send", "tasks/cancel"]
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "cancelled"


def test_peer_approval_security_and_privacy(env, owner_headers, monkeypatch):
    p, remote, peer, task = setup_remote(env, owner_headers, monkeypatch)
    ah = {"Authorization": f"Bearer {remote['token']}"}
    assert env.post(f"/api/v1/projects/{p}/a2a/remotes", headers=ah,
                    json={"url": URL, "agent_id": remote['id']}).status_code == 403
    assert env.post(f"/api/v1/tasks/{task['id']}/a2a/delegate", headers=ah,
                    json={"endpoint_id": peer['id']}).status_code == 403
    assert env.get(f"/api/v1/a2a/delegations/random", headers=ah).status_code == 403
    assert env.post(f"/api/v1/projects/{p}/a2a/remotes", headers=owner_headers,
                    json={"url": "http://127.0.0.1:8000/a2a", "agent_id": remote['id']}).status_code == 422
    assert env.post(f"/api/v1/projects/{p}/a2a/remotes", headers=owner_headers,
                    json={"url": "https://localhost/a2a", "agent_id": remote['id']}).status_code == 422
    assert env.post(f"/api/v1/projects/{p}/a2a/remotes", headers=owner_headers,
                    json={"url": "https://evil.example.org/a2a", "agent_id": remote['id']}).status_code == 422
    assert env.get(f"/api/v1/projects/{p}/a2a/remotes", headers=owner_headers).json()[0]["url"] == URL
    assert "example-remote-bearer-token" not in json.dumps(env.get(f"/api/v1/projects/{p}/a2a/remotes", headers=owner_headers).json())
    high = env.post(f"/api/v1/projects/{p}/tasks", headers=owner_headers,
                    json={"title": "High", "instruction": "Send payment", "acceptance": "done", "risk": "R3"}).json()
    assert delegate(env, owner_headers, high, peer).status_code == 409


def test_remote_failure_is_blocked_not_automatically_retried(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    delegation = delegate(env, owner_headers, task, peer).json()
    with mock_client(lambda req: a2a_result(req, state="failed")) as client:
        dispatch_once(database_factory(), client=client)
        assert dispatch_once(database_factory(), client=client)["sent"] == 0
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "blocked"
    assert env.get(f"/api/v1/a2a/delegations/{delegation['id']}", headers=owner_headers).json()["status"] == "failed"
    assert env.post(f"/api/v1/tasks/{task['id']}/unblock", headers=owner_headers).status_code == 409


def test_interrupted_sending_becomes_unknown_without_retry(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    d = delegate(env, owner_headers, task, peer).json()
    with database_factory()() as db:
        item = db.get(A2ADelegation, d["id"])
        item.status = "sending"
        item.updated_at = now() - timedelta(minutes=5)
        db.commit()
    with mock_client(lambda _: pytest.fail("No automatic resend allowed")) as client:
        dispatch_once(database_factory(), client=client)
    assert env.get(f"/api/v1/a2a/delegations/{d['id']}", headers=owner_headers).json()["status"] == "unknown"


def test_review_rejected_requires_explicit_release_before_rework(env, owner_headers, monkeypatch):
    _, remote, peer, task = setup_remote(env, owner_headers, monkeypatch)
    queued = delegate(env, owner_headers, task, peer).json()
    with mock_client(lambda req: a2a_result(req, state="completed", artifacts=[{
        "artifactId": "draft", "parts": [{"kind": "text", "text": "Needs corrections"}],
    }])) as client:
        assert dispatch_once(database_factory(), client=client)["sent"] == 1
    rejected = env.post(f"/api/v1/tasks/{task['id']}/review", headers=owner_headers,
                        json={"accepted": False, "reason": "Insufficient evidence"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "blocked"
    assert env.post(f"/api/v1/tasks/{task['id']}/unblock", headers=owner_headers).status_code == 409
    released = env.post(f"/api/v1/a2a/delegations/{queued['id']}/release", headers=owner_headers)
    assert released.status_code == 200 and released.json()["status"] == "released"
    assert env.post(f"/api/v1/tasks/{task['id']}/unblock", headers=owner_headers).status_code == 200
    restarted = env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()
    assert restarted["status"] == "ready" and restarted["target_agent_id"] is None
    # An owner must explicitly choose an alternate worker. No silent remote replay.
    assert env.get(f"/api/v1/a2a/delegations/{queued['id']}", headers=owner_headers).json()["status"] == "released"
    assert delegate(env, owner_headers, task, peer).status_code == 409


def test_agent_revocation_prevents_unsent_delegation(env, owner_headers, monkeypatch):
    _, remote, peer, task = setup_remote(env, owner_headers, monkeypatch)
    d = delegate(env, owner_headers, task, peer).json()
    assert env.post(f"/api/v1/agents/{remote['id']}/disable", headers=owner_headers).status_code == 200
    with mock_client(lambda _: pytest.fail("Revoked Agent must not receive task")) as client:
        assert dispatch_once(database_factory(), client=client)["sent"] == 0
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "blocked"
    assert env.get(f"/api/v1/a2a/delegations/{d['id']}", headers=owner_headers).json()["status"] == "failed"
    assert env.post(f"/api/v1/a2a/delegations/{d['id']}/release", headers=owner_headers).status_code == 200


def test_untrusted_remote_result_keeps_task_unaccepted(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    d = delegate(env, owner_headers, task, peer).json()
    with mock_client(lambda req: a2a_result(req, remote_id="remote-uncertain", state="working") if req["method"] == "message/send" else
                     a2a_result(req, remote_id="different-task", state="completed")) as client:
        dispatch_once(database_factory(), client=client)
        with database_factory()() as db:
            record = db.get(A2ADelegation, d["id"])
            record.next_poll_at = now() - timedelta(seconds=1)
            db.commit()
        dispatch_once(database_factory(), client=client)
    assert env.get(f"/api/v1/a2a/delegations/{d['id']}", headers=owner_headers).json()["status"] == "unknown"
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "running"
    assert env.post(f"/api/v1/a2a/delegations/{d['id']}/release", headers=owner_headers).status_code == 409


def test_read_only_poll_with_no_transition_does_not_flood_audit(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    d = delegate(env, owner_headers, task, peer).json()
    with mock_client(lambda req: a2a_result(req, state="working")) as client:
        dispatch_once(database_factory(), client=client)
        before = env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["version"]
        with database_factory()() as db:
            item = db.get(A2ADelegation, d["id"])
            item.next_poll_at = now() - timedelta(seconds=1)
            db.commit()
        dispatch_once(database_factory(), client=client)
    after = env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["version"]
    assert before == after


def test_unresolvable_peer_is_blocked_without_network_send(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    d = delegate(env, owner_headers, task, peer).json()
    monkeypatch.setattr("otternest.a2a_outbound.socket.getaddrinfo", lambda *a, **kw: [])
    with mock_client(lambda _: pytest.fail("No HTTP request is allowed")) as client:
        dispatch_once(database_factory(), client=client)
    assert env.get(f"/api/v1/a2a/delegations/{d['id']}", headers=owner_headers).json()["status"] == "failed"
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "blocked"


def test_cross_project_peer_cannot_be_attached(env, owner_headers, monkeypatch):
    _, _, peer, _ = setup_remote(env, owner_headers, monkeypatch)
    project = env.post("/api/v1/projects", headers=owner_headers, json={"name": "Isolated"}).json()["id"]
    target = env.post(f"/api/v1/projects/{project}/tasks", headers=owner_headers,
                      json={"title": "Private task", "instruction": "Private work", "acceptance": "Owner"}).json()
    response = env.post(f"/api/v1/tasks/{target['id']}/a2a/delegate", headers=owner_headers,
                        json={"endpoint_id": peer["id"]})
    assert response.status_code == 404
    assert env.get(f"/api/v1/tasks/{target['id']}", headers=owner_headers).json()["status"] == "ready"


def test_pending_delegation_cancelled_before_network(env, owner_headers, monkeypatch):
    _, _, peer, task = setup_remote(env, owner_headers, monkeypatch)
    d = delegate(env, owner_headers, task, peer).json()
    cancelled = env.post(f"/api/v1/a2a/delegations/{d['id']}/cancel", headers=owner_headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "canceled"
    with mock_client(lambda _: pytest.fail("Canceled task must not be sent")) as client:
        dispatch_once(database_factory(), client=client)
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()["status"] == "cancelled"
