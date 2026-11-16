"""Durable per-rule cursors for automatic event-driven workflows.

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_cursors",
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("workflow_rules.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_cursors_last_event_id", "workflow_cursors", ["last_event_id"])
    op.execute(sa.text("""
        INSERT INTO workflow_cursors (rule_id, last_event_id, updated_at)
        SELECT wr.id, COALESCE(MAX(e.id), 0), CURRENT_TIMESTAMP
        FROM workflow_rules wr
        LEFT JOIN events e ON e.project_id = wr.project_id
        GROUP BY wr.id
    """))
    with op.batch_alter_table("workflow_cursors") as batch:
        batch.alter_column("last_event_id", existing_type=sa.Integer(), server_default=None)


def downgrade():
    op.drop_index("ix_workflow_cursors_last_event_id", table_name="workflow_cursors")
    op.drop_table("workflow_cursors")
