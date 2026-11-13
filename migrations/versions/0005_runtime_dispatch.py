"""Agent runtime identities, verified capabilities and task requirements.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("declared_capabilities", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("agents", sa.Column("verified_capabilities", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("tasks", sa.Column("required_capabilities", sa.JSON(), nullable=False, server_default="[]"))
    op.create_table(
        "agent_runtimes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("environment", sa.String(160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "label", name="uq_runtime_label"),
    )
    op.create_index("ix_agent_runtimes_agent_id", "agent_runtimes", ["agent_id"])


def downgrade():
    op.drop_index("ix_agent_runtimes_agent_id", "agent_runtimes")
    op.drop_table("agent_runtimes")
    op.drop_column("tasks", "required_capabilities")
    op.drop_column("agents", "verified_capabilities")
    op.drop_column("agents", "declared_capabilities")
