"""Add version-checked command plans and independent decision reviews.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "command_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_command_plans_project_id", "command_plans", ["project_id"])
    op.create_index("ix_command_plans_task_id", "command_plans", ["task_id"])
    op.add_column("decisions", sa.Column("owner_review_note", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "decision_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("stance", sa.String(24), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("decision_id", "agent_id", name="uq_decision_review_agent"),
    )
    op.create_index("ix_decision_reviews_project_id", "decision_reviews", ["project_id"])
    op.create_index("ix_decision_reviews_decision_id", "decision_reviews", ["decision_id"])


def downgrade():
    op.drop_index("ix_decision_reviews_decision_id", table_name="decision_reviews")
    op.drop_index("ix_decision_reviews_project_id", table_name="decision_reviews")
    op.drop_table("decision_reviews")
    op.drop_column("decisions", "owner_review_note")
    op.drop_index("ix_command_plans_task_id", table_name="command_plans")
    op.drop_index("ix_command_plans_project_id", table_name="command_plans")
    op.drop_table("command_plans")
