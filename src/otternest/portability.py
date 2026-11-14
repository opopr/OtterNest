"""Owner-controlled validation and restore for portable project bundles."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import (
    Agent,
    Artifact,
    CrossProjectShare,
    Decision,
    DecisionDeliberation,
    DecisionRebuttal,
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
from .services import emit

router = APIRouter(prefix="/api/v1", tags=["Portable Project Restore"])

MAX_BUNDLE_BYTES = 10 * 1024 * 1024
COLLECTIONS = (
    "agents",
    "goals",
    "memberships",
    "tasks",
    "task_dependencies",
    "checkpoints",
    "side_effects",
    "memories",
    "decisions",
    "decision_reviews",
    "decision_rebuttals",
    "decision_deliberations",
    "skills",
    "skill_evaluations",
    "cross_project_shares",
    "artifacts",
    "events",
)
ID_COLLECTIONS = {
    "agents": "id",
    "goals": "id",
    "memberships": "id",
    "tasks": "id",
    "checkpoints": "id",
    "side_effects": "id",
    "memories": "id",
    "decisions": "id",
    "decision_reviews": "id",
    "decision_rebuttals": "id",
    "skills": "id",
    "skill_evaluations": "id",
    "cross_project_shares": "id",
    "artifacts": "id",
}
TASK_STATES = {"ready", "waiting_dependencies", "waiting_approval", "running", "submitted", "blocked", "completed", "cancelled"}
SIDE_EFFECT_STATES = {"pending", "reported_executed", "unknown", "verified_executed", "verified_not_executed"}


class BundleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: dict[str, Any]


class ImportIn(BundleIn):
    new_name: str | None = Field(default=None, min_length=1, max_length=160)
    confirm_restore: bool


def _fail(message: str, status: int = 422) -> None:
    raise HTTPException(status, message)


def _plain_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 36:
        _fail(f"Invalid {label}")
    return value


def _parse_time(value: Any, label: str, *, fallback: datetime | None = None) -> datetime:
    if value is None and fallback is not None:
        return fallback
    if not isinstance(value, str):
        _fail(f"Invalid {label} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"Invalid {label} timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _canonical_digest(data: dict[str, Any]) -> str:
    payload = {key: value for key, value in data.items() if key not in {"schema_version", "exported_at", "secrets_included", "sha256_digest"}}
    try:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError):
        _fail("Project bundle is not valid JSON data")
    return hashlib.sha256(canonical).hexdigest()


def validate_bundle(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        _fail("Project bundle must be a JSON object")
    try:
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        _fail("Project bundle is not valid JSON data")
    if len(encoded) > MAX_BUNDLE_BYTES:
        _fail("Project bundle exceeds the 10 MiB import limit", 413)
    if data.get("schema_version") != "otternest.export.v1":
        _fail("Unsupported project bundle schema_version")
    if data.get("secrets_included") is not False:
        _fail("Project bundle must declare secrets_included=false")
    digest = data.get("sha256_digest")
    if not isinstance(digest, str) or not secrets.compare_digest(digest, _canonical_digest(data)):
        _fail("Project bundle SHA-256 digest does not match its contents")

    project = data.get("project")
    if not isinstance(project, dict):
        _fail("Project bundle is missing project metadata")
    source_project_id = _plain_id(project.get("id"), "project.id")
    if not isinstance(project.get("name"), str) or not project["name"].strip():
        _fail("Project bundle has an invalid project name")

    normalized = dict(data)
    normalized["project"] = project
    for collection in COLLECTIONS:
        rows = data.get(collection, [])
        if not isinstance(rows, list) or len(rows) > 50000 or any(not isinstance(row, dict) for row in rows):
            _fail(f"Project bundle collection '{collection}' must be a list of at most 50,000 objects")
        normalized[collection] = rows

    ids: dict[str, set[str]] = {}
    for collection, field in ID_COLLECTIONS.items():
        found: set[str] = set()
        for row in normalized[collection]:
            value = _plain_id(row.get(field), f"{collection}.{field}")
            if value in found:
                _fail(f"Duplicate ID in project bundle collection '{collection}'")
            found.add(value)
        ids[collection] = found

    agent_ids = set(ids["agents"])
    for collection, field in (
        ("memberships", "agent_id"),
        ("checkpoints", "agent_id"),
        ("side_effects", "agent_id"),
        ("decision_reviews", "agent_id"),
        ("decision_rebuttals", "agent_id"),
    ):
        for row in normalized[collection]:
            agent_ids.add(_plain_id(row.get(field), f"{collection}.{field}"))
    for row in normalized["tasks"]:
        for field in ("assigned_agent_id", "target_agent_id"):
            if row.get(field) is not None:
                agent_ids.add(_plain_id(row[field], f"tasks.{field}"))
    for row in normalized["skill_evaluations"]:
        if row.get("actor_kind") == "agent":
            agent_ids.add(_plain_id(row.get("actor_id"), "skill_evaluations.actor_id"))
    if ids["agents"] and not agent_ids.issubset(ids["agents"]):
        _fail("Agent references are missing from the exported agents collection")
    normalized["_source_agent_ids"] = sorted(agent_ids)

    task_ids = ids["tasks"]
    goal_ids = ids["goals"]
    decision_ids = ids["decisions"]
    skill_ids = ids["skills"]
    memory_ids = ids["memories"]
    artifact_ids = ids["artifacts"]
    review_ids = ids["decision_reviews"]
    artifact_family_versions: set[tuple[str, int]] = set()
    for row in normalized["tasks"]:
        goal_id = row.get("goal_id")
        if goal_id is not None and _plain_id(goal_id, "tasks.goal_id") not in goal_ids:
            _fail("Task references a goal outside the project bundle")
        if not isinstance(row.get("status"), str) or row["status"] not in TASK_STATES:
            _fail("Task bundle contains an unsupported status")
        if not isinstance(row.get("risk"), str) or row["risk"] not in {"R0", "R1", "R2", "R3"}:
            _fail("Task bundle contains an unsupported risk level")
    edge_set: set[tuple[str, str]] = set()
    indegree = {task_id: 0 for task_id in task_ids}
    successors: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    prerequisite_count: dict[str, int] = {task_id: 0 for task_id in task_ids}
    for row in normalized["task_dependencies"]:
        task_id = _plain_id(row.get("task_id"), "task_dependencies.task_id")
        predecessor_id = _plain_id(row.get("depends_on_id"), "task_dependencies.depends_on_id")
        edge = (task_id, predecessor_id)
        if task_id not in task_ids or predecessor_id not in task_ids or task_id == predecessor_id or edge in edge_set:
            _fail("Task dependency contains an invalid, duplicate, or self-referencing edge")
        edge_set.add(edge)
        indegree[task_id] += 1
        prerequisite_count[task_id] += 1
        successors[predecessor_id].append(task_id)
    if any(count > 64 for count in prerequisite_count.values()):
        _fail("A task has more than 64 prerequisites")
    ready = deque(task_id for task_id, count in indegree.items() if count == 0)
    acyclic_count = 0
    while ready:
        completed = ready.popleft()
        acyclic_count += 1
        for successor in successors[completed]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if acyclic_count != len(task_ids):
        _fail("Task dependency graph contains a cycle")

    for row in normalized["checkpoints"]:
        checkpoint_artifacts = row.get("artifact_ids") or []
        if not isinstance(checkpoint_artifacts, list):
            _fail("Checkpoint artifact_ids must be a list")
        checkpoint_artifact_ids = {_plain_id(item, "checkpoints.artifact_ids") for item in checkpoint_artifacts}
        if (_plain_id(row.get("task_id"), "checkpoints.task_id") not in task_ids
                or not checkpoint_artifact_ids.issubset(artifact_ids)):
            _fail("Checkpoint references a task or artifact outside the project bundle")
    for row in normalized["side_effects"]:
        if (_plain_id(row.get("task_id"), "side_effects.task_id") not in task_ids
                or not isinstance(row.get("status"), str) or row["status"] not in SIDE_EFFECT_STATES):
            _fail("Side-effect entry contains an invalid task reference or status")
    for row in normalized["artifacts"]:
        if (_plain_id(row.get("task_id"), "artifacts.task_id") not in task_ids or not isinstance(row.get("checksum"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", row["checksum"])):
            _fail("Artifact references a task outside the project bundle or has an invalid checksum")
        family_id = _plain_id(row.get("family_id", row.get("id")), "artifacts.family_id")
        version = row.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            _fail("Artifact version must be a positive integer")
        if (family_id, version) in artifact_family_versions:
            _fail("Artifact version is duplicated within its immutable family")
        artifact_family_versions.add((family_id, version))
    for row in normalized["decision_reviews"]:
        if _plain_id(row.get("decision_id"), "decision_reviews.decision_id") not in decision_ids:
            _fail("Decision review references a decision outside the project bundle")
    for row in normalized["decision_rebuttals"]:
        if (_plain_id(row.get("decision_id"), "decision_rebuttals.decision_id") not in decision_ids
                or _plain_id(row.get("responds_to_review_id"), "decision_rebuttals.responds_to_review_id") not in review_ids):
            _fail("Decision rebuttal references a review or decision outside the project bundle")
    for row in normalized["skill_evaluations"]:
        task_id = row.get("task_id")
        if (_plain_id(row.get("skill_id"), "skill_evaluations.skill_id") not in skill_ids
                or (task_id is not None and _plain_id(task_id, "skill_evaluations.task_id") not in task_ids)):
            _fail("Skill evaluation references a skill or task outside the project bundle")
    for row in normalized["decision_deliberations"]:
        if _plain_id(row.get("decision_id"), "decision_deliberations.decision_id") not in decision_ids:
            _fail("Deliberation references a decision outside the project bundle")

    source_projects = {source_project_id}
    for row in normalized["cross_project_shares"]:
        source = _plain_id(row.get("source_project_id"), "cross_project_shares.source_project_id")
        target = _plain_id(row.get("target_project_id"), "cross_project_shares.target_project_id")
        source_item_id = _plain_id(row.get("source_item_id"), "cross_project_shares.source_item_id")
        if row.get("promoted_item_id") is not None:
            _plain_id(row["promoted_item_id"], "cross_project_shares.promoted_item_id")
        source_projects.update((source, target))
        if not isinstance(row.get("item_kind"), str) or row["item_kind"] not in {"memory", "skill"}:
            _fail("Cross-project share contains an unsupported item kind")
        if source == source_project_id:
            item_ids = memory_ids if row["item_kind"] == "memory" else skill_ids
            if source_item_id not in item_ids:
                _fail("Cross-project share references an item outside the project bundle")
    normalized["_source_project_ids"] = sorted(source_projects)
    return normalized


def _summary(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": bundle["schema_version"],
        "source_project": {"id": bundle["project"]["id"], "name": bundle["project"]["name"]},
        "sha256_digest": bundle["sha256_digest"],
        "counts": {key: len(bundle[key]) for key in COLLECTIONS},
        "restore_boundaries": {
            "project_status": "paused",
            "nonterminal_tasks": "blocked; owner must review and unblock; R3 tasks require fresh authorization",
            "agent_identities": "recreated disabled without credentials; capabilities are not trusted",
            "memories_and_skills": "non-archived entries restored as proposed",
            "decisions": "restored as proposed",
            "artifact_contents": "not included in JSON export; versions restore as pending local review",
            "external_connections": "not restored or triggered",
        },
    }


@router.post("/projects/import/preview")
def preview_project_import(body: BundleIn, actor: Actor = Depends(authenticate)):
    owner_only(actor)
    return _summary(validate_bundle(body.bundle))


@router.post("/projects/import", status_code=201)
def import_project_bundle(body: ImportIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    bundle = validate_bundle(body.bundle)
    if not body.confirm_restore:
        _fail("confirm_restore must be true to create a restored project")

    source_project = bundle["project"]
    source_project_id = source_project["id"]
    project_map = {source_id: str(uuid.uuid4()) for source_id in bundle["_source_project_ids"]}
    project_id = project_map[source_project_id]
    name = (body.new_name or f"{source_project['name'][:154]} (导入)").strip()
    if not name:
        _fail("new_name must not be empty")
    agent_map = {source_id: str(uuid.uuid4()) for source_id in bundle["_source_agent_ids"]}

    def make_map(collection: str) -> dict[str, str]:
        return {row["id"]: str(uuid.uuid4()) for row in bundle[collection]}

    id_maps = {collection: make_map(collection) for collection in ID_COLLECTIONS if collection != "agents"}
    artifact_family_map = {
        source_family_id: str(uuid.uuid4())
        for source_family_id in {_plain_id(row.get("family_id", row["id"]), "artifacts.family_id") for row in bundle["artifacts"]}
    }
    side_effects_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in bundle["side_effects"]:
        side_effects_by_task.setdefault(row["task_id"], []).append(row)
    task_statuses: dict[str, str] = {}
    for task_row in bundle["tasks"]:
        status = task_row["status"]
        unsafe_effect = any(effect["status"] in {"pending", "unknown"} for effect in side_effects_by_task.get(task_row["id"], []))
        if status == "cancelled":
            restored_status = "cancelled"
        elif status == "completed" and not unsafe_effect:
            restored_status = "completed"
        elif status == "waiting_approval" and task_row.get("risk") == "R3" and not unsafe_effect:
            restored_status = "waiting_approval"
        else:
            restored_status = "blocked"
        task_statuses[task_row["id"]] = restored_status

    created_counts = {key: 0 for key in COLLECTIONS}
    created_agents: set[str] = set()
    try:
        restored_project = Project(
            id=project_id,
            name=name[:160],
            description=source_project.get("description", "")[:3000],
            status="paused",
            created_at=_parse_time(source_project.get("created_at"), "project", fallback=now()),
        )
        db.add(restored_project)
        db.flush()

        # Cross-project share history can refer to projects not included in this package.
        # Represent those endpoints as archived placeholders with no members or content.
        for external_source_id in bundle["_source_project_ids"]:
            if external_source_id == source_project_id:
                continue
            db.add(Project(
                id=project_map[external_source_id],
                name=f"Imported reference {external_source_id}"[:160],
                description="Archived placeholder only; this project's contents were not part of the imported bundle.",
                status="archived",
            ))
        agent_meta = {row["id"]: row for row in bundle["agents"]}
        for source_agent_id in bundle["_source_agent_ids"]:
            source_agent = agent_meta.get(source_agent_id, {})
            db.add(Agent(
                id=agent_map[source_agent_id],
                name=(source_agent.get("name") or f"Imported agent {source_agent_id}")[:120],
                platform=(source_agent.get("platform") or "imported")[:80],
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                enabled=False,
                execution_mode="manual",
                declared_capabilities=[],
                verified_capabilities=[],
            ))
            created_agents.add(source_agent_id)
        db.flush()
        created_counts["agents"] = len(created_agents)

        charter = bundle.get("charter")
        if isinstance(charter, dict):
            max_total_tasks = max(1, min(2000, int(charter.get("max_total_tasks", 200))))
            max_concurrent_tasks = max(1, min(50, int(charter.get("max_concurrent_tasks", 5)), max_total_tasks))
            db.add(ProjectCharter(
                project_id=project_id,
                scope_boundary=str(charter.get("scope_boundary", ""))[:12000],
                stop_conditions=str(charter.get("stop_conditions", ""))[:12000],
                max_concurrent_tasks=max_concurrent_tasks,
                max_total_tasks=max_total_tasks,
                updated_at=_parse_time(charter.get("updated_at"), "charter", fallback=now()),
            ))

        for row in bundle["goals"]:
            db.add(Goal(id=id_maps["goals"][row["id"]], project_id=project_id,
                        title=str(row.get("title", ""))[:240],
                        success_criteria=str(row.get("success_criteria", ""))[:5000],
                        status=row.get("status", "active")))
            created_counts["goals"] += 1
        db.flush()

        for row in bundle["memberships"]:
            db.add(Membership(id=id_maps["memberships"][row["id"]], project_id=project_id,
                              agent_id=agent_map[row["agent_id"]], role=row.get("role", "worker")))
            created_counts["memberships"] += 1

        for row in bundle["tasks"]:
            old_id = row["id"]
            old_status = task_statuses[old_id]
            db.add(Task(
                id=id_maps["tasks"][old_id], project_id=project_id,
                goal_id=id_maps["goals"].get(row.get("goal_id")),
                title=str(row.get("title", ""))[:240],
                instruction=str(row.get("instruction", ""))[:12000],
                acceptance=str(row.get("acceptance", ""))[:6000],
                status=old_status, risk=row.get("risk", "R1"),
                assigned_agent_id=None, target_agent_id=None,
                priority=max(0, min(100, int(row.get("priority", 50)))),
                required_capabilities=list(row.get("required_capabilities") or [])[:32],
                approval_granted=False, lease_epoch=max(0, int(row.get("lease_epoch", 0))) + (1 if old_status != "completed" else 0),
                lease_until=None, result_summary=row.get("result_summary"),
                version=max(1, int(row.get("version", 1))) + 1,
                created_at=_parse_time(row.get("created_at"), "task", fallback=now()),
            ))
            created_counts["tasks"] += 1
        db.flush()
        for row in bundle["task_dependencies"]:
            db.add(TaskDependency(task_id=id_maps["tasks"][row["task_id"]],
                                  depends_on_id=id_maps["tasks"][row["depends_on_id"]]))
            created_counts["task_dependencies"] += 1

        for row in bundle["artifacts"]:
            artifact_id = id_maps["artifacts"][row["id"]]
            checksum = str(row.get("checksum", ""))
            source_family_id = row.get("family_id", row["id"])
            db.add(Artifact(id=artifact_id, project_id=project_id,
                            family_id=artifact_family_map[source_family_id],
                            version=max(1, int(row.get("version", 1))),
                            task_id=id_maps["tasks"][row["task_id"]],
                            name=str(row.get("name", ""))[:240], checksum=checksum[:64],
                            uri=f"unavailable://sha256/{checksum}",
                            review_status="pending",
                            review_note=("Imported metadata; local owner review is required. Original review note: "
                                         + str(row.get("review_note", ""))[:2800]),
                            reviewed_at=None,
                            created_at=_parse_time(row.get("created_at"), "artifact", fallback=now())))
            created_counts["artifacts"] += 1

        for row in bundle["checkpoints"]:
            db.add(TaskCheckpoint(
                id=id_maps["checkpoints"][row["id"]], project_id=project_id,
                task_id=id_maps["tasks"][row["task_id"]], agent_id=agent_map[row["agent_id"]],
                lease_epoch=max(0, int(row.get("lease_epoch", 0))),
                step_number=max(1, int(row.get("step_number", 1))),
                summary=str(row.get("summary", ""))[:12000], next_steps=str(row.get("next_steps", ""))[:12000],
                artifact_ids=[id_maps["artifacts"][artifact_id] for artifact_id in row.get("artifact_ids", [])],
                verification_status="self_reported", owner_note="",
                created_at=_parse_time(row.get("created_at"), "checkpoint", fallback=now()), verified_at=None,
            ))
            created_counts["checkpoints"] += 1

        for row in bundle["side_effects"]:
            db.add(TaskSideEffect(
                id=id_maps["side_effects"][row["id"]], project_id=project_id,
                task_id=id_maps["tasks"][row["task_id"]], agent_id=agent_map[row["agent_id"]],
                lease_epoch=max(0, int(row.get("lease_epoch", 0))),
                idempotency_key=str(row.get("idempotency_key", ""))[:128],
                target_system=str(row.get("target_system", ""))[:120],
                description=str(row.get("description", ""))[:12000],
                status=row["status"], evidence_summary=str(row.get("evidence_summary", ""))[:12000],
                owner_note="Imported reconciliation note: " + str(row.get("owner_note", ""))[:2900],
                created_at=_parse_time(row.get("created_at"), "side effect", fallback=now()),
                updated_at=_parse_time(row.get("updated_at"), "side effect", fallback=now()),
            ))
            created_counts["side_effects"] += 1

        for row in bundle["memories"]:
            db.add(Memory(
                id=id_maps["memories"][row["id"]], project_id=project_id,
                kind=row.get("kind", "fact"), title=str(row.get("title", ""))[:240],
                content=str(row.get("content", ""))[:20000], source=str(row.get("source", ""))[:3000],
                status="archived" if row.get("status") == "archived" else "proposed",
                version=max(1, int(row.get("version", 1))),
                created_at=_parse_time(row.get("created_at"), "memory", fallback=now()),
            ))
            created_counts["memories"] += 1

        for row in bundle["decisions"]:
            db.add(Decision(
                id=id_maps["decisions"][row["id"]], project_id=project_id,
                question=str(row.get("question", ""))[:12000], resolution=str(row.get("resolution", ""))[:12000],
                rationale=str(row.get("rationale", ""))[:12000], status="proposed",
                owner_review_note="Imported decision; local owner review is required. Original note: " + str(row.get("owner_review_note", ""))[:9000],
            ))
            created_counts["decisions"] += 1
        db.flush()

        for row in bundle["decision_reviews"]:
            db.add(DecisionReview(
                id=id_maps["decision_reviews"][row["id"]], project_id=project_id,
                decision_id=id_maps["decisions"][row["decision_id"]], agent_id=agent_map[row["agent_id"]],
                stance=str(row.get("stance", "concern"))[:24], reasoning=str(row.get("reasoning", ""))[:12000],
                evidence=row.get("evidence") or [], created_at=_parse_time(row.get("created_at"), "decision review", fallback=now()),
            ))
            created_counts["decision_reviews"] += 1
        for row in bundle["decision_rebuttals"]:
            db.add(DecisionRebuttal(
                id=id_maps["decision_rebuttals"][row["id"]], project_id=project_id,
                decision_id=id_maps["decisions"][row["decision_id"]], agent_id=agent_map[row["agent_id"]],
                responds_to_review_id=id_maps["decision_reviews"][row["responds_to_review_id"]],
                stance=str(row.get("stance", "concern"))[:24], reasoning=str(row.get("reasoning", ""))[:12000],
                evidence=row.get("evidence") or [], created_at=_parse_time(row.get("created_at"), "decision rebuttal", fallback=now()),
            ))
            created_counts["decision_rebuttals"] += 1
        for row in bundle["decision_deliberations"]:
            db.add(DecisionDeliberation(
                decision_id=id_maps["decisions"][row["decision_id"]], project_id=project_id,
                status="closed", closing_note=("Imported deliberation history; prior round is not resumed. " + str(row.get("closing_note", "")))[:12000],
                opened_at=_parse_time(row.get("opened_at"), "deliberation", fallback=now()),
                closed_at=now(),
            ))
            created_counts["decision_deliberations"] += 1

        for row in bundle["skills"]:
            db.add(Skill(
                id=id_maps["skills"][row["id"]], project_id=project_id,
                name=str(row.get("name", ""))[:120], version=str(row.get("version", ""))[:40],
                content=str(row.get("content", ""))[:20000], source=str(row.get("source", ""))[:3000],
                status="archived" if row.get("status") == "archived" else "proposed",
                created_at=_parse_time(row.get("created_at"), "skill", fallback=now()),
            ))
            created_counts["skills"] += 1
        db.flush()
        for row in bundle["skill_evaluations"]:
            actor_kind = row.get("actor_kind", "agent")
            source_actor_id = str(row.get("actor_id", ""))
            db.add(SkillEvaluation(
                id=id_maps["skill_evaluations"][row["id"]], project_id=project_id,
                skill_id=id_maps["skills"][row["skill_id"]], actor_kind=actor_kind,
                actor_id=agent_map.get(source_actor_id, "owner" if actor_kind == "owner" else source_actor_id),
                verdict=row.get("verdict", "concern"), notes=str(row.get("notes", ""))[:12000],
                task_id=id_maps["tasks"].get(row.get("task_id")),
                created_at=_parse_time(row.get("created_at"), "skill evaluation", fallback=now()),
            ))
            created_counts["skill_evaluations"] += 1

        for row in bundle["cross_project_shares"]:
            share_id = id_maps["cross_project_shares"][row["id"]]
            source_item_id = row.get("source_item_id")
            if row.get("source_project_id") == source_project_id:
                key = "memories" if row["item_kind"] == "memory" else "skills"
                source_item_id = id_maps[key].get(source_item_id, source_item_id)
            promoted_item_id = row.get("promoted_item_id")
            if promoted_item_id:
                promoted_item_id = _plain_id(promoted_item_id, "cross_project_shares.promoted_item_id")
                key = "memories" if row["item_kind"] == "memory" else "skills"
                promoted_item_id = id_maps[key].get(promoted_item_id, promoted_item_id)
            db.add(CrossProjectShare(
                id=share_id,
                source_project_id=project_map[row["source_project_id"]],
                target_project_id=project_map[row["target_project_id"]],
                item_kind=row["item_kind"], source_item_id=source_item_id,
                promoted_item_id=promoted_item_id,
                proposed_by=(row.get("proposed_by", "import")[:100]),
                rationale=str(row.get("rationale", ""))[:12000],
                status=row.get("status", "rejected"),
                owner_note=("Imported share history; new approval is not implied. " + str(row.get("owner_note", "")))[:12000],
                created_at=_parse_time(row.get("created_at"), "share", fallback=now()),
                resolved_at=_parse_time(row.get("resolved_at"), "share", fallback=now()) if row.get("resolved_at") else None,
            ))
            created_counts["cross_project_shares"] += 1

        for row in bundle["events"]:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            imported_payload = {
                "source_project_id": source_project_id,
                "source_event_id": row.get("id"),
                "source_payload": payload,
            }
            db.add(Event(
                project_id=project_id,
                kind=("imported." + str(row.get("kind", "event")))[:100],
                subject_id=str(row.get("subject_id", ""))[:36],
                payload=imported_payload,
                created_at=_parse_time(row.get("created_at"), "event", fallback=now()),
            ))
            created_counts["events"] += 1

        db.flush()
        emit(db, actor, "project.imported", project_id, project_id,
             {"source_project_id": source_project_id, "sha256_digest": bundle["sha256_digest"],
              "source_counts": {key: len(bundle[key]) for key in COLLECTIONS}})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        # Keep SQL and constraint details out of the public response.
        raise HTTPException(422, "Project bundle could not be restored atomically; no project was created") from None

    return {
        "status": "restored_paused",
        "project_id": project_id,
        "project_name": name[:160],
        "source_project_id": source_project_id,
        "sha256_digest": bundle["sha256_digest"],
        "created": created_counts,
        "disabled_agents": len(created_agents),
        "external_project_placeholders": len(project_map) - 1,
        "artifacts_metadata_only": created_counts["artifacts"],
        "activation_required": True,
        "notice": "Credentials and external connections were not restored. Review data before activating this project.",
    }
