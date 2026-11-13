"""Side-effect idempotency ledger, skill evaluations, and cross-project knowledge shares.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_side_effects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("target_system", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_task_side_effect_key"),
    )
    op.create_index("ix_task_side_effects_project_id", "task_side_effects", ["project_id"])
    op.create_index("ix_task_side_effects_task_id", "task_side_effects", ["task_id"])

    op.create_table(
        "skill_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("skill_id", sa.String(36), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_skill_evaluations_project_id", "skill_evaluations", ["project_id"])
    op.create_index("ix_skill_evaluations_skill_id", "skill_evaluations", ["skill_id"])

    op.create_table(
        "cross_project_shares",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("target_project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("item_kind", sa.String(16), nullable=False),
        sa.Column("source_item_id", sa.String(36), nullable=False),
        sa.Column("promoted_item_id", sa.String(36), nullable=True),
        sa.Column("proposed_by", sa.String(100), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cross_project_shares_source_project_id", "cross_project_shares", ["source_project_id"])
    op.create_index("ix_cross_project_shares_target_project_id", "cross_project_shares", ["target_project_id"])


def downgrade():
    op.drop_index("ix_cross_project_shares_target_project_id", table_name="cross_project_shares")
    op.drop_index("ix_cross_project_shares_source_project_id", table_name="cross_project_shares")
    op.drop_table("cross_project_shares")
    op.drop_index("ix_skill_evaluations_skill_id", table_name="skill_evaluations")
    op.drop_index("ix_skill_evaluations_project_id", table_name="skill_evaluations")
    op.drop_table("skill_evaluations")
    op.drop_index("ix_task_side_effects_task_id", table_name="task_side_effects")
    op.drop_index("ix_task_side_effects_project_id", table_name="task_side_effects")
    op.drop_table("task_side_effects")
