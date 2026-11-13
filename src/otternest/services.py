"""Transactional business invariants shared by all future protocol surfaces."""
from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import A2ADelegation, Agent, AgentRuntime, Audit, Event, Goal, Membership, Outbox, Project, Task, TaskDependency, now
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
    project = db.get(Project, task.project_id)
    if not project or project.status != "active":
        raise Conflict("Project is not active")
    agent = db.get(Agent, actor.id)
    if agent is None or not agent.enabled or not set(task.required_capabilities).issubset(set(agent.verified_capabilities)):
        raise Conflict("Agent lacks verified capabilities")
    if db.scalar(select(A2ADelegation.id).where(
            A2ADelegation.task_id == task.id, A2ADelegation.status != "released")):
        raise Conflict("Task has an external A2A delegation; local claim forbidden")
    if task.target_agent_id and task.target_agent_id != actor.id:
        raise Conflict("Task is assigned to a different agent")
    if task.risk == "R3" and not task.approval_granted:
        raise Conflict("High-risk task requires owner approval")
    if not dependencies_completed(db, task.id):
        raise Conflict("Task prerequisites have not been accepted")
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


MAX_DEPENDENCIES = 64
MAX_GRAPH_SCAN = 2000


def dependency_ids(db: Session, task_id: str) -> list[str]:
    return list(db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)))


def dependencies_completed(db: Session, task_id: str) -> bool:
    predecessors = dependency_ids(db, task_id)
    if not predecessors:
        return True
    rows = db.scalars(select(Task.id).where(Task.id.in_(predecessors), Task.status == "completed")).all()
    return len(rows) == len(predecessors)


def task_ready_status(db: Session, task: Task) -> str:
    if task.risk == "R3" and not task.approval_granted:
        return "waiting_approval"
    return "ready" if dependencies_completed(db, task.id) else "waiting_dependencies"


def update_dependencies(db: Session, task: Task, predecessor_id: str, actor: Actor, *, remove: bool = False) -> None:
    """Project-lock serializes DAG edits in PostgreSQL and protects cycle detection."""
    db.scalar(select(Project).where(Project.id == task.project_id).with_for_update())
    db.refresh(task)
    if task.status not in ("ready", "waiting_approval", "waiting_dependencies"):
        raise Conflict("Cannot modify dependencies after execution or completion")
    predecessor = db.get(Task, predecessor_id)
    if predecessor is None or predecessor.project_id != task.project_id:
        raise HTTPException(422, "Dependency must belong to the same project")
    if predecessor_id == task.id:
        raise Conflict("A task cannot depend on itself")
    existing = db.get(TaskDependency, (task.id, predecessor_id))
    if remove:
        if existing is None:
            raise HTTPException(404, "Dependency not found")
        db.delete(existing)
        db.flush()
    else:
        if existing is not None:
            raise Conflict("Dependency already exists")
        if len(dependency_ids(db, task.id)) >= MAX_DEPENDENCIES:
            raise Conflict("Too many dependencies for one task")
        # Walk the upstream dependencies of the proposed predecessor.
        # Adding task -> predecessor would be cyclic if predecessor already reaches task.
        visited: set[str] = set()
        pending = [predecessor_id]
        while pending:
            if len(visited) >= MAX_GRAPH_SCAN:
                raise Conflict("Dependency graph too large to validate")
            candidate = pending.pop()
            if candidate == task.id:
                raise Conflict("Dependency would create a cycle")
            if candidate in visited:
                continue
            visited.add(candidate)
            pending.extend(dependency_ids(db, candidate))
        db.add(TaskDependency(task_id=task.id, depends_on_id=predecessor_id))
        db.flush()
    task.status = task_ready_status(db, task)
    task.version += 1
    emit(db, actor, "task.dependency_removed" if remove else "task.dependency_added", task.id,
         task.project_id, {"predecessor_id": predecessor_id})


def release_dependents(db: Session, accepted: Task, actor: Actor) -> None:
    """Called within the acceptance transaction; release only fully satisfied tasks."""
    child_ids = db.scalars(select(TaskDependency.task_id).where(TaskDependency.depends_on_id == accepted.id)).all()
    for child_id in child_ids:
        child = db.scalar(select(Task).where(Task.id == child_id).with_for_update())
        if child is None or child.status != "waiting_dependencies":
            continue
        next_status = task_ready_status(db, child)
        if next_status != child.status:
            child.status = next_status
            child.version += 1
            emit(db, actor, "task.dependencies_satisfied", child.id, child.project_id)


def choose_dispatch_targets(db: Session, project_id: str, actor: Actor, limit: int = 20,
                            heartbeat_ttl_seconds: int = 180) -> list[dict]:
    """Capacity-aware deterministic routing for live polling agents.

    Uses one project row lock for consistency with other project-level DAG operations.
    Routing != accepting/starting remote execution; the worker must still claim.
    """
    from datetime import timedelta
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "Project not found")
    if project.status != "active":
        raise Conflict("Project is not active")
    cutoff = now() - timedelta(seconds=heartbeat_ttl_seconds)
    members = db.execute(
        select(Membership, Agent)
        .join(Agent, Membership.agent_id == Agent.id)
        .where(Membership.project_id == project_id,
               Membership.role.in_(("worker", "manager", "reviewer")),
               Agent.enabled.is_(True), Agent.execution_mode == "polling")
        .order_by(Agent.id)
    ).all()
    candidates: dict[str, Agent] = {}
    for member, agent in members:
        alive = db.scalars(select(AgentRuntime).where(
            AgentRuntime.agent_id == agent.id, AgentRuntime.enabled.is_(True),
            AgentRuntime.status == "idle", AgentRuntime.last_seen >= cutoff
        )).first()
        if alive is not None:
            candidates[agent.id] = agent
    if not candidates:
        return []
    # Bound outstanding work per agent: running leases and previously reserved ready tasks.
    loads: dict[str, int] = {}
    for agent_id in candidates:
        loads[agent_id] = db.scalar(select(func.count()).select_from(Task).where(
            ((Task.assigned_agent_id == agent_id) & (Task.status == "running")) |
            ((Task.target_agent_id == agent_id) & (Task.status == "ready"))
        )) or 0
    tasks = db.scalars(select(Task).where(
        Task.project_id == project_id, Task.status == "ready", Task.target_agent_id.is_(None)
    ).order_by(Task.priority.desc(), Task.created_at, Task.id).with_for_update().limit(limit * 10)).all()
    routes = []
    for task in tasks:
        if len(routes) >= limit:
            break
        eligible = [agent for aid, agent in candidates.items()
                    if loads[aid] == 0 and set(task.required_capabilities).issubset(set(agent.verified_capabilities))]
        if not eligible:
            continue
        chosen = min(eligible, key=lambda agent: (loads[agent.id], agent.id))
        task.target_agent_id = chosen.id
        task.version += 1
        loads[chosen.id] += 1
        emit(db, actor, "task.routed", task.id, task.project_id,
             {"target_agent_id": chosen.id, "method": "live_verified_scheduler"})
        routes.append({"task_id": task.id, "agent_id": chosen.id})
    return routes
