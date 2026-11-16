"""WebSocket event stream, AG-UI event projection, and A2UI component whitelist validation."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_engine, get_session
from .models import Event, Task
from .security import actor_from_token, authenticate
from .services import require_project

router = APIRouter(prefix="/api/v1", tags=["Realtime, AG-UI & A2UI"])

A2UI_ALLOWED_COMPONENTS = {"card", "text", "badge", "metric", "list", "table", "action_preview"}
A2UI_MAX_NODES = 60
A2UI_MAX_DEPTH = 4


class A2UIValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[dict[str, Any]] = Field(max_length=60)


def _sanitize_a2ui_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    val = value.strip()
    if val.startswith(("https://", "/", "#")):
        return val[:512]
    return None


def _sanitize_a2ui_node(node: Any, depth: int, state: dict[str, int], stripped: list[str]) -> dict[str, Any] | None:
    if depth > A2UI_MAX_DEPTH:
        stripped.append("max_depth_exceeded")
        return None
    if state["count"] >= A2UI_MAX_NODES:
        stripped.append("max_nodes_exceeded")
        return None
    if not isinstance(node, dict):
        stripped.append("non_object_node")
        return None
    ctype = str(node.get("type", "")).strip()
    if ctype not in A2UI_ALLOWED_COMPONENTS:
        stripped.append(f"disallowed_type:{ctype or 'empty'}")
        return None

    state["count"] += 1
    out: dict[str, Any] = {"type": ctype}
    for key in ("title", "label", "text", "value", "tone", "subtitle", "plan_id", "action"):
        if key in node and node[key] is not None:
            out[key] = str(node[key])[:500]
    if "href" in node:
        safe_url = _sanitize_a2ui_url(node["href"])
        if safe_url:
            out["href"] = safe_url
        else:
            stripped.append("unsafe_href")
    if "items" in node and isinstance(node["items"], list):
        out["items"] = [str(x)[:300] for x in node["items"][:30]]
    if "rows" in node and isinstance(node["rows"], list):
        out["rows"] = [
            [str(cell)[:200] for cell in r[:10]]
            for r in node["rows"][:30]
            if isinstance(r, list)
        ]
    if "children" in node and isinstance(node["children"], list):
        kids = []
        for child in node["children"][:20]:
            cleaned = _sanitize_a2ui_node(child, depth + 1, state, stripped)
            if cleaned is not None:
                kids.append(cleaned)
        out["children"] = kids
    return out


def sanitize_a2ui_tree(components: list[dict[str, Any]]) -> dict[str, Any]:
    state = {"count": 0}
    stripped: list[str] = []
    cleaned = []
    for comp in components[:A2UI_MAX_NODES]:
        res = _sanitize_a2ui_node(comp, 1, state, stripped)
        if res is not None:
            cleaned.append(res)
    return {
        "valid": len(stripped) == 0,
        "node_count": state["count"],
        "stripped": stripped,
        "components": cleaned,
    }


@router.post("/a2ui/validate")
def validate_a2ui(body: A2UIValidateIn, actor: Actor = Depends(authenticate)):
    """Validate and sanitize declarative A2UI components against a strict whitelist."""
    return sanitize_a2ui_tree(body.components)


def _map_event_to_ag_ui(ev: Event) -> dict[str, Any]:
    kind = ev.kind
    ag_type = "CUSTOM"
    if kind in ("task.claimed", "a2a.delegation_sent"):
        ag_type = "RUN_STARTED"
    elif kind in ("task.accepted", "task.submitted"):
        ag_type = "RUN_FINISHED"
    elif kind in ("task.blocked", "system.mode_changed"):
        ag_type = "RUN_ERROR"
    elif kind.startswith("command."):
        ag_type = "TOOL_CALL_START"
    elif kind.startswith("decision.") or kind.startswith("memory."):
        ag_type = "TEXT_MESSAGE_CONTENT"
    return {
        "type": ag_type,
        "event_id": ev.id,
        "otternest_kind": ev.kind,
        "subject_id": ev.subject_id,
        "timestamp": ev.created_at.isoformat() if ev.created_at else None,
        "payload": ev.payload or {},
    }


@router.get("/projects/{project_id}/ag-ui/events")
def list_ag_ui_events(project_id: str, after: int = 0, limit: int = 50, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    if limit < 1 or limit > 200:
        raise HTTPException(422, "limit must be 1..200")
    rows = db.scalars(
        select(Event).where(Event.project_id == project_id, Event.id > after).order_by(Event.id).limit(limit)
    ).all()
    tasks = db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc()).limit(50)).all()
    return {
        "protocol": "ag-ui-projection-v1",
        "project_id": project_id,
        "state_snapshot": {
            "task_counts": {
                st: sum(1 for t in tasks if t.status == st)
                for st in sorted({t.status for t in tasks})
            }
        },
        "events": [_map_event_to_ag_ui(e) for e in rows],
    }


from contextlib import contextmanager


@contextmanager
def _short_session(app):
    override = app.dependency_overrides.get(get_session)
    if override is not None:
        gen = override()
        session = next(gen)
        try:
            yield session
        finally:
            try:
                next(gen)
            except StopIteration:
                pass
    else:
        with Session(get_engine()) as session:
            yield session


@router.get("/projects/{project_id}/ag-ui/stream")
async def stream_ag_ui(project_id: str, request: Request, after: int = 0, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    async def generate():
        cursor = after
        with _short_session(request.app) as session:
            tasks = session.scalars(select(Task).where(Task.project_id == project_id)).all()
            snap = json.dumps({
                "type": "STATE_SNAPSHOT",
                "snapshot": {"project_id": project_id, "total_tasks": len(tasks)},
            }, ensure_ascii=False)
            yield f"event: ag_ui\ndata: {snap}\n\n"
        while not await request.is_disconnected():
            with _short_session(request.app) as session:
                try:
                    current = actor_from_token(token, session)
                    require_project(session, project_id, current)
                except HTTPException:
                    break
                rows = session.scalars(
                    select(Event).where(Event.project_id == project_id, Event.id > cursor).order_by(Event.id).limit(50)
                ).all()
            for ev in rows:
                cursor = ev.id
                yield f"id: {ev.id}\nevent: ag_ui\ndata: {json.dumps(_map_event_to_ag_ui(ev), ensure_ascii=False)}\n\n"
            if not rows:
                yield ": keepalive\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.websocket("/projects/{project_id}/events/ws")
async def websocket_events(websocket: WebSocket, project_id: str, after: int = 0):
    """Authenticated WebSocket stream backed by durable PostgreSQL/SQLite Event rows."""
    origin = websocket.headers.get("origin", "")
    allowed_origins = {
        item.strip().rstrip("/")
        for item in os.environ.get("OTTERNEST_CORS_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin and allowed_origins and origin.rstrip("/") not in allowed_origins:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    token = ""
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    cursor = max(0, after)

    try:
        if not token:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            frame = json.loads(raw)
            if not isinstance(frame, dict) or frame.get("type") != "auth" or not isinstance(frame.get("token"), str):
                await websocket.close(code=4401)
                return
            token = frame["token"]
            if isinstance(frame.get("after"), int) and frame["after"] >= 0:
                cursor = frame["after"]

        with _short_session(websocket.app) as session:
            actor = actor_from_token(token, session)
            require_project(session, project_id, actor)
        await websocket.send_json({"type": "connected", "project_id": project_id, "after": cursor})

        while True:
            with _short_session(websocket.app) as session:
                try:
                    actor = actor_from_token(token, session)
                    require_project(session, project_id, actor)
                except HTTPException:
                    await websocket.close(code=4403)
                    return
                events = session.scalars(
                    select(Event).where(Event.project_id == project_id, Event.id > cursor).order_by(Event.id).limit(100)
                ).all()
                batch = [
                    {
                        "type": "event",
                        "id": e.id,
                        "kind": e.kind,
                        "subject_id": e.subject_id,
                        "payload": e.payload,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                    }
                    for e in events
                ]
            for item in batch:
                cursor = item["id"]
                await websocket.send_json(item)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.5)
                if msg.strip().lower() == "ping":
                    await websocket.send_json({"type": "pong", "cursor": cursor})
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, HTTPException, json.JSONDecodeError, asyncio.TimeoutError):
        try:
            await websocket.close()
        except Exception:
            pass
