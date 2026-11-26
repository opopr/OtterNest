"""Owner and agent credentials. Never trust caller-supplied Agent IDs."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Agent, AgentRuntime, OwnerAccount, OwnerSession


SENSITIVITY_ORDER: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def sensitivity_allowed(record_level: str, allowed_level: str) -> bool:
    return SENSITIVITY_ORDER.get(record_level, 99) <= SENSITIVITY_ORDER.get(allowed_level, -1)


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalize naive SQLite timestamps to UTC-aware datetimes."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Actor:
    kind: str
    id: str
    auth_method: str = "token"
    runtime_id: str | None = None

    @property
    def is_owner(self) -> bool:
        return self.kind == "owner"


def new_agent_token() -> str:
    return "on_agent_" + secrets.token_urlsafe(40)


def new_runtime_token() -> str:
    return "on_runtime_" + secrets.token_urlsafe(40)


def new_monitor_token() -> str:
    return "on_monitor_" + secrets.token_urlsafe(40)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def actor_from_token(token: str, db: Session) -> Actor:
    owner = os.environ.get("OTTERNEST_OWNER_TOKEN", "")
    if len(owner) < 32 or owner.startswith("REPLACE_"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Owner token is not configured")
    if hmac.compare_digest(token, owner):
        return Actor("owner", "owner", "owner_token")
    if token.startswith("on_session_"):
        session = db.scalar(
            select(OwnerSession).where(
                OwnerSession.token_hash == hash_token(token),
                OwnerSession.kind == "session",
                OwnerSession.revoked.is_(False),
            )
        )
        now_dt = datetime.now(timezone.utc)
        expires = as_utc(session.expires_at) if session else None
        if session and expires and expires > now_dt:
            account = db.get(OwnerAccount, session.owner_id)
            if account and (not account.totp_enabled or session.mfa_verified):
                return Actor("owner", "owner", "session")
    if token.startswith("on_agent_"):
        agent = db.scalar(select(Agent).where(Agent.token_hash == hash_token(token), Agent.enabled.is_(True), Agent.deleted_at.is_(None)))
        if agent:
            return Actor("agent", agent.id, "agent_token")
    if token.startswith(("on_runtime_", "on_monitor_")):
        runtime = db.scalar(select(AgentRuntime).where(AgentRuntime.token_hash == hash_token(token), AgentRuntime.enabled.is_(True)))
        if runtime:
            agent = db.get(Agent, runtime.agent_id)
            if agent and agent.enabled and agent.deleted_at is None:
                return Actor("monitor" if token.startswith("on_monitor_") else "agent", agent.id,
                             "monitor_token" if token.startswith("on_monitor_") else "runtime_token",
                             runtime_id=runtime.id)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


def authenticate(request: Request, authorization: str | None = Header(default=None), db: Session = Depends(get_session)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
    actor = actor_from_token(authorization[7:], db)
    if actor.kind == "monitor" and not (
        request.method == "POST" and
        request.url.path == f"/api/v1/agent/runtimes/{actor.runtime_id}/heartbeat"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Monitor token permits heartbeat only")
    return actor


def owner_only(actor: Actor) -> None:
    if not actor.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner permission required")
