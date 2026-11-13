"""Fail-closed Chinese command templates; no LLM or execution authority."""
from __future__ import annotations
import re
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_session
from .governance import CommandDraft, create_command_plan
from .models import Agent, Membership, Task
from .security import Actor, authenticate, owner_only
from .services import Conflict, require_project

router = APIRouter(prefix="/api/v1", tags=["command-interpretation"])
_QUOTED = r'[「“\"]([^「」“”\"]{1,240})[」”\"]'
_REF = rf'(?:{_QUOTED}|([0-9a-fA-F]{{8}}-[0-9a-fA-F-]{{27,28}}))'
_RULES = [
    (re.compile(rf'^(?:请)?批准任务\s*{_REF}[。.]?$'), "authorize_task"),
    (re.compile(rf'^(?:请)?取消任务\s*{_REF}[。.]?$'), "cancel_task"),
    (re.compile(rf'^(?:请)?验收通过任务\s*{_REF}[。.]?$'), "pass"),
    (re.compile(rf'^(?:请)?验收驳回任务\s*{_REF}\s*原因[：:]\s*(.{1,3000})$'), "reject"),
    (re.compile(rf'^(?:请)?指派任务\s*{_REF}\s*给\s*{_REF}[。.]?$'), "route_task"),
]

class Utterance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    text: str = Field(min_length=4, max_length=3500)

def _resolve(db: Session, cls, project_id: str, text: str):
    val = text.strip()
    if not val:
        raise HTTPException(422, "Empty reference")
    try:
        identifier = str(uuid.UUID(val))
    except ValueError:
        identifier = None
    if cls is Task:
        q = select(Task).where(Task.project_id == project_id)
        col = Task.title
    else:
        q = (select(Agent).join(Membership, Membership.agent_id == Agent.id)
             .where(Membership.project_id == project_id,
                    Membership.role.in_(("worker", "manager", "reviewer")),
                    Agent.enabled.is_(True)))
        col = Agent.name
    q = q.where(cls.id == identifier) if identifier else q.where(col == val)
    found = db.scalars(q.limit(2)).all()
    if len(found) > 1:
        raise Conflict("Ambiguous reference; use its UUID")
    if not found:
        raise HTTPException(404, "Reference not found in selected project")
    return found[0]

@router.post("/command/interpret", status_code=201)
def interpret_command(body: Utterance, actor: Actor = Depends(authenticate),
                      db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, body.project_id, actor)
    for regex, intent in _RULES:
        match = regex.fullmatch(body.text.strip())
        if match is None:
            continue
        g = match.groups()
        task = _resolve(db, Task, body.project_id, g[0] or g[1])
        command = {"task_id": task.id}
        if intent == "pass":
            command.update(action="review_task", accepted=True)
        elif intent == "reject":
            reason = g[2].strip()
            if len(reason) < 3:
                raise HTTPException(422, "Meaningful rejection reason required")
            command.update(action="review_task", accepted=False, reason=reason)
        elif intent == "route_task":
            target = _resolve(db, Agent, body.project_id, g[2] or g[3])
            command.update(action="route_task", target_agent_id=target.id)
        else:
            command["action"] = intent
        # Reuse the exact versioned preview and owner-only confirm path.
        plan = create_command_plan(CommandDraft(**command), actor, db)
        return {**plan, "interpretation": {
            "mode": "restricted_zh_templates_v1", "llm_interpreted": False,
            "action": command["action"], "task_id": task.id,
            "requires_explicit_confirmation": True}}
    raise HTTPException(422, "Ambiguous/unsupported instruction; no operation created")
