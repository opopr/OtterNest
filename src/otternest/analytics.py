"""Portfolio fair scheduling, quality metrics, failure attribution, daily briefings, experiments, and notifications."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import (
    Agent,
    Decision,
    Experiment,
    Memory,
    Notification,
    Project,
    Skill,
    Task,
    TaskSideEffect,
    now,
)
from .security import Actor, as_utc, authenticate, owner_only
from .services import Conflict, choose_dispatch_targets, emit, require_project, require_writable_system

router = APIRouter(prefix="/api/v1", tags=["Portfolio, Metrics, Experiments & Notifications"])


class PortfolioScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit_per_project: int = Field(default=10, ge=1, le=50)
    heartbeat_ttl_seconds: int = Field(default=180, ge=30, le=900)


class ExperimentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: Literal["skill", "context_policy", "workflow_rule"]
    target_id: str = Field(min_length=1, max_length=36)
    hypothesis: str = Field(min_length=5, max_length=3000)


class ExperimentRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_score: int = Field(ge=0, le=100)
    candidate_score: int = Field(ge=0, le=100)
    sample_count: int = Field(ge=1, le=10000)
    notes: str = Field(default="", max_length=3000)


class ExperimentResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=5, max_length=2000)


def fair_active_projects(db: Session) -> list[Project]:
    """Order active projects by priority plus starvation-age boost so low-priority projects never starve."""
    active = db.scalars(select(Project).where(Project.status == "active")).all()
    cur_now = now()
    scored: list[tuple[float, Project]] = []
    for p in active:
        oldest_ready = db.scalar(
            select(Task.created_at)
            .where(Task.project_id == p.id, Task.status == "ready", Task.target_agent_id.is_(None))
            .order_by(Task.created_at.asc())
            .limit(1)
        )
        wait_hours = 0.0
        if oldest_ready is not None:
            wait_hours = max(0.0, (cur_now - as_utc(oldest_ready)).total_seconds() / 3600.0)
        # Every hour waiting boosts effective priority by 5 points (capped at +60) for starvation protection.
        effective_score = float(p.priority) + min(60.0, wait_hours * 5.0)
        scored.append((effective_score, p))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [p for _, p in scored]


@router.post("/portfolio/schedule")
def schedule_portfolio(body: PortfolioScheduleIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Fairness-aware dispatch routing across all active projects."""
    owner_only(actor)
    require_writable_system(db, allow_degraded=False)
    ordered = fair_active_projects(db)
    per_project = []
    total_routes = 0
    for proj in ordered:
        routes = choose_dispatch_targets(db, proj.id, actor, body.limit_per_project, body.heartbeat_ttl_seconds)
        per_project.append({"project_id": proj.id, "project_name": proj.name, "priority": proj.priority, "routes": routes})
        total_routes += len(routes)
    db.commit()
    return {"projects_evaluated": len(ordered), "total_routes": total_routes, "per_project": per_project}


def _compute_project_metrics(db: Session, project_id: str) -> dict:
    tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
    total = len(tasks)
    completed = [t for t in tasks if t.status == "completed"]
    blocked = [t for t in tasks if t.status == "blocked"]
    reworked = [t for t in tasks if t.rework_count > 0]
    reviewed_total = len(completed) + len(reworked)

    durations: list[float] = []
    for t in completed:
        if t.completed_at is not None:
            dt = (as_utc(t.completed_at) - as_utc(t.created_at)).total_seconds()
            if dt >= 0:
                durations.append(dt)

    failure_counts: dict[str, int] = {}
    for t in tasks:
        if t.failure_reason:
            failure_counts[t.failure_reason] = failure_counts.get(t.failure_reason, 0) + 1

    side_effects = db.scalars(select(TaskSideEffect).where(TaskSideEffect.project_id == project_id)).all()
    unknown_se = sum(1 for se in side_effects if se.status == "unknown")

    agent_stats: dict[str, dict] = {}
    for t in tasks:
        aid = t.assigned_agent_id or t.target_agent_id
        if not aid:
            continue
        rec = agent_stats.setdefault(aid, {"agent_id": aid, "completed": 0, "blocked": 0, "rework_tasks": 0})
        if t.status == "completed":
            rec["completed"] += 1
        elif t.status == "blocked":
            rec["blocked"] += 1
        if t.rework_count > 0:
            rec["rework_tasks"] += 1
    for rec in agent_stats.values():
        samples = rec["completed"] + rec["blocked"]
        rec["sample_count"] = samples
        rec["reliability"] = round(rec["completed"] / samples, 3) if samples >= 3 else None
        rec["confidence"] = "measured" if samples >= 3 else "insufficient_data"

    return {
        "project_id": project_id,
        "total_tasks": total,
        "completed_count": len(completed),
        "blocked_count": len(blocked),
        "rework_task_count": len(reworked),
        "acceptance_pass_rate": round(len(completed) / reviewed_total, 3) if reviewed_total > 0 else None,
        "rework_rate": round(len(reworked) / total, 3) if total > 0 else None,
        "side_effect_total": len(side_effects),
        "unknown_side_effect_rate": round(unknown_se / len(side_effects), 3) if side_effects else None,
        "avg_completion_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "failure_attribution": failure_counts,
        "agent_reliability": list(agent_stats.values()),
    }


@router.get("/projects/{project_id}/metrics")
def project_metrics(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    return _compute_project_metrics(db, project_id)


@router.get("/portfolio/metrics")
def portfolio_metrics(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    projects = db.scalars(select(Project).order_by(Project.priority.desc(), Project.created_at.desc())).all()
    items = [_compute_project_metrics(db, p.id) for p in projects]
    total_tasks = sum(m["total_tasks"] for m in items)
    total_completed = sum(m["completed_count"] for m in items)
    total_blocked = sum(m["blocked_count"] for m in items)
    return {
        "project_count": len(items),
        "total_tasks": total_tasks,
        "completed_count": total_completed,
        "blocked_count": total_blocked,
        "projects": items,
    }


@router.get("/briefing/daily")
def daily_briefing(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Evidence-backed daily briefing across projects referencing real IDs and pending owner work."""
    owner_only(actor)
    projects = db.scalars(select(Project).order_by(Project.priority.desc(), Project.created_at.desc())).all()
    project_rows = []
    pending_r3_refs = []
    pending_review_refs = []
    blocked_refs = []
    for p in projects:
        tasks = db.scalars(select(Task).where(Task.project_id == p.id)).all()
        for t in tasks:
            if t.status == "waiting_approval":
                pending_r3_refs.append({"project_id": p.id, "task_id": t.id, "title": t.title})
            elif t.status == "submitted":
                pending_review_refs.append({"project_id": p.id, "task_id": t.id, "title": t.title})
            elif t.status == "blocked":
                blocked_refs.append({"project_id": p.id, "task_id": t.id, "title": t.title, "failure_reason": t.failure_reason})
        project_rows.append({
            "project_id": p.id,
            "name": p.name,
            "status": p.status,
            "priority": p.priority,
            "task_total": len(tasks),
            "completed": sum(1 for t in tasks if t.status == "completed"),
            "running": sum(1 for t in tasks if t.status == "running"),
            "proposed_memories": db.query(Memory).filter(Memory.project_id == p.id, Memory.status == "proposed").count(),
            "proposed_decisions": db.query(Decision).filter(Decision.project_id == p.id, Decision.status == "proposed").count(),
            "proposed_skills": db.query(Skill).filter(Skill.project_id == p.id, Skill.status == "proposed").count(),
        })
    unread_urgent = db.query(Notification).filter(Notification.read.is_(False), Notification.level == "urgent").count()
    unread_total = db.query(Notification).filter(Notification.read.is_(False)).count()
    online_agents = db.query(Agent).filter(Agent.enabled.is_(True)).count()
    return {
        "generated_at": now(),
        "enabled_agents": online_agents,
        "unread_notifications": unread_total,
        "unread_urgent_notifications": unread_urgent,
        "pending_r3_tasks": pending_r3_refs[:20],
        "pending_review_tasks": pending_review_refs[:20],
        "blocked_tasks": blocked_refs[:20],
        "projects": project_rows,
    }


def _experiment_public(exp: Experiment) -> dict:
    return {
        "id": exp.id,
        "project_id": exp.project_id,
        "target_kind": exp.target_kind,
        "target_id": exp.target_id,
        "hypothesis": exp.hypothesis,
        "baseline_score": exp.baseline_score,
        "candidate_score": exp.candidate_score,
        "sample_count": exp.sample_count,
        "status": exp.status,
        "results": exp.results,
        "owner_note": exp.owner_note,
        "created_at": exp.created_at,
        "resolved_at": exp.resolved_at,
    }


@router.post("/projects/{project_id}/experiments", status_code=201)
def create_experiment(project_id: str, body: ExperimentIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_project(db, project_id, actor)
    exp = Experiment(
        project_id=project_id,
        target_kind=body.target_kind,
        target_id=body.target_id,
        hypothesis=body.hypothesis.strip(),
        status="proposed",
    )
    db.add(exp)
    db.flush()
    emit(db, actor, "experiment.proposed", exp.id, project_id, {"target_kind": exp.target_kind, "target_id": exp.target_id})
    db.commit()
    return _experiment_public(exp)


@router.get("/projects/{project_id}/experiments")
def list_experiments(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    rows = db.scalars(select(Experiment).where(Experiment.project_id == project_id).order_by(Experiment.created_at.desc())).all()
    return [_experiment_public(e) for e in rows]


@router.post("/experiments/{exp_id}/record-run")
def record_experiment_run(exp_id: str, body: ExperimentRunIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    exp = db.scalar(select(Experiment).where(Experiment.id == exp_id).with_for_update())
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    if exp.status in ("promoted", "rolled_back"):
        raise Conflict("Experiment is already finalized")
    exp.baseline_score = body.baseline_score
    exp.candidate_score = body.candidate_score
    exp.sample_count = body.sample_count
    exp.status = "evaluated"
    exp.results = {
        "delta": body.candidate_score - body.baseline_score,
        "passed_regression_gate": body.candidate_score >= body.baseline_score and body.sample_count >= 3,
        "notes": body.notes,
        "evaluated_at": now().isoformat(),
    }
    emit(db, actor, "experiment.evaluated", exp.id, exp.project_id, exp.results)
    db.commit()
    return _experiment_public(exp)


@router.post("/experiments/{exp_id}/promote")
def promote_experiment(exp_id: str, body: ExperimentResolveIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    exp = db.scalar(select(Experiment).where(Experiment.id == exp_id).with_for_update())
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    if exp.status != "evaluated":
        raise Conflict("Experiment must be evaluated before promotion")
    if not exp.results.get("passed_regression_gate"):
        raise Conflict("Candidate did not pass regression gate (score >= baseline and sample_count >= 3)")
    exp.status = "promoted"
    exp.owner_note = body.note.strip()
    exp.resolved_at = now()
    emit(db, actor, "experiment.promoted", exp.id, exp.project_id, {"target_kind": exp.target_kind, "target_id": exp.target_id})
    db.commit()
    return _experiment_public(exp)


@router.post("/experiments/{exp_id}/rollback")
def rollback_experiment(exp_id: str, body: ExperimentResolveIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    exp = db.scalar(select(Experiment).where(Experiment.id == exp_id).with_for_update())
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    if exp.status == "rolled_back":
        raise Conflict("Experiment is already rolled back")
    exp.status = "rolled_back"
    exp.owner_note = body.note.strip()
    exp.resolved_at = now()
    emit(db, actor, "experiment.rolled_back", exp.id, exp.project_id, {"target_kind": exp.target_kind, "target_id": exp.target_id})
    db.commit()
    return _experiment_public(exp)


def _notification_public(n: Notification) -> dict:
    return {
        "id": n.id,
        "project_id": n.project_id,
        "level": n.level,
        "title": n.title,
        "body": n.body,
        "source_kind": n.source_kind,
        "source_id": n.source_id,
        "read": n.read,
        "created_at": n.created_at,
    }


@router.get("/notifications")
def list_notifications(level: str | None = None, unread_only: bool = False, limit: int = 50, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    if limit < 1 or limit > 200:
        raise HTTPException(422, "limit must be 1..200")
    query = select(Notification)
    if level in ("urgent", "important", "digest"):
        query = query.where(Notification.level == level)
    if unread_only:
        query = query.where(Notification.read.is_(False))
    rows = db.scalars(query.order_by(Notification.created_at.desc()).limit(limit)).all()
    return [_notification_public(n) for n in rows]


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    item = db.get(Notification, notification_id)
    if item is None:
        raise HTTPException(404, "Notification not found")
    item.read = True
    db.commit()
    return _notification_public(item)


@router.post("/notifications/read-all")
def mark_all_notifications_read(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    items = db.scalars(select(Notification).where(Notification.read.is_(False))).all()
    for item in items:
        item.read = True
    db.commit()
    return {"marked_read": len(items)}
