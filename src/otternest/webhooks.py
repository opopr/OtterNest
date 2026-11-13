"""Owner-approved, signed, persistent at-least-once outbound webhook delivery.

Webhook hosts must be in OTTERNEST_WEBHOOK_ALLOWED_HOSTS, even if an owner
registers them. Workers must not follow redirects, and nonpublic destination
addresses are rejected before each delivery.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
from datetime import timedelta, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .db import get_engine, get_session
from .models import Event, Outbox, WebhookDelivery, WebhookEndpoint, now
from .security import Actor, authenticate, owner_only
from .services import emit, require_project

router = APIRouter(prefix="/api/v1")


class WebhookIn(BaseModel):
    url: str = Field(min_length=12, max_length=2048)
    event_prefix: str = Field(default="task.", min_length=1, max_length=100)


def get_signing_key() -> bytes:
    key = os.environ.get("OTTERNEST_WEBHOOK_SIGNING_KEY", "")
    if len(key) < 32 or key.startswith("REPLACE_"):
        raise HTTPException(503, "OTTERNEST_WEBHOOK_SIGNING_KEY is not configured")
    return key.encode("utf-8")


def endpoint_secret(endpoint_id: str) -> str:
    return hmac.new(get_signing_key(), f"otternest-webhook:{endpoint_id}".encode(), hashlib.sha256).hexdigest()


def allowed_target(url: str, *, check_dns: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise HTTPException(422, "Only approved HTTPS webhook destinations are allowed")
    if parts.port not in (None, 443):
        raise HTTPException(422, "Webhook destinations must use HTTPS port 443")
    host = parts.hostname.lower().rstrip(".")
    allowed = {h.strip().lower().rstrip(".") for h in os.environ.get("OTTERNEST_WEBHOOK_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host not in allowed:
        raise HTTPException(422, "Webhook host is not in OTTERNEST_WEBHOOK_ALLOWED_HOSTS")
    if check_dns:
        try:
            addresses = {result[4][0] for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror:
            raise HTTPException(503, "Could not resolve webhook host") from None
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise HTTPException(422, "Webhook resolved to a non-public destination")
    return host


@router.post("/projects/{project_id}/webhooks", status_code=201)
def register_webhook(project_id: str, body: WebhookIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    allowed_target(body.url)
    get_signing_key()
    endpoint = WebhookEndpoint(project_id=project_id, url=body.url, event_prefix=body.event_prefix)
    db.add(endpoint)
    db.flush()
    emit(db, actor, "webhook.registered", endpoint.id, project_id)
    db.commit()
    return {"id": endpoint.id, "url": endpoint.url, "event_prefix": endpoint.event_prefix,
            "signing_secret": endpoint_secret(endpoint.id)}  # show once; do not log


@router.get("/projects/{project_id}/webhooks")
def list_webhooks(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    return [{"id": e.id, "url": e.url, "event_prefix": e.event_prefix, "enabled": e.enabled}
            for e in db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.project_id == project_id))]


@router.post("/webhooks/{webhook_id}/disable")
def disable_webhook(webhook_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    endpoint = db.get(WebhookEndpoint, webhook_id)
    if not endpoint:
        raise HTTPException(404, "Webhook not found")
    endpoint.enabled = False
    emit(db, actor, "webhook.disabled", endpoint.id, endpoint.project_id)
    db.commit()
    return {"id": endpoint.id, "enabled": False}


def signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return "sha256=" + mac


def prep_outbox(db: Session, batch: int = 100) -> int:
    """Atomically convert pending events into per-subscription delivery jobs."""
    rows = db.execute(
        select(Outbox, Event).join(Event, Outbox.event_id == Event.id)
        .where(Outbox.status == "pending").order_by(Event.id).limit(batch).with_for_update(skip_locked=True)
    ).all()
    for item, event in rows:
        if event.project_id:
            endpoints = db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.project_id == event.project_id,
                                                                  WebhookEndpoint.enabled.is_(True),
                                                                  WebhookEndpoint.created_at <= event.created_at)).all()
            for endpoint in endpoints:
                if endpoint.event_prefix == "*" or event.kind.startswith(endpoint.event_prefix):
                    db.add(WebhookDelivery(event_id=event.id, endpoint_id=endpoint.id))
        item.status = "prepared"
    db.commit()
    return len(rows)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def dispatch_once(session_factory: sessionmaker, *, client: httpx.Client | None = None, batch: int = 25) -> dict:
    """Perform one bounded dispatch iteration; run by a separate authorized worker.

    Delivery is at least once. A crash after remote acceptance but before the
    database acknowledgement can cause a repeat: receivers must dedupe event ID.
    """
    if batch < 1 or batch > 100:
        raise ValueError("batch must be 1..100")
    with session_factory() as db:
        prepared = prep_outbox(db, batch)
        stale = now() - timedelta(minutes=2)
        rows = db.scalars(select(WebhookDelivery).where(or_(
            (WebhookDelivery.status == "pending") & (WebhookDelivery.retry_at <= now()),
            (WebhookDelivery.status == "sending") & (WebhookDelivery.last_attempt_at <= stale)
        )).order_by(WebhookDelivery.retry_at, WebhookDelivery.id).limit(batch).with_for_update(skip_locked=True)).all()
        jobs = []
        for delivery in rows:
            endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
            event = db.get(Event, delivery.event_id)
            if not endpoint or not endpoint.enabled or not event or event.project_id != endpoint.project_id:
                delivery.status = "dead"
                delivery.last_error = "Destination no longer authorized"
                continue
            delivery.status = "sending"
            delivery.attempts += 1
            delivery.last_attempt_at = now()
            jobs.append((delivery.id, endpoint.id, endpoint.url, event.id, event.project_id, event.kind, event.subject_id, event.payload, delivery.attempts))
        db.commit()

    owned_client = client is None
    client = client or httpx.Client(follow_redirects=False, timeout=8)
    delivered = 0
    failed = 0
    try:
        for delivery_id, endpoint_id, url, event_id, project_id, kind, subject_id, payload, attempt in jobs:
            error = ""
            success = False
            try:
                allowed_target(url, check_dns=True)
                body = json.dumps({"id": event_id, "project_id": project_id, "kind": kind,
                                   "subject_id": subject_id, "payload": payload}, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
                timestamp = str(int(now().timestamp()))
                headers = {"Content-Type": "application/json",
                           "X-OtterNest-Event-ID": str(event_id),
                           "X-OtterNest-Timestamp": timestamp,
                           "X-OtterNest-Signature": signature(endpoint_secret(endpoint_id), timestamp, body)}
                with client.stream("POST", url, content=body, headers=headers, follow_redirects=False) as response:
                    success = 200 <= response.status_code < 300
                    if not success:
                        error = f"HTTP {response.status_code}"
            except (HTTPException, httpx.HTTPError, OSError, ValueError) as exc:
                error = type(exc).__name__  # never log body, tokens or sensitive URL here
            with session_factory() as db:
                d = db.get(WebhookDelivery, delivery_id, with_for_update=True)
                if d and d.status == "sending" and d.attempts == attempt:
                    if success:
                        d.status = "delivered"
                        d.last_error = ""
                        delivered += 1
                    else:
                        d.status = "dead" if attempt >= 6 else "pending"
                        d.last_error = error[:240]
                        d.retry_at = now() + timedelta(seconds=min(3600, 2 ** attempt * 15))
                        failed += 1
                    db.commit()
    finally:
        if owned_client:
            client.close()
    return {"prepared": prepared, "delivered": delivered, "failed": failed}


def main():
    import argparse
    import time
    parser = argparse.ArgumentParser(description="OtterNest signed webhook dispatcher")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be >= 1")
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    while True:
        outcome = dispatch_once(factory)
        print(outcome, flush=True)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
