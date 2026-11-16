"""Workflow rules, goal dependencies, portfolio priority, task metrics, experiments, and notifications.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="50"))
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("priority", existing_type=sa.Integer(), server_default=None)

    with op.batch_alter_table("goals") as batch:
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="50"))
    with op.batch_alter_table("goals") as batch:
        batch.alter_column("priority", existing_type=sa.Integer(), server_default=None)

    op.create_table(
        "goal_dependencies",
        sa.Column("goal_id", sa.String(36), sa.ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("depends_on_id", sa.String(36), sa.ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_goal_dependencies_predecessor", "goal_dependencies", ["depends_on_id"])

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("correlation_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("failure_reason", sa.String(64), nullable=True))
        batch.add_column(sa.Column("rework_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE tasks SET correlation_id = id WHERE correlation_id IS NULL"))
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("correlation_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("rework_count", existing_type=sa.Integer(), server_default=None)

    op.create_table(
        "workflow_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("event_prefix", sa.String(100), nullable=False),
        sa.Column("condition_status", sa.String(32), nullable=True),
        sa.Column("action_kind", sa.String(32), nullable=False),
        sa.Column("action_config", sa.JSON(), nullable=False),
        sa.Column("max_chain_depth", sa.Integer(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("triggered_count", sa.Integer(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_rules_project_id", "workflow_rules", ["project_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("workflow_rules.id"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("chain_depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_task_id", sa.String(36), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rule_id", "event_id", name="uq_workflow_rule_event"),
    )
    op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])
    op.create_index("ix_workflow_runs_rule_id", "workflow_runs", ["rule_id"])
    op.create_index("ix_workflow_runs_event_id", "workflow_runs", ["event_id"])

    op.create_table(
        "experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("baseline_score", sa.Integer(), nullable=False),
        sa.Column("candidate_score", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_experiments_project_id", "experiments", ["project_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notifications_project_id", "notifications", ["project_id"])


def downgrade():
    op.drop_index("ix_notifications_project_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_experiments_project_id", table_name="experiments")
    op.drop_table("experiments")
    op.drop_index("ix_workflow_runs_event_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_rule_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_project_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_workflow_rules_project_id", table_name="workflow_rules")
    op.drop_table("workflow_rules")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("rework_count")
        batch.drop_column("failure_reason")
        batch.drop_column("correlation_id")
    op.drop_index("ix_goal_dependencies_predecessor", table_name="goal_dependencies")
    op.drop_table("goal_dependencies")
    with op.batch_alter_table("goals") as batch:
        batch.drop_column("priority")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("priority")
