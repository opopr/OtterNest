"""Project-scoped provenance lookup. Presence is NOT factual truth verification.

No external URL fetches; existence / publication only, intentionally avoiding
SSRF and evidence laundering. Foreign IDs look the same as missing ones.
"""
from __future__ import annotations

import re
from typing import Iterable

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .db import get_session
from .governance import _decision
from .models import Artifact, Decision, Memory, Task
from .security import Actor, authenticate, owner_only

router = APIRouter(prefix="/api/v1", tags=["evidence"])

_REF = re.compile(r"^(artifact|memory|decision|task):([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: list[str] = Field(min_length=1, max_length=20)


def inspect_references(db: Session, project_id: str, references: Iterable[str]) -> list[dict]:
    """Resolve only authorized, already-stored IDs; never assert they prove a claim."""
    mapping = {"artifact": Artifact, "memory": Memory, "decision": Decision, "task": Task}
    result = []
    for raw in references:
        ref = raw.strip() if isinstance(raw, str) else ""
        match = _REF.fullmatch(ref) if len(ref) <= 120 else None
        if not match:
            result.append({"reference": raw, "status": "unsupported", "factual_claim_verified": False})
            continue
        kind, ident = match.groups()
        record = db.get(mapping[kind], ident.lower())
        resolved = bool(record and record.project_id == project_id)
        if resolved and kind == "memory":
            resolved = record.status == "published"
        if resolved and kind == "decision":
            resolved = record.status == "approved"
        if resolved and kind == "task":
            resolved = record.status == "completed"
        result.append({"reference": raw, "status": "linked" if resolved else "unresolved",
                       "factual_claim_verified": False})
    return result


@router.post("/decisions/{decision_id}/evidence/inspect")
def inspect_decision_evidence(decision_id: str, body: EvidenceRequest,
                              actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    decision = _decision(db, decision_id, actor)
    return {"decision_id": decision.id, "checks": inspect_references(db, decision.project_id, body.references),
            "content_verification": "not_performed"}
