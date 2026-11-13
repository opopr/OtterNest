"""Owner-authorized outbound A2A v0.3 JSON-RPC peer delegation.

Network sends are explicitly requested, never inferred from Agent Card discovery.
A lost acknowledgement is UNKNOWN: the worker never blindly re-sends message/send.
No remote credential is persisted; secrets are injected by endpoint-specific env vars.
"""
from __future__ import annotations

import argparse
import json
import ipaddress
import logging
import os
import secrets
import socket
import time
from datetime import timedelta, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import get_engine, get_session
from .models import A2ADelegation, A2ARemoteEndpoint, Agent, Membership, Project, Task, now
from .security import Actor, authenticate, owner_only
from .services import Conflict, dependencies_completed, emit, require_project, require_task

log = logging.getLogger("otternest.a2a_outbound")
router = APIRouter(prefix="/api/v1", tags=["A2A Outbound"])
MAX_REPLY_BYTES = 128 * 1024
REMOTE_STATES = {"submitted", "working", "completed", "failed", "canceled", "rejected", "input-required", "auth-required"}


class RemoteIn(BaseModel):
    agent_id: str
    url: str = Field(min_length=12, max_length=2048)
    remote_project_id: str | None = Field(default=None, min_length=1, max_length=128)


class DelegationIn(BaseModel):
    endpoint_id: str


class ReconcileIn(BaseModel):
    remote_task_id: str = Field(min_length=1, max_length=128)


def token_env_name(endpoint_id: str) -> str:
    return "OTTERNEST_A2A_REMOTE_TOKEN_" + endpoint_id.replace("-", "_").upper()


def remote_token(endpoint_id: str) -> str:
    token = os.getenv(token_env_name(endpoint_id), "").strip()
    if len(token) < 12 or "\n" in token or "\r" in token or token.startswith("REPLACE_"):
        raise HTTPException(503, "Configure the remote endpoint bearer token outside the database")
    return token


def approved_remote_url(url: str, *, check_dns: bool = False) -> str:
    """Require a fixed, explicit HTTPS hostname allowlist; never follow redirects.

    Network-layer egress filtering / DNS pinning are recommended for production:
    preflight DNS checks alone cannot eliminate DNS rebinding races.
    """
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {h.strip().lower().rstrip(".") for h in os.getenv("OTTERNEST_A2A_ALLOWED_HOSTS", "").split(",") if h.strip()}
    try:
        port_ok = parsed.port in (None, 443)
    except ValueError:
        port_ok = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if (parsed.scheme != "https" or not host or host not in allowed or is_ip or not port_ok
            or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith("/")):
        raise HTTPException(422, "A2A peer must use an approved public HTTPS host on port 443")
    if check_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except (socket.gaierror, OSError):
            raise HTTPException(503, "Cannot resolve approved A2A peer") from None
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise HTTPException(422, "A2A peer resolves to a non-public address")
    return host


def public_delegation(item: A2ADelegation) -> dict:
    return {"id": item.id, "project_id": item.project_id, "task_id": item.task_id,
            "endpoint_id": item.endpoint_id, "status": item.status,
            "remote_task_id": item.remote_task_id, "remote_state": item.remote_state,
            "attempts": item.attempts, "last_error": item.last_error,
            "updated_at": item.updated_at}


@router.post("/projects/{project_id}/a2a/remotes", status_code=201)
def register_remote(project_id: str, body: RemoteIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    approved_remote_url(body.url)
    agent = db.get(Agent, body.agent_id)
    membership = db.scalar(select(Membership).where(Membership.project_id == project_id, Membership.agent_id == body.agent_id,
                                                     Membership.role.in_(("worker", "manager", "reviewer"))))
    if not agent or not agent.enabled or agent.execution_mode != "remote" or not membership:
        raise HTTPException(422, "Peer requires an enabled remote-mode Agent and project membership")
    existing = db.scalar(select(A2ARemoteEndpoint).where(A2ARemoteEndpoint.project_id == project_id,
                                                         A2ARemoteEndpoint.agent_id == body.agent_id))
    if existing:
        raise Conflict("This remote Agent already has a peer endpoint in the project")
    endpoint = A2ARemoteEndpoint(project_id=project_id, agent_id=body.agent_id, url=body.url,
                                 remote_project_id=body.remote_project_id)
    db.add(endpoint)
    db.flush()
    emit(db, actor, "a2a.remote_registered", endpoint.id, project_id, {"agent_id": agent.id})
    db.commit()
    return {"id": endpoint.id, "agent_id": endpoint.agent_id, "url": endpoint.url,
            "enabled": endpoint.enabled, "secret_env": token_env_name(endpoint.id)}


@router.get("/projects/{project_id}/a2a/remotes")
def list_remotes(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    return [{"id": e.id, "agent_id": e.agent_id, "url": e.url,
             "enabled": e.enabled, "remote_project_id": e.remote_project_id}
            for e in db.scalars(select(A2ARemoteEndpoint).where(A2ARemoteEndpoint.project_id == project_id))]


@router.post("/a2a/remotes/{endpoint_id}/disable")
def disable_remote(endpoint_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    e = db.get(A2ARemoteEndpoint, endpoint_id, with_for_update=True)
    if e is None:
        raise HTTPException(404, "A2A peer not found")
    e.enabled = False
    emit(db, actor, "a2a.remote_disabled", e.id, e.project_id)
    db.commit()
    return {"id": e.id, "enabled": False}


@router.post("/tasks/{task_id}/a2a/delegate", status_code=201)
def queue_delegation(task_id: str, body: DelegationIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Only the owner explicitly authorizes a low-risk cross-system handoff."""
    owner_only(actor)
    task = require_task(db, task_id, actor, lock=True)
    project = db.get(Project, task.project_id)
    endpoint = db.get(A2ARemoteEndpoint, body.endpoint_id)
    if not project or project.status != "active" or task.status != "ready" or not dependencies_completed(db, task.id):
        raise Conflict("Task is not ready for outbound delegation")
    if task.risk not in {"R0", "R1"}:
        raise Conflict("Only R0/R1 tasks can be delegated to external A2A peers")
    if (endpoint is None or not endpoint.enabled or endpoint.project_id != task.project_id):
        raise HTTPException(404, "A2A peer not found in project")
    agent = db.get(Agent, endpoint.agent_id)
    membership = db.scalar(select(Membership).where(
        Membership.project_id == task.project_id, Membership.agent_id == endpoint.agent_id,
        Membership.role.in_(("worker", "manager", "reviewer"))))
    if (agent is None or not agent.enabled or agent.execution_mode != "remote" or not membership or
            not set(task.required_capabilities).issubset(set(agent.verified_capabilities))):
        raise Conflict("Remote Agent membership or verified capabilities are missing")
    if task.target_agent_id is not None and task.target_agent_id != agent.id:
        raise Conflict("Task is reserved for another Agent")
    if db.scalar(select(A2ADelegation.id).where(A2ADelegation.task_id == task.id)):
        raise Conflict("Task already has an outbound A2A delegation")
    approved_remote_url(endpoint.url)
    remote_token(endpoint.id)  # fail before writing anything; never expose token in response
    delegation = A2ADelegation(project_id=task.project_id, task_id=task.id, endpoint_id=endpoint.id,
                               message_id=secrets.token_hex(24))
    db.add(delegation)
    task.target_agent_id = agent.id
    task.version += 1
    db.flush()
    emit(db, actor, "a2a.delegation_queued", delegation.id, task.project_id, {"task_id": task.id, "endpoint_id": endpoint.id})
    db.commit()
    return public_delegation(delegation)


@router.get("/a2a/delegations/{delegation_id}")
def get_delegation(delegation_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    d = db.get(A2ADelegation, delegation_id)
    if d is None:
        raise HTTPException(404, "Delegation not found")
    return public_delegation(d)


@router.post("/a2a/delegations/{delegation_id}/cancel")
def request_cancel(delegation_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    d = db.get(A2ADelegation, delegation_id, with_for_update=True)
    if d is None:
        raise HTTPException(404, "Delegation not found")
    task = db.get(Task, d.task_id, with_for_update=True)
    if d.status == "pending" and task.status == "ready":
        d.status = "canceled"
        task.status = "cancelled"
        task.lease_epoch += 1
        task.version += 1
    elif d.status == "remote" and d.remote_task_id and task.status == "running":
        d.status = "cancel_requested"
    else:
        raise Conflict("Cancellation not safe now; inspect remote status first")
    d.updated_at = now()
    emit(db, actor, "a2a.delegation_cancel_requested", d.id, d.project_id)
    db.commit()
    return public_delegation(d)


@router.post("/a2a/delegations/{delegation_id}/release")
def release_terminal_delegation(delegation_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Explicitly detach a terminal remote attempt so the task can be retried locally.

    Unknown, live and cancel-requested tasks CANNOT be released as if stopped.
    The historical remote mapping remains immutable for audit.
    """
    owner_only(actor)
    d = db.get(A2ADelegation, delegation_id, with_for_update=True)
    if d is None:
        raise HTTPException(404, "Delegation not found")
    task = db.get(Task, d.task_id, with_for_update=True)
    if d.status not in {"failed", "changes_requested"} or task.status != "blocked":
        raise Conflict("Only a confirmed terminal blocked A2A task can be released")
    d.status = "released"
    d.updated_at = now()
    task.target_agent_id = None
    task.assigned_agent_id = None
    task.lease_epoch += 1
    task.version += 1
    emit(db, actor, "a2a.delegation_released", d.id, d.project_id, {"task_id": task.id})
    db.commit()
    return public_delegation(d)


@router.post("/a2a/delegations/{delegation_id}/reconcile")
def reconcile_unknown(delegation_id: str, body: ReconcileIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Owner supplies a verified external task ID after an ambiguous send; never resend automatically."""
    owner_only(actor)
    d = db.get(A2ADelegation, delegation_id, with_for_update=True)
    if d is None:
        raise HTTPException(404, "Delegation not found")
    task = db.get(Task, d.task_id, with_for_update=True)
    if d.status != "unknown" or task.status not in {"ready", "running"}:
        raise Conflict("Only unknown ongoing delegations can be reconciled")
    if d.remote_task_id and d.remote_task_id != body.remote_task_id:
        raise Conflict("Remote task ID differs from previously verified mapping")
    d.remote_task_id = body.remote_task_id
    d.status = "remote"
    d.next_poll_at = now()
    d.updated_at = now()
    emit(db, actor, "a2a.delegation_reconcile_requested", d.id, d.project_id)
    db.commit()
    return public_delegation(d)


def _response(client: httpx.Client, endpoint: A2ARemoteEndpoint, method: str, params: dict, *, request_id: str) -> dict:
    approved_remote_url(endpoint.url, check_dns=True)
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + remote_token(endpoint.id)}
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    with client.stream("POST", endpoint.url, json=payload, headers=headers, follow_redirects=False) as response:
        if response.is_redirect or response.status_code < 200 or response.status_code >= 300:
            raise ValueError("Remote A2A returned non-2xx response")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_REPLY_BYTES:
                raise ValueError("Remote A2A response is too large")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise ValueError("Remote A2A returned invalid JSON") from None
    if not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != request_id:
        raise ValueError("Remote A2A response is not correlated to request")
    if "error" in result or not isinstance(result.get("result"), dict):
        raise ValueError("Remote A2A returned a protocol error")
    return result["result"]


def _mark_remote_result(db: Session, d: A2ADelegation, endpoint: A2ARemoteEndpoint, result: dict, *, is_send: bool) -> None:
    task = db.get(Task, d.task_id, with_for_update=True)
    remote_id = result.get("id")
    remote_state = (result.get("status") or {}).get("state") if isinstance(result.get("status"), dict) else None
    if (result.get("kind") != "task" or not isinstance(remote_id, str) or not 0 < len(remote_id) <= 128
            or remote_state not in REMOTE_STATES):
        raise ValueError("Remote A2A task response is malformed")
    if is_send:
        d.remote_task_id = remote_id
        context = result.get("contextId")
        d.remote_context_id = context if isinstance(context, str) and len(context) <= 128 else None
    elif d.remote_task_id != remote_id:
        raise ValueError("Remote task ID mismatch")
    if task.status not in {"ready", "running"}:
        raise ValueError("Local task was changed during remote request")
    if not endpoint.enabled:
        raise ValueError("Remote peer was disabled during request")
    if not is_send and task.status == "running" and d.remote_state == remote_state and remote_state in {"submitted", "working", "input-required", "auth-required"}:
        # A status poll without a state transition is not a new business event.
        # Keep the single-core control plane's outbox and audit from growing each minute.
        d.status = "remote"
        d.last_error = ""
        d.updated_at = now()
        return
    if remote_state in {"submitted", "working", "input-required", "auth-required"}:
        task.status = "running"
        task.assigned_agent_id = endpoint.agent_id
        task.lease_until = None  # remote Agent is tracked separately; not a local lease
        if is_send:
            task.lease_epoch += 1
        d.status = "remote"
    elif remote_state == "completed":
        summaries = []
        for artifact in result.get("artifacts", [])[:10] if isinstance(result.get("artifacts"), list) else []:
            for part in artifact.get("parts", [])[:10] if isinstance(artifact, dict) and isinstance(artifact.get("parts"), list) else []:
                if isinstance(part, dict) and part.get("kind") == "text" and isinstance(part.get("text"), str):
                    summaries.append(part["text"])
        task.result_summary = "\n".join(summaries)[:20000] or "Remote A2A reports completed; review evidence on the remote peer."
        task.status = "submitted"  # the owner must still review and accept
        task.assigned_agent_id = endpoint.agent_id
        d.status = "submitted"
    else:
        task.status = "blocked" if remote_state in {"failed", "rejected"} else "cancelled"
        task.result_summary = ("Remote A2A reported " + remote_state)[:20000]
        task.lease_until = None
        d.status = "failed" if task.status == "blocked" else "canceled"
    d.remote_state = remote_state
    d.last_error = ""
    d.updated_at = now()
    task.version += 1
    emit(db, Actor("owner", "a2a-dispatcher"), "a2a.remote_status", d.id, d.project_id,
         {"task_id": task.id, "remote_state": remote_state})


def _fail_before_send(factory: sessionmaker, delegation_id: str, attempt: int, message: str) -> None:
    """Preflight rejected the job before the network call; owner may release it."""
    with factory() as db:
        d = db.get(A2ADelegation, delegation_id, with_for_update=True)
        if not d or d.status != "sending" or d.attempts != attempt:
            return
        task = db.get(Task, d.task_id, with_for_update=True)
        d.status = "failed"
        d.last_error = message[:240]
        d.updated_at = now()
        if task and task.status == "ready":
            task.status = "blocked"
            task.version += 1
        emit(db, Actor("owner", "a2a-dispatcher"), "a2a.dispatch_blocked", d.id, d.project_id,
             {"task_id": d.task_id, "reason": message[:120]})
        db.commit()


def _claim_jobs(factory: sessionmaker, batch: int) -> list[tuple[str, str, int]]:
    jobs = []
    with factory() as db:
        rows = db.scalars(select(A2ADelegation).where(
            (A2ADelegation.status.in_(("pending", "cancel_requested"))) |
            ((A2ADelegation.status == "remote") & (A2ADelegation.next_poll_at <= now()))
        ).order_by(A2ADelegation.created_at, A2ADelegation.id)
          .limit(batch).with_for_update(skip_locked=True)).all()
        for d in rows:
            action = {"pending": "send", "remote": "poll", "cancel_requested": "cancel"}[d.status]
            d.status = {"send": "sending", "poll": "polling", "cancel": "canceling"}[action]
            d.updated_at = now()
            d.attempts += 1
            jobs.append((d.id, action, d.attempts))
        db.commit()
    return jobs


def dispatch_once(factory: sessionmaker, *, client: httpx.Client | None = None, batch: int = 10) -> dict:
    """One bounded outbound A2A cycle. Invoking it does not auto-create delegations."""
    if not 1 <= batch <= 100:
        raise ValueError("batch must be 1..100")
    # Crash recovery: in-flight requests may have reached remote. Never re-send.
    with factory() as db:
        cutoff = now() - timedelta(minutes=3)
        stale = db.scalars(select(A2ADelegation).where(
            A2ADelegation.status.in_(("sending", "polling", "canceling")),
            A2ADelegation.updated_at < cutoff).with_for_update(skip_locked=True)).all()
        for d in stale:
            d.status = "unknown" if d.status != "polling" else "remote"
            d.last_error = "Interrupted outbound request; external result not verified"
            d.updated_at = now()
            emit(db, Actor("owner", "a2a-dispatcher"), "a2a.outbound_interrupted", d.id, d.project_id,
                 {"task_id": d.task_id, "status": d.status})
        db.commit()
    jobs = _claim_jobs(factory, batch)
    owned_client = client is None
    client = client or httpx.Client(timeout=8, follow_redirects=False, trust_env=False)
    summary = {"sent": 0, "polled": 0, "cancelled": 0, "unknown": 0}
    try:
        for delegation_id, action, attempt in jobs:
            with factory() as db:
                d = db.get(A2ADelegation, delegation_id)
                endpoint = db.get(A2ARemoteEndpoint, d.endpoint_id) if d else None
                task = db.get(Task, d.task_id) if d else None
                if d is None or endpoint is None or task is None:
                    continue
                agent = db.get(Agent, endpoint.agent_id)
                project = db.get(Project, d.project_id)
                member = db.scalar(select(Membership).where(Membership.project_id == d.project_id,
                                                              Membership.agent_id == endpoint.agent_id,
                                                              Membership.role.in_(("worker", "manager", "reviewer"))))
                if (not endpoint.enabled or not agent or not agent.enabled or not member or
                        (action == "send" and (not project or project.status != "active" or
                         agent.execution_mode != "remote" or
                         not set(task.required_capabilities).issubset(set(agent.verified_capabilities))))):
                    if action == "send":
                        _fail_before_send(factory, delegation_id, attempt, "Remote Agent authorization was revoked")
                    else:
                        with factory() as update:
                            current = update.get(A2ADelegation, delegation_id, with_for_update=True)
                            if current and current.attempts == attempt:
                                current.status = "unknown"
                                current.last_error = "Remote Agent authorization was revoked"
                                current.updated_at = now()
                                update.commit()
                    continue
                if action == "send" and (task.status != "ready" or task.target_agent_id != endpoint.agent_id):
                    _fail_before_send(factory, delegation_id, attempt, "Local task is no longer ready")
                    continue
                if action in {"poll", "cancel"} and not d.remote_task_id:
                    continue
                if action == "send":
                    metadata = {"otternest:localTaskId": task.id, "title": task.title,
                                "acceptance": task.acceptance, "risk": task.risk}
                    if endpoint.remote_project_id:
                        metadata["projectId"] = endpoint.remote_project_id
                    params = {"message": {"kind": "message", "role": "user",
                                          "messageId": d.message_id,
                                          "parts": [{"kind": "text", "text": task.instruction}]},
                              "metadata": metadata}
                    method = "message/send"
                elif action == "poll":
                    params, method = {"id": d.remote_task_id}, "tasks/get"
                else:
                    params, method = {"id": d.remote_task_id}, "tasks/cancel"
                remote_endpoint = endpoint  # mapped values are read before network I/O
            if action == "send":
                # Fail closed *before* crossing the network boundary when configuration is invalid.
                try:
                    approved_remote_url(remote_endpoint.url, check_dns=True)
                    remote_token(remote_endpoint.id)
                except (HTTPException, ValueError, OSError):
                    _fail_before_send(factory, delegation_id, attempt, "A2A peer is not safely reachable or configured")
                    continue
            try:
                result = _response(client, remote_endpoint, method, params, request_id=delegation_id + ":" + action)
                with factory() as db:
                    d = db.get(A2ADelegation, delegation_id, with_for_update=True)
                    e = db.get(A2ARemoteEndpoint, d.endpoint_id) if d else None
                    if d and e and d.status == {"send": "sending", "poll": "polling", "cancel": "canceling"}[action] and d.attempts == attempt:
                        try:
                            _mark_remote_result(db, d, e, result, is_send=(action == "send"))
                        except ValueError:
                            d.status = "unknown"
                            d.last_error = "Remote status could not be verified"
                            d.updated_at = now()
                        else:
                            if d.status == "remote":
                                d.next_poll_at = now() + timedelta(seconds=60)
                            summary[{"send": "sent", "poll": "polled", "cancel": "cancelled"}[action]] += 1
                        db.commit()
            except (ValueError, HTTPException, httpx.HTTPError, OSError):
                with factory() as db:
                    d = db.get(A2ADelegation, delegation_id, with_for_update=True)
                    if d and d.attempts == attempt and d.status in {"sending", "polling", "canceling"}:
                        if action == "poll":
                            # Read-only tasks/get can be retried without duplicating side effects.
                            d.status = "remote"
                            d.next_poll_at = now() + timedelta(seconds=min(3600, 30 * 2 ** min(attempt, 6)))
                        else:
                            # The remote may have performed the operation despite a lost reply.
                            d.status = "unknown"
                            summary["unknown"] += 1
                        d.last_error = "Remote transport or protocol outcome unknown"
                        d.updated_at = now()
                        emit(db, Actor("owner", "a2a-dispatcher"), "a2a.outbound_transport_error", d.id, d.project_id,
                             {"task_id": d.task_id, "status": d.status})
                        db.commit()
    finally:
        if owned_client:
            client.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Owner-approved outbound A2A worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--batch", type=int, default=10)
    args = parser.parse_args()
    if args.interval < 3:
        parser.error("--interval must be >= 3 seconds")
    logging.basicConfig(level=logging.INFO)
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    while True:
        outcome = dispatch_once(factory, batch=args.batch)
        log.info("A2A outbound dispatch counts: %s", outcome)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
