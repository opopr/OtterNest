"""Add vector embedding support to memories table.

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
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("embedding", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("memories") as batch:
        batch.drop_column("embedding")
