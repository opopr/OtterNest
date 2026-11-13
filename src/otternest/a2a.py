"""A2A v0.3 JSON-RPC 2.0 gateway over the existing authoritative task engine.

This gateway is a *task coordinator*, not an LLM. It advertises only polling
and text tasks; it does not claim to push, stream, or wake external agents.
"""
from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_session
from .models import A2AInboxMessage, Project, Task
from .security import Actor, authenticate
from .services import Conflict, emit, enforce_project_task_total_budget, require_project, task_ready_status

router = APIRouter(include_in_schema=False)
MAX_RPC_BYTES = 32 * 1024


class RPCError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message


def rpc_error(request_id, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def public_base_url() -> str:
    """Never derive advertised URLs from untrusted Host / X-Forwarded headers."""
    raw = os.environ.get("OTTERNEST_A2A_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(raw)
    valid_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not raw or parsed.scheme not in ({"http", "https"} if valid_local else {"https"})
            or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise HTTPException(503, "Set OTTERNEST_A2A_BASE_URL to the public HTTPS origin (or local test origin)")
    return raw


@router.get("/.well-known/agent-card.json")
def agent_card():
    base = public_base_url()
    return {
        "protocolVersion": "0.3.0",
        "name": "OtterNest Task Coordination",
        "description": "Owner-governed project task handoff; results require human acceptance.",
        "url": f"{base}/a2a",
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": "governed-task-handoff", "name": "Governed Task Handoff",
            "description": "Create and poll an authorized project task using projectId metadata; no model execution guarantee.",
            "tags": ["task", "coordination", "governance"],
        }],
    }


STATE_MAP = {
    "ready": "submitted",
    "waiting_dependencies": "submitted",
    "waiting_approval": "input-required",
    "running": "working",
    "submitted": "input-required",  # worker has submitted; human acceptance still pending
    "blocked": "input-required",
    "completed": "completed",
    "cancelled": "canceled",
    "failed": "failed",
}


def a2a_task(task: Task, inbox: A2AInboxMessage, *, include_history: bool = False) -> dict:
    status = STATE_MAP.get(task.status, "unknown")
    result = {"kind": "task", "id": task.id, "contextId": inbox.project_id,
              "status": {"state": status},
              "metadata": {"otternest:taskVersion": task.version}}
    if task.status == "completed" and task.result_summary:
        result["artifacts"] = [{"artifactId": task.id + "-summary", "name": "result-summary",
                                "parts": [{"kind": "text", "text": task.result_summary}]}]
    if include_history:
        result["history"] = [{"kind": "message", "role": "user", "messageId": inbox.message_id,
                              "contextId": inbox.project_id, "taskId": task.id,
                              "parts": [{"kind": "text", "text": task.instruction}]}]
    return result


def expect_object(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise RPCError(-32602, f"{name} must be an object")
    return value


def task_for_a2a(db: Session, task_id, actor: Actor) -> tuple[Task, A2AInboxMessage]:
    if not isinstance(task_id, str) or not 0 < len(task_id) <= 128:
        raise RPCError(-32602, "Task id must be a non-empty string")
    inbox = db.scalar(select(A2AInboxMessage).where(A2AInboxMessage.task_id == task_id))
    if inbox is None or (not actor.is_owner and inbox.actor_id != actor.id):
        raise RPCError(-32001, "Task not found")
    try:
        require_project(db, inbox.project_id, actor)
    except HTTPException:
        raise RPCError(-32001, "Task not found") from None
    task = db.get(Task, task_id)
    if task is None:
        raise RPCError(-32001, "Task not found")
    return task, inbox


def send_message(db: Session, actor: Actor, params: dict) -> dict:
    message = expect_object(params.get("message"), "message")
    metadata = expect_object(params.get("metadata", {}), "metadata")
    config = expect_object(params.get("configuration", {}), "configuration")
    if config.get("blocking") is True:
        raise RPCError(-32004, "Blocking message/send is not supported; poll with tasks/get")
    if "pushNotificationConfig" in config:
        raise RPCError(-32003, "Push notifications are not supported")
    if config.get("acceptedOutputModes") and config["acceptedOutputModes"] != ["text/plain"]:
        if not isinstance(config["acceptedOutputModes"], list) or "text/plain" not in config["acceptedOutputModes"]:
            raise RPCError(-32005, "Only text/plain output is supported")
    if message.get("kind", "message") != "message" or message.get("role") != "user" or "taskId" in message:
        raise RPCError(-32004, "Only new user messages creating tasks are supported")
    message_id = message.get("messageId")
    if not isinstance(message_id, str) or not 0 < len(message_id) <= 128:
        raise RPCError(-32602, "messageId must be 1..128 characters")
    project_id = metadata.get("projectId")
    if not isinstance(project_id, str) or len(project_id) != 36:
        raise RPCError(-32602, "metadata.projectId must be a project UUID")
    if message.get("contextId") is not None and message.get("contextId") != project_id:
        raise RPCError(-32602, "contextId must match the authorized projectId")
    parts = message.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 16:
        raise RPCError(-32602, "Expected 1..16 text parts")
    if any(not isinstance(p, dict) or p.get("kind") != "text" or not isinstance(p.get("text"), str) for p in parts):
        raise RPCError(-32005, "Only text parts are supported")
    instruction = "\n".join(p["text"] for p in parts).strip()
    if not 1 <= len(instruction) <= 12000:
        raise RPCError(-32602, "Instruction must be 1..12000 characters")
    title = metadata.get("title", instruction.splitlines()[0][:160])
    acceptance = metadata.get("acceptance", "Owner must review and accept the submitted result")
    risk = metadata.get("risk", "R1")
    if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 240
            or not isinstance(acceptance, str) or not 1 <= len(acceptance.strip()) <= 6000
            or risk not in {"R0", "R1", "R2", "R3"}):
        raise RPCError(-32602, "Invalid title, acceptance or risk")
    # Project lock serializes duplicate message IDs in PostgreSQL across workers.
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise RPCError(-32001, "Project not found")
    try:
        require_project(db, project_id, actor, roles=("manager",))
    except HTTPException:
        raise RPCError(-32001, "Project not found") from None
    if project.status != "active":
        raise RPCError(-32004, "Project is not active")
    if not actor.is_owner and risk not in {"R0", "R1"}:
        raise RPCError(-32004, "Only the owner can create R2/R3 tasks")
    canonical = {"project": project_id, "instruction": instruction,
                 "title": title, "acceptance": acceptance, "risk": risk}
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    previous = db.scalar(select(A2AInboxMessage).where(
        A2AInboxMessage.project_id == project_id,
        A2AInboxMessage.actor_id == actor.id,
        A2AInboxMessage.message_id == message_id))
    if previous:
        if previous.request_hash != digest:
            raise RPCError(-32602, "messageId was already used with different content")
        existing = db.get(Task, previous.task_id)
        if existing is None:
            raise RPCError(-32001, "Task not found")
        return a2a_task(existing, previous)
    if not actor.is_owner:
        # A bounded, project-scoped work queue prevents delegated A2A callers from
        # creating unlimited unreviewed tasks. The project row lock serializes it.
        outstanding = db.scalar(select(func.count()).select_from(A2AInboxMessage)
            .join(Task, Task.id == A2AInboxMessage.task_id)
            .where(A2AInboxMessage.project_id == project_id,
                   A2AInboxMessage.actor_id == actor.id,
                   Task.status.in_(("ready", "waiting_dependencies", "waiting_approval",
                                   "running", "submitted", "blocked")))) or 0
        if outstanding >= 25:
            raise RPCError(-32004, "Agent outstanding A2A task limit reached")
    try:
        enforce_project_task_total_budget(db, project_id)
    except Conflict:
        raise RPCError(-32004, "Project total task budget reached") from None
    task = Task(project_id=project_id, title=title.strip(), instruction=instruction,
                acceptance=acceptance.strip(), risk=risk)
    db.add(task)
    db.flush()
    task.status = task_ready_status(db, task)
    inbox = A2AInboxMessage(project_id=project_id, task_id=task.id,
                            actor_id=actor.id, message_id=message_id, request_hash=digest)
    db.add(inbox)
    emit(db, actor, "a2a.task_created", task.id, project_id, {"risk": risk, "via": "a2a"})
    db.commit()
    return a2a_task(task, inbox)


def get_task(db: Session, actor: Actor, params: dict) -> dict:
    history_length = params.get("historyLength", 0)
    if not isinstance(history_length, int) or isinstance(history_length, bool) or not 0 <= history_length <= 20:
        raise RPCError(-32602, "historyLength must be between 0 and 20")
    task, inbox = task_for_a2a(db, params.get("id"), actor)
    return a2a_task(task, inbox, include_history=history_length > 0)


def cancel_task(db: Session, actor: Actor, params: dict) -> dict:
    task, inbox = task_for_a2a(db, params.get("id"), actor)
    # Never say a remote worker stopped merely because its lease was revoked.
    db.scalar(select(Task).where(Task.id == task.id).with_for_update())
    db.refresh(task)
    if task.status not in {"ready", "waiting_dependencies", "waiting_approval", "submitted", "blocked"}:
        raise RPCError(-32002, "Task cannot be canceled safely in its current state")
    task.status = "cancelled"
    task.lease_epoch += 1
    task.lease_until = None
    task.version += 1
    emit(db, actor, "task.cancelled", task.id, task.project_id, {"via": "a2a"})
    db.commit()
    return a2a_task(task, inbox)


@router.post("/a2a")
async def jsonrpc(request: Request, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Authenticated JSON-RPC 2.0 subset; transport auth is outside RPC envelopes."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_RPC_BYTES:
            return rpc_error(None, -32600, "Request exceeds 32 KiB")
    try:
        packet = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return rpc_error(None, -32700, "Invalid JSON")
    if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0":
        return rpc_error(None, -32600, "Invalid JSON-RPC request")
    ident = packet.get("id")
    if ident is None or isinstance(ident, bool) or not isinstance(ident, (str, int)):
        return rpc_error(None, -32600, "An integer or string request id is required")
    if not isinstance(packet.get("method"), str):
        return rpc_error(ident, -32600, "A method is required")
    try:
        params = expect_object(packet.get("params", {}), "params")
        if packet["method"] == "message/send":
            result = send_message(db, actor, params)
        elif packet["method"] == "tasks/get":
            result = get_task(db, actor, params)
        elif packet["method"] == "tasks/cancel":
            result = cancel_task(db, actor, params)
        else:
            raise RPCError(-32601, "Method not supported")
    except RPCError as exc:
        db.rollback()
        return rpc_error(ident, exc.code, exc.message)
    return {"jsonrpc": "2.0", "id": ident, "result": result}
