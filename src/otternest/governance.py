"""Owner-reviewed command plans and evidence-first multi-agent decisions.

No natural-language interpretation or model authority lives here. A future LLM
may propose structured intents, but only the authenticated owner can preview
and confirm them. Every execution rechecks authoritative state under locks.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import (A2ADelegation, Agent, CommandPlan, Decision, DecisionReview,
                     Membership, Project, Task, now)
from .security import Actor, authenticate, owner_only
from .services import (Conflict, emit, release_dependents, require_project,
                       require_task, task_ready_status)

router = APIRouter(prefix="/api/v1", tags=["governance"])


class CommandDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    action: Literal["authorize_task", "review_task", "cancel_task", "route_task"]
    accepted: bool | None = None
    reason: str = Field(default="", max_length=3000)
    target_agent_id: str | None = None


class ReviewDraft(BaseModel):
    stance: Literal["support", "oppose", "alternative"]
    reasoning: str = Field(min_length=10, max_length=6000)
    evidence: list[str] = Field(default_factory=list, max_length=8)


class ResolutionDraft(BaseModel):
    owner_note: str = Field(default="", max_length=6000)


def _command_public(plan: CommandPlan) -> dict:
    return {"id": plan.id, "task_id": plan.task_id, "project_id": plan.project_id,
            "action": plan.action, "parameters": plan.parameters,
            "expected_version": plan.expected_version, "status": plan.status,
            "expires_at": plan.expires_at, "result": plan.result,
            "confirmation_required": True}


def _normalize(draft: CommandDraft) -> dict:
    """Strictly separate allowed parameters for each operation."""
    if draft.action == "review_task":
        if draft.accepted is None:
            raise HTTPException(422, "Review outcome required")
        if not draft.accepted and not draft.reason.strip():
            raise HTTPException(422, "Rejection reason required")
        if draft.target_agent_id is not None:
            raise HTTPException(422, "Unexpected target for review")
        return {"accepted": draft.accepted, "reason": draft.reason}
    if draft.accepted is not None or draft.reason or (draft.action != "route_task" and draft.target_agent_id is not None):
        raise HTTPException(422, "Unexpected command parameters")
    return {"target_agent_id": draft.target_agent_id} if draft.action == "route_task" else {}


def _check_command(db: Session, task: Task, action: str, params: dict) -> None:
    project = db.get(Project, task.project_id)
    if project is None or project.status != "active":
        raise Conflict("Project is not active")
    delegation = db.scalar(select(A2ADelegation).where(A2ADelegation.task_id == task.id))
    if action == "authorize_task":
        if task.status != "waiting_approval" or task.approval_granted:
            raise Conflict("Task is not awaiting owner authorization")
    elif action == "review_task":
        if task.status != "submitted":
            raise Conflict("Task is not submitted for review")
    elif action == "cancel_task":
        if task.status in {"completed", "cancelled"}:
            raise Conflict("Task is terminal")
        if delegation and delegation.status not in {"canceled", "failed", "submitted"}:
            raise Conflict("Remote delegation is active or unknown; use remote cancellation first")
    elif action == "route_task":
        if task.status not in {"ready", "waiting_dependencies", "waiting_approval"}:
            raise Conflict("Active/terminal tasks cannot be rerouted")
        if delegation is not None and delegation.status != "released":
            raise Conflict("Release external delegation before rerouting")
        agent_id = params["target_agent_id"]
        if agent_id:
            member = db.scalar(select(Membership).join(Agent, Membership.agent_id == Agent.id).where(
                Membership.project_id == task.project_id, Membership.agent_id == agent_id,
                Membership.role.in_(("worker", "manager", "reviewer")), Agent.enabled.is_(True)))
            agent = db.get(Agent, agent_id)
            if member is None or agent is None:
                raise HTTPException(422, "Target must be an enabled project worker")
            if not set(task.required_capabilities).issubset(set(agent.verified_capabilities)):
                raise HTTPException(422, "Target lacks verified capabilities")
    else:
        raise HTTPException(422, "Unknown command action")


@router.post("/command/plans", status_code=201)
def create_command_plan(body: CommandDraft, actor: Actor = Depends(authenticate),
                        db: Session = Depends(get_session)):
    owner_only(actor)
    task = require_task(db, body.task_id, actor, lock=True)
    params = _normalize(body)
    _check_command(db, task, body.action, params)
    plan = CommandPlan(project_id=task.project_id, task_id=task.id,
                       action=body.action, parameters=params, expected_version=task.version,
                       expires_at=now() + timedelta(minutes=10))
    db.add(plan)
    db.flush()
    emit(db, actor, "command.previewed", plan.id, plan.project_id,
         {"action": plan.action, "task_id": task.id, "expected_version": task.version})
    db.commit()
    return {**_command_public(plan), "task_title": task.title,
            "task_status": task.status, "task_risk": task.risk}


@router.get("/command/plans/{plan_id}")
def get_command_plan(plan_id: str, actor: Actor = Depends(authenticate),
                     db: Session = Depends(get_session)):
    owner_only(actor)
    plan = db.get(CommandPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "Command plan not found")
    return _command_public(plan)


@router.post("/command/plans/{plan_id}/confirm")
def confirm_command_plan(plan_id: str, actor: Actor = Depends(authenticate),
                         db: Session = Depends(get_session)):
    owner_only(actor)
    plan = db.scalar(select(CommandPlan).where(CommandPlan.id == plan_id).with_for_update())
    if plan is None:
        raise HTTPException(404, "Command plan not found")
    if plan.status == "executed":
        return _command_public(plan)  # idempotent confirmation, no repeated event
    if plan.status != "pending":
        raise Conflict("Command is no longer pending")
    expiry = plan.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now():
        raise Conflict("Command preview expired; create a new preview")
    task = require_task(db, plan.task_id, actor, lock=True)
    if task.project_id != plan.project_id or task.version != plan.expected_version:
        raise Conflict("Task changed after preview; create a new preview")
    _check_command(db, task, plan.action, plan.parameters)
    if plan.action == "authorize_task":
        task.approval_granted = True
        task.status = task_ready_status(db, task)
        event = "task.authorized"
    elif plan.action == "route_task":
        task.target_agent_id = plan.parameters["target_agent_id"]
        event = "task.routed"
    elif plan.action == "review_task":
        accepted = plan.parameters["accepted"]
        if task.risk == "R3" and not accepted:
            task.approval_granted = False
        delegation = db.scalar(select(A2ADelegation).where(A2ADelegation.task_id == task.id))
        if delegation and not accepted:
            delegation.status = "changes_requested"
            delegation.updated_at = now()
        task.status = "completed" if accepted else ("blocked" if delegation else task_ready_status(db, task))
        task.assigned_agent_id = None
        event = "task.accepted" if accepted else "task.changes_requested"
    else:  # cancel_task
        task.status = "cancelled"
        task.lease_epoch += 1
        task.lease_until = None
        event = "task.cancelled"
    task.version += 1
    emit(db, actor, event, task.id, task.project_id,
         {"via_command": plan.id, **plan.parameters})
    if plan.action == "review_task" and plan.parameters["accepted"]:
        db.flush()
        release_dependents(db, task, actor)
    plan.status = "executed"
    plan.executed_at = now()
    plan.result = {"task_id": task.id, "status": task.status, "version": task.version}
    emit(db, actor, "command.executed", plan.id, plan.project_id, {"action": plan.action})
    db.commit()
    return _command_public(plan)


def _decision(db: Session, decision_id: str, actor: Actor, *, lock: bool = False) -> Decision:
    query = select(Decision).where(Decision.id == decision_id)
    if lock:
        query = query.with_for_update()
    decision = db.scalar(query)
    if decision is None:
        raise HTTPException(404, "Decision not found")
    require_project(db, decision.project_id, actor)
    return decision


def _review_public(review: DecisionReview) -> dict:
    return {"id": review.id, "decision_id": review.decision_id,
            "agent_id": review.agent_id, "stance": review.stance,
            "reasoning": review.reasoning, "evidence": review.evidence,
            "created_at": review.created_at, "evidence_verified": False}


@router.post("/decisions/{decision_id}/reviews", status_code=201)
def add_decision_review(decision_id: str, body: ReviewDraft,
                        actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    if actor.is_owner:
        raise HTTPException(403, "A registered Agent must submit independent evidence")
    decision = _decision(db, decision_id, actor, lock=True)
    require_project(db, decision.project_id, actor,
                    roles=("worker", "reviewer", "manager", "advisor"))
    if decision.status != "proposed":
        raise Conflict("Decision is already resolved")
    if any(not e.strip() or len(e) > 500 for e in body.evidence):
        raise HTTPException(422, "Invalid evidence reference")
    if db.scalar(select(DecisionReview.id).where(
            DecisionReview.decision_id == decision.id, DecisionReview.agent_id == actor.id)):
        raise Conflict("Agent has already submitted independent review")
    review = DecisionReview(project_id=decision.project_id, decision_id=decision.id,
                            agent_id=actor.id, stance=body.stance,
                            reasoning=body.reasoning, evidence=body.evidence)
    db.add(review)
    db.flush()
    emit(db, actor, "decision.review_submitted", decision.id, decision.project_id,
         {"review_id": review.id, "stance": review.stance})
    db.commit()
    return _review_public(review)


@router.get("/decisions/{decision_id}/reviews")
def get_decision_reviews(decision_id: str, actor: Actor = Depends(authenticate),
                         db: Session = Depends(get_session)):
    decision = _decision(db, decision_id, actor)
    if not actor.is_owner and decision.status != "approved":
        # Prevent anchoring while reviews are still being collected.
        raise HTTPException(403, "Reviews remain blind until the owner resolves the decision")
    reviews = db.scalars(select(DecisionReview).where(DecisionReview.decision_id == decision.id)
                         .order_by(DecisionReview.created_at, DecisionReview.id)).all()
    return [_review_public(r) for r in reviews]


@router.post("/decisions/{decision_id}/resolve")
def resolve_decision(decision_id: str, body: ResolutionDraft,
                     actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    decision = _decision(db, decision_id, actor, lock=True)
    if decision.status != "proposed":
        raise Conflict("Decision is no longer proposed")
    dissent = db.scalars(select(DecisionReview.id).where(
        DecisionReview.decision_id == decision.id,
        DecisionReview.stance.in_(("oppose", "alternative")))).all()
    if dissent and len(body.owner_note.strip()) < 10:
        raise Conflict("Independent dissent requires a substantive owner resolution note")
    decision.status = "approved"
    decision.owner_review_note = body.owner_note.strip()
    emit(db, actor, "decision.approved", decision.id, decision.project_id,
         {"dissent_count": len(dissent), "resolution_note": bool(decision.owner_review_note)})
    db.commit()
    return {"id": decision.id, "status": decision.status,
            "dissent_count": len(dissent), "owner_review_note": decision.owner_review_note}
