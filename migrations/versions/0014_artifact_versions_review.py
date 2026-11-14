"""Immutable artifact versions and owner review state.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("artifacts", sa.Column("family_id", sa.String(36), nullable=True))
    op.add_column("artifacts", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("artifacts", sa.Column("review_status", sa.String(24), nullable=False, server_default="pending"))
    op.add_column("artifacts", sa.Column("review_note", sa.Text(), nullable=False, server_default=""))
    op.add_column("artifacts", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE artifacts SET family_id = id WHERE family_id IS NULL"))
    with op.batch_alter_table("artifacts") as batch:
        batch.alter_column("family_id", existing_type=sa.String(36), nullable=False)
        batch.create_index("ix_artifacts_family_id", ["family_id"])
        batch.create_unique_constraint("uq_artifact_family_version", ["family_id", "version"])
        batch.alter_column("version", existing_type=sa.Integer(), server_default=None)
        batch.alter_column("review_status", existing_type=sa.String(24), server_default=None)
        batch.alter_column("review_note", existing_type=sa.Text(), server_default=None)


def downgrade():
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("uq_artifact_family_version", type_="unique")
        batch.drop_index("ix_artifacts_family_id")
        batch.drop_column("reviewed_at")
        batch.drop_column("review_note")
        batch.drop_column("review_status")
        batch.drop_column("version")
        batch.drop_column("family_id")
