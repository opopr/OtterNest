"""Durable signed Webhook destinations and delivery receipts.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("event_prefix", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_endpoints_project_id", "webhook_endpoints", ["project_id"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("webhook_endpoints.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(240), nullable=False),
        sa.UniqueConstraint("event_id", "endpoint_id", name="uq_event_endpoint"),
    )
    op.create_index("ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"])
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])


def downgrade():
    op.drop_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event_id", "webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_endpoints_project_id", "webhook_endpoints")
    op.drop_table("webhook_endpoints")
