"""ABAC sensitivity, membership expiry, task grants, runtime credentials, artifact GC metadata, and system protection mode.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_runtimes") as batch:
        batch.add_column(sa.Column("token_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("last_health", sa.JSON(), nullable=False, server_default="{}"))
        batch.create_unique_constraint("uq_agent_runtimes_token_hash", ["token_hash"])
    with op.batch_alter_table("agent_runtimes") as batch:
        batch.alter_column("last_health", existing_type=sa.JSON(), server_default=None)

    with op.batch_alter_table("memberships") as batch:
        batch.add_column(sa.Column("max_sensitivity", sa.String(24), nullable=False, server_default="confidential"))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("memberships") as batch:
        batch.alter_column("max_sensitivity", existing_type=sa.String(24), server_default=None)

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("sensitivity", sa.String(24), nullable=False, server_default="internal"))
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("sensitivity", existing_type=sa.String(24), server_default=None)

    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("sensitivity", sa.String(24), nullable=False, server_default="internal"))
    with op.batch_alter_table("memories") as batch:
        batch.alter_column("sensitivity", existing_type=sa.String(24), server_default=None)

    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("sensitivity", sa.String(24), nullable=False, server_default="internal"))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("artifacts") as batch:
        batch.alter_column("size_bytes", existing_type=sa.Integer(), server_default=None)
        batch.alter_column("sensitivity", existing_type=sa.String(24), server_default=None)

    op.create_table(
        "task_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("max_sensitivity", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_grants_project_id", "task_grants", ["project_id"])
    op.create_index("ix_task_grants_task_id", "task_grants", ["task_id"])
    op.create_index("ix_task_grants_agent_id", "task_grants", ["agent_id"])

    op.create_table(
        "system_states",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("system_states")
    op.drop_index("ix_task_grants_agent_id", table_name="task_grants")
    op.drop_index("ix_task_grants_task_id", table_name="task_grants")
    op.drop_index("ix_task_grants_project_id", table_name="task_grants")
    op.drop_table("task_grants")

    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("sensitivity")
        batch.drop_column("size_bytes")

    with op.batch_alter_table("memories") as batch:
        batch.drop_column("sensitivity")

    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("sensitivity")

    with op.batch_alter_table("memberships") as batch:
        batch.drop_column("expires_at")
        batch.drop_column("max_sensitivity")

    with op.batch_alter_table("agent_runtimes") as batch:
        batch.drop_constraint("uq_agent_runtimes_token_hash", type_="unique")
        batch.drop_column("last_health")
        batch.drop_column("token_hash")
