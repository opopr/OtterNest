"""Frozen initial OtterNest schema; do not import evolving model metadata here.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()
sa.Table(
    'agents', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('name', sa.String(length=120), primary_key=False, nullable=False),
    sa.Column('platform', sa.String(length=80), primary_key=False, nullable=False),
    sa.Column('token_hash', sa.String(length=64), primary_key=False, nullable=False),
    sa.Column('enabled', sa.Boolean(), primary_key=False, nullable=False),
    sa.Column('execution_mode', sa.String(length=24), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
    sa.UniqueConstraint('token_hash'),
)

sa.Table(
    'audit', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('actor', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('action', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('subject', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Table(
    'projects', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('name', sa.String(length=160), primary_key=False, nullable=False),
    sa.Column('description', sa.Text(), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=24), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Table(
    'decisions', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
    sa.Column('question', sa.Text(), primary_key=False, nullable=False),
    sa.Column('resolution', sa.Text(), primary_key=False, nullable=False),
    sa.Column('rationale', sa.Text(), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=24), primary_key=False, nullable=False),
)

sa.Table(
    'events', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=True),
    sa.Column('kind', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('subject_id', sa.String(length=36), primary_key=False, nullable=False),
    sa.Column('payload', sa.JSON(), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Table(
    'goals', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
    sa.Column('title', sa.String(length=240), primary_key=False, nullable=False),
    sa.Column('success_criteria', sa.Text(), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=24), primary_key=False, nullable=False),
)

sa.Table(
    'memberships', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
    sa.Column('agent_id', sa.String(length=36), sa.ForeignKey('agents.id'), primary_key=False, nullable=False),
    sa.Column('role', sa.String(length=32), primary_key=False, nullable=False),
    sa.UniqueConstraint('project_id', 'agent_id', name='uq_membership'),
)

sa.Table(
    'memories', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
    sa.Column('kind', sa.String(length=32), primary_key=False, nullable=False),
    sa.Column('title', sa.String(length=240), primary_key=False, nullable=False),
    sa.Column('content', sa.Text(), primary_key=False, nullable=False),
    sa.Column('source', sa.Text(), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=24), primary_key=False, nullable=False),
    sa.Column('version', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Table(
    'outbox', metadata,
    sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id'), primary_key=True, nullable=False),
    sa.Column('status', sa.String(length=24), primary_key=False, nullable=False),
    sa.Column('attempts', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Table(
    'tasks', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
    sa.Column('goal_id', sa.String(length=36), sa.ForeignKey('goals.id'), primary_key=False, nullable=True),
    sa.Column('title', sa.String(length=240), primary_key=False, nullable=False),
    sa.Column('instruction', sa.Text(), primary_key=False, nullable=False),
    sa.Column('acceptance', sa.Text(), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=24), primary_key=False, nullable=False),
    sa.Column('risk', sa.String(length=2), primary_key=False, nullable=False),
    sa.Column('assigned_agent_id', sa.String(length=36), sa.ForeignKey('agents.id'), primary_key=False, nullable=True),
    sa.Column('lease_epoch', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), primary_key=False, nullable=True),
    sa.Column('result_summary', sa.Text(), primary_key=False, nullable=True),
    sa.Column('version', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Table(
    'artifacts', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id'), primary_key=False, nullable=False),
    sa.Column('task_id', sa.String(length=36), sa.ForeignKey('tasks.id'), primary_key=False, nullable=False),
    sa.Column('name', sa.String(length=240), primary_key=False, nullable=False),
    sa.Column('checksum', sa.String(length=64), primary_key=False, nullable=False),
    sa.Column('uri', sa.Text(), primary_key=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), primary_key=False, nullable=False),
)

sa.Index('ix_decisions_project_id', metadata.tables['decisions'].c['project_id'])
sa.Index('ix_events_project_id', metadata.tables['events'].c['project_id'])
sa.Index('ix_goals_project_id', metadata.tables['goals'].c['project_id'])
sa.Index('ix_memberships_agent_id', metadata.tables['memberships'].c['agent_id'])
sa.Index('ix_memberships_project_id', metadata.tables['memberships'].c['project_id'])
sa.Index('ix_memories_project_id', metadata.tables['memories'].c['project_id'])
sa.Index('ix_tasks_project_id', metadata.tables['tasks'].c['project_id'])
sa.Index('ix_tasks_project_status', metadata.tables['tasks'].c['project_id'], metadata.tables['tasks'].c['status'])
sa.Index('ix_artifacts_project_id', metadata.tables['artifacts'].c['project_id'])
sa.Index('ix_artifacts_task_id', metadata.tables['artifacts'].c['task_id'])

def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())

def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
