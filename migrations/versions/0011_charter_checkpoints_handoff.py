"""Project charter, task checkpoints, and handoff continuity.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_charters",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("scope_boundary", sa.Text(), nullable=False),
        sa.Column("stop_conditions", sa.Text(), nullable=False),
        sa.Column("max_concurrent_tasks", sa.Integer(), nullable=False),
        sa.Column("max_total_tasks", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "task_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("next_steps", sa.Text(), nullable=False),
        sa.Column("artifact_ids", sa.JSON(), nullable=False),
        sa.Column("verification_status", sa.String(24), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", "lease_epoch", "step_number", name="uq_task_checkpoint_epoch_step"),
    )
    op.create_index("ix_task_checkpoints_project_id", "task_checkpoints", ["project_id"])
    op.create_index("ix_task_checkpoints_task_id", "task_checkpoints", ["task_id"])


def downgrade():
    op.drop_index("ix_task_checkpoints_task_id", table_name="task_checkpoints")
    op.drop_index("ix_task_checkpoints_project_id", table_name="task_checkpoints")
    op.drop_table("task_checkpoints")
    op.drop_table("project_charters")
