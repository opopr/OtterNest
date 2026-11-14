"""External side-effect idempotency ledger, knowledge/skill lifecycle, cross-project promotion, and portable export."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import (
    Agent,
    Artifact,
    CrossProjectShare,
    Decision,
    DecisionRebuttal,
    DecisionDeliberation,
    DecisionReview,
    Event,
    Goal,
    Membership,
    Memory,
    Project,
    ProjectCharter,
    Skill,
    SkillEvaluation,
    Task,
    TaskCheckpoint,
    TaskDependency,
    TaskSideEffect,
    now,
)
from .security import Actor, authenticate, owner_only
from .services import Conflict, emit, ensure_lease, require_project, require_task

router = APIRouter(prefix="/api/v1", tags=["Side Effects, Knowledge Lifecycle & Export"])


class SideEffectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(ge=1)
    idempotency_key: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    target_system: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)


class SideEffectReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(ge=1)
    status: Literal["reported_executed", "unknown"]
    evidence_summary: str = Field(min_length=1, max_length=6000)


class SideEffectReconcileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["verified_executed", "verified_not_executed"]
    note: str = Field(min_length=5, max_length=3000)


class MemoryReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=20000)
    source: str = Field(min_length=1, max_length=3000)
    reason: str = Field(min_length=5, max_length=2000)


class ArchiveReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=2000)


class SkillEvaluationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "fail", "concern"]
    notes: str = Field(min_length=5, max_length=4000)
    task_id: str | None = None


class CrossProjectShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_project_id: str
    item_kind: Literal["memory", "skill"]
    source_item_id: str
    rationale: str = Field(min_length=5, max_length=3000)


class ResolveShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    note: str = Field(min_length=5, max_length=2000)


def _side_effect_public(se: TaskSideEffect) -> dict:
    return {
        "id": se.id,
        "project_id": se.project_id,
        "task_id": se.task_id,
        "agent_id": se.agent_id,
        "lease_epoch": se.lease_epoch,
        "idempotency_key": se.idempotency_key,
        "target_system": se.target_system,
        "description": se.description,
        "status": se.status,
        "owner_verified": se.status in ("verified_executed", "verified_not_executed"),
        "reversible_by_control_plane": False,
        "evidence_summary": se.evidence_summary,
        "owner_note": se.owner_note,
        "created_at": se.created_at,
        "updated_at": se.updated_at,
    }


def _skill_eval_public(ev: SkillEvaluation) -> dict:
    return {
        "id": ev.id,
        "project_id": ev.project_id,
        "skill_id": ev.skill_id,
        "actor_kind": ev.actor_kind,
        "actor_id": ev.actor_id,
        "verdict": ev.verdict,
        "notes": ev.notes,
        "task_id": ev.task_id,
        "created_at": ev.created_at,
    }


def _share_public(share: CrossProjectShare) -> dict:
    return {
        "id": share.id,
        "source_project_id": share.source_project_id,
        "target_project_id": share.target_project_id,
        "item_kind": share.item_kind,
        "source_item_id": share.source_item_id,
        "promoted_item_id": share.promoted_item_id,
        "proposed_by": share.proposed_by,
        "rationale": share.rationale,
        "status": share.status,
        "owner_approval_required": True,
        "owner_note": share.owner_note,
        "created_at": share.created_at,
        "resolved_at": share.resolved_at,
    }


@router.post("/tasks/{task_id}/side-effects", status_code=201)
def register_side_effect(
    task_id: str, body: SideEffectIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    target_system = body.target_system.strip()
    description = body.description.strip()
    if not target_system or not description:
        raise HTTPException(422, "target_system and description must be non-empty")
    task = require_task(db, task_id, actor, lock=True)
    ensure_lease(task, actor, body.epoch)
    existing = db.scalar(
        select(TaskSideEffect)
        .where(TaskSideEffect.task_id == task.id, TaskSideEffect.idempotency_key == body.idempotency_key)
        .with_for_update()
    )
    if existing is not None:
        if existing.status == "verified_not_executed":
            existing.agent_id = actor.id
            existing.lease_epoch = task.lease_epoch
            existing.target_system = target_system
            existing.description = description
            existing.status = "pending"
            existing.evidence_summary = ""
            existing.updated_at = now()
            emit(
                db,
                actor,
                "task.side_effect_reopened",
                task.id,
                task.project_id,
                {"side_effect_id": existing.id, "idempotency_key": existing.idempotency_key, "epoch": task.lease_epoch},
            )
            db.commit()
            return _side_effect_public(existing)
        raise Conflict("Side-effect idempotency key already registered or executed; do not repeat external action")

    record = TaskSideEffect(
        project_id=task.project_id,
        task_id=task.id,
        agent_id=actor.id,
        lease_epoch=task.lease_epoch,
        idempotency_key=body.idempotency_key,
        target_system=target_system,
        description=description,
        status="pending",
    )
    db.add(record)
    db.flush()
    emit(
        db,
        actor,
        "task.side_effect_registered",
        task.id,
        task.project_id,
        {"side_effect_id": record.id, "idempotency_key": record.idempotency_key, "epoch": task.lease_epoch},
    )
    db.commit()
    return _side_effect_public(record)


@router.post("/side-effects/{effect_id}/report")
def report_side_effect(
    effect_id: str, body: SideEffectReportIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    evidence = body.evidence_summary.strip()
    if not evidence:
        raise HTTPException(422, "evidence_summary must be non-empty")
    effect = db.scalar(select(TaskSideEffect).where(TaskSideEffect.id == effect_id).with_for_update())
    if effect is None:
        raise HTTPException(404, "Side effect not found")
    task = require_task(db, effect.task_id, actor, lock=True)
    ensure_lease(task, actor, body.epoch)
    if effect.agent_id != actor.id or effect.lease_epoch != body.epoch:
        raise Conflict("Side-effect belongs to a different lease epoch or agent")
    if effect.status != "pending":
        raise Conflict("Side-effect outcome was already reported or reconciled")
    effect.status = body.status
    effect.evidence_summary = evidence
    effect.updated_at = now()
    emit(
        db,
        actor,
        "task.side_effect_reported",
        task.id,
        task.project_id,
        {"side_effect_id": effect.id, "status": effect.status, "epoch": body.epoch},
    )
    db.commit()
    return _side_effect_public(effect)


@router.post("/side-effects/{effect_id}/reconcile")
def reconcile_side_effect(
    effect_id: str, body: SideEffectReconcileIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    note = body.note.strip()
    if len(note) < 5:
        raise HTTPException(422, "Reconciliation note must be at least 5 characters")
    effect = db.scalar(select(TaskSideEffect).where(TaskSideEffect.id == effect_id).with_for_update())
    if effect is None:
        raise HTTPException(404, "Side effect not found")
    effect.status = body.status
    effect.owner_note = note
    effect.updated_at = now()
    emit(
        db,
        actor,
        "task.side_effect_reconciled",
        effect.task_id,
        effect.project_id,
        {"side_effect_id": effect.id, "status": effect.status},
    )
    db.commit()
    return _side_effect_public(effect)


@router.get("/tasks/{task_id}/side-effects")
def list_side_effects(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    task = require_task(db, task_id, actor)
    rows = db.scalars(
        select(TaskSideEffect)
        .where(TaskSideEffect.task_id == task.id)
        .order_by(TaskSideEffect.created_at.asc(), TaskSideEffect.id.asc())
    ).all()
    return [_side_effect_public(se) for se in rows]


@router.post("/memories/{memory_id}/revise")
def revise_memory(
    memory_id: str, body: MemoryReviseIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    title = body.title.strip()
    content = body.content.strip()
    source = body.source.strip()
    reason = body.reason.strip()
    if not title or not content or not source or len(reason) < 5:
        raise HTTPException(422, "Title, content, source, and substantive revision reason are required")
    memory = db.scalar(select(Memory).where(Memory.id == memory_id).with_for_update())
    if memory is None:
        raise HTTPException(404, "Memory not found")
    if memory.status == "archived":
        raise Conflict("Archived memory cannot be revised")
    memory.title = title
    memory.content = content
    memory.source = source
    memory.status = "published"
    memory.version += 1
    emit(
        db,
        actor,
        "memory.revised",
        memory.id,
        memory.project_id,
        {"version": memory.version, "reason": reason},
    )
    db.commit()
    return {
        "id": memory.id,
        "project_id": memory.project_id,
        "kind": memory.kind,
        "title": memory.title,
        "content": memory.content,
        "source": memory.source,
        "status": memory.status,
        "version": memory.version,
    }


@router.post("/memories/{memory_id}/archive")
def archive_memory(
    memory_id: str, body: ArchiveReasonIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, "Archive reason must be at least 5 characters")
    memory = db.scalar(select(Memory).where(Memory.id == memory_id).with_for_update())
    if memory is None:
        raise HTTPException(404, "Memory not found")
    if memory.status == "archived":
        raise Conflict("Memory is already archived")
    memory.status = "archived"
    memory.version += 1
    emit(
        db,
        actor,
        "memory.archived",
        memory.id,
        memory.project_id,
        {"version": memory.version, "reason": reason},
    )
    db.commit()
    return {"id": memory.id, "status": memory.status, "version": memory.version}


@router.post("/skills/{skill_id}/evaluations", status_code=201)
def add_skill_evaluation(
    skill_id: str, body: SkillEvaluationIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    notes = body.notes.strip()
    if len(notes) < 5:
        raise HTTPException(422, "Evaluation notes must be at least 5 characters")
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    require_project(db, skill.project_id, actor, roles=("worker", "manager", "reviewer"))
    if body.task_id is not None:
        task = db.get(Task, body.task_id)
        if task is None or task.project_id != skill.project_id:
            raise HTTPException(422, "Task must belong to the same project as the skill")
    ev = SkillEvaluation(
        project_id=skill.project_id,
        skill_id=skill.id,
        actor_kind=actor.kind,
        actor_id=actor.id,
        verdict=body.verdict,
        notes=notes,
        task_id=body.task_id,
    )
    db.add(ev)
    db.flush()
    emit(
        db,
        actor,
        "skill.evaluated",
        skill.id,
        skill.project_id,
        {"evaluation_id": ev.id, "verdict": ev.verdict},
    )
    db.commit()
    return _skill_eval_public(ev)


@router.get("/skills/{skill_id}/evaluations")
def list_skill_evaluations(skill_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    require_project(db, skill.project_id, actor)
    rows = db.scalars(
        select(SkillEvaluation)
        .where(SkillEvaluation.skill_id == skill.id)
        .order_by(SkillEvaluation.created_at.asc(), SkillEvaluation.id.asc())
    ).all()
    return [_skill_eval_public(ev) for ev in rows]


@router.post("/skills/{skill_id}/archive")
def archive_skill(
    skill_id: str, body: ArchiveReasonIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, "Archive reason must be at least 5 characters")
    skill = db.scalar(select(Skill).where(Skill.id == skill_id).with_for_update())
    if skill is None:
        raise HTTPException(404, "Skill not found")
    if skill.status == "archived":
        raise Conflict("Skill is already archived")
    skill.status = "archived"
    emit(
        db,
        actor,
        "skill.archived",
        skill.id,
        skill.project_id,
        {"name": skill.name, "version": skill.version, "reason": reason},
    )
    db.commit()
    return {"id": skill.id, "name": skill.name, "version": skill.version, "status": skill.status}


@router.post("/projects/{project_id}/knowledge-shares", status_code=201)
def propose_knowledge_share(
    project_id: str, body: CrossProjectShareIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    rationale = body.rationale.strip()
    if len(rationale) < 5:
        raise HTTPException(422, "Rationale must be at least 5 characters")
    if project_id == body.target_project_id:
        raise HTTPException(422, "Target project must differ from source project")
    require_project(db, project_id, actor, roles=("worker", "manager", "reviewer"))
    require_project(db, body.target_project_id, actor, roles=("worker", "manager", "reviewer"))
    if body.item_kind == "memory":
        item = db.get(Memory, body.source_item_id)
    else:
        item = db.get(Skill, body.source_item_id)
    if item is None or item.project_id != project_id:
        raise HTTPException(404, "Source item not found in source project")
    if item.status != "published":
        raise Conflict("Only published project knowledge or skills can be proposed for cross-project sharing")

    share = CrossProjectShare(
        source_project_id=project_id,
        target_project_id=body.target_project_id,
        item_kind=body.item_kind,
        source_item_id=body.source_item_id,
        proposed_by=f"{actor.kind}:{actor.id}",
        rationale=rationale,
        status="proposed",
    )
    db.add(share)
    db.flush()
    emit(
        db,
        actor,
        "knowledge.share_proposed",
        share.id,
        project_id,
        {
            "target_project_id": body.target_project_id,
            "item_kind": body.item_kind,
            "source_item_id": body.source_item_id,
        },
    )
    db.commit()
    return _share_public(share)


@router.get("/projects/{project_id}/knowledge-shares")
def list_knowledge_shares(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    require_project(db, project_id, actor)
    rows = db.scalars(
        select(CrossProjectShare)
        .where(
            (CrossProjectShare.source_project_id == project_id)
            | (CrossProjectShare.target_project_id == project_id)
        )
        .order_by(CrossProjectShare.created_at.asc(), CrossProjectShare.id.asc())
    ).all()
    return [_share_public(s) for s in rows]


@router.post("/knowledge-shares/{share_id}/resolve")
def resolve_knowledge_share(
    share_id: str, body: ResolveShareIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)
):
    owner_only(actor)
    note = body.note.strip()
    if len(note) < 5:
        raise HTTPException(422, "Resolution note must be at least 5 characters")
    share = db.scalar(select(CrossProjectShare).where(CrossProjectShare.id == share_id).with_for_update())
    if share is None:
        raise HTTPException(404, "Knowledge share proposal not found")
    if share.status != "proposed":
        raise Conflict("Knowledge share is no longer proposed")
    target_project = db.scalar(select(Project).where(Project.id == share.target_project_id).with_for_update())
    if body.approved:
        if target_project is None or target_project.status != "active":
            raise Conflict("Target project is not active")
        if share.item_kind == "memory":
            src_mem = db.get(Memory, share.source_item_id)
            if src_mem is None or src_mem.project_id != share.source_project_id or src_mem.status != "published":
                raise Conflict("Source memory is no longer published")
            cloned = Memory(
                project_id=share.target_project_id,
                kind=src_mem.kind,
                title=src_mem.title,
                content=src_mem.content,
                source=f"cross_project:{share.source_project_id}:memory:{src_mem.id} | {src_mem.source}"[:3000],
                status="published",
            )
            db.add(cloned)
            db.flush()
            share.promoted_item_id = cloned.id
        else:
            src_skill = db.get(Skill, share.source_item_id)
            if src_skill is None or src_skill.project_id != share.source_project_id or src_skill.status != "published":
                raise Conflict("Source skill is no longer published")
            dup = db.scalar(
                select(Skill.id).where(
                    Skill.project_id == share.target_project_id,
                    Skill.name == src_skill.name,
                    Skill.version == src_skill.version,
                )
            )
            if dup is not None:
                raise Conflict("Target project already has this skill revision")
            cloned = Skill(
                project_id=share.target_project_id,
                name=src_skill.name,
                version=src_skill.version,
                content=src_skill.content,
                source=f"cross_project:{share.source_project_id}:skill:{src_skill.id} | {src_skill.source}"[:3000],
                status="published",
            )
            db.add(cloned)
            db.flush()
            share.promoted_item_id = cloned.id
        share.status = "approved"
    else:
        share.status = "rejected"

    share.owner_note = note
    share.resolved_at = now()
    emit(
        db,
        actor,
        "knowledge.share_approved" if body.approved else "knowledge.share_rejected",
        share.id,
        share.target_project_id,
        {"source_project_id": share.source_project_id, "promoted_item_id": share.promoted_item_id},
    )
    db.commit()
    return _share_public(share)


@router.get("/projects/{project_id}/export")
def export_project_bundle(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    project = require_project(db, project_id, actor)
    charter = db.get(ProjectCharter, project_id)
    goals = db.scalars(select(Goal).where(Goal.project_id == project_id).order_by(Goal.id)).all()
    memberships = db.scalars(select(Membership).where(Membership.project_id == project_id).order_by(Membership.id)).all()
    tasks = db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.created_at, Task.id)).all()
    task_ids = [t.id for t in tasks]
    deps = (
        db.scalars(
            select(TaskDependency)
            .where(TaskDependency.task_id.in_(task_ids))
            .order_by(TaskDependency.task_id, TaskDependency.depends_on_id)
        ).all()
        if task_ids
        else []
    )
    checkpoints = db.scalars(
        select(TaskCheckpoint)
        .where(TaskCheckpoint.project_id == project_id)
        .order_by(TaskCheckpoint.task_id, TaskCheckpoint.lease_epoch, TaskCheckpoint.step_number)
    ).all()
    side_effects = db.scalars(
        select(TaskSideEffect)
        .where(TaskSideEffect.project_id == project_id)
        .order_by(TaskSideEffect.created_at, TaskSideEffect.id)
    ).all()
    memories = db.scalars(
        select(Memory).where(Memory.project_id == project_id).order_by(Memory.created_at, Memory.id)
    ).all()
    decisions = db.scalars(select(Decision).where(Decision.project_id == project_id).order_by(Decision.id)).all()
    decision_reviews = db.scalars(
        select(DecisionReview)
        .where(DecisionReview.project_id == project_id)
        .order_by(DecisionReview.created_at, DecisionReview.id)
    ).all()
    decision_rebuttals = db.scalars(
        select(DecisionRebuttal)
        .where(DecisionRebuttal.project_id == project_id)
        .order_by(DecisionRebuttal.created_at, DecisionRebuttal.id)
    ).all()
    decision_deliberations = db.scalars(
        select(DecisionDeliberation).where(DecisionDeliberation.project_id == project_id).order_by(DecisionDeliberation.opened_at, DecisionDeliberation.decision_id)
    ).all()
    skills = db.scalars(
        select(Skill).where(Skill.project_id == project_id).order_by(Skill.created_at, Skill.id)
    ).all()
    skill_evaluations = db.scalars(
        select(SkillEvaluation)
        .where(SkillEvaluation.project_id == project_id)
        .order_by(SkillEvaluation.created_at, SkillEvaluation.id)
    ).all()
    shares = db.scalars(
        select(CrossProjectShare)
        .where(
            (CrossProjectShare.source_project_id == project_id)
            | (CrossProjectShare.target_project_id == project_id)
        )
        .order_by(CrossProjectShare.created_at, CrossProjectShare.id)
    ).all()
    artifacts = db.scalars(
        select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.created_at, Artifact.id)
    ).all()
    events = db.scalars(select(Event).where(Event.project_id == project_id).order_by(Event.id)).all()
    agent_ids = {
        *(m.agent_id for m in memberships),
        *(agent_id for t in tasks for agent_id in (t.assigned_agent_id, t.target_agent_id) if agent_id),
        *(cp.agent_id for cp in checkpoints),
        *(effect.agent_id for effect in side_effects),
        *(review.agent_id for review in decision_reviews),
        *(rebuttal.agent_id for rebuttal in decision_rebuttals),
        *(evaluation.actor_id for evaluation in skill_evaluations if evaluation.actor_kind == "agent"),
    }
    agents = db.scalars(select(Agent).where(Agent.id.in_(agent_ids)).order_by(Agent.id)).all() if agent_ids else []

    bundle_data = {
        "agents": [{"id": a.id, "name": a.name, "platform": a.platform} for a in agents],
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "status": project.status,
            "created_at": project.created_at,
        },
        "charter": None
        if charter is None
        else {
            "scope_boundary": charter.scope_boundary,
            "stop_conditions": charter.stop_conditions,
            "max_concurrent_tasks": charter.max_concurrent_tasks,
            "max_total_tasks": charter.max_total_tasks,
            "updated_at": charter.updated_at,
        },
        "goals": [
            {"id": g.id, "title": g.title, "success_criteria": g.success_criteria, "status": g.status}
            for g in goals
        ],
        "memberships": [{"id": m.id, "agent_id": m.agent_id, "role": m.role} for m in memberships],
        "tasks": [
            {
                "id": t.id,
                "goal_id": t.goal_id,
                "title": t.title,
                "instruction": t.instruction,
                "acceptance": t.acceptance,
                "status": t.status,
                "risk": t.risk,
                "assigned_agent_id": t.assigned_agent_id,
                "target_agent_id": t.target_agent_id,
                "priority": t.priority,
                "required_capabilities": t.required_capabilities,
                "approval_granted": t.approval_granted,
                "lease_epoch": t.lease_epoch,
                "result_summary": t.result_summary,
                "version": t.version,
                "created_at": t.created_at,
            }
            for t in tasks
        ],
        "task_dependencies": [{"task_id": d.task_id, "depends_on_id": d.depends_on_id} for d in deps],
        "checkpoints": [
            {
                "id": cp.id,
                "task_id": cp.task_id,
                "agent_id": cp.agent_id,
                "lease_epoch": cp.lease_epoch,
                "step_number": cp.step_number,
                "summary": cp.summary,
                "next_steps": cp.next_steps,
                "artifact_ids": cp.artifact_ids,
                "verification_status": cp.verification_status,
                "owner_note": cp.owner_note,
                "created_at": cp.created_at,
                "verified_at": cp.verified_at,
            }
            for cp in checkpoints
        ],
        "side_effects": [_side_effect_public(se) for se in side_effects],
        "memories": [
            {
                "id": m.id,
                "kind": m.kind,
                "title": m.title,
                "content": m.content,
                "source": m.source,
                "status": m.status,
                "version": m.version,
                "created_at": m.created_at,
            }
            for m in memories
        ],
        "decisions": [
            {
                "id": d.id,
                "question": d.question,
                "resolution": d.resolution,
                "rationale": d.rationale,
                "status": d.status,
                "owner_review_note": d.owner_review_note,
            }
            for d in decisions
        ],
        "decision_reviews": [
            {
                "id": r.id,
                "decision_id": r.decision_id,
                "agent_id": r.agent_id,
                "stance": r.stance,
                "reasoning": r.reasoning,
                "evidence": r.evidence,
                "created_at": r.created_at,
            }
            for r in decision_reviews
        ],
        "decision_rebuttals": [
            {
                "id": rb.id,
                "decision_id": rb.decision_id,
                "agent_id": rb.agent_id,
                "responds_to_review_id": rb.responds_to_review_id,
                "stance": rb.stance,
                "reasoning": rb.reasoning,
                "evidence": rb.evidence,
                "created_at": rb.created_at,
            }
            for rb in decision_rebuttals
        ],
        "decision_deliberations": [
            {
                "decision_id": d.decision_id,
                "status": d.status,
                "closing_note": d.closing_note,
                "opened_at": d.opened_at,
                "closed_at": d.closed_at,
            }
            for d in decision_deliberations
        ],
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "version": s.version,
                "content": s.content,
                "source": s.source,
                "status": s.status,
                "created_at": s.created_at,
            }
            for s in skills
        ],
        "skill_evaluations": [_skill_eval_public(ev) for ev in skill_evaluations],
        "cross_project_shares": [_share_public(sh) for sh in shares],
        "artifacts": [
            {
                "id": a.id,
                "family_id": a.family_id,
                "version": a.version,
                "task_id": a.task_id,
                "name": a.name,
                "checksum": a.checksum,
                "review_status": a.review_status,
                "review_note": a.review_note,
                "reviewed_at": a.reviewed_at,
                "created_at": a.created_at,
            }
            for a in artifacts
        ],
        "events": [
            {
                "id": e.id,
                "kind": e.kind,
                "subject_id": e.subject_id,
                "payload": e.payload,
                "created_at": e.created_at,
            }
            for e in events
        ],
    }
    encoded_bundle = jsonable_encoder(bundle_data)
    canonical = json.dumps(encoded_bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return {
        **encoded_bundle,
        "schema_version": "otternest.export.v1",
        "exported_at": now(),
        "secrets_included": False,
        "sha256_digest": digest,
    }
