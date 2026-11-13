"""At most two rounds of independent, owner-controlled decision review."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_session
from .governance import _decision, _review_public
from .models import (DecisionDeliberation, DecisionRebuttal, DecisionReview, DecisionAutoPolicy,
                     Agent, Membership, now)
from .security import Actor, authenticate, owner_only
from .services import Conflict, emit, require_project

router = APIRouter(prefix="/api/v1", tags=["deliberation"])

class RebuttalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    responds_to_review_id: str
    stance: str = Field(pattern="^(support|oppose|alternative)$")
    reasoning: str = Field(min_length=10, max_length=6000)
    evidence: list[str] = Field(default_factory=list, max_length=8)

class CloseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=6000)

def _session(s):
    return {"decision_id": s.decision_id, "project_id": s.project_id, "round": 2,
            "status": s.status, "opened_at": s.opened_at,
            "closed_at": s.closed_at, "closing_note": s.closing_note}

def _reply(r, db: Session | None = None):
    from .evidence import inspect_references
    checks = inspect_references(db, r.project_id, r.evidence) if db is not None else []
    return {"id": r.id, "decision_id": r.decision_id, "agent_id": r.agent_id,
            "responds_to_review_id": r.responds_to_review_id, "stance": r.stance,
            "reasoning": r.reasoning, "evidence": r.evidence, "evidence_checks": checks,
            "evidence_verified": False, "created_at": r.created_at}

def _participant(db, decision_id, actor):
    return bool(db.scalar(select(DecisionReview.id).where(
        DecisionReview.decision_id == decision_id, DecisionReview.agent_id == actor.id)))


class AutoPolicyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_ids: list[str] = Field(min_length=2, max_length=8)

    @field_validator("reviewer_ids")
    @classmethod
    def unique_reviewers(cls, ids: list[str]) -> list[str]:
        if len(set(ids)) != len(ids):
            raise ValueError("Reviewer IDs must be distinct")
        return ids


@router.post("/decisions/{decision_id}/deliberation/auto-policy", status_code=201)
def configure_auto_policy(decision_id: str, body: AutoPolicyDraft,
                          actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    decision = _decision(db, decision_id, actor, lock=True)
    if decision.status != "proposed" or db.get(DecisionAutoPolicy, decision.id) is not None:
        raise Conflict("Only one automatic reviewer policy may be set for a proposed decision")
    if db.scalar(select(DecisionReview.id).where(DecisionReview.decision_id == decision.id)):
        raise Conflict("Reviewer roster must be fixed before first-round review")
    participants = db.scalars(select(Membership.agent_id).join(
        Agent, Membership.agent_id == Agent.id).where(
        Membership.project_id == decision.project_id,
        Membership.agent_id.in_(body.reviewer_ids),
        Membership.role.in_(("reviewer", "worker", "manager", "advisor")),
        Agent.enabled.is_(True))).all()
    if set(participants) != set(body.reviewer_ids):
        raise HTTPException(422, "Every invited reviewer must be enabled and in this project")
    db.add(DecisionAutoPolicy(decision_id=decision.id, project_id=decision.project_id,
                              reviewer_ids=body.reviewer_ids))
    emit(db, actor, "decision.auto_review_configured", decision.id, decision.project_id,
         {"reviewer_count": len(body.reviewer_ids)})
    db.commit()
    return {"decision_id": decision.id, "reviewer_ids": body.reviewer_ids,
            "owner_approval_required": True, "max_rounds": 2}


@router.get("/decisions/{decision_id}/deliberation/auto-policy")
def get_auto_policy(decision_id: str, actor: Actor = Depends(authenticate),
                    db: Session = Depends(get_session)):
    owner_only(actor)
    decision = _decision(db, decision_id, actor)
    policy = db.get(DecisionAutoPolicy, decision.id)
    if policy is None:
        raise HTTPException(404, "No automatic reviewer policy")
    return {"decision_id": decision.id, "reviewer_ids": policy.reviewer_ids,
            "owner_approval_required": True, "max_rounds": 2}


@router.post("/decisions/{decision_id}/deliberation/open", status_code=201)
def open_second_round(decision_id: str, actor: Actor = Depends(authenticate),
                      db: Session = Depends(get_session)):
    owner_only(actor)
    decision = _decision(db, decision_id, actor, lock=True)
    if decision.status != "proposed":
        raise Conflict("Decision must be proposed")
    if db.get(DecisionDeliberation, decision.id):
        raise Conflict("Second round already exists; no third round")
    from .governance import require_auto_review_quorum
    require_auto_review_quorum(db, decision)
    reviews = db.scalars(select(DecisionReview.agent_id).where(
        DecisionReview.decision_id == decision.id)).all()
    if len(set(reviews)) < 2:
        raise Conflict("At least two independent reviewers are required")
    record = DecisionDeliberation(decision_id=decision.id, project_id=decision.project_id)
    db.add(record)
    db.flush()
    emit(db, actor, "decision.second_round_opened", decision.id, decision.project_id,
         {"reviewer_count": len(reviews)})
    db.commit()
    return _session(record)

@router.get("/decisions/{decision_id}/deliberation")
def get_deliberation(decision_id: str, actor: Actor = Depends(authenticate),
                     db: Session = Depends(get_session)):
    decision = _decision(db, decision_id, actor)
    if not actor.is_owner:
        require_project(db, decision.project_id, actor,
                        roles=("worker", "manager", "reviewer", "advisor"))
        if not _participant(db, decision.id, actor):
            raise HTTPException(403, "Second round requires first-round participation")
    state = db.get(DecisionDeliberation, decision.id)
    if not state:
        raise HTTPException(404, "No second round")
    reviews = db.scalars(select(DecisionReview).where(
        DecisionReview.decision_id == decision.id).order_by(
            DecisionReview.created_at, DecisionReview.id)).all()
    q = select(DecisionRebuttal).where(DecisionRebuttal.decision_id == decision.id)
    if not actor.is_owner and state.status == "open" and decision.status != "approved":
        q = q.where(DecisionRebuttal.agent_id == actor.id)
    rebuttals = db.scalars(q.order_by(
        DecisionRebuttal.created_at, DecisionRebuttal.id)).all()
    return {**_session(state), "first_round": [_review_public(r, db) for r in reviews],
            "rebuttals": [_reply(r, db) for r in rebuttals]}

@router.post("/decisions/{decision_id}/deliberation/rebuttals", status_code=201)
def submit_rebuttal(decision_id: str, body: RebuttalDraft,
                    actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    if actor.is_owner:
        raise HTTPException(403, "Agent credentials required")
    decision = _decision(db, decision_id, actor, lock=True)
    require_project(db, decision.project_id, actor,
                    roles=("worker", "manager", "reviewer", "advisor"))
    state = db.get(DecisionDeliberation, decision.id)
    if decision.status != "proposed" or state is None or state.status != "open":
        raise Conflict("Second round is closed or absent")
    if not _participant(db, decision.id, actor):
        raise HTTPException(403, "First-round participation required")
    target = db.get(DecisionReview, body.responds_to_review_id)
    if not target or target.decision_id != decision.id or target.agent_id == actor.id:
        raise HTTPException(422, "Respond to another review from this decision")
    if any(not e.strip() or len(e) > 500 for e in body.evidence):
        raise HTTPException(422, "Invalid evidence reference")
    if db.scalar(select(DecisionRebuttal.id).where(
            DecisionRebuttal.decision_id == decision.id,
            DecisionRebuttal.agent_id == actor.id)):
        raise Conflict("Agent already submitted its second-round reply")
    reply = DecisionRebuttal(decision_id=decision.id, project_id=decision.project_id,
                            agent_id=actor.id, responds_to_review_id=target.id,
                            stance=body.stance, reasoning=body.reasoning, evidence=body.evidence)
    db.add(reply)
    db.flush()
    emit(db, actor, "decision.rebuttal_submitted", decision.id, decision.project_id,
         {"rebuttal_id": reply.id, "responds_to_review_id": target.id})
    policy = db.get(DecisionAutoPolicy, decision.id)
    if policy:
        respondents = set(db.scalars(select(DecisionRebuttal.agent_id).where(
            DecisionRebuttal.decision_id == decision.id)))
        if respondents == set(policy.reviewer_ids):
            state.status = "closed"
            state.closed_at = now()
            state.closing_note = "Automatically closed after all invited reviewers replied; owner must resolve."
            emit(db, actor, "decision.second_round_closed", decision.id, decision.project_id,
                 {"automated": True, "owner_approval_required": True})
    db.commit()
    return _reply(reply, db)

@router.post("/decisions/{decision_id}/deliberation/close")
def close_second_round(decision_id: str, body: CloseDraft,
                       actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    decision = _decision(db, decision_id, actor, lock=True)
    state = db.get(DecisionDeliberation, decision.id)
    if decision.status != "proposed" or state is None or state.status != "open":
        raise Conflict("Second round is not open")
    reason = body.reason.strip()
    if len(reason) < 10:
        raise HTTPException(422, "Substantive owner closing reason required")
    state.status = "closed"
    state.closing_note = reason
    state.closed_at = now()
    emit(db, actor, "decision.second_round_closed", decision.id, decision.project_id,
         {"reason_recorded": True})
    db.commit()
    return _session(state)
