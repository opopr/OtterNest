"""Owner-only chat surface for safe project questions and command previews."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .intent import Utterance, interpret_command
from .model_planner import ModelHistoryTurn, ModelUtterance, interpret_model
from .models import Project, Task
from .security import Actor, authenticate, owner_only
from .services import require_project

router = APIRouter(prefix="/api/v1", tags=["command-center"])

_STATUS_LABELS = {
    "ready": "待执行", "waiting_dependencies": "等待依赖", "waiting_approval": "等待授权",
    "running": "执行中", "submitted": "等待验收", "blocked": "已阻塞",
    "completed": "已完成", "cancelled": "已取消",
}


class CommandChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    text: str = Field(min_length=1, max_length=3500)
    focus_task_id: str | None = None
    history: list[ModelHistoryTurn] = Field(default_factory=list, max_length=20)
    mode: Literal["template", "model"] = "template"

    @model_validator(mode="after")
    def bound_history(self):
        if sum(len(turn.text) for turn in self.history) > 12000:
            raise ValueError("Conversation history exceeds the 12000-character limit")
        return self


def _focused_command(text: str, task_id: str | None) -> str:
    if not task_id:
        return text
    value = text.strip()
    patterns = (
        (r"^(?:请)?批准它[。.]?$", f"请批准任务「{task_id}」"),
        (r"^(?:请)?取消它[。.]?$", f"请取消任务「{task_id}」"),
        (r"^(?:请)?验收通过它[。.]?$", f"请验收通过任务「{task_id}」"),
    )
    for pattern, replacement in patterns:
        if re.fullmatch(pattern, value):
            return replacement
    rejected = re.fullmatch(r"^(?:请)?验收驳回它\s*原因[：:]\s*(.{1,3000})$", value)
    if rejected:
        return f"请验收驳回任务「{task_id}」原因：{rejected.group(1).strip()}"
    routed = re.fullmatch(r"^(?:请)?指派它给\s*[「“\"]?([^」”\"]{1,120})[」”\"]?[。.]?$", value)
    if routed:
        return f"请指派任务「{task_id}」给「{routed.group(1).strip()}」"
    return text


def _project_status(project_id: str, db: Session) -> dict:
    project = db.get(Project, project_id)
    tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    labels = [f"{_STATUS_LABELS.get(status, status)} {count}" for status, count in sorted(counts.items())]
    detail = "、".join(labels) if labels else "暂无任务"
    return {"status": "answered", "reply": f"项目「{project.name}」当前状态为{project.status}，共 {len(tasks)} 项任务：{detail}。"}


def _blocked_answer(project_id: str, focus_task_id: str | None, db: Session) -> dict:
    if focus_task_id:
        task = db.scalar(select(Task).where(Task.id == focus_task_id, Task.project_id == project_id))
        if task is None:
            raise HTTPException(404, "Focused task not found in selected project")
        if task.status != "blocked":
            return {"status": "answered", "focus_task_id": task.id,
                    "reply": f"任务「{task.title}」当前状态为「{_STATUS_LABELS.get(task.status, task.status)}」，并非阻塞状态。"}
        reason = task.failure_reason or "未记录标准化失败原因"
        summary = task.result_summary or "暂无失败摘要"
        return {"status": "answered", "focus_task_id": task.id,
                "reply": f"任务「{task.title}」已阻塞。失败原因：{reason}。执行记录：{summary}"}

    blocked = db.scalars(
        select(Task).where(Task.project_id == project_id, Task.status == "blocked")
        .order_by(Task.priority.desc(), Task.created_at, Task.id).limit(8)
    ).all()
    if not blocked:
        return {"status": "answered", "reply": "当前项目没有处于阻塞状态的任务。"}
    return {
        "status": "needs_clarification",
        "reply": "当前项目有多个阻塞任务。请选择一个任务，我再解释它的失败原因。",
        "candidates": [{"id": task.id, "title": task.title, "status": task.status, "risk": task.risk} for task in blocked],
    }


def _is_project_status_query(text: str) -> bool:
    return text.strip().rstrip("。.!！?？") in {"查看项目状态和指标", "查看项目状态", "查看指标", "项目概况", "项目状态"}


def _is_blocked_query(text: str) -> bool:
    return text.strip().rstrip("。.!！?？") in {"为什么任务被阻塞", "为什么阻塞", "查看阻塞任务", "解释阻塞原因"}


def _plan_reply(plan: dict, mode: str) -> dict:
    labels = {"authorize_task": "授权执行", "review_task": "任务验收", "cancel_task": "取消任务", "route_task": "指派任务"}
    return {
        "status": "plan_ready",
        "reply": f"已生成「{labels.get(plan.get('action'), plan.get('action', '任务操作'))}」预览。此时尚未执行，请核对任务、参数和版本后再明确确认。",
        "focus_task_id": plan.get("task_id"),
        "plan": plan,
        "interpretation_mode": mode,
    }


@router.post("/command/chat")
def command_chat(body: CommandChatRequest, actor: Actor = Depends(authenticate),
                 db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, body.project_id, actor)

    if body.focus_task_id is not None:
        focused = db.scalar(select(Task.id).where(Task.id == body.focus_task_id, Task.project_id == body.project_id))
        if focused is None:
            raise HTTPException(404, "Focused task not found in selected project")

    if _is_project_status_query(body.text):
        return _project_status(body.project_id, db)
    if _is_blocked_query(body.text):
        return _blocked_answer(body.project_id, body.focus_task_id, db)

    if body.mode == "template":
        command_text = _focused_command(body.text, body.focus_task_id)
        if len(command_text.strip()) < 4:
            return {
                "status": "needs_clarification",
                "focus_task_id": body.focus_task_id,
                "reply": "请补充具体操作和任务名称，或先选择一个当前任务再使用简短指令。",
            }
        try:
            plan = interpret_command(Utterance(
                project_id=body.project_id, text=command_text
            ), actor, db)
        except HTTPException as exc:
            if exc.status_code in (404, 409, 422):
                return {
                    "status": "needs_clarification",
                    "focus_task_id": body.focus_task_id,
                    "reply": "我没有创建任何操作。请补充准确的任务名称或 ID；如果任务名称重复，请选择任务并使用当前任务上下文。",
                }
            raise
        return _plan_reply(plan, "restricted_zh_templates_v1")

    if len(body.text.strip()) < 4:
        return {
            "status": "needs_clarification",
            "focus_task_id": body.focus_task_id,
            "reply": "请补充更完整的操作指令；模型模式至少需要四个字符的请求。",
        }

    try:
        plan = interpret_model(ModelUtterance(
            project_id=body.project_id,
            text=body.text,
            focus_task_id=body.focus_task_id,
            history=body.history,
        ), actor, db)
    except HTTPException as exc:
        if exc.status_code in (422, 502):
            return {
                "status": "needs_clarification",
                "focus_task_id": body.focus_task_id,
                "reply": "模型没有形成唯一且有效的操作建议，本次没有创建计划。请补充任务名称、任务 ID 或目标 Agent，或切换到规则模板模式。",
            }
        raise
    return _plan_reply(plan, "opt_in_model_proposal_v1")
