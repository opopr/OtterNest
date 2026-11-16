"""Persist owner-confirmed multi-step goal plan drafts.

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "goal_plan_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
    )
    op.create_index("ix_goal_plan_drafts_project_id", "goal_plan_drafts", ["project_id"])
    op.create_index("ix_goal_plan_drafts_status_expires", "goal_plan_drafts", ["status", "expires_at"])


def downgrade():
    op.drop_index("ix_goal_plan_drafts_status_expires", table_name="goal_plan_drafts")
    op.drop_index("ix_goal_plan_drafts_project_id", table_name="goal_plan_drafts")
    op.drop_table("goal_plan_drafts")
