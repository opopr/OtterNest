"""Task dependencies, stable owner routing and approval tracking.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("priority", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("tasks", sa.Column("target_agent_id", sa.String(36), nullable=True))
    op.add_column("tasks", sa.Column("approval_granted", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("tasks") as batch:
        batch.create_foreign_key("fk_tasks_target_agent", "agents", ["target_agent_id"], ["id"])
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("depends_on_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_task_dependencies_predecessor", "task_dependencies", ["depends_on_id"])
    # Preserve approval for already-running R3 tasks during migration.
    op.execute("UPDATE tasks SET approval_granted = TRUE WHERE risk = 'R3' AND status IN ('ready', 'running', 'submitted', 'completed')")


def downgrade():
    op.drop_index("ix_task_dependencies_predecessor", "task_dependencies")
    op.drop_table("task_dependencies")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_target_agent", type_="foreignkey")
    op.drop_column("tasks", "approval_granted")
    op.drop_column("tasks", "target_agent_id")
    op.drop_column("tasks", "priority")
