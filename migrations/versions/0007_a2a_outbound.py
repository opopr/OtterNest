"""Add authorized external A2A endpoints and durable outbound delegations.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "a2a_remote_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("remote_project_id", sa.String(128), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "agent_id", name="uq_a2a_remote_agent_project"),
    )
    op.create_index("ix_a2a_remote_endpoints_project_id", "a2a_remote_endpoints", ["project_id"])
    op.create_table(
        "a2a_delegations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("a2a_remote_endpoints.id"), nullable=False),
        sa.Column("message_id", sa.String(128), unique=True, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("remote_task_id", sa.String(128), nullable=True),
        sa.Column("remote_context_id", sa.String(128), nullable=True),
        sa.Column("remote_state", sa.String(40), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(240), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_a2a_delegations_project_id", "a2a_delegations", ["project_id"])


def downgrade():
    op.drop_index("ix_a2a_delegations_project_id", "a2a_delegations")
    op.drop_table("a2a_delegations")
    op.drop_index("ix_a2a_remote_endpoints_project_id", "a2a_remote_endpoints")
    op.drop_table("a2a_remote_endpoints")
