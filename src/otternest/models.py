"""Core normalized records and transactional outbox."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)
    declared_capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    verified_capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class AgentRuntime(Base):
    """One real execution instance. Heartbeats are self-reported, not remote attestation."""
    __tablename__ = "agent_runtimes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("agent_id", "label", name="uq_runtime_label"),)


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), default="worker", nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "agent_id", name="uq_membership"),)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", nullable=False)
    risk: Mapped[str] = mapped_column(String(2), default="R1", nullable=False)
    assigned_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    target_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    approval_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (Index("ix_tasks_project_status", "project_id", "status"),)


class TaskDependency(Base):
    """An immutable-with-respect-to-execution edge: task waits for depends_on_id."""
    __tablename__ = "task_dependencies"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    __table_args__ = (Index("ix_task_dependencies_predecessor", "depends_on_id"),)


class A2AInboxMessage(Base):
    """Durable and idempotent inbound A2A message ↔ authoritative task mapping."""
    __tablename__ = "a2a_inbox_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "actor_id", "message_id", name="uq_a2a_message_origin"),)




class A2ARemoteEndpoint(Base):
    """Owner-approved HTTPS A2A peer, tied to a verified project Agent.

    Secrets are referenced by endpoint ID and held outside the database.
    """
    __tablename__ = "a2a_remote_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    remote_project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "agent_id", name="uq_a2a_remote_agent_project"),)


class A2ADelegation(Base):
    """Durable owner-authorized outbound request and remote-task correlation.

    A lost message/send acknowledgement is *unknown*, not retryable on its own.
    """
    __tablename__ = "a2a_delegations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, unique=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("a2a_remote_endpoints.id"), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    remote_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_context_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="fact", nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="proposed", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="proposed", nullable=False)
    owner_review_note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Outbox(Base):
    __tablename__ = "outbox"
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Skill(Base):
    """Versioned Agent Skills, treated as untrusted instructions until approved."""
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="proposed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "name", "version", name="uq_skill_revision"),)


class WebhookEndpoint(Base):
    """Owner-approved project webhook destination; signing key is derived, not stored."""
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    event_prefix: Mapped[str] = mapped_column(String(100), nullable=False, default="task.")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class WebhookDelivery(Base):
    """One durable attempt series per event/subscription; at-least-once delivery."""
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    __table_args__ = (UniqueConstraint("event_id", "endpoint_id", name="uq_event_endpoint"),)

class CommandPlan(Base):
    """One owner-authored command preview, committed once against a task version."""
    __tablename__ = "command_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DecisionReview(Base):
    """Independent evidence/position from a project Agent, not an automatic vote."""
    __tablename__ = "decision_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    stance: Mapped[str] = mapped_column(String(24), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("decision_id", "agent_id", name="uq_decision_review_agent"),)


class DecisionAutoPolicy(Base):
    """Explicit owner-approved reviewer quorum; never authorizes decision approval."""
    __tablename__ = "decision_auto_policies"
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    reviewer_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class DecisionDeliberation(Base):
    """At most one explicitly opened second round for each decision."""
    __tablename__ = "decision_deliberations"
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    closing_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class DecisionRebuttal(Base):
    """One independent second-round rebuttal per first-round participant."""
    __tablename__ = "decision_rebuttals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    responds_to_review_id: Mapped[str] = mapped_column(ForeignKey("decision_reviews.id"), nullable=False)
    stance: Mapped[str] = mapped_column(String(24), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("decision_id", "agent_id", name="uq_decision_rebuttal_agent"),)


class ProjectCharter(Base):
    """Owner-governed project scope, stop conditions, and hard task/concurrency budgets."""
    __tablename__ = "project_charters"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    scope_boundary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stop_conditions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_total_tasks: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class TaskCheckpoint(Base):
    """Epoch-fenced work checkpoint; owner_verified status is separated from agent self-report."""
    __tablename__ = "task_checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    next_steps: Mapped[str] = mapped_column(Text, default="", nullable=False)
    artifact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(24), default="self_reported", nullable=False)
    owner_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("task_id", "lease_epoch", "step_number", name="uq_task_checkpoint_epoch_step"),)


class TaskSideEffect(Base):
    """Idempotency and reconciliation ledger for external task side effects."""
    __tablename__ = "task_side_effects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_system: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    owner_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("task_id", "idempotency_key", name="uq_task_side_effect_key"),)


class SkillEvaluation(Base):
    """Structured skill evaluation record; never self-authorizes root policy changes."""
    __tablename__ = "skill_evaluations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), nullable=False, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class CrossProjectShare(Base):
    """Owner-approved promotion of published knowledge/skills without opening source project ACLs."""
    __tablename__ = "cross_project_shares"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    target_project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    item_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    promoted_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    proposed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="proposed", nullable=False)
    owner_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OwnerAccount(Base):
    """Web console owner account credentials and RFC 6238 TOTP two-factor state."""
    __tablename__ = "owner_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="owner")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="管理员", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_pending_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_code_hashes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class OwnerSession(Base):
    """Short-lived 2FA challenges and active Web owner sessions (only SHA-256 hashes stored)."""
    __tablename__ = "owner_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owner_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="session", nullable=False)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
