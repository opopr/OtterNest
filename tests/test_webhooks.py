"""Webhook security policy and at-least-once event delivery tests."""
import hashlib
import hmac
import ipaddress
import json
import socket
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from otternest.db import Base
from otternest import models  # noqa: F401
from otternest.models import Event, Outbox, Project, WebhookDelivery, WebhookEndpoint, now
from otternest.webhooks import allowed_target, dispatch_once, endpoint_secret, signature


def test_owner_configures_webhook_and_agent_cannot(env, owner_headers, monkeypatch):
    monkeypatch.setenv("OTTERNEST_WEBHOOK_ALLOWED_HOSTS", "events.example.net")
    monkeypatch.setenv("OTTERNEST_WEBHOOK_SIGNING_KEY", "long-random-webhook-key-at-least-32-chars")
    p = env.post("/api/v1/projects", headers=owner_headers, json={"name": "Alpha"}).json()["id"]
    bad = env.post(f"/api/v1/projects/{p}/webhooks", headers=owner_headers,
                   json={"url": "http://127.0.0.1:8000/evil", "event_prefix": "task."})
    assert bad.status_code == 422
    registered = env.post(f"/api/v1/projects/{p}/webhooks", headers=owner_headers,
                          json={"url": "https://events.example.net/hook", "event_prefix": "task."})
    assert registered.status_code == 201, registered.text
    assert len(registered.json()["signing_secret"]) == 64
    listed = env.get(f"/api/v1/projects/{p}/webhooks", headers=owner_headers)
    assert len(listed.json()) == 1
    assert "signing_secret" not in listed.json()[0]
    assert env.post(f"/api/v1/webhooks/{registered.json()['id']}/disable", headers=owner_headers).status_code == 200


def test_public_destination_policy(monkeypatch):
    monkeypatch.setenv("OTTERNEST_WEBHOOK_ALLOWED_HOSTS", "valid.example.net,127.0.0.1")
    assert allowed_target("https://valid.example.net/webhook") == "valid.example.net"
    with pytest.raises(HTTPException):
        allowed_target("https://unknown.example.net/")
    with pytest.raises(HTTPException):
        allowed_target("https://valid.example.net:8443/")
    with pytest.raises(HTTPException):
        allowed_target("https://user:password@valid.example.net/")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(HTTPException):
        allowed_target("https://valid.example.net/webhook", check_dns=True)


def test_signed_webhook_dispatch_dedupe_and_retry(monkeypatch):
    monkeypatch.setenv("OTTERNEST_WEBHOOK_ALLOWED_HOSTS", "events.example.net")
    monkeypatch.setenv("OTTERNEST_WEBHOOK_SIGNING_KEY", "long-random-webhook-key-at-least-32-chars")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, None, None, None, ("1.1.1.1", 443))])
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        p = Project(name="Test")
        db.add(p)
        db.flush()
        endpoint = WebhookEndpoint(project_id=p.id, url="https://events.example.net/ingest", event_prefix="task.")
        db.add(endpoint)
        db.flush()
        event = Event(project_id=p.id, kind="task.submitted", subject_id="TASK-1", payload={"result": "ready"},
                      created_at=now() + timedelta(seconds=2))
        db.add(event)
        db.flush()
        db.add(Outbox(event_id=event.id))
        db.commit()
        endpoint_id, event_id = endpoint.id, event.id
    responses = [503, 204]
    delivered = []

    def receive(req):
        delivered.append(req)
        assert req.headers["X-OtterNest-Event-ID"] == str(event_id)
        timestamp = req.headers["X-OtterNest-Timestamp"]
        expected = signature(endpoint_secret(endpoint_id), timestamp, req.content)
        assert hmac.compare_digest(req.headers["X-OtterNest-Signature"], expected)
        assert json.loads(req.content)["kind"] == "task.submitted"
        return httpx.Response(responses.pop(0))

    with httpx.Client(transport=httpx.MockTransport(receive), follow_redirects=False) as client:
        first = dispatch_once(factory, client=client)
        assert first == {"prepared": 1, "delivered": 0, "failed": 1}
        with factory() as db:
            delivery = db.scalar(select(WebhookDelivery))
            assert delivery.status == "pending" and delivery.attempts == 1
            delivery.retry_at = now() - timedelta(seconds=1)
            db.commit()
        second = dispatch_once(factory, client=client)
        assert second == {"prepared": 0, "delivered": 1, "failed": 0}
        third = dispatch_once(factory, client=client)
        assert third == {"prepared": 0, "delivered": 0, "failed": 0}
    assert len(delivered) == 2
    engine.dispose()
