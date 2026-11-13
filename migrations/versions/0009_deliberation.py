"""Second-round governance records.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "decision_deliberations",
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id"), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("closing_note", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_decision_deliberations_project_id", "decision_deliberations", ["project_id"])
    op.create_table(
        "decision_rebuttals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("responds_to_review_id", sa.String(36), sa.ForeignKey("decision_reviews.id"), nullable=False),
        sa.Column("stance", sa.String(24), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("decision_id", "agent_id", name="uq_decision_rebuttal_agent"),
    )
    op.create_index("ix_decision_rebuttals_decision_id", "decision_rebuttals", ["decision_id"])
    op.create_index("ix_decision_rebuttals_project_id", "decision_rebuttals", ["project_id"])

def downgrade():
    op.drop_index("ix_decision_rebuttals_project_id", table_name="decision_rebuttals")
    op.drop_index("ix_decision_rebuttals_decision_id", table_name="decision_rebuttals")
    op.drop_table("decision_rebuttals")
    op.drop_index("ix_decision_deliberations_project_id", table_name="decision_deliberations")
    op.drop_table("decision_deliberations")
