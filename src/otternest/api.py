"""REST API backed by the same transactional domain services future MCP/A2A need."""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import A2ADelegation, Agent, AgentRuntime, Artifact, Decision, DecisionReview, DecisionDeliberation, DecisionRebuttal, Event, Goal, Membership, Memory, Project, Task, TaskDependency, now
from .security import Actor, authenticate, hash_token, new_agent_token, owner_only
from .services import Conflict, claim, dependency_ids, dependencies_completed, emit, enforce_project_task_total_budget, ensure_lease, has_unreconciled_side_effects, release_dependents, require_project, require_task, task_ready_status, update_dependencies, validate_goal, choose_dispatch_targets

router = APIRouter(prefix="/api/v1")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=3000)


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    success_criteria: str = Field(min_length=1, max_length=5000)


def valid_capabilities(values: list[str]) -> list[str]:
    import re
    if len(set(values)) != len(values) or any(not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", v) for v in values):
        raise ValueError("Capabilities must be unique lower-case identifiers")
    return values


class CapabilityList(BaseModel):
    capabilities: list[str] = Field(default_factory=list, max_length=32)

    _validate_capabilities = field_validator("capabilities")(valid_capabilities)


class AgentIn(CapabilityList):
    name: str = Field(min_length=1, max_length=120)
    platform: str = Field(min_length=1, max_length=80)
    execution_mode: Literal["manual", "polling", "remote"] = "manual"


class RuntimeIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    environment: str = Field(min_length=1, max_length=160)


class RuntimeHeartbeat(BaseModel):
    status: Literal["idle", "busy", "stopped"] = "idle"


class ScheduleIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    heartbeat_ttl_seconds: int = Field(default=180, ge=30, le=900)


class MemberIn(BaseModel):
    agent_id: str
    role: Literal["worker", "manager", "reviewer", "advisor"] = "worker"


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    instruction: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(min_length=1, max_length=6000)
    goal_id: str | None = None
    risk: Literal["R0", "R1", "R2", "R3"] = "R1"
    priority: int = Field(default=50, ge=0, le=100)
    dependency_ids: list[str] = Field(default_factory=list, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)

    _validate_required = field_validator("required_capabilities")(valid_capabilities)


class DependencyIn(BaseModel):
    predecessor_id: str


class TargetIn(BaseModel):
    agent_id: str | None = None


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
    agent = Agent(name=body.name, platform=body.platform, execution_mode=body.execution_mode,
                  declared_capabilities=body.capabilities, verified_capabilities=[], token_hash=hash_token(credential))
    db.add(agent)
    db.flush()
    emit(db, actor, "agent.registered", agent.id)
    db.commit()
    # The token is only returned at creation time; never included in list responses.
    return {**as_dict(agent, "id", "name", "platform", "execution_mode", "enabled", "declared_capabilities", "verified_capabilities"), "token": credential}


@router.get("/agents")
def agents(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    return [as_dict(a, "id", "name", "platform", "execution_mode", "enabled", "declared_capabilities", "verified_capabilities") for a in db.scalars(select(Agent))]


@router.put("/agents/{agent_id}/verified-capabilities")
def verify_agent_capabilities(agent_id: str, body: CapabilityList, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Owner verifies evidence outside this endpoint; an agent cannot certify itself."""
    owner_only(actor)
    agent = db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if not set(body.capabilities).issubset(set(agent.declared_capabilities)):
        raise HTTPException(422, "Only declared capabilities may be verified")
    agent.verified_capabilities = body.capabilities
    emit(db, actor, "agent.capabilities_verified", agent.id, payload={"capabilities": body.capabilities})
    db.commit()
    return {"id": agent.id, "verified_capabilities": agent.verified_capabilities}


@router.post("/agents/{agent_id}/runtimes", status_code=201)
def register_runtime(agent_id: str, body: RuntimeIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    agent = db.get(Agent, agent_id)
    if agent is None or not agent.enabled:
        raise HTTPException(404, "Agent not available")
    existing = db.scalar(select(AgentRuntime.id).where(AgentRuntime.agent_id == agent_id, AgentRuntime.label == body.label))
    if existing:
        raise Conflict("Runtime label already registered")
    runtime = AgentRuntime(agent_id=agent_id, label=body.label, environment=body.environment)
    db.add(runtime)
    db.flush()
    emit(db, actor, "agent.runtime_registered", runtime.id, payload={"agent_id": agent_id})
    db.commit()
    return as_dict(runtime, "id", "agent_id", "label", "environment", "enabled", "status", "last_seen")


@router.get("/agents/{agent_id}/runtimes")
def list_runtimes(agent_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    if db.get(Agent, agent_id) is None:
        raise HTTPException(404, "Agent not found")
    cutoff = now() - timedelta(seconds=180)
    result = []
    for r in db.scalars(select(AgentRuntime).where(AgentRuntime.agent_id == agent_id)):
        last_seen = r.last_seen
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        record = as_dict(r, "id", "agent_id", "label", "environment", "enabled", "status", "last_seen")
        record["online"] = bool(r.enabled and last_seen and last_seen >= cutoff and r.status in {"idle", "busy"})
        result.append(record)
    return result


@router.post("/agent/runtimes/{runtime_id}/heartbeat")
def runtime_heartbeat(runtime_id: str, body: RuntimeHeartbeat, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    if actor.is_owner:
        raise HTTPException(403, "Agent credential required")
    runtime = db.scalar(select(AgentRuntime).where(AgentRuntime.id == runtime_id, AgentRuntime.agent_id == actor.id).with_for_update())
    if runtime is None or not runtime.enabled:
        raise HTTPException(404, "Runtime not found")
    runtime.status = body.status
    runtime.last_seen = now()
    # Frequent self-reported status updates are deliberately not entered into the durable outbox.
    db.commit()
    return {"id": runtime.id, "status": runtime.status, "last_seen": runtime.last_seen}


@router.post("/agents/{agent_id}/runtimes/{runtime_id}/disable")
def disable_runtime(agent_id: str, runtime_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    runtime = db.scalar(select(AgentRuntime).where(AgentRuntime.id == runtime_id, AgentRuntime.agent_id == agent_id).with_for_update())
    if runtime is None:
        raise HTTPException(404, "Runtime not found")
    runtime.enabled = False
    emit(db, actor, "agent.runtime_disabled", runtime.id, payload={"agent_id": agent_id})
    db.commit()
    return {"id": runtime.id, "enabled": False}


@router.post("/projects/{project_id}/schedule")
def schedule_project(project_id: str, body: ScheduleIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Route eligible work only; no external process is launched or falsely marked running."""
    owner_only(actor)
    result = choose_dispatch_targets(db, project_id, actor, body.limit, body.heartbeat_ttl_seconds)
    db.commit()
    return {"project_id": project_id, "routes": result, "count": len(result)}


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
    # Lock the project row so concurrent owner DAG edits validate against a stable graph.
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    enforce_project_task_total_budget(db, project_id)
    predecessor_ids = list(dict.fromkeys(body.dependency_ids))
    if len(predecessor_ids) != len(body.dependency_ids):
        raise HTTPException(422, "Duplicate dependency IDs")
    if predecessor_ids:
        found = db.scalars(select(Task.id).where(Task.id.in_(predecessor_ids), Task.project_id == project_id)).all()
        if len(found) != len(predecessor_ids):
            raise HTTPException(422, "All dependencies must belong to this project")
    task = Task(project_id=project_id, goal_id=body.goal_id, title=body.title,
                instruction=body.instruction, acceptance=body.acceptance,
                priority=body.priority, risk=body.risk, required_capabilities=body.required_capabilities)
    db.add(task)
    db.flush()
    for predecessor_id in predecessor_ids:
        db.add(TaskDependency(task_id=task.id, depends_on_id=predecessor_id))
    db.flush()
    task.status = task_ready_status(db, task)
    emit(db, actor, "task.created", task.id, project_id,
         {"risk": task.risk, "dependency_ids": predecessor_ids, "priority": task.priority, "required_capabilities": task.required_capabilities})
    db.commit()
    return task_public(task)


def task_public(t: Task) -> dict:
    return as_dict(t, "id", "project_id", "goal_id", "title", "instruction", "acceptance", "risk", "status", "assigned_agent_id", "lease_epoch", "lease_until", "version", "result_summary", "priority", "target_agent_id", "approval_granted", "required_capabilities")


@router.get("/projects/{project_id}/tasks")
def tasks(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    return [task_public(t) for t in db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc()))]


@router.get("/projects/{project_id}/task-graph")
def task_graph(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """One snapshot of the project DAG for the Web UI and automation tools."""
    require_project(db, project_id, actor)
    tasks = db.scalars(select(Task).where(Task.project_id == project_id)
                       .order_by(Task.priority.desc(), Task.created_at, Task.id)).all()
    ids = [t.id for t in tasks]
    edges = db.scalars(select(TaskDependency).where(TaskDependency.task_id.in_(ids))).all() if ids else []
    return {"tasks": [task_public(t) for t in tasks],
            "dependencies": [{"task_id": d.task_id, "depends_on_id": d.depends_on_id} for d in edges]}


@router.get("/tasks/{task_id}")
def task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    return task_public(require_task(db, task_id, actor))


@router.get("/tasks/{task_id}/dependencies")
def get_dependencies(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    row = require_task(db, task_id, actor)
    return {"task_id": task_id, "depends_on": dependency_ids(db, row.id),
            "satisfied": dependencies_completed(db, row.id)}


@router.post("/tasks/{task_id}/dependencies", status_code=201)
def add_dependency(task_id: str, body: DependencyIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    update_dependencies(db, row, body.predecessor_id, actor)
    db.commit()
    return {"task": task_public(row), "depends_on": dependency_ids(db, row.id)}


@router.delete("/tasks/{task_id}/dependencies/{predecessor_id}")
def remove_dependency(task_id: str, predecessor_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    update_dependencies(db, row, predecessor_id, actor, remove=True)
    db.commit()
    return {"task": task_public(row), "depends_on": dependency_ids(db, row.id)}


@router.put("/tasks/{task_id}/target")
def target_task(task_id: str, body: TargetIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Owner routing is a reservation of intent, never a claim of remote execution."""
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status not in {"ready", "waiting_dependencies", "waiting_approval"}:
        raise Conflict("Cannot reassign an active or terminal task")
    if body.agent_id:
        member = db.scalar(select(Membership).join(Agent, Membership.agent_id == Agent.id).where(
            Membership.project_id == row.project_id, Membership.agent_id == body.agent_id,
            Membership.role.in_(["worker", "manager", "reviewer"]), Agent.enabled.is_(True)))
        if member is None:
            raise HTTPException(422, "Target must be an enabled project worker")
        agent = db.get(Agent, body.agent_id)
        if not set(row.required_capabilities).issubset(set(agent.verified_capabilities)):
            raise HTTPException(422, "Target lacks verified task capabilities")
    row.target_agent_id = body.agent_id
    row.version += 1
    emit(db, actor, "task.routed", row.id, row.project_id, {"target_agent_id": body.agent_id})
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/authorize")
def authorize_task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status != "waiting_approval":
        raise Conflict("Task is not waiting for approval")
    row.approval_granted = True
    row.status = task_ready_status(db, row)
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
    delegation = db.scalar(select(A2ADelegation).where(A2ADelegation.task_id == task_id))
    if delegation is not None and delegation.status != "released":
        raise Conflict("Release the terminal A2A delegation before retrying locally")
    if has_unreconciled_side_effects(db, task_id):
        raise Conflict("Reconcile pending or unknown external side effects before unblocking")
    row.approval_granted = False if row.risk == "R3" else row.approval_granted
    row.status = task_ready_status(db, row)
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
    if not body.accepted and row.risk == "R3":
        row.approval_granted = False
    delegation = db.scalar(select(A2ADelegation).where(A2ADelegation.task_id == task_id))
    if delegation is not None and not body.accepted:
        # Remote result cannot be implicitly re-dispatched using a stale mapping.
        delegation.status = "changes_requested"
        delegation.updated_at = now()
    row.status = "completed" if body.accepted else ("blocked" if delegation else task_ready_status(db, row))
    row.assigned_agent_id = None
    row.version += 1
    emit(db, actor, "task.accepted" if body.accepted else "task.changes_requested", task_id, row.project_id, {"reason": body.reason})
    if body.accepted:
        db.flush()
        release_dependents(db, row, actor)
    db.commit()
    return task_public(row)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    row = require_task(db, task_id, actor, lock=True)
    if row.status in {"completed", "cancelled"}:
        raise Conflict("Task is already terminal")
    external = db.scalar(select(A2ADelegation).where(A2ADelegation.task_id == task_id))
    if external and external.status not in {"canceled", "failed", "submitted"}:
        raise Conflict("External A2A delegation active or unknown; use the remote cancellation workflow")
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
    # Serialize with independent review submissions and the new /resolve path.
    d = db.scalar(select(Decision).where(Decision.id == decision_id).with_for_update())
    if not d:
        raise HTTPException(404, "Decision not found")
    if d.status != "proposed":
        raise Conflict("Decision is not proposed")
    from .governance import require_auto_review_quorum
    require_auto_review_quorum(db, d)
    state = db.get(DecisionDeliberation, d.id)
    if state is not None and state.status != "closed":
        raise Conflict("Second round must close before approval")
    dissent_one = db.scalar(select(DecisionReview.id).where(DecisionReview.decision_id == d.id,
                       DecisionReview.stance.in_(("oppose", "alternative"))))
    dissent_two = db.scalar(select(DecisionRebuttal.id).where(
        DecisionRebuttal.decision_id == d.id,
        DecisionRebuttal.stance.in_(("oppose", "alternative"))))
    if dissent_one or dissent_two:
        raise Conflict("Independent dissent requires owner resolution note; use /resolve")
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
