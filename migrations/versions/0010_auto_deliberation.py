"""Owner-approved automatic two-round deliberation roster.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decision_auto_policies",
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id"), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("reviewer_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_auto_policies_project_id", "decision_auto_policies", ["project_id"])


def downgrade():
    op.drop_index("ix_decision_auto_policies_project_id", table_name="decision_auto_policies")
    op.drop_table("decision_auto_policies")
