"""Persist inbound A2A messages and their authoritative task mapping.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "a2a_inbox_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "actor_id", "message_id", name="uq_a2a_message_origin"),
    )
    op.create_index("ix_a2a_inbox_messages_project_id", "a2a_inbox_messages", ["project_id"])


def downgrade():
    op.drop_index("ix_a2a_inbox_messages_project_id", "a2a_inbox_messages")
    op.drop_table("a2a_inbox_messages")
