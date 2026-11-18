"""REST API backed by the same transactional domain services future MCP/A2A need."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from datetime import timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import A2ADelegation, Agent, AgentRuntime, Artifact, Decision, DecisionReview, DecisionDeliberation, DecisionRebuttal, Event, Goal, Membership, Memory, Notification, Project, Task, TaskDependency, new_id, now
from .security import Actor, SENSITIVITY_ORDER, authenticate, hash_token, new_agent_token, new_runtime_token, owner_only, sensitivity_allowed
from .services import Conflict, claim, dependency_ids, dependencies_completed, effective_sensitivity, emit, enforce_project_task_total_budget, ensure_lease, has_unreconciled_side_effects, release_dependents, require_project, require_task, require_writable_system, task_ready_status, update_dependencies, validate_goal, choose_dispatch_targets

router = APIRouter(prefix="/api/v1")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=3000)
    priority: int = Field(default=50, ge=0, le=100)


class ProjectUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=3000)
    priority: int | None = Field(default=None, ge=0, le=100)


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    success_criteria: str = Field(min_length=1, max_length=5000)
    priority: int = Field(default=50, ge=0, le=100)


class GoalUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    success_criteria: str | None = Field(default=None, min_length=1, max_length=5000)
    priority: int | None = Field(default=None, ge=0, le=100)


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
    health: dict = Field(default_factory=dict)


class ScheduleIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    heartbeat_ttl_seconds: int = Field(default=180, ge=30, le=900)


class MemberIn(BaseModel):
    agent_id: str
    role: Literal["worker", "manager", "reviewer", "advisor"] = "worker"
    max_sensitivity: Literal["public", "internal", "confidential", "restricted"] = "confidential"
    expires_in_hours: int | None = Field(default=None, ge=1, le=8760)


class MemberUpdateIn(BaseModel):
    role: Literal["worker", "manager", "reviewer", "advisor"] | None = None
    max_sensitivity: Literal["public", "internal", "confidential", "restricted"] | None = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    instruction: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(min_length=1, max_length=6000)
    goal_id: str | None = None
    risk: Literal["R0", "R1", "R2", "R3"] = "R1"
    priority: int = Field(default=50, ge=0, le=100)
    sensitivity: Literal["public", "internal", "confidential", "restricted"] = "internal"
    dependency_ids: list[str] = Field(default_factory=list, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)

    _validate_required = field_validator("required_capabilities")(valid_capabilities)


class TaskUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    instruction: str | None = Field(default=None, min_length=1, max_length=12000)
    acceptance: str | None = Field(default=None, min_length=1, max_length=6000)
    risk: Literal["R0", "R1", "R2", "R3"] | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    sensitivity: Literal["public", "internal", "confidential", "restricted"] | None = None
    required_capabilities: list[str] | None = Field(default=None, max_length=32)

    @field_validator("required_capabilities")
    @classmethod
    def _validate_optional_caps(cls, v: list[str] | None) -> list[str] | None:
        return valid_capabilities(v) if v is not None else None


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


class FailIn(ResultIn):
    failure_reason: Literal[
        "context_stale",
        "dependency_missing",
        "model_error",
        "external_service_failure",
        "acceptance_issue",
        "policy_blocked",
        "unknown",
    ] = "unknown"


class ReviewIn(BaseModel):
    accepted: bool
    reason: str = Field(default="", max_length=3000)


class ArtifactReviewIn(BaseModel):
    accepted: bool
    note: str = Field(default="", max_length=3000)


class MemoryIn(BaseModel):
    kind: Literal["fact", "decision", "experience", "skill", "procedure"] = "fact"
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=20000)
    source: str = Field(min_length=1, max_length=3000)
    sensitivity: Literal["public", "internal", "confidential", "restricted"] = "internal"


class DecisionIn(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    resolution: str = Field(min_length=1, max_length=6000)
    rationale: str = Field(min_length=1, max_length=6000)


def as_dict(item, *names: str) -> dict:
    return {name: getattr(item, name) for name in names}


def runtime_public(runtime: AgentRuntime, agent: Agent | None = None, *, cutoff=None) -> dict:
    """Serialize a Runtime snapshot using the same server-side heartbeat rule everywhere."""
    cutoff = cutoff or (now() - timedelta(seconds=180))
    last_seen = runtime.last_seen
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    record = as_dict(runtime, "id", "agent_id", "label", "environment", "enabled", "status", "last_health", "last_seen")
    record["has_token"] = bool(runtime.token_hash)
    record["online"] = bool(runtime.enabled and last_seen and last_seen >= cutoff and runtime.status in {"idle", "busy"})
    if agent is not None:
        record.update({
            "agent_name": agent.name,
            "agent_platform": agent.platform,
            "agent_enabled": agent.enabled,
        })
    return record


@router.get("/me")
def me(actor: Actor = Depends(authenticate)):
    return as_dict(actor, "kind", "id")


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    project = Project(name=body.name, description=body.description, priority=body.priority)
    db.add(project)
    db.flush()
    emit(db, actor, "project.created", project.id, project.id)
    db.commit()
    return as_dict(project, "id", "name", "description", "status", "priority")


@router.get("/projects")
def projects(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    if actor.is_owner:
        rows = db.scalars(select(Project).order_by(Project.priority.desc(), Project.created_at.desc())).all()
    else:
        rows = db.scalars(select(Project).join(Membership, Project.id == Membership.project_id).where(Membership.agent_id == actor.id)).all()
    return [as_dict(p, "id", "name", "description", "status", "priority") for p in rows]


@router.post("/projects/{project_id}/goals", status_code=201)
def create_goal(project_id: str, body: GoalIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    require_project(db, project_id, actor)
    goal = Goal(project_id=project_id, title=body.title, success_criteria=body.success_criteria, priority=body.priority)
    db.add(goal)
    db.flush()
    emit(db, actor, "goal.created", goal.id, project_id)
    db.commit()
    return as_dict(goal, "id", "project_id", "title", "status", "priority", "success_criteria")


@router.get("/projects/{project_id}/goals")
def goals(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    return [as_dict(g, "id", "title", "success_criteria", "priority", "status") for g in db.scalars(select(Goal).where(Goal.project_id == project_id).order_by(Goal.priority.desc()))]


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    project = require_project(db, project_id, actor)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.priority is not None:
        project.priority = body.priority
    emit(db, actor, "project.updated", project_id, project_id)
    db.commit()
    return as_dict(project, "id", "name", "description", "status", "priority")


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    project = require_project(db, project_id, actor)
    running_task = db.scalar(select(Task.id).where(Task.project_id == project_id, Task.status == "running"))
    if running_task:
        raise Conflict("Cannot delete project while it has running tasks")

    from .models import (
        A2AInboxMessage, A2ARemoteEndpoint, CommandPlan, CrossProjectShare, DecisionAutoPolicy,
        Experiment, GoalDependency, GoalPlanDraft, ProjectCharter, Skill, SkillEvaluation,
        TaskCheckpoint, TaskGrant, TaskSideEffect, WebhookDelivery, WebhookEndpoint,
        WorkflowCursor, WorkflowRule, WorkflowRun,
    )
    task_ids = [t.id for t in db.scalars(select(Task).where(Task.project_id == project_id))]
    if task_ids:
        for dep in db.scalars(select(TaskDependency).where((TaskDependency.task_id.in_(task_ids)) | (TaskDependency.depends_on_id.in_(task_ids)))):
            db.delete(dep)
    goal_ids = [g.id for g in db.scalars(select(Goal).where(Goal.project_id == project_id))]
    if goal_ids:
        for gd in db.scalars(select(GoalDependency).where((GoalDependency.goal_id.in_(goal_ids)) | (GoalDependency.depends_on_id.in_(goal_ids)))):
            db.delete(gd)
    webhook_ids = [w.id for w in db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.project_id == project_id))]
    if webhook_ids:
        for wd in db.scalars(select(WebhookDelivery).where(WebhookDelivery.endpoint_id.in_(webhook_ids))):
            db.delete(wd)
    rule_ids = [r.id for r in db.scalars(select(WorkflowRule).where(WorkflowRule.project_id == project_id))]
    if rule_ids:
        for wc in db.scalars(select(WorkflowCursor).where(WorkflowCursor.rule_id.in_(rule_ids))):
            db.delete(wc)
    artifact_uris = [
        a.uri for a in db.scalars(select(Artifact).where(Artifact.project_id == project_id)) if a.uri
    ]
    for model in (
        WorkflowRun, WorkflowRule,
        SkillEvaluation, Skill, TaskCheckpoint, TaskSideEffect, TaskGrant, CommandPlan,
        A2AInboxMessage, A2ADelegation, A2ARemoteEndpoint, Artifact, Task, Goal, GoalPlanDraft,
        Membership, Memory, DecisionReview, DecisionDeliberation, DecisionRebuttal, DecisionAutoPolicy, Decision,
        ProjectCharter, WebhookEndpoint, Experiment, Notification,
    ):
        for row in db.scalars(select(model).where(model.project_id == project_id)):
            db.delete(row)
    for share in db.scalars(select(CrossProjectShare).where((CrossProjectShare.source_project_id == project_id) | (CrossProjectShare.target_project_id == project_id))):
        db.delete(share)
    for ev in db.scalars(select(Event).where(Event.project_id == project_id)):
        ev.project_id = None
    db.delete(project)
    emit(db, actor, "project.deleted", project_id)
    db.commit()
    if artifact_uris:
        from .storage import get_storage_backend
        backend = get_storage_backend()
        for uri in artifact_uris:
            try:
                backend.delete(uri)
            except Exception:
                pass
    return {"id": project_id, "deleted": True}


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: str, body: GoalUpdateIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    goal = db.scalar(select(Goal).where(Goal.id == goal_id).with_for_update())
    if not goal:
        raise HTTPException(404, "Goal not found")
    if body.title is not None:
        goal.title = body.title
    if body.success_criteria is not None:
        goal.success_criteria = body.success_criteria
    if body.priority is not None:
        goal.priority = body.priority
    emit(db, actor, "goal.updated", goal.id, goal.project_id)
    db.commit()
    return as_dict(goal, "id", "project_id", "title", "success_criteria", "priority", "status")


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    goal = db.scalar(select(Goal).where(Goal.id == goal_id).with_for_update())
    if not goal:
        raise HTTPException(404, "Goal not found")
    running_task = db.scalar(select(Task.id).where(Task.goal_id == goal_id, Task.status == "running"))
    if running_task:
        raise Conflict("Cannot delete goal while it has running tasks")
    from .models import GoalDependency
    for gd in db.scalars(select(GoalDependency).where((GoalDependency.goal_id == goal_id) | (GoalDependency.depends_on_id == goal_id))):
        db.delete(gd)
    for t in db.scalars(select(Task).where(Task.goal_id == goal_id)):
        t.goal_id = None
    pid = goal.project_id
    db.delete(goal)
    emit(db, actor, "goal.deleted", goal_id, pid)
    db.commit()
    return {"id": goal_id, "deleted": True}


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
    return {**as_dict(agent, "id", "name", "platform", "execution_mode", "enabled", "declared_capabilities", "verified_capabilities", "deleted_at"), "token": credential}


@router.get("/agents")
def agents(include_deleted: bool = False, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    query = select(Agent)
    if not include_deleted:
        query = query.where(Agent.deleted_at.is_(None))
    return [as_dict(a, "id", "name", "platform", "execution_mode", "enabled", "declared_capabilities", "verified_capabilities", "deleted_at") for a in db.scalars(query)]


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
    return [
        runtime_public(runtime, cutoff=cutoff)
        for runtime in db.scalars(select(AgentRuntime).where(AgentRuntime.agent_id == agent_id))
    ]


@router.get("/runtimes")
def list_all_runtimes(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Return one owner-scoped snapshot for the global Runtime monitor."""
    owner_only(actor)
    cutoff = now() - timedelta(seconds=180)
    rows = db.execute(
        select(AgentRuntime, Agent)
        .join(Agent, Agent.id == AgentRuntime.agent_id)
        .order_by(Agent.name, AgentRuntime.label)
    ).all()
    return [runtime_public(runtime, agent, cutoff=cutoff) for runtime, agent in rows]


@router.post("/agents/{agent_id}/runtimes/{runtime_id}/token")
def issue_runtime_token(agent_id: str, runtime_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Issue or rotate an independent credential dedicated to one AgentRuntime instance."""
    owner_only(actor)
    runtime = db.scalar(select(AgentRuntime).where(AgentRuntime.id == runtime_id, AgentRuntime.agent_id == agent_id).with_for_update())
    if runtime is None or not runtime.enabled:
        raise HTTPException(404, "Runtime not found")
    token = new_runtime_token()
    runtime.token_hash = hash_token(token)
    emit(db, actor, "agent.runtime_token_issued", runtime.id, payload={"agent_id": agent_id})
    db.commit()
    return {"id": runtime.id, "agent_id": agent_id, "token": token}


@router.post("/agent/runtimes/{runtime_id}/heartbeat")
def runtime_heartbeat(runtime_id: str, body: RuntimeHeartbeat, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    if actor.is_owner:
        raise HTTPException(403, "Agent or runtime credential required")
    if actor.runtime_id is not None and actor.runtime_id != runtime_id:
        raise HTTPException(403, "Runtime credential does not match target runtime")
    runtime = db.scalar(select(AgentRuntime).where(AgentRuntime.id == runtime_id, AgentRuntime.agent_id == actor.id).with_for_update())
    if runtime is None or not runtime.enabled:
        raise HTTPException(404, "Runtime not found")
    runtime.status = body.status
    if body.health:
        runtime.last_health = {str(k)[:64]: str(v)[:240] for k, v in list(body.health.items())[:16]}
    runtime.last_seen = now()
    # Frequent self-reported status updates are deliberately not entered into the durable outbox.
    db.commit()
    return {"id": runtime.id, "status": runtime.status, "last_seen": runtime.last_seen, "last_health": runtime.last_health}


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
    if not agent or agent.deleted_at is not None:
        raise HTTPException(404, "Agent not found")
    agent.enabled = False
    emit(db, actor, "agent.disabled", agent.id)
    db.commit()
    return {"id": agent_id, "enabled": False}


@router.post("/agents/{agent_id}/enable")
def enable_agent(agent_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    agent = db.get(Agent, agent_id)
    if not agent or agent.deleted_at is not None:
        raise HTTPException(404, "Agent not found or is in recycle bin")
    agent.enabled = True
    emit(db, actor, "agent.enabled", agent.id)
    db.commit()
    return {"id": agent_id, "enabled": True}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Soft-delete an agent into the recycle bin."""
    owner_only(actor)
    agent = db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.deleted_at is not None:
        raise Conflict("Agent is already in the recycle bin")
    running_task = db.scalar(
        select(Task).where(Task.assigned_agent_id == agent_id, Task.status == "running")
    )
    if running_task:
        raise Conflict("Cannot delete agent while it holds an active running task")
    agent.enabled = False
    agent.deleted_at = now()
    for membership in db.scalars(select(Membership).where(Membership.agent_id == agent_id)):
        membership.enabled = False
    emit(db, actor, "agent.deleted", agent.id)
    db.commit()
    return {"id": agent_id, "deleted_at": agent.deleted_at.isoformat(), "enabled": False}


@router.post("/agents/{agent_id}/restore")
def restore_agent(agent_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Restore a soft-deleted agent from the recycle bin."""
    owner_only(actor)
    agent = db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.deleted_at is None:
        raise Conflict("Agent is not in the recycle bin")
    agent.deleted_at = None
    agent.enabled = True
    emit(db, actor, "agent.restored", agent.id)
    db.commit()
    return as_dict(agent, "id", "name", "platform", "execution_mode", "enabled", "declared_capabilities", "verified_capabilities", "deleted_at")


@router.delete("/agents/{agent_id}/permanent")
def permanent_delete_agent(agent_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Permanently purge an agent from the recycle bin if no historical tasks/records depend on it."""
    owner_only(actor)
    agent = db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent.deleted_at is None:
        raise Conflict("Move agent to recycle bin before permanent deletion")
    has_task = db.scalar(
        select(Task.id).where((Task.assigned_agent_id == agent_id) | (Task.target_agent_id == agent_id)).limit(1)
    )
    if has_task:
        raise Conflict("Agent has historical task references and cannot be permanently purged; keep it in recycle bin")
    for rt in db.scalars(select(AgentRuntime).where(AgentRuntime.agent_id == agent_id)):
        db.delete(rt)
    for m in db.scalars(select(Membership).where(Membership.agent_id == agent_id)):
        db.delete(m)
    try:
        db.delete(agent)
        db.flush()
    except Exception:
        db.rollback()
        raise Conflict("Agent has historical references and cannot be permanently purged") from None
    emit(db, actor, "agent.permanently_deleted", agent_id)
    db.commit()
    return {"id": agent_id, "permanently_deleted": True}


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(project_id: str, body: MemberIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    agent = db.get(Agent, body.agent_id)
    if not agent or not agent.enabled or agent.deleted_at is not None:
        raise HTTPException(404, "Agent not available")
    existing = db.scalar(select(Membership).where(Membership.project_id == project_id, Membership.agent_id == body.agent_id))
    if existing:
        raise Conflict("Agent is already in the project")
    expires_at = now() + timedelta(hours=body.expires_in_hours) if body.expires_in_hours else None
    member = Membership(
        project_id=project_id,
        agent_id=body.agent_id,
        role=body.role,
        max_sensitivity=body.max_sensitivity,
        expires_at=expires_at,
    )
    db.add(member)
    db.flush()
    emit(db, actor, "project.member_added", member.id, project_id, {"agent_id": body.agent_id, "role": body.role, "max_sensitivity": body.max_sensitivity})
    db.commit()
    return as_dict(member, "id", "project_id", "agent_id", "role", "max_sensitivity", "expires_at")


@router.get("/projects/{project_id}/members")
def members(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    query = select(Membership, Agent).join(Agent, Membership.agent_id == Agent.id).where(Membership.project_id == project_id)
    return [{"id": m.id, "agent_id": a.id, "agent_name": a.name, "role": m.role, "enabled": a.enabled and a.deleted_at is None, "max_sensitivity": m.max_sensitivity, "expires_at": m.expires_at} for m, a in db.execute(query)]


@router.patch("/projects/{project_id}/members/{agent_id}")
def update_member(
    project_id: str,
    agent_id: str,
    body: MemberUpdateIn,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    owner_only(actor)
    require_project(db, project_id, actor)
    member = db.scalar(
        select(Membership).where(Membership.project_id == project_id, Membership.agent_id == agent_id).with_for_update()
    )
    if not member:
        raise HTTPException(404, "Project member not found")
    if body.role is not None:
        member.role = body.role
    if body.max_sensitivity is not None:
        member.max_sensitivity = body.max_sensitivity
    emit(db, actor, "project.member_updated", member.id, project_id, {"agent_id": agent_id, "role": member.role, "max_sensitivity": member.max_sensitivity})
    db.commit()
    return as_dict(member, "id", "project_id", "agent_id", "role", "max_sensitivity", "expires_at")


@router.delete("/projects/{project_id}/members/{agent_id}")
def remove_member(
    project_id: str,
    agent_id: str,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    owner_only(actor)
    require_project(db, project_id, actor)
    member = db.scalar(
        select(Membership).where(Membership.project_id == project_id, Membership.agent_id == agent_id).with_for_update()
    )
    if not member:
        raise HTTPException(404, "Project member not found")
    running_task = db.scalar(
        select(Task.id).where(Task.project_id == project_id, Task.assigned_agent_id == agent_id, Task.status == "running")
    )
    if running_task:
        raise Conflict("Cannot remove member while it holds an active running task in this project")
    member_id = member.id
    db.delete(member)
    emit(db, actor, "project.member_removed", member_id, project_id, {"agent_id": agent_id})
    db.commit()
    return {"project_id": project_id, "agent_id": agent_id, "removed": True}


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, body: TaskIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
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
                priority=body.priority, risk=body.risk, sensitivity=body.sensitivity,
                required_capabilities=body.required_capabilities)
    db.add(task)
    db.flush()
    for predecessor_id in predecessor_ids:
        db.add(TaskDependency(task_id=task.id, depends_on_id=predecessor_id))
    db.flush()
    task.status = task_ready_status(db, task)
    if task.risk == "R3" and not task.approval_granted:
        db.add(Notification(
            project_id=project_id,
            level="urgent",
            title=f"R3 高风险任务待审批：{task.title}",
            body=f"任务 {task.id} 需要系统所有者明确审批后才可领取。",
            source_kind="task",
            source_id=task.id,
        ))
    emit(db, actor, "task.created", task.id, project_id,
         {"risk": task.risk, "dependency_ids": predecessor_ids, "priority": task.priority,
          "sensitivity": task.sensitivity, "correlation_id": task.correlation_id, "required_capabilities": task.required_capabilities})
    db.commit()
    return task_public(task)


def task_public(t: Task) -> dict:
    return as_dict(t, "id", "project_id", "goal_id", "title", "instruction", "acceptance", "risk", "status", "assigned_agent_id", "lease_epoch", "lease_until", "version", "result_summary", "priority", "sensitivity", "correlation_id", "failure_reason", "rework_count", "completed_at", "target_agent_id", "approval_granted", "required_capabilities")


def _read_artifact_content(artifact: Artifact) -> bytes:
    if artifact.deleted_at is not None:
        raise HTTPException(410, "Artifact content was garbage-collected")
    if artifact.uri.startswith("s3://"):
        from .storage import StorageError, get_storage_backend
        try:
            content = get_storage_backend().load(artifact.uri)
        except StorageError as exc:
            raise HTTPException(410, str(exc)) from None
        except Exception:
            raise HTTPException(410, "Artifact content is missing from object storage") from None
    else:
        base = Path(os.environ.get("OTTERNEST_ARTIFACT_DIR", "./var/artifacts")).resolve()
        project_root = (base / artifact.project_id).resolve()
        try:
            path = Path(artifact.uri).resolve(strict=True)
            if not project_root.is_relative_to(base) or not path.is_relative_to(project_root) or not path.is_file():
                raise HTTPException(404, "Artifact content is unavailable")
            if path.stat().st_size > 5 * 1024 * 1024:
                raise HTTPException(413, "Artifact exceeds the supported 5 MiB limit")
            content = path.read_bytes()
        except HTTPException:
            raise
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(410, "Artifact content is missing from local storage") from None
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Artifact exceeds the supported 5 MiB limit")
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), artifact.checksum):
        raise HTTPException(409, "Artifact content checksum does not match its recorded SHA-256")
    return content


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
    require_writable_system(db)
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
def fail_task(task_id: str, body: FailIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """A failed worker blocks the task; it is never silently re-run."""
    row = require_task(db, task_id, actor, lock=True)
    ensure_lease(row, actor, body.epoch)
    row.result_summary = body.summary
    row.failure_reason = body.failure_reason
    row.status = "blocked"
    row.lease_until = None
    row.version += 1
    db.add(Notification(
        project_id=row.project_id,
        level="important",
        title=f"任务已阻塞：{row.title}",
        body=f"失败原因：{body.failure_reason} · {body.summary[:160]}",
        source_kind="task",
        source_id=row.id,
    ))
    emit(db, actor, "task.blocked", task_id, row.project_id, {"failure_reason": body.failure_reason, "correlation_id": row.correlation_id})
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
    if body.accepted:
        row.completed_at = now()
    else:
        row.rework_count += 1
        db.add(Notification(
            project_id=row.project_id,
            level="important",
            title=f"任务退回返工：{row.title}",
            body=body.reason[:200] or "所有者要求修改后重新提交",
            source_kind="task",
            source_id=row.id,
        ))
    row.assigned_agent_id = None
    row.version += 1
    emit(db, actor, "task.accepted" if body.accepted else "task.changes_requested", task_id, row.project_id, {"reason": body.reason, "rework_count": row.rework_count, "correlation_id": row.correlation_id})
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


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdateIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    row = require_task(db, task_id, actor, lock=True)
    if row.status in {"running", "completed", "cancelled"}:
        raise Conflict(f"Cannot edit task in status '{row.status}'")
    if body.title is not None:
        row.title = body.title
    if body.instruction is not None:
        row.instruction = body.instruction
    if body.acceptance is not None:
        row.acceptance = body.acceptance
    if body.risk is not None:
        row.risk = body.risk
        if body.risk == "R3":
            row.approval_granted = False
            if row.status == "ready":
                row.status = "waiting_approval"
    if body.priority is not None:
        row.priority = body.priority
    if body.sensitivity is not None:
        row.sensitivity = body.sensitivity
    if body.required_capabilities is not None:
        row.required_capabilities = body.required_capabilities
    row.version += 1
    emit(db, actor, "task.updated", task_id, row.project_id)
    db.commit()
    return task_public(row)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Safely delete a non-running task and its dependent records."""
    owner_only(actor)
    require_writable_system(db)
    row = require_task(db, task_id, actor, lock=True)
    if row.status == "running":
        raise Conflict("Cannot delete a running task; cancel it first")
    external = db.scalar(select(A2ADelegation).where(A2ADelegation.task_id == task_id))
    if external and external.status not in {"canceled", "failed", "submitted"}:
        raise Conflict("External A2A delegation active or unknown; resolve or cancel it before deleting")

    from .models import A2AInboxMessage, CommandPlan, SkillEvaluation, TaskCheckpoint, TaskGrant, TaskSideEffect
    for dep in db.scalars(select(TaskDependency).where((TaskDependency.task_id == task_id) | (TaskDependency.depends_on_id == task_id))):
        db.delete(dep)
    artifact_uris = [
        a.uri for a in db.scalars(select(Artifact).where(Artifact.task_id == task_id)) if a.uri
    ]
    for model in (TaskCheckpoint, TaskSideEffect, TaskGrant, CommandPlan, A2AInboxMessage, A2ADelegation, Artifact):
        for child in db.scalars(select(model).where(model.task_id == task_id)):
            db.delete(child)
    for ev in db.scalars(select(SkillEvaluation).where(SkillEvaluation.task_id == task_id)):
        ev.task_id = None

    project_id = row.project_id
    db.delete(row)
    emit(db, actor, "task.deleted", task_id, project_id)
    db.commit()
    if artifact_uris:
        from .storage import get_storage_backend
        backend = get_storage_backend()
        for uri in artifact_uris:
            try:
                backend.delete(uri)
            except Exception:
                pass
    return {"id": task_id, "deleted": True}


@router.post("/projects/{project_id}/memories", status_code=201)
def add_memory(project_id: str, body: MemoryIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_writable_system(db)
    require_project(db, project_id, actor)
    from .vector import dumps_embedding, embed_text
    record = Memory(
        project_id=project_id,
        kind=body.kind,
        title=body.title,
        content=body.content,
        source=body.source,
        sensitivity=body.sensitivity,
        embedding=dumps_embedding(embed_text(f"{body.title}\n{body.content}")),
    )
    db.add(record)
    db.flush()
    emit(db, actor, "memory.proposed", record.id, project_id)
    db.commit()
    return as_dict(record, "id", "kind", "title", "content", "source", "sensitivity", "status", "version")


@router.get("/projects/{project_id}/memories")
def memories(
    project_id: str,
    q: str | None = None,
    limit: int = 50,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    require_project(db, project_id, actor)
    query = select(Memory).where(Memory.project_id == project_id)
    if not actor.is_owner:
        query = query.where(Memory.status == "published")
    allowed = effective_sensitivity(db, project_id, actor)
    rows = [m for m in db.scalars(query) if sensitivity_allowed(m.sensitivity, allowed)]
    if q and q.strip():
        from .vector import search_hybrid
        return search_hybrid(rows, q.strip(), allowed_sensitivity=allowed, limit=max(1, min(limit, 200)))
    return [as_dict(m, "id", "kind", "title", "content", "source", "sensitivity", "status", "version") for m in rows]


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
async def upload_artifact(
    task_id: str,
    request: Request,
    epoch: int | None = None,
    filename: str = Header(default="artifact.bin", alias="X-Artifact-Name"),
    filename_encoded: str | None = Header(default=None, alias="X-Artifact-Name-Encoded"),
    sensitivity: str = Header(default="internal", alias="X-Artifact-Sensitivity"),
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    require_writable_system(db)
    if filename_encoded is not None:
        try:
            filename = unquote(filename_encoded, encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(422, "Invalid UTF-8 artifact filename") from None
    if sensitivity not in SENSITIVITY_ORDER:
        raise HTTPException(422, "Invalid artifact sensitivity")
    row = require_task(db, task_id, actor, lock=True)
    if not actor.is_owner:
        if epoch is None:
            raise HTTPException(422, "epoch required for agent upload")
        ensure_lease(row, actor, epoch)
    if len(filename) > 240 or not filename or any(ord(c) < 32 for c in filename) or "/" in filename or "\\" in filename:
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
    latest = db.scalar(
        select(Artifact)
        .where(Artifact.task_id == task_id, Artifact.name == filename)
        .order_by(Artifact.version.desc(), Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
        .with_for_update()
    )
    record = Artifact(
        family_id=latest.family_id if latest else new_id(),
        version=(latest.version + 1) if latest else 1,
        project_id=row.project_id,
        task_id=task_id,
        name=filename,
        checksum=checksum,
        size_bytes=len(content),
        uri="",
        sensitivity=sensitivity,
        review_status="pending",
    )
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
    return as_dict(record, "id", "family_id", "version", "project_id", "task_id", "name", "checksum", "size_bytes", "sensitivity", "review_status", "review_note", "reviewed_at", "deleted_at", "created_at")


@router.get("/tasks/{task_id}/artifacts")
def artifacts(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    task_row = require_task(db, task_id, actor)
    allowed = effective_sensitivity(db, task_row.project_id, actor, task_id)
    rows = db.scalars(
        select(Artifact)
        .where(Artifact.task_id == task_id)
        .order_by(Artifact.name, Artifact.version, Artifact.created_at, Artifact.id)
    ).all()
    filtered = [a for a in rows if sensitivity_allowed(a.sensitivity, allowed)]
    return [as_dict(a, "id", "family_id", "version", "name", "checksum", "size_bytes", "sensitivity", "review_status", "review_note", "reviewed_at", "deleted_at", "created_at") for a in filtered]


@router.get("/artifacts/{artifact_id}/content")
def download_artifact(artifact_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    require_task(db, artifact.task_id, actor)
    allowed = effective_sensitivity(db, artifact.project_id, actor, artifact.task_id)
    if not sensitivity_allowed(artifact.sensitivity, allowed):
        raise HTTPException(403, "Agent lacks sensitivity clearance for this artifact")
    content = _read_artifact_content(artifact)
    disposition_name = quote(artifact.name, safe="")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{disposition_name}"},
    )


@router.post("/artifacts/{artifact_id}/review")
def review_artifact(artifact_id: str, body: ArtifactReviewIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    note = body.note.strip()
    if not body.accepted and len(note) < 5:
        raise HTTPException(422, "A rejection note must be at least 5 characters")
    artifact = db.scalar(select(Artifact).where(Artifact.id == artifact_id).with_for_update())
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    if artifact.review_status != "pending":
        raise Conflict("Artifact review is already final; upload a new version for changes")
    if body.accepted:
        _read_artifact_content(artifact)
    artifact.review_status = "accepted" if body.accepted else "rejected"
    artifact.review_note = note
    artifact.reviewed_at = now()
    emit(
        db,
        actor,
        "artifact.accepted" if body.accepted else "artifact.rejected",
        artifact.id,
        artifact.project_id,
        {"task_id": artifact.task_id, "family_id": artifact.family_id, "version": artifact.version},
    )
    db.commit()
    return as_dict(artifact, "id", "family_id", "version", "project_id", "task_id", "name", "checksum", "size_bytes", "sensitivity", "review_status", "review_note", "reviewed_at", "deleted_at", "created_at")


@router.get("/system/capabilities")
def system_capabilities(actor: Actor = Depends(authenticate)):
    import shutil
    openai_ready = bool(os.environ.get("OTTERNEST_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    codex_cli = shutil.which("codex")
    gemini_ready = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or shutil.which("gemini"))
    muse_ready = bool(
        (os.environ.get("OTTERNEST_MUSE_BASE_URL") or os.environ.get("MUSE_API_URL"))
        and (os.environ.get("OTTERNEST_MUSE_TOKEN") or os.environ.get("MUSE_API_TOKEN"))
    )
    storage_type = os.environ.get("OTTERNEST_STORAGE_TYPE", "local").lower()
    kms_key_source = (
        "OTTERNEST_KMS_KEY"
        if os.environ.get("OTTERNEST_KMS_KEY")
        else ("OTTERNEST_OWNER_TOKEN" if os.environ.get("OTTERNEST_OWNER_TOKEN") else "ephemeral")
    )
    return {
        "executors": {
            "openai_worker": {"available": openai_ready, "entrypoint": "otternest-openai-worker"},
            "codex_worker": {"available": codex_cli is not None, "entrypoint": "otternest-codex-worker"},
            "gemini_worker": {"available": gemini_ready, "entrypoint": "otternest-gemini-worker"},
            "muse_adapter": {"available": muse_ready, "entrypoint": "otternest-muse-worker"},
        },
        "storage": {
            "backend": storage_type,
            "multipart_enabled": True,
            "s3_bucket": os.environ.get("OTTERNEST_S3_BUCKET", "") if storage_type == "s3" else None,
        },
        "vector_search": {
            "enabled": True,
            "mode": "hybrid",
            "embedding_dim": 64,
        },
        "kms": {
            "enabled": True,
            "provider": "EnvelopeKmsProvider",
            "key_source": kms_key_source,
        },
    }


@router.get("/system/security-audit")
def system_security_audit(actor: Actor = Depends(authenticate)):
    owner_only(actor)
    from .security_audit import audit_configuration
    findings = audit_configuration()
    critical_count = sum(1 for f in findings if f["level"] == "critical")
    warning_count = sum(1 for f in findings if f["level"] == "warning")
    pass_count = sum(1 for f in findings if f["level"] == "pass")
    return {
        "status": "critical" if critical_count else ("warning" if warning_count else "pass"),
        "summary": {"critical": critical_count, "warning": warning_count, "pass": pass_count},
        "findings": findings,
    }


class UpdateApplyIn(BaseModel):
    force: bool = False


@router.get("/system/update/check")
async def system_update_check(fetch: bool = True, actor: Actor = Depends(authenticate)):
    owner_only(actor)
    from .updater import UpdateError, check_for_updates
    try:
        return await asyncio.to_thread(check_for_updates, fetch=fetch)
    except UpdateError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/system/update/status")
def system_update_status(actor: Actor = Depends(authenticate)):
    owner_only(actor)
    from .updater import get_update_status
    return get_update_status()


@router.post("/system/update/apply")
def system_update_apply(
    body: UpdateApplyIn | None = None,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    owner_only(actor)
    from sqlalchemy import func
    from .updater import UpdateError, start_update_job
    running_count = db.scalar(select(func.count(Task.id)).where(Task.status == "running")) or 0
    if running_count > 0 and not (body and body.force):
        raise Conflict(f"There are {running_count} running tasks; finish or cancel them first, or confirm force update")
    try:
        status_data = start_update_job()
    except UpdateError as exc:
        raise HTTPException(400, str(exc)) from None
    emit(db, actor, "system.update_started", "system", payload={"force": bool(body and body.force)})
    db.commit()
    return status_data


class MultipartInitIn(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    sensitivity: str = "internal"


class MultipartCompletePart(BaseModel):
    part_number: int = Field(ge=1)
    sha256: str = ""
    size: int = 0


class MultipartCompleteIn(BaseModel):
    parts: list[MultipartCompletePart] = Field(min_length=1)


def _active_storage():
    from .storage import LocalStorageBackend, S3StorageBackend
    stype = os.environ.get("OTTERNEST_STORAGE_TYPE", "local").lower()
    if stype == "s3":
        return S3StorageBackend(
            bucket=os.environ.get("OTTERNEST_S3_BUCKET", "otternest-artifacts"),
            endpoint_url=os.environ.get("OTTERNEST_S3_ENDPOINT"),
        )
    return LocalStorageBackend()


@router.post("/tasks/{task_id}/artifacts/multipart/init", status_code=201)
def init_multipart_artifact(
    task_id: str,
    body: MultipartInitIn,
    epoch: int | None = None,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    require_writable_system(db)
    if body.sensitivity not in SENSITIVITY_ORDER:
        raise HTTPException(422, "Invalid artifact sensitivity")
    if any(ord(c) < 32 for c in body.filename) or "/" in body.filename or "\\" in body.filename:
        raise HTTPException(422, "Invalid artifact filename")
    row = require_task(db, task_id, actor, lock=True)
    if not actor.is_owner:
        if epoch is None:
            raise HTTPException(422, "epoch required for agent upload")
        ensure_lease(row, actor, epoch)
    backend = _active_storage()
    artifact_id = new_id()
    upload_id = backend.initiate_multipart(row.project_id, artifact_id, body.filename)
    return {
        "upload_id": upload_id,
        "artifact_id": artifact_id,
        "task_id": task_id,
        "project_id": row.project_id,
        "filename": body.filename,
        "sensitivity": body.sensitivity,
    }


@router.put("/tasks/{task_id}/artifacts/multipart/{upload_id}/parts/{part_number}")
async def upload_multipart_part(
    task_id: str,
    upload_id: str,
    part_number: int,
    request: Request,
    epoch: int | None = None,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    require_writable_system(db)
    if part_number < 1:
        raise HTTPException(422, "part_number must be >= 1")
    row = require_task(db, task_id, actor)
    if not actor.is_owner:
        if epoch is None:
            raise HTTPException(422, "epoch required for agent upload")
        ensure_lease(row, actor, epoch)
    content = await request.body()
    backend = _active_storage()
    try:
        return backend.upload_part(upload_id, part_number, content)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/tasks/{task_id}/artifacts/multipart/{upload_id}/complete", status_code=201)
def complete_multipart_artifact(
    task_id: str,
    upload_id: str,
    body: MultipartCompleteIn,
    filename: str,
    sensitivity: str = "internal",
    epoch: int | None = None,
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    require_writable_system(db)
    if sensitivity not in SENSITIVITY_ORDER:
        raise HTTPException(422, "Invalid artifact sensitivity")
    row = require_task(db, task_id, actor, lock=True)
    if not actor.is_owner:
        if epoch is None:
            raise HTTPException(422, "epoch required for agent upload")
        ensure_lease(row, actor, epoch)
    backend = _active_storage()
    try:
        uri, checksum, total_size = backend.complete_multipart(
            upload_id,
            [p.model_dump() for p in body.parts],
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from None

    latest = db.scalar(
        select(Artifact)
        .where(Artifact.task_id == task_id, Artifact.name == filename)
        .order_by(Artifact.version.desc(), Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
        .with_for_update()
    )
    record = Artifact(
        family_id=latest.family_id if latest else new_id(),
        version=(latest.version + 1) if latest else 1,
        project_id=row.project_id,
        task_id=task_id,
        name=filename,
        checksum=checksum,
        size_bytes=total_size,
        uri=uri,
        sensitivity=sensitivity,
        review_status="pending",
    )
    db.add(record)
    db.flush()
    emit(db, actor, "artifact.submitted", record.id, row.project_id, {"task_id": task_id, "sha256": checksum, "multipart": True})
    db.commit()
    return as_dict(record, "id", "family_id", "version", "project_id", "task_id", "name", "checksum", "size_bytes", "sensitivity", "review_status", "review_note", "reviewed_at", "deleted_at", "created_at")
