"""Add deleted_at soft-delete column to agents table for recycle bin support.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "deleted_at")
