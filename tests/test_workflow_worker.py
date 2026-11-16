from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from otternest.db import Base
from otternest.models import Event, Notification, Project, WorkflowCursor, WorkflowRule, WorkflowRun
from otternest import workflow_worker


def _seeded_engine(project_status="active"):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(id="project-1", name="Workflow project", status=project_status)
        rule = WorkflowRule(
            id="rule-1",
            project_id=project.id,
            name="Notify on accepted task",
            event_prefix="task.accepted",
            action_kind="notify_inbox",
            action_config={"title": "Task accepted", "body": "A task was accepted."},
            max_chain_depth=3,
            cooldown_seconds=0,
            enabled=True,
        )
        event = Event(
            id=1,
            project_id=project.id,
            kind="task.accepted",
            subject_id="task-1",
            payload={"correlation_id": "corr-1"},
        )
        cursor = WorkflowCursor(rule_id=rule.id, last_event_id=0)
        db.add_all([project, rule, event, cursor])
        db.commit()
    return engine


def test_worker_processes_events_once_and_advances_durable_cursor(monkeypatch):
    engine = _seeded_engine()
    monkeypatch.setattr(workflow_worker, "get_engine", lambda: engine)
    try:
        assert workflow_worker.process_once(batch_size=10) == 1
        with Session(engine) as db:
            cursor = db.get(WorkflowCursor, "rule-1")
            assert cursor.last_event_id == 1
            runs = db.scalars(select(WorkflowRun)).all()
            notifications = db.scalars(select(Notification)).all()
            assert len(runs) == 1
            assert runs[0].status == "executed"
            assert runs[0].event_id == 1
            assert len(notifications) == 1
            assert notifications[0].title == "Task accepted"

        # The same event is not delivered twice after a committed cursor advance.
        assert workflow_worker.process_once(batch_size=10) == 0
        with Session(engine) as db:
            assert len(db.scalars(select(WorkflowRun)).all()) == 1
            assert len(db.scalars(select(Notification)).all()) == 1
    finally:
        engine.dispose()


def test_worker_does_not_process_paused_projects(monkeypatch):
    engine = _seeded_engine(project_status="paused")
    monkeypatch.setattr(workflow_worker, "get_engine", lambda: engine)
    try:
        assert workflow_worker.process_once(batch_size=10) == 0
        with Session(engine) as db:
            assert db.scalars(select(WorkflowRun)).all() == []
            assert db.get(WorkflowCursor, "rule-1").last_event_id == 0
    finally:
        engine.dispose()


def test_worker_rejects_invalid_batch_size():
    import pytest
    with pytest.raises(ValueError):
        workflow_worker.process_once(batch_size=0)
