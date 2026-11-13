"""Project charters, hard budgets, epoch-fenced checkpoints, lease revocation, and operational briefings."""
from __future__ import annotations

from datetime import timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_session
from .models import (
    A2ADelegation,
    Agent,
    Artifact,
    Decision,
    Goal,
    Membership,
    Memory,
    Project,
    ProjectCharter,
    Skill,
    Task,
    TaskCheckpoint,
    TaskSideEffect,
    now,
)
from .security import Actor, authenticate, owner_only
from .services import (
    Conflict,
    UNRECONCILED_SIDE_EFFECT_STATES,
    dependencies_completed,
    dependency_ids,
    emit,
    ensure_lease,
    require_project,
    require_task,
    task_ready_status,
)

router = APIRouter(prefix="/api/v1", tags=["Continuity & Charter"])

MAX_CHECKPOINTS_PER_EPOCH = 50
ACTIVE_A2A_DELEGATION_STATES = ("pending", "remote", "unknown", "cancel_requested")


class CharterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_boundary: str = Field(default="", max_length=4000)
    stop_conditions: str = Field(default="", max_length=4000)
    max_concurrent_tasks: int = Field(default=5, ge=1, le=50)
    max_total_tasks: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def validate_budget_bounds(self) -> "CharterIn":
        if self.max_concurrent_tasks > self.max_total_tasks:
            raise ValueError("max_concurrent_tasks cannot exceed max_total_tasks")
        return self


class ProjectStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "paused", "archived"]
    reason: str = Field(min_length=5, max_length=2000)


class CheckpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=10000)
    next_steps: str = Field(default="", max_length=6000)
    artifact_ids: list[str] = Field(default_factory=list, max_length=16)


class VerifyCheckpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["owner_verified", "rejected"]
    note: str = Field(default="", max_length=2000)


class RevokeLeaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=2000)
    target_agent_id: str | None = None


def _project_budget_usage(db: Session, project_id: str) -> tuple[int, int]:
    running_tasks = db.scalar(
        select(func.count()).select_from(Task).where(Task.project_id == project_id, Task.status == "running")
    ) or 0
    active_total_tasks = db.scalar(
        select(func.count()).select_from(Task).where(Task.project_id == project_id, Task.status != "cancelled")
    ) or 0
    return running_tasks, active_total_tasks


def _charter_public(db: Session, project_id: str, charter: ProjectCharter | None) -> dict:
    running_tasks, active_total_tasks = _project_budget_usage(db, project_id)
    if charter is None:
        return {
            "project_id": project_id,
            "configured": False,
            "scope_boundary": "",
            "stop_conditions": "",
            "max_concurrent_tasks": 5,
            "max_total_tasks": 200,
            "updated_at": None,
            "running_tasks": running_tasks,
            "active_total_tasks": active_total_tasks,
        }
    return {
        "project_id": charter.project_id,
        "configured": True,
        "scope_boundary": charter.scope_boundary,
        "stop_conditions": charter.stop_conditions,
        "max_concurrent_tasks": charter.max_concurrent_tasks,
        "max_total_tasks": charter.max_total_tasks,
        "updated_at": charter.updated_at,
        "running_tasks": running_tasks,
        "active_total_tasks": active_total_tasks,
    }


def _checkpoint_public(cp: TaskCheckpoint) -> dict:
    return {
        "id": cp.id,
        "project_id": cp.project_id,
        "task_id": cp.task_id,
        "agent_id": cp.agent_id,
        "lease_epoch": cp.lease_epoch,
        "step_number": cp.step_number,
        "summary": cp.summary,
        "next_steps": cp.next_steps,
        "artifact_ids": cp.artifact_ids,
        "verification_status": cp.verification_status,
        "verified_by_owner": cp.verification_status == "owner_verified",
        "owner_note": cp.owner_note,
        "created_at": cp.created_at,
        "verified_at": cp.verified_at,
    }


def _task_public(t: Task) -> dict:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "goal_id": t.goal_id,
        "title": t.title,
        "instruction": t.instruction,
        "acceptance": t.acceptance,
        "risk": t.risk,
        "status": t.status,
        "assigned_agent_id": t.assigned_agent_id,
        "lease_epoch": t.lease_epoch,
        "lease_until": t.lease_until,
        "version": t.version,
        "result_summary": t.result_summary,
        "priority": t.priority,
        "target_agent_id": t.target_agent_id,
        "approval_granted": t.approval_granted,
        "required_capabilities": t.required_capabilities,
    }


@router.put("/projects/{project_id}/charter")
def put_project_charter(
    project_id: str, body: CharterIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    require_project(db, project_id, actor)
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    charter = db.get(ProjectCharter, project_id)
    if charter is None:
        charter = ProjectCharter(
            project_id=project_id,
            scope_boundary=body.scope_boundary.strip(),
            stop_conditions=body.stop_conditions.strip(),
            max_concurrent_tasks=body.max_concurrent_tasks,
            max_total_tasks=body.max_total_tasks,
            updated_at=now(),
        )
        db.add(charter)
    else:
        charter.scope_boundary = body.scope_boundary.strip()
        charter.stop_conditions = body.stop_conditions.strip()
        charter.max_concurrent_tasks = body.max_concurrent_tasks
        charter.max_total_tasks = body.max_total_tasks
        charter.updated_at = now()
    db.flush()
    emit(
        db,
        actor,
        "project.charter_updated",
        project_id,
        project_id,
        {"max_concurrent_tasks": charter.max_concurrent_tasks, "max_total_tasks": charter.max_total_tasks},
    )
    db.commit()
    return _charter_public(db, project_id, charter)


@router.get("/projects/{project_id}/charter")
def get_project_charter(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    charter = db.get(ProjectCharter, project_id)
    return _charter_public(db, project_id, charter)


@router.post("/projects/{project_id}/status")
def set_project_status(
    project_id: str, body: ProjectStatusIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, "Status change reason must be at least 5 characters")
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "Project not found")
    project.status = body.status
    running_count = db.scalar(
        select(func.count()).select_from(Task).where(Task.project_id == project_id, Task.status == "running")
    ) or 0
    a2a_count = db.scalar(
        select(func.count()).select_from(A2ADelegation).where(
            A2ADelegation.project_id == project_id,
            A2ADelegation.status.in_(ACTIVE_A2A_DELEGATION_STATES),
        )
    ) or 0
    emit(
        db,
        actor,
        "project.status_changed",
        project.id,
        project.id,
        {
            "status": body.status,
            "reason": reason,
            "in_flight_running_tasks": running_count,
            "active_a2a_delegations": a2a_count,
        },
    )
    db.commit()
    return {
        "id": project.id,
        "status": project.status,
        "in_flight_running_tasks": running_count,
        "active_a2a_delegations": a2a_count,
        "remote_processes_stopped": False,
        "notice": (
            "Control plane scheduling and new claims are halted when not active; "
            "external running processes are not automatically terminated."
        ),
    }


@router.post("/tasks/{task_id}/checkpoints", status_code=201)
def record_checkpoint(
    task_id: str, body: CheckpointIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    summary = body.summary.strip()
    if not summary:
        raise HTTPException(422, "Checkpoint summary is required")
    task = require_task(db, task_id, actor, lock=True)
    ensure_lease(task, actor, body.epoch)
    artifact_ids = list(dict.fromkeys(body.artifact_ids))
    if artifact_ids:
        found = db.scalars(
            select(Artifact.id).where(Artifact.task_id == task.id, Artifact.id.in_(artifact_ids))
        ).all()
        if len(found) != len(artifact_ids):
            raise HTTPException(422, "All referenced artifacts must belong to this task")
    max_step = db.scalar(
        select(func.max(TaskCheckpoint.step_number)).where(
            TaskCheckpoint.task_id == task.id, TaskCheckpoint.lease_epoch == task.lease_epoch
        )
    )
    step_number = (max_step or 0) + 1
    if step_number > MAX_CHECKPOINTS_PER_EPOCH:
        raise Conflict("Checkpoint limit reached for this lease epoch")
    cp = TaskCheckpoint(
        project_id=task.project_id,
        task_id=task.id,
        agent_id=actor.id,
        lease_epoch=task.lease_epoch,
        step_number=step_number,
        summary=summary,
        next_steps=body.next_steps.strip(),
        artifact_ids=artifact_ids,
        verification_status="self_reported",
    )
    db.add(cp)
    db.flush()
    emit(
        db,
        actor,
        "task.checkpoint_recorded",
        task.id,
        task.project_id,
        {"checkpoint_id": cp.id, "epoch": cp.lease_epoch, "step_number": cp.step_number},
    )
    db.commit()
    return _checkpoint_public(cp)


@router.get("/tasks/{task_id}/checkpoints")
def list_checkpoints(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    task = require_task(db, task_id, actor)
    rows = db.scalars(
        select(TaskCheckpoint)
        .where(TaskCheckpoint.task_id == task.id)
        .order_by(TaskCheckpoint.lease_epoch.asc(), TaskCheckpoint.step_number.asc())
    ).all()
    return [_checkpoint_public(cp) for cp in rows]


@router.post("/checkpoints/{checkpoint_id}/verify")
def verify_checkpoint(
    checkpoint_id: str, body: VerifyCheckpointIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    note = body.note.strip()
    if body.status == "rejected" and len(note) < 5:
        raise HTTPException(422, "Rejection note must be at least 5 characters")
    cp = db.scalar(select(TaskCheckpoint).where(TaskCheckpoint.id == checkpoint_id).with_for_update())
    if cp is None:
        raise HTTPException(404, "Checkpoint not found")
    cp.verification_status = body.status
    cp.owner_note = note
    cp.verified_at = now()
    emit(
        db,
        actor,
        "task.checkpoint_verified" if body.status == "owner_verified" else "task.checkpoint_rejected",
        cp.task_id,
        cp.project_id,
        {"checkpoint_id": cp.id, "epoch": cp.lease_epoch, "step_number": cp.step_number},
    )
    db.commit()
    return _checkpoint_public(cp)


@router.post("/tasks/{task_id}/revoke-lease")
def revoke_task_lease(
    task_id: str, body: RevokeLeaseIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, "Revocation reason must be at least 5 characters")
    task = require_task(db, task_id, actor, lock=True)
    if task.status != "running":
        raise Conflict("Task does not have an active or running lease to revoke")
    if db.scalar(
        select(A2ADelegation.id).where(A2ADelegation.task_id == task.id, A2ADelegation.status != "released")
    ):
        raise Conflict("Task has an active external A2A delegation; use the remote cancellation workflow")
    if body.target_agent_id is not None:
        member = db.scalar(
            select(Membership)
            .join(Agent, Membership.agent_id == Agent.id)
            .where(
                Membership.project_id == task.project_id,
                Membership.agent_id == body.target_agent_id,
                Membership.role.in_(("worker", "manager", "reviewer")),
                Agent.enabled.is_(True),
            )
        )
        agent = db.get(Agent, body.target_agent_id)
        if member is None or agent is None:
            raise HTTPException(422, "Target must be an enabled project worker")
        if not set(task.required_capabilities).issubset(set(agent.verified_capabilities)):
            raise HTTPException(422, "Target lacks verified task capabilities")

    old_epoch = task.lease_epoch
    old_agent = task.assigned_agent_id
    task.lease_epoch += 1
    task.lease_until = None
    task.assigned_agent_id = None
    task.target_agent_id = body.target_agent_id
    task.version += 1
    if task.risk == "R3":
        task.approval_granted = False
        task.status = "blocked"
    else:
        task.status = task_ready_status(db, task)
    emit(
        db,
        actor,
        "task.lease_revoked",
        task.id,
        task.project_id,
        {
            "previous_agent_id": old_agent,
            "fenced_epoch": old_epoch,
            "new_epoch": task.lease_epoch,
            "target_agent_id": body.target_agent_id,
            "reason": reason,
        },
    )
    db.commit()
    return {
        **_task_public(task),
        "fenced_epoch": old_epoch,
        "remote_execution_stopped_verified": False,
    }


@router.get("/tasks/{task_id}/handoff")
def task_handoff_package(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    task = require_task(db, task_id, actor)
    if not actor.is_owner:
        require_project(db, task.project_id, actor, roles=("worker", "manager", "reviewer"))
    goal = db.get(Goal, task.goal_id) if task.goal_id else None
    latest_verified = db.scalar(
        select(TaskCheckpoint)
        .where(TaskCheckpoint.task_id == task.id, TaskCheckpoint.verification_status == "owner_verified")
        .order_by(
            TaskCheckpoint.lease_epoch.desc(),
            TaskCheckpoint.step_number.desc(),
            TaskCheckpoint.created_at.desc(),
        )
    )
    latest_self_reported = db.scalar(
        select(TaskCheckpoint)
        .where(TaskCheckpoint.task_id == task.id, TaskCheckpoint.verification_status == "self_reported")
        .order_by(
            TaskCheckpoint.lease_epoch.desc(),
            TaskCheckpoint.step_number.desc(),
            TaskCheckpoint.created_at.desc(),
        )
    )
    artifacts = db.scalars(
        select(Artifact).where(Artifact.task_id == task.id).order_by(Artifact.created_at.asc(), Artifact.id.asc())
    ).all()
    decisions = db.scalars(
        select(Decision).where(Decision.project_id == task.project_id, Decision.status == "approved").limit(10)
    ).all()
    side_effects = db.scalars(
        select(TaskSideEffect)
        .where(TaskSideEffect.task_id == task.id)
        .order_by(TaskSideEffect.created_at.asc(), TaskSideEffect.id.asc())
    ).all()
    return {
        "task": {
            "id": task.id,
            "project_id": task.project_id,
            "goal_id": task.goal_id,
            "title": task.title,
            "instruction": task.instruction,
            "acceptance": task.acceptance,
            "risk": task.risk,
            "status": task.status,
            "version": task.version,
            "lease_epoch": task.lease_epoch,
            "assigned_agent_id": task.assigned_agent_id,
            "target_agent_id": task.target_agent_id,
            "required_capabilities": task.required_capabilities,
        },
        "goal": None
        if goal is None
        else {
            "id": goal.id,
            "title": goal.title,
            "success_criteria": goal.success_criteria,
            "status": goal.status,
        },
        "dependencies": {
            "depends_on": dependency_ids(db, task.id),
            "satisfied": dependencies_completed(db, task.id),
        },
        "latest_verified_checkpoint": _checkpoint_public(latest_verified) if latest_verified else None,
        "latest_self_reported_checkpoint": _checkpoint_public(latest_self_reported) if latest_self_reported else None,
        "artifacts": [
            {"id": a.id, "name": a.name, "checksum": a.checksum, "created_at": a.created_at} for a in artifacts
        ],
        "approved_decisions": [
            {"id": d.id, "question": d.question, "resolution": d.resolution, "rationale": d.rationale}
            for d in decisions
        ],
        "side_effects": [
            {
                "id": se.id,
                "lease_epoch": se.lease_epoch,
                "idempotency_key": se.idempotency_key,
                "target_system": se.target_system,
                "description": se.description,
                "status": se.status,
                "owner_verified": se.status in ("verified_executed", "verified_not_executed"),
                "evidence_summary": se.evidence_summary,
                "owner_note": se.owner_note,
                "updated_at": se.updated_at,
            }
            for se in side_effects
        ],
        "handoff_boundary": (
            "Only latest_verified_checkpoint has been verified by the owner; "
            "latest_self_reported_checkpoint is unverified agent output. "
            "Model conversation state does not migrate across hosts, and external side effects "
            "from prior epochs are not automatically rolled back."
        ),
    }


@router.get("/projects/{project_id}/briefing")
def project_briefing(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    project = require_project(db, project_id, actor)
    charter = db.get(ProjectCharter, project_id)
    tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
    current_time = now()

    counts = {
        "waiting_dependencies": 0,
        "waiting_approval": 0,
        "ready": 0,
        "running_lease_active": 0,
        "running_lease_expired_unverified": 0,
        "submitted": 0,
        "blocked": 0,
        "completed": 0,
        "cancelled": 0,
    }
    for task in tasks:
        if task.status == "running":
            expiry = task.lease_until
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry is not None and expiry > current_time:
                counts["running_lease_active"] += 1
            else:
                counts["running_lease_expired_unverified"] += 1
        elif task.status in counts:
            counts[task.status] += 1

    counts["a2a_active_or_unknown"] = db.scalar(
        select(func.count()).select_from(A2ADelegation).where(
            A2ADelegation.project_id == project_id,
            A2ADelegation.status.in_(ACTIVE_A2A_DELEGATION_STATES),
        )
    ) or 0

    decisions_proposed = db.scalar(
        select(func.count()).select_from(Decision).where(
            Decision.project_id == project_id, Decision.status == "proposed"
        )
    ) or 0
    memories_proposed = db.scalar(
        select(func.count()).select_from(Memory).where(
            Memory.project_id == project_id, Memory.status == "proposed"
        )
    ) or 0
    skills_proposed = db.scalar(
        select(func.count()).select_from(Skill).where(
            Skill.project_id == project_id, Skill.status == "proposed"
        )
    ) or 0
    side_effects_unreconciled = db.scalar(
        select(func.count()).select_from(TaskSideEffect).where(
            TaskSideEffect.project_id == project_id,
            TaskSideEffect.status.in_(UNRECONCILED_SIDE_EFFECT_STATES),
        )
    ) or 0

    recent_verified_checkpoints = db.scalars(
        select(TaskCheckpoint)
        .where(TaskCheckpoint.project_id == project_id, TaskCheckpoint.verification_status == "owner_verified")
        .order_by(TaskCheckpoint.verified_at.desc(), TaskCheckpoint.created_at.desc())
        .limit(5)
    ).all()
    recent_artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.project_id == project_id)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(5)
    ).all()

    return {
        "project": {"id": project.id, "name": project.name, "status": project.status},
        "charter": _charter_public(db, project_id, charter),
        "task_counts": counts,
        "pending_owner_actions": {
            "tasks_waiting_approval": counts["waiting_approval"],
            "tasks_submitted_for_review": counts["submitted"],
            "tasks_blocked": counts["blocked"],
            "tasks_lease_expired_unverified": counts["running_lease_expired_unverified"],
            "decisions_proposed": decisions_proposed,
            "memories_proposed": memories_proposed,
            "skills_proposed": skills_proposed,
            "side_effects_unreconciled": side_effects_unreconciled,
        },
        "recent_verified_checkpoints": [_checkpoint_public(cp) for cp in recent_verified_checkpoints],
        "recent_artifacts": [
            {
                "id": a.id,
                "task_id": a.task_id,
                "name": a.name,
                "checksum": a.checksum,
                "created_at": a.created_at,
            }
            for a in recent_artifacts
        ],
        "honesty_notice": (
            "Running lease state reflects central lease timestamps and self-reported heartbeats, "
            "not verified remote process execution."
        ),
    }
