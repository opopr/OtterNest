"""Initial OtterNest control-plane schema.

Revision ID: 0001
Revises:
"""
from alembic import op
from otternest.db import Base
from otternest import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Initial schema is the single source of truth; follow-on changes require
    # additive Alembic migrations rather than create_all on server startup.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
