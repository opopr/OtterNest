"""REST API backed by the same transactional domain services future MCP/A2A need."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Agent, Artifact, Decision, Event, Goal, Membership, Memory, Project, Task, now
from .security import Actor, authenticate, hash_token, new_agent_token, owner_only
from .services import Conflict, claim, emit, ensure_lease, require_project, require_task, validate_goal

router = APIRouter(prefix="/api/v1")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=3000)


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    success_criteria: str = Field(min_length=1, max_length=5000)


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: str = Field(min_length=1, max_length=80)
    execution_mode: Literal["manual", "polling", "remote"] = "manual"


class MemberIn(BaseModel):
    agent_id: str
    role: Literal["worker", "manager", "reviewer", "advisor"] = "worker"


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    instruction: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(min_length=1, max_length=6000)
    goal_id: str | None = None
    risk: Literal["R0", "R1", "R2", "R3"] = "R1"


class LeaseIn(BaseModel):
    lease_seconds: int = Field(default=120, ge=30, le=900)


class EpochIn(BaseModel):
    epoch: int = Field(ge=1)


class ResultIn(EpochIn):
    summary: str = Field(min_length=1, max_length=20000)


class ReviewIn(BaseModel):
    accepted: bool
    reason: str = Field(default="", max_length=3000)


class MemoryIn(BaseModel):
    kind: Literal["fact", "decision", "experience", "skill", "procedure"] = "fact"
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=20000)
    source: str = Field(min_length=1, max_length=3000)


class DecisionIn(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    resolution: str = Field(min_length=1, max_length=6000)
    rationale: str = Field(min_length=1, max_length=6000)


def as_dict(item, *names: str) -> dict:
    return {name: getattr(item, name) for name in names}


@router.get("/me")
def me(actor: Actor = Depends(authenticate)):
    return as_dict(actor, "kind", "id")


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    project = Project(name=body.name, description=body.description)
    db.add(project)
    db.flush()
    emit(db, actor, "project.created", project.id, project.id)
    db.commit()
    return as_dict(project, "id", "name", "description", "status")


@router.get("/projects")
def projects(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    if actor.is_owner:
        rows = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    else:
        rows = db.scalars(select(Project).join(Membership, Project.id == Membership.project_id).where(Membership.agent_id == actor.id)).all()
    return [as_dict(p, "id", "name", "description", "status") for p in rows]


@router.post("/projects/{project_id}/goals", status_code=201)
def create_goal(project_id: str, body: GoalIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    goal = Goal(project_id=project_id, title=body.title, success_criteria=body.success_criteria)
    db.add(goal)
    db.flush()
    emit(db, actor, "goal.created", goal.id, project_id)
    db.commit()
    return as_dict(goal, "id", "project_id", "title", "status", "success_criteria")


@router.get("/projects/{project_id}/goals")
def goals(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    return [as_dict(g, "id", "title", "success_criteria", "status") for g in db.scalars(select(Goal).where(Goal.project_id == project_id))]


@router.post("/agents", status_code=201)
def register_agent(body: AgentIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    credential = new_agent_token()
    agent = Agent(name=body.name, platform=body.platform, execution_mode=body.execution_mode, token_hash=hash_token(credential))
    db.add(agent)
    db.flush()
    emit(db, actor, "agent.registered", agent.id)
    db.commit()
    # The token is only returned at creation time; never included in list responses.
    return {**as_dict(agent, "id", "name", "platform", "execution_mode", "enabled"), "token": credential}


@router.get("/agents")
def agents(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    return [as_dict(a, "id", "name", "platform", "execution_mode", "enabled") for a in db.scalars(select(Agent))]


@router.post("/agents/{agent_id}/disable")
def disable_agent(agent_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    agent.enabled = False
    emit(db, actor, "agent.disabled", agent.id)
    db.commit()
    return {"id": agent_id, "enabled": False}


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(project_id: str, body: MemberIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    agent = db.get(Agent, body.agent_id)
    if not agent or not agent.enabled:
        raise HTTPException(404, "Agent not available")
    existing = db.scalar(select(Membership).where(Membership.project_id == project_id, Membership.agent_id == body.agent_id))
    if existing:
        raise Conflict("Agent is already in the project")
    member = Membership(project_id=project_id, agent_id=body.agent_id, role=body.role)
    db.add(member)
    db.flush()
    emit(db, actor, "project.member_added", member.id, project_id, {"agent_id": body.agent_id, "role": body.role})
    db.commit()
    return as_dict(member, "id", "project_id", "agent_id", "role")


@router.get("/projects/{project_id}/members")
def members(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    query = select(Membership, Agent).join(Agent, Membership.agent_id == Agent.id).where(Membership.project_id == project_id)
    return [{"id": m.id, "agent_id": a.id, "agent_name": a.name, "role": m.role, "enabled": a.enabled} for m, a in db.execute(query)]


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, body: TaskIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    project = require_project(db, project_id, actor)
    if project.status != "active":
        raise Conflict("Project is not active")
    validate_goal(db, project_id, body.goal_id)
    task = Task(project_id=project_id, goal_id=body.goal_id, title=body.title, instruction=body.instruction,
                acceptance=body.acceptance, risk=body.risk, status="waiting_approval" if body.risk == "R3" else "ready")
    db.add(task)
    db.flush()
    emit(db, actor, "task.created", task.id, project_id, {"risk": task.risk})
    db.commit()
    return task_public(task)


def task_public(t: Task) -> dict:
    return as_dict(t, "id", "project_id", "goal_id", "title", "instruction", "acceptance", "risk", "status", "assigned_agent_id", "lease_epoch", "lease_until", "version", "result_summary")


@router.get("/projects/{project_id}/tasks")
def tasks(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    return [task_public(t) for t in db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc()))]


@router.get("/tasks/{task_id}")
def task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    return task_public(require_task(db, task_id, actor))


@router.post("/tasks/{task_id}/authorize")
def authorize_task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status != "waiting_approval":
        raise Conflict("Task is not waiting for approval")
    row.status = "ready"
    row.version += 1
    emit(db, actor, "task.authorized", task_id, row.project_id)
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: str, body: LeaseIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    return task_public(claim(db, task_id, actor, body.lease_seconds))


@router.post("/tasks/{task_id}/heartbeat")
def heartbeat(task_id: str, body: EpochIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    row = require_task(db, task_id, actor, lock=True)
    ensure_lease(row, actor, body.epoch)
    from datetime import timedelta
    row.lease_until = now() + timedelta(seconds=120)
    db.commit()
    return {"lease_until": row.lease_until, "epoch": row.lease_epoch}


@router.post("/tasks/{task_id}/submit")
def submit(task_id: str, body: ResultIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    row = require_task(db, task_id, actor, lock=True)
    ensure_lease(row, actor, body.epoch)
    row.result_summary = body.summary
    row.status = "submitted"
    row.lease_until = None
    row.version += 1
    emit(db, actor, "task.submitted", task_id, row.project_id)
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/fail")
def fail_task(task_id: str, body: ResultIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """A failed worker blocks the task; it is never silently re-run."""
    row = require_task(db, task_id, actor, lock=True)
    ensure_lease(row, actor, body.epoch)
    row.result_summary = body.summary
    row.status = "blocked"
    row.lease_until = None
    row.version += 1
    emit(db, actor, "task.blocked", task_id, row.project_id)
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/unblock")
def unblock_task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Owner explicitly decides whether a blocked task may be retried."""
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status != "blocked":
        raise Conflict("Task is not blocked")
    row.status = "waiting_approval" if row.risk == "R3" else "ready"
    row.assigned_agent_id = None
    row.lease_epoch += 1
    row.lease_until = None
    row.version += 1
    emit(db, actor, "task.unblocked", task_id, row.project_id)
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/review")
def review(task_id: str, body: ReviewIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status != "submitted":
        raise Conflict("Task has not been submitted")
    row.status = "completed" if body.accepted else "ready"
    row.assigned_agent_id = None
    row.version += 1
    emit(db, actor, "task.accepted" if body.accepted else "task.changes_requested", task_id, row.project_id, {"reason": body.reason})
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status in {"completed", "cancelled"}:
        raise Conflict("Task is already terminal")
    row.status = "cancelled"
    row.lease_epoch += 1  # fence old workers from writing to the central system
    row.lease_until = None
    row.version += 1
    emit(db, actor, "task.cancelled", task_id, row.project_id)
    db.commit()
    return task_public(row)


@router.post("/projects/{project_id}/memories", status_code=201)
def add_memory(project_id: str, body: MemoryIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    record = Memory(project_id=project_id, kind=body.kind, title=body.title, content=body.content, source=body.source)
    db.add(record)
    db.flush()
    emit(db, actor, "memory.proposed", record.id, project_id)
    db.commit()
    return as_dict(record, "id", "kind", "title", "content", "source", "status", "version")


@router.get("/projects/{project_id}/memories")
def memories(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    query = select(Memory).where(Memory.project_id == project_id)
    if not actor.is_owner:
        query = query.where(Memory.status == "published")
    return [as_dict(m, "id", "kind", "title", "content", "source", "status", "version") for m in db.scalars(query)]


@router.post("/memories/{memory_id}/publish")
def publish_memory(memory_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    record = db.get(Memory, memory_id)
    if not record:
        raise HTTPException(404, "Memory not found")
    if record.status != "proposed":
        raise Conflict("Memory is not proposed")
    record.status = "published"
    record.version += 1
    emit(db, actor, "memory.published", record.id, record.project_id)
    db.commit()
    return {"id": record.id, "status": record.status, "version": record.version}


@router.post("/projects/{project_id}/decisions", status_code=201)
def propose_decision(project_id: str, body: DecisionIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    record = Decision(project_id=project_id, question=body.question, resolution=body.resolution, rationale=body.rationale)
    db.add(record)
    db.flush()
    emit(db, actor, "decision.proposed", record.id, project_id)
    db.commit()
    return as_dict(record, "id", "question", "resolution", "rationale", "status")


@router.get("/projects/{project_id}/decisions")
def decisions(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    query = select(Decision).where(Decision.project_id == project_id)
    if not actor.is_owner:
        query = query.where(Decision.status == "approved")
    return [as_dict(d, "id", "question", "resolution", "rationale", "status") for d in db.scalars(query)]


@router.post("/decisions/{decision_id}/approve")
def approve_decision(decision_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    d = db.get(Decision, decision_id)
    if not d:
        raise HTTPException(404, "Decision not found")
    if d.status != "proposed":
        raise Conflict("Decision is not proposed")
    d.status = "approved"
    emit(db, actor, "decision.approved", d.id, d.project_id)
    db.commit()
    return {"id": d.id, "status": d.status}


@router.get("/projects/{project_id}/events")
def events(project_id: str, after: int = 0, limit: int = 100, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    if limit < 1 or limit > 500:
        raise HTTPException(422, "limit must be 1..500")
    items = db.scalars(select(Event).where(Event.project_id == project_id, Event.id > after).order_by(Event.id).limit(limit)).all()
    return [as_dict(e, "id", "kind", "subject_id", "payload", "created_at") for e in items]


@router.post("/tasks/{task_id}/artifacts", status_code=201)
async def upload_artifact(task_id: str, request: Request, epoch: int | None = None, filename: str = Header(default="artifact.bin", alias="X-Artifact-Name"), actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    row = require_task(db, task_id, actor, lock=True)
    if not actor.is_owner:
        if epoch is None:
            raise HTTPException(422, "epoch required for agent upload")
        ensure_lease(row, actor, epoch)
    if len(filename) > 240 or not filename or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "Invalid artifact filename")
    max_size = 5 * 1024 * 1024  # bounded until object storage policy is designed
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_size:
            raise HTTPException(413, "Artifact exceeds 5 MiB")
        chunks.append(chunk)
    content = b"".join(chunks)
    checksum = hashlib.sha256(content).hexdigest()
    record = Artifact(project_id=row.project_id, task_id=task_id, name=filename, checksum=checksum, uri="")
    db.add(record)
    db.flush()
    base = Path(os.environ.get("OTTERNEST_ARTIFACT_DIR", "./var/artifacts")).resolve()
    dest = base / row.project_id / record.id
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(dest, "xb") as f:
        f.write(content)
    record.uri = str(dest)
    emit(db, actor, "artifact.submitted", record.id, row.project_id, {"task_id": task_id, "sha256": checksum})
    db.commit()
    return as_dict(record, "id", "project_id", "task_id", "name", "checksum")


@router.get("/tasks/{task_id}/artifacts")
def artifacts(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_task(db, task_id, actor)
    return [as_dict(a, "id", "name", "checksum", "created_at") for a in db.scalars(select(Artifact).where(Artifact.task_id == task_id))]
