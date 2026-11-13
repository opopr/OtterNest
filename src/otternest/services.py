"""Transactional business invariants shared by all future protocol surfaces."""
from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Agent, Audit, Event, Goal, Membership, Outbox, Project, Task, now
from .security import Actor


class Conflict(HTTPException):
    def __init__(self, detail: str):
        super().__init__(409, detail)


def emit(db: Session, actor: Actor, action: str, subject: str, project_id: str | None = None, payload: dict | None = None) -> None:
    event = Event(project_id=project_id, kind=action, subject_id=subject, payload=payload or {})
    db.add(event)
    db.flush()
    db.add(Outbox(event_id=event.id))
    db.add(Audit(actor=f"{actor.kind}:{actor.id}", action=action, subject=subject))


def require_project(db: Session, project_id: str, actor: Actor, *, roles: tuple[str, ...] | None = None) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if actor.is_owner:
        return project
    membership = db.scalar(select(Membership).where(Membership.agent_id == actor.id, Membership.project_id == project_id))
    if not membership or (roles is not None and membership.role not in roles):
        # Do not disclose which other projects exist.
        raise HTTPException(404, "Project not found")
    return project


def require_task(db: Session, task_id: str, actor: Actor, *, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == task_id)
    if lock:
        query = query.with_for_update()
    task = db.scalar(query)
    if not task:
        raise HTTPException(404, "Task not found")
    require_project(db, task.project_id, actor)
    return task


def ensure_lease(task: Task, actor: Actor, epoch: int) -> None:
    if actor.is_owner:
        raise HTTPException(403, "Agent credentials required")
    if task.status != "running" or task.assigned_agent_id != actor.id or task.lease_epoch != epoch:
        raise Conflict("Stale or foreign task lease")
    expiry = task.lease_until
    if expiry is None:
        raise Conflict("Task has no active lease")
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now():
        raise Conflict("Lease expired; do not submit stale work")


def claim(db: Session, task_id: str, actor: Actor, lease_seconds: int) -> Task:
    if actor.is_owner:
        raise HTTPException(403, "Only agent credentials can claim")
    task = require_task(db, task_id, actor, lock=True)
    require_project(db, task.project_id, actor, roles=("worker", "manager", "reviewer"))
    if task.status == "running":
        if task.lease_until is None:
            raise Conflict("Existing task has no expiry")
        expiry = task.lease_until
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry > now():
            raise Conflict("Task already leased")
        if task.risk == "R3":
            raise Conflict("High-risk task requires manual recovery")
        # An expired lease is not proof a remote worker stopped. Epoch fences stale writes.
    elif task.status != "ready":
        raise Conflict("Task is not claimable")
    task.status = "running"
    task.assigned_agent_id = actor.id
    task.lease_epoch += 1
    task.lease_until = now() + timedelta(seconds=lease_seconds)
    task.version += 1
    emit(db, actor, "task.claimed", task.id, task.project_id, {"epoch": task.lease_epoch})
    db.commit()
    db.refresh(task)
    return task


def validate_goal(db: Session, project_id: str, goal_id: str | None) -> None:
    if goal_id and not db.scalar(select(Goal.id).where(Goal.id == goal_id, Goal.project_id == project_id)):
        raise HTTPException(422, "Goal does not belong to project")
