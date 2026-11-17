"""Goal dependencies and bounded event-driven workflow rules with circuit breakers."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import (
    Event,
    Goal,
    GoalDependency,
    Notification,
    Project,
    Task,
    WorkflowRule,
    WorkflowCursor,
    WorkflowRun,
    now,
)
from .security import Actor, as_utc, authenticate, owner_only
from .services import (
    MAX_DEPENDENCIES,
    MAX_GRAPH_SCAN,
    Conflict,
    emit,
    enforce_project_task_total_budget,
    require_project,
    require_writable_system,
    task_ready_status,
)

router = APIRouter(prefix="/api/v1", tags=["Goal Dependencies & Workflow Rules"])


class GoalDependencyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    predecessor_id: str


class GoalStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "paused", "completed"]
    note: str = Field(default="", max_length=2000)


class WorkflowRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    event_prefix: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.*-]+$")
    condition_status: str | None = Field(default=None, max_length=32)
    action_kind: Literal["create_task", "notify_inbox"]
    action_config: dict = Field(default_factory=dict)
    max_chain_depth: int = Field(default=3, ge=1, le=5)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)


def _goal_dep_ids(db: Session, goal_id: str) -> list[str]:
    return list(db.scalars(select(GoalDependency.depends_on_id).where(GoalDependency.goal_id == goal_id)))


def _goal_dependencies_completed(db: Session, goal_id: str) -> bool:
    preds = _goal_dep_ids(db, goal_id)
    if not preds:
        return True
    done = db.scalars(select(Goal.id).where(Goal.id.in_(preds), Goal.status == "completed")).all()
    return len(done) == len(preds)


@router.get("/projects/{project_id}/goal-board")
def goal_board(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Return goal state, dependency progress, and owner-completion readiness."""
    require_project(db, project_id, actor)
    goals = db.scalars(
        select(Goal).where(Goal.project_id == project_id).order_by(Goal.priority.desc(), Goal.id)
    ).all()
    if not goals:
        return {"project_id": project_id, "goals": []}

    goal_ids = [goal.id for goal in goals]
    status_by_id = {goal.id: goal.status for goal in goals}
    title_by_id = {goal.id: goal.title for goal in goals}
    dependency_map: dict[str, list[str]] = {goal_id: [] for goal_id in goal_ids}
    for edge in db.scalars(select(GoalDependency).where(GoalDependency.goal_id.in_(goal_ids))).all():
        dependency_map[edge.goal_id].append(edge.depends_on_id)

    task_counts = {goal_id: {"total": 0, "completed": 0} for goal_id in goal_ids}
    tasks = db.scalars(select(Task).where(Task.goal_id.in_(goal_ids))).all()
    for task in tasks:
        counts = task_counts[task.goal_id]
        counts["total"] += 1
        if task.status == "completed":
            counts["completed"] += 1

    rows = []
    for goal in goals:
        depends_on = dependency_map[goal.id]
        dependencies_satisfied = all(status_by_id.get(dep_id) == "completed" for dep_id in depends_on)
        counts = task_counts[goal.id]
        open_tasks = counts["total"] - counts["completed"]
        blockers = []
        if not dependencies_satisfied:
            blockers.append("prerequisite_goals_incomplete")
        if open_tasks:
            blockers.append("linked_tasks_incomplete")
        rows.append({
            "id": goal.id,
            "project_id": goal.project_id,
            "title": goal.title,
            "success_criteria": goal.success_criteria,
            "priority": goal.priority,
            "status": goal.status,
            "depends_on": [
                {"id": dep_id, "title": title_by_id.get(dep_id, dep_id), "status": status_by_id.get(dep_id, "unknown")}
                for dep_id in depends_on
            ],
            "dependencies_satisfied": dependencies_satisfied,
            "task_count": counts["total"],
            "completed_task_count": counts["completed"],
            "open_task_count": open_tasks,
            "can_complete": goal.status in ("active", "paused") and not blockers,
            "completion_blockers": blockers,
        })
    return {"project_id": project_id, "goals": rows}


@router.post("/goals/{goal_id}/status")
def update_goal_status(goal_id: str, body: GoalStatusIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    project_id = db.scalar(select(Goal.project_id).where(Goal.id == goal_id))
    if project_id is None:
        raise HTTPException(404, "Goal not found")
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    require_writable_system(db)
    goal = db.scalar(select(Goal).where(Goal.id == goal_id).with_for_update())
    if goal is None:
        raise HTTPException(404, "Goal not found")
    require_project(db, project_id, actor)

    if body.status == goal.status:
        return {"id": goal.id, "project_id": project_id, "status": goal.status, "changed": False}
    if goal.status == "completed":
        raise Conflict("Completed goals are terminal; create a follow-up goal instead")
    if goal.status not in ("active", "paused"):
        raise Conflict("Goal has an unsupported current status")

    note = body.note.strip()
    if body.status == "completed":
        if len(note) < 5:
            raise HTTPException(422, "Provide a completion note (at least 5 characters)")
        if not _goal_dependencies_completed(db, goal.id):
            raise Conflict("Prerequisite goals must be completed first")
        open_task_id = db.scalar(
            select(Task.id).where(Task.goal_id == goal.id, Task.status != "completed").limit(1)
        )
        if open_task_id is not None:
            raise Conflict("All tasks linked to this goal must be accepted as completed first")

    previous = goal.status
    goal.status = body.status
    emit(db, actor, "goal.status_changed", goal.id, project_id, {
        "from": previous,
        "to": goal.status,
        **({"completion_note": note} if goal.status == "completed" else {}),
    })
    db.commit()
    return {"id": goal.id, "project_id": project_id, "status": goal.status, "changed": True}


@router.get("/goals/{goal_id}/dependencies")
def list_goal_dependencies(goal_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    require_project(db, goal.project_id, actor)
    return {
        "goal_id": goal_id,
        "depends_on": _goal_dep_ids(db, goal_id),
        "satisfied": _goal_dependencies_completed(db, goal_id),
    }


@router.post("/goals/{goal_id}/dependencies", status_code=201)
def add_goal_dependency(goal_id: str, body: GoalDependencyIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    require_project(db, goal.project_id, actor)
    db.scalar(select(Project).where(Project.id == goal.project_id).with_for_update())
    db.refresh(goal)
    pred = db.get(Goal, body.predecessor_id)
    if pred is None or pred.project_id != goal.project_id:
        raise HTTPException(422, "Goal dependency must belong to the same project")
    if pred.id == goal.id:
        raise Conflict("A goal cannot depend on itself")
    if goal.status == "completed":
        raise Conflict("Dependencies of a completed goal cannot be changed")
    if db.get(GoalDependency, (goal.id, pred.id)) is not None:
        raise Conflict("Goal dependency already exists")
    if len(_goal_dep_ids(db, goal.id)) >= MAX_DEPENDENCIES:
        raise Conflict("Too many dependencies for one goal")

    visited: set[str] = set()
    pending = [pred.id]
    while pending:
        if len(visited) >= MAX_GRAPH_SCAN:
            raise Conflict("Goal dependency graph too large to validate")
        cur = pending.pop()
        if cur == goal.id:
            raise Conflict("Goal dependency would create a cycle")
        if cur in visited:
            continue
        visited.add(cur)
        pending.extend(_goal_dep_ids(db, cur))

    db.add(GoalDependency(goal_id=goal.id, depends_on_id=pred.id))
    db.flush()
    emit(db, actor, "goal.dependency_added", goal.id, goal.project_id, {"predecessor_id": pred.id})
    db.commit()
    return {"goal_id": goal.id, "depends_on": _goal_dep_ids(db, goal.id)}


@router.delete("/goals/{goal_id}/dependencies/{predecessor_id}")
def remove_goal_dependency(goal_id: str, predecessor_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    require_project(db, goal.project_id, actor)
    require_writable_system(db)
    db.scalar(select(Project).where(Project.id == goal.project_id).with_for_update())
    db.refresh(goal)
    if goal.status == "completed":
        raise Conflict("Dependencies of a completed goal cannot be changed")
    edge = db.get(GoalDependency, (goal.id, predecessor_id))
    if edge is None:
        raise HTTPException(404, "Goal dependency not found")
    db.delete(edge)
    emit(db, actor, "goal.dependency_removed", goal.id, goal.project_id, {"predecessor_id": predecessor_id})
    db.commit()
    return {"goal_id": goal.id, "depends_on": _goal_dep_ids(db, goal.id)}


def _rule_public(rule: WorkflowRule) -> dict:
    return {
        "id": rule.id,
        "project_id": rule.project_id,
        "name": rule.name,
        "event_prefix": rule.event_prefix,
        "condition_status": rule.condition_status,
        "action_kind": rule.action_kind,
        "action_config": rule.action_config,
        "max_chain_depth": rule.max_chain_depth,
        "cooldown_seconds": rule.cooldown_seconds,
        "enabled": rule.enabled,
        "triggered_count": rule.triggered_count,
        "last_triggered_at": rule.last_triggered_at,
        "created_at": rule.created_at,
    }


def _run_public(run: WorkflowRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "rule_id": run.rule_id,
        "event_id": run.event_id,
        "correlation_id": run.correlation_id,
        "chain_depth": run.chain_depth,
        "status": run.status,
        "created_task_id": run.created_task_id,
        "detail": run.detail,
        "created_at": run.created_at,
    }


@router.post("/projects/{project_id}/workflow-rules", status_code=201)
def create_workflow_rule(project_id: str, body: WorkflowRuleIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    if body.action_kind == "create_task":
        title = str(body.action_config.get("title", "")).strip()
        if not title:
            raise HTTPException(422, "create_task action_config requires a non-empty 'title'")
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    rule = WorkflowRule(
        project_id=project_id,
        name=body.name.strip(),
        event_prefix=body.event_prefix.strip(),
        condition_status=body.condition_status,
        action_kind=body.action_kind,
        action_config=body.action_config,
        max_chain_depth=body.max_chain_depth,
        cooldown_seconds=body.cooldown_seconds,
    )
    db.add(rule)
    db.flush()
    emit(db, actor, "workflow.rule_created", rule.id, project_id, {"name": rule.name, "event_prefix": rule.event_prefix})
    latest_event_id = db.scalar(
        select(Event.id).where(Event.project_id == project_id).order_by(Event.id.desc()).limit(1)
    ) or 0
    db.add(WorkflowCursor(rule_id=rule.id, last_event_id=latest_event_id, updated_at=now()))
    db.commit()
    return _rule_public(rule)


@router.get("/projects/{project_id}/workflow-rules")
def list_workflow_rules(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    rows = db.scalars(select(WorkflowRule).where(WorkflowRule.project_id == project_id).order_by(WorkflowRule.created_at.desc())).all()
    return [_rule_public(r) for r in rows]


@router.post("/workflow-rules/{rule_id}/toggle")
def toggle_workflow_rule(rule_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    rule = db.scalar(select(WorkflowRule).where(WorkflowRule.id == rule_id).with_for_update())
    if rule is None:
        raise HTTPException(404, "Workflow rule not found")
    db.scalar(select(Project).where(Project.id == rule.project_id).with_for_update())
    reenabled = not rule.enabled
    rule.enabled = not rule.enabled
    emit(db, actor, "workflow.rule_toggled", rule.id, rule.project_id, {"enabled": rule.enabled})
    if reenabled:
        latest_event_id = db.scalar(
            select(Event.id).where(Event.project_id == rule.project_id).order_by(Event.id.desc()).limit(1)
        ) or 0
        cursor = db.get(WorkflowCursor, rule.id)
        if cursor is None:
            db.add(WorkflowCursor(rule_id=rule.id, last_event_id=latest_event_id, updated_at=now()))
        else:
            cursor.last_event_id = latest_event_id
            cursor.updated_at = now()
    db.commit()
    return _rule_public(rule)


@router.delete("/workflow-rules/{rule_id}")
def delete_workflow_rule(rule_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    rule = db.scalar(select(WorkflowRule).where(WorkflowRule.id == rule_id).with_for_update())
    if rule is None:
        raise HTTPException(404, "Workflow rule not found")
    for run in db.scalars(select(WorkflowRun).where(WorkflowRun.rule_id == rule_id)):
        db.delete(run)
    cursor = db.get(WorkflowCursor, rule_id)
    if cursor is not None:
        db.delete(cursor)
    pid = rule.project_id
    db.delete(rule)
    emit(db, actor, "workflow.rule_deleted", rule_id, pid)
    db.commit()
    return {"id": rule_id, "deleted": True}


@router.get("/projects/{project_id}/workflow-runs")
def list_workflow_runs(project_id: str, limit: int = 50, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    if limit < 1 or limit > 200:
        raise HTTPException(422, "limit must be 1..200")
    rows = db.scalars(
        select(WorkflowRun).where(WorkflowRun.project_id == project_id).order_by(WorkflowRun.created_at.desc()).limit(limit)
    ).all()
    return [_run_public(r) for r in rows]


@router.post("/projects/{project_id}/workflow-rules/evaluate")
def evaluate_workflow_rules(project_id: str, limit: int = 50, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Evaluate matching project events against enabled workflow rules with strict circuit breakers."""
    owner_only(actor)
    require_writable_system(db, allow_degraded=False)
    project = require_project(db, project_id, actor)
    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project.status != "active":
        raise Conflict("Project is not active")

    rules = db.scalars(
        select(WorkflowRule).where(WorkflowRule.project_id == project_id, WorkflowRule.enabled.is_(True)).order_by(WorkflowRule.created_at)
    ).all()
    if not rules:
        return {"project_id": project_id, "runs": [], "executed": 0, "skipped": 0}

    events = db.scalars(
        select(Event).where(Event.project_id == project_id).order_by(Event.id.desc()).limit(limit)
    ).all()
    events = list(reversed(events))

    runs_out: list[dict] = []
    executed = 0
    skipped = 0

    for rule in rules:
        for ev in events:
            if not ev.kind.startswith(rule.event_prefix):
                continue
            if db.scalar(select(WorkflowRun.id).where(WorkflowRun.rule_id == rule.id, WorkflowRun.event_id == ev.id)):
                continue
            if rule.condition_status:
                subject_task = db.get(Task, ev.subject_id)
                subject_goal = db.get(Goal, ev.subject_id) if subject_task is None else None
                subject_status = subject_task.status if subject_task is not None else (
                    subject_goal.status if subject_goal is not None and subject_goal.project_id == project_id else None
                )
                if subject_status != rule.condition_status:
                    continue

            payload = ev.payload or {}
            corr_id = str(payload.get("correlation_id") or ev.subject_id)[:36]
            prior_depth = int(payload.get("workflow_chain_depth") or 0)
            next_depth = prior_depth + 1

            # Circuit breaker 1: bounded chain depth prevents cyclic rule storms
            if next_depth > rule.max_chain_depth:
                run = WorkflowRun(
                    project_id=project_id,
                    rule_id=rule.id,
                    event_id=ev.id,
                    correlation_id=corr_id,
                    chain_depth=next_depth,
                    status="skipped_circuit_breaker",
                    detail=f"Chain depth {next_depth} exceeded max_chain_depth={rule.max_chain_depth}",
                )
                db.add(run)
                db.add(Notification(
                    project_id=project_id,
                    level="urgent",
                    title=f"工作流熔断触发：{rule.name}",
                    body=run.detail,
                    source_kind="workflow_rule",
                    source_id=rule.id,
                ))
                runs_out.append(_run_public(run))
                skipped += 1
                continue

            # Circuit breaker 2: cooldown window
            last_trig = as_utc(rule.last_triggered_at)
            if rule.cooldown_seconds > 0 and last_trig is not None and (now() - last_trig) < timedelta(seconds=rule.cooldown_seconds):
                run = WorkflowRun(
                    project_id=project_id,
                    rule_id=rule.id,
                    event_id=ev.id,
                    correlation_id=corr_id,
                    chain_depth=next_depth,
                    status="skipped_cooldown",
                    detail=f"Rule in cooldown ({rule.cooldown_seconds}s)",
                )
                db.add(run)
                runs_out.append(_run_public(run))
                skipped += 1
                continue

            created_task_id: str | None = None
            status_val = "executed"
            detail = f"Executed {rule.action_kind}"

            if rule.action_kind == "create_task":
                try:
                    enforce_project_task_total_budget(db, project_id)
                except Conflict as exc:
                    run = WorkflowRun(
                        project_id=project_id,
                        rule_id=rule.id,
                        event_id=ev.id,
                        correlation_id=corr_id,
                        chain_depth=next_depth,
                        status="blocked_budget",
                        detail=str(exc.detail),
                    )
                    db.add(run)
                    runs_out.append(_run_public(run))
                    skipped += 1
                    continue
                cfg = rule.action_config or {}
                risk = cfg.get("risk", "R1")
                if risk not in ("R0", "R1", "R2", "R3"):
                    risk = "R1"
                priority = int(cfg.get("priority", 50))
                priority = max(0, min(100, priority))
                new_task = Task(
                    project_id=project_id,
                    title=str(cfg.get("title", f"Follow-up: {ev.kind}"))[:240],
                    instruction=str(cfg.get("instruction", f"Triggered by event #{ev.id} ({ev.kind})"))[:12000],
                    acceptance=str(cfg.get("acceptance", "Verified by owner"))[:6000],
                    risk=risk,
                    priority=priority,
                    correlation_id=corr_id,
                )
                db.add(new_task)
                db.flush()
                new_task.status = task_ready_status(db, new_task)
                created_task_id = new_task.id
                emit(db, actor, "task.created", new_task.id, project_id, {
                    "risk": new_task.risk,
                    "correlation_id": corr_id,
                    "workflow_rule_id": rule.id,
                    "workflow_chain_depth": next_depth,
                })
            elif rule.action_kind == "notify_inbox":
                cfg = rule.action_config or {}
                lvl = cfg.get("level", "important")
                if lvl not in ("urgent", "important", "digest"):
                    lvl = "important"
                db.add(Notification(
                    project_id=project_id,
                    level=lvl,
                    title=str(cfg.get("title", f"工作流提醒：{rule.name}"))[:240],
                    body=str(cfg.get("body", f"事件 {ev.kind} (#{ev.id}) 触发规则 {rule.name}"))[:2000],
                    source_kind="workflow_rule",
                    source_id=rule.id,
                ))

            rule.triggered_count += 1
            rule.last_triggered_at = now()
            run = WorkflowRun(
                project_id=project_id,
                rule_id=rule.id,
                event_id=ev.id,
                correlation_id=corr_id,
                chain_depth=next_depth,
                status=status_val,
                created_task_id=created_task_id,
                detail=detail,
            )
            db.add(run)
            runs_out.append(_run_public(run))
            executed += 1

    db.commit()
    return {"project_id": project_id, "runs": runs_out, "executed": executed, "skipped": skipped}



def process_rule_batch(db: Session, rule: WorkflowRule, events: list[Event], actor: Actor) -> dict:
    """Process one rule's ordered event batch inside the caller's transaction."""
    executed = 0
    skipped = 0
    runs_out: list[dict] = []
    project_id = rule.project_id
    for ev in events:
        if not ev.kind.startswith(rule.event_prefix):
            continue
        if db.scalar(select(WorkflowRun.id).where(
            WorkflowRun.rule_id == rule.id, WorkflowRun.event_id == ev.id
        )):
            continue
        if rule.condition_status:
            subject_task = db.get(Task, ev.subject_id)
            subject_goal = db.get(Goal, ev.subject_id) if subject_task is None else None
            subject_status = subject_task.status if subject_task is not None else (
                subject_goal.status if subject_goal is not None and subject_goal.project_id == project_id else None
            )
            if subject_status != rule.condition_status:
                continue

        payload = ev.payload or {}
        corr_id = str(payload.get("correlation_id") or ev.subject_id)[:36]
        next_depth = int(payload.get("workflow_chain_depth") or 0) + 1
        if next_depth > rule.max_chain_depth:
            run = WorkflowRun(
                project_id=project_id, rule_id=rule.id, event_id=ev.id,
                correlation_id=corr_id, chain_depth=next_depth,
                status="skipped_circuit_breaker",
                detail=f"Chain depth {next_depth} exceeded max_chain_depth={rule.max_chain_depth}",
            )
            db.add(run)
            db.add(Notification(
                project_id=project_id, level="urgent",
                title=f"工作流熔断触发：{rule.name}", body=run.detail,
                source_kind="workflow_rule", source_id=rule.id,
            ))
            runs_out.append(_run_public(run)); skipped += 1
            continue

        last_trig = as_utc(rule.last_triggered_at)
        if rule.cooldown_seconds > 0 and last_trig is not None and (
            now() - last_trig
        ) < timedelta(seconds=rule.cooldown_seconds):
            run = WorkflowRun(
                project_id=project_id, rule_id=rule.id, event_id=ev.id,
                correlation_id=corr_id, chain_depth=next_depth,
                status="skipped_cooldown", detail=f"Rule in cooldown ({rule.cooldown_seconds}s)",
            )
            db.add(run); runs_out.append(_run_public(run)); skipped += 1
            continue

        created_task_id: str | None = None
        detail = f"Executed {rule.action_kind}"
        if rule.action_kind == "create_task":
            try:
                enforce_project_task_total_budget(db, project_id)
            except Conflict as exc:
                run = WorkflowRun(
                    project_id=project_id, rule_id=rule.id, event_id=ev.id,
                    correlation_id=corr_id, chain_depth=next_depth,
                    status="blocked_budget", detail=str(exc.detail),
                )
                db.add(run); runs_out.append(_run_public(run)); skipped += 1
                continue
            cfg = rule.action_config or {}
            risk = cfg.get("risk", "R1")
            if risk not in ("R0", "R1", "R2", "R3"):
                risk = "R1"
            priority = max(0, min(100, int(cfg.get("priority", 50))))
            task = Task(
                project_id=project_id,
                title=str(cfg.get("title", f"Follow-up: {ev.kind}"))[:240],
                instruction=str(cfg.get("instruction", f"Triggered by event #{ev.id} ({ev.kind})"))[:12000],
                acceptance=str(cfg.get("acceptance", "Verified by owner"))[:6000],
                risk=risk, priority=priority, correlation_id=corr_id,
            )
            db.add(task); db.flush()
            task.status = task_ready_status(db, task)
            created_task_id = task.id
            emit(db, actor, "task.created", task.id, project_id, {
                "risk": task.risk, "correlation_id": corr_id,
                "workflow_rule_id": rule.id, "workflow_chain_depth": next_depth,
            })
        elif rule.action_kind == "notify_inbox":
            cfg = rule.action_config or {}
            level = cfg.get("level", "important")
            if level not in ("urgent", "important", "digest"):
                level = "important"
            db.add(Notification(
                project_id=project_id, level=level,
                title=str(cfg.get("title", f"工作流提醒：{rule.name}"))[:240],
                body=str(cfg.get("body", f"事件 {ev.kind} (#{ev.id}) 触发规则 {rule.name}"))[:2000],
                source_kind="workflow_rule", source_id=rule.id,
            ))
        else:
            run = WorkflowRun(
                project_id=project_id, rule_id=rule.id, event_id=ev.id,
                correlation_id=corr_id, chain_depth=next_depth,
                status="skipped_unsupported", detail="Unsupported workflow action",
            )
            db.add(run); runs_out.append(_run_public(run)); skipped += 1
            continue

        rule.triggered_count += 1
        rule.last_triggered_at = now()
        run = WorkflowRun(
            project_id=project_id, rule_id=rule.id, event_id=ev.id,
            correlation_id=corr_id, chain_depth=next_depth,
            status="executed", created_task_id=created_task_id, detail=detail,
        )
        db.add(run); runs_out.append(_run_public(run)); executed += 1
    return {"project_id": project_id, "runs": runs_out, "executed": executed, "skipped": skipped}
