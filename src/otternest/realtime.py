"""WebSocket event stream, AG-UI event projection, A2UI component whitelist validation, and multi-turn Command Center."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import _compute_project_metrics
from .db import get_engine, get_session
from .governance import CommandDraft, create_command_plan
from .intent import interpret_command, Utterance
from .models import Agent, Event, Memory, Task, now
from .security import Actor, actor_from_token, authenticate, owner_only
from .services import Conflict, require_project

router = APIRouter(prefix="/api/v1", tags=["Realtime, AG-UI, A2UI & Command Chat"])

A2UI_ALLOWED_COMPONENTS = {"card", "text", "badge", "metric", "list", "table", "action_preview"}
A2UI_MAX_NODES = 60
A2UI_MAX_DEPTH = 4


class A2UIValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[dict[str, Any]] = Field(max_length=60)


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=3500)
    task_id: str | None = None


class CommandChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    text: str = Field(min_length=1, max_length=3500)
    focus_task_id: str | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    mode: Literal["template", "model"] = "template"


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


def _resolve_context_task(db: Session, body: CommandChatIn) -> tuple[Task | None, list[Task]]:
    if body.focus_task_id:
        t = db.get(Task, body.focus_task_id)
        if t and t.project_id == body.project_id:
            return t, [t]
    for turn in reversed(body.history):
        if turn.task_id:
            t = db.get(Task, turn.task_id)
            if t and t.project_id == body.project_id:
                return t, [t]
    tasks = db.scalars(
        select(Task).where(Task.project_id == body.project_id).order_by(Task.created_at.desc()).limit(30)
    ).all()
    actionable = [t for t in tasks if t.status in ("waiting_approval", "submitted", "blocked", "ready")]
    if len(actionable) == 1:
        return actionable[0], actionable
    return None, actionable[:5]


@router.post("/command/chat")
def command_chat(body: CommandChatIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Multi-turn Command Center supporting contextual focus, clarification, explanation, and preview plans."""
    owner_only(actor)
    project = require_project(db, body.project_id, actor)
    text = body.text.strip()

    # 1. Informational queries answered directly from authoritative state + A2UI cards
    if any(kw in text for kw in ("简报", "状态", "进度", "指标", "质量", "总结", "概览")):
        metrics = _compute_project_metrics(db, project.id)
        tasks = db.scalars(select(Task).where(Task.project_id == project.id)).all()
        memories = db.scalars(select(Memory).where(Memory.project_id == project.id, Memory.status == "published").limit(5)).all()
        a2ui = sanitize_a2ui_tree([
            {
                "type": "card",
                "title": f"{project.name} · 实时状态与质量摘要",
                "subtitle": f"总任务 {metrics['total_tasks']} · 已完成 {metrics['completed_count']} · 阻塞 {metrics['blocked_count']}",
                "children": [
                    {
                        "type": "metric",
                        "label": "验收通过率",
                        "value": f"{round(metrics['acceptance_pass_rate'] * 100)}%" if metrics["acceptance_pass_rate"] is not None else "样本不足（未知）",
                    },
                    {
                        "type": "list",
                        "title": "引用参考记忆",
                        "items": [f"{m.title} (v{m.version}, {m.sensitivity})" for m in memories] or ["暂无已发布记忆"],
                    },
                ],
            }
        ])
        return {
            "status": "answered",
            "reply": f"项目「{project.name}」当前共有 {len(tasks)} 项任务（已完成 {metrics['completed_count']}，阻塞 {metrics['blocked_count']}）。",
            "evidence": {"task_count": len(tasks), "memory_ids": [m.id for m in memories]},
            "a2ui": a2ui,
        }

    # 2. Explain why an agent was selected or why a task is waiting/blocked
    if any(kw in text for kw in ("为什么", "原因", "解释")):
        focused, candidates = _resolve_context_task(db, body)
        if focused is None:
            return {
                "status": "needs_clarification",
                "reply": "当前项目存在多个候选任务，请先指定你要查看哪一项任务：",
                "candidates": [{"id": t.id, "title": t.title, "status": t.status, "risk": t.risk} for t in candidates],
            }
        agent = db.get(Agent, focused.assigned_agent_id or focused.target_agent_id) if (focused.assigned_agent_id or focused.target_agent_id) else None
        reasons = [f"任务当前状态：{focused.status}（风险 {focused.risk}，版本 v{focused.version}）"]
        if focused.failure_reason:
            reasons.append(f"最近阻塞归因：{focused.failure_reason}")
        if agent:
            reasons.append(f"关联 Agent：{agent.name}（已验证能力：{', '.join(agent.verified_capabilities) or '无'}）")
        else:
            reasons.append("尚未指派或领取执行 Agent。")
        return {
            "status": "answered",
            "focus_task_id": focused.id,
            "reply": "；".join(reasons),
            "a2ui": sanitize_a2ui_tree([{"type": "card", "title": focused.title, "items": reasons}]),
        }

    # 3. Try exact template match first
    try:
        plan = interpret_command(Utterance(project_id=project.id, text=text), actor, db)
        return {
            "status": "plan_ready",
            "focus_task_id": plan["task_id"],
            "reply": f"已为任务「{plan['task_title']}」生成待确认执行计划（{plan['action']}）。",
            "plan": plan,
            "a2ui": sanitize_a2ui_tree([{
                "type": "action_preview",
                "title": plan["task_title"],
                "action": plan["action"],
                "plan_id": plan["id"],
                "value": f"v{plan['expected_version']}",
            }]),
        }
    except HTTPException:
        pass

    # 4. Contextual follow-up action ("批准", "继续", "验收通过", "取消")
    focused, candidates = _resolve_context_task(db, body)
    action_map = None
    if any(k in text for k in ("批准", "放行", "授权")):
        action_map = {"action": "authorize_task"}
    elif any(k in text for k in ("验收通过", "通过", "同意验收")):
        action_map = {"action": "review_task", "accepted": True}
    elif any(k in text for k in ("取消", "终止")):
        action_map = {"action": "cancel_task"}
    elif "继续" in text:
        if focused and focused.status == "waiting_approval":
            action_map = {"action": "authorize_task"}
        elif focused and focused.status == "submitted":
            action_map = {"action": "review_task", "accepted": True}

    if action_map is not None:
        if focused is None:
            return {
                "status": "needs_clarification",
                "reply": "指令对象不明确。请选择你要操作的具体任务（系统不会擅自猜测执行）：",
                "candidates": [{"id": t.id, "title": t.title, "status": t.status, "risk": t.risk} for t in candidates],
            }
        try:
            plan = create_command_plan(CommandDraft(task_id=focused.id, **action_map), actor, db)
        except Conflict as exc:
            return {
                "status": "answered",
                "focus_task_id": focused.id,
                "reply": f"任务「{focused.title}」当前状态为 {focused.status}，无法执行该操作：{exc.detail}",
            }
        return {
            "status": "plan_ready",
            "focus_task_id": focused.id,
            "reply": f"结合当前会话上下文，已针对任务「{focused.title}」生成待确认计划。",
            "plan": plan,
            "a2ui": sanitize_a2ui_tree([{
                "type": "action_preview",
                "title": focused.title,
                "action": plan["action"],
                "plan_id": plan["id"],
                "value": f"v{plan['expected_version']}",
            }]),
        }

    return {
        "status": "needs_clarification",
        "reply": "未能识别可执行意图。你可以询问项目「状态/指标/简报」、询问「为什么阻塞」，或使用「批准/验收通过/取消」针对指定任务生成待确认预览。",
        "candidates": [{"id": t.id, "title": t.title, "status": t.status, "risk": t.risk} for t in candidates],
    }
