"""Integration test: PostgreSQL must serialize competing task claims.

Skipped during fast SQLite unit tests; enabled with OTTERNEST_PG_TEST_URL in CI.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from otternest.models import Agent, Membership, Project, Task
from otternest.security import Actor
from otternest.services import Conflict, claim


@pytest.mark.skipif(not os.getenv("OTTERNEST_PG_TEST_URL"), reason="PostgreSQL integration URL not provided")
def test_postgres_two_agent_claims_only_one_wins():
    url = os.environ["OTTERNEST_PG_TEST_URL"]
    if not url.startswith("postgresql+"):
        pytest.fail("This test must use PostgreSQL, not SQLite")
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        project = Project(name="Concurrent claim test")
        agent1 = Agent(name="Worker one", platform="test", token_hash="0" * 64)
        agent2 = Agent(name="Worker two", platform="test", token_hash="1" * 64)
        db.add_all([project, agent1, agent2])
        db.flush()
        db.add_all([Membership(project_id=project.id, agent_id=agent1.id, role="worker"),
                    Membership(project_id=project.id, agent_id=agent2.id, role="worker")])
        task = Task(project_id=project.id, title="Competing task", instruction="work",
                    acceptance="one owner", risk="R1")
        db.add(task)
        db.commit()
        task_id, actor_ids = task.id, (agent1.id, agent2.id)

    ready = Barrier(2)

    def attempt(agent_id):
        with factory() as db:
            ready.wait(timeout=10)
            try:
                row = claim(db, task_id, Actor("agent", agent_id), lease_seconds=120)
                return ("claimed", row.assigned_agent_id)
            except Conflict:
                db.rollback()
                return ("conflict", agent_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, actor_ids))
    assert sorted(status for status, _ in results) == ["claimed", "conflict"]
    with Session(engine) as db:
        persisted = db.get(Task, task_id)
        assert persisted.status == "running"
        assert persisted.lease_epoch == 1
        assert persisted.assigned_agent_id in actor_ids
    engine.dispose()


@pytest.mark.skipif(not os.getenv("OTTERNEST_PG_TEST_URL"), reason="PostgreSQL integration URL not provided")
def test_postgres_concurrent_schedulers_do_not_double_reserve():
    """Project row lock serializes two schedulers; a worker has one outstanding slot."""
    import secrets
    from otternest.models import AgentRuntime, now
    from otternest.services import choose_dispatch_targets
    url = os.environ["OTTERNEST_PG_TEST_URL"]
    if not url.startswith("postgresql+"):
        pytest.fail("PostgreSQL required")
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        p = Project(name="Scheduler concurrency")
        a = Agent(name="Scheduler worker", platform="test", execution_mode="polling",
                  token_hash=secrets.token_hex(32), declared_capabilities=["python"],
                  verified_capabilities=["python"])
        db.add_all([p, a])
        db.flush()
        db.add(Membership(project_id=p.id, agent_id=a.id, role="worker"))
        db.add(AgentRuntime(agent_id=a.id, label="live-runner", environment="ci",
                            enabled=True, status="idle", last_seen=now()))
        t1 = Task(project_id=p.id, title="Priority 1", instruction="run", acceptance="done",
                  priority=90, required_capabilities=["python"])
        t2 = Task(project_id=p.id, title="Priority 2", instruction="run", acceptance="done",
                  priority=80, required_capabilities=["python"])
        db.add_all([t1, t2])
        db.commit()
        project_id, task_ids, agent_id = p.id, (t1.id, t2.id), a.id

    barrier = Barrier(2)

    def route(_):
        with factory() as db:
            barrier.wait(timeout=10)
            results = choose_dispatch_targets(db, project_id, Actor("owner", "scheduler"), limit=2)
            db.commit()
            return results

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(route, range(2)))
    assert sum(map(len, results)) == 1
    with factory() as db:
        reserved = db.scalars(select(Task).where(Task.id.in_(task_ids), Task.target_agent_id == agent_id)).all()
        assert len(reserved) == 1
        assert reserved[0].id == task_ids[0]
    engine.dispose()


@pytest.mark.skipif(not os.getenv("OTTERNEST_PG_TEST_URL"), reason="PostgreSQL integration URL not provided")
def test_postgres_a2a_duplicate_messages_create_one_task():
    """One messageId must never fan out into multiple tasks under concurrent sends."""
    from otternest.a2a import send_message
    from otternest.models import A2AInboxMessage
    import secrets

    url = os.environ["OTTERNEST_PG_TEST_URL"]
    if not url.startswith("postgresql+"):
        pytest.fail("PostgreSQL required")
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        project = Project(name="A2A dedupe concurrency")
        db.add(project)
        db.commit()
        project_id = project.id

    params = {
        "message": {"kind": "message", "role": "user",
                    "messageId": secrets.token_hex(16),
                    "parts": [{"kind": "text", "text": "Build a checked report"}]},
        "metadata": {"projectId": project_id, "title": "Report", "risk": "R1"},
    }
    barrier = Barrier(2)

    def send(_):
        with factory() as db:
            barrier.wait(timeout=10)
            return send_message(db, Actor("owner", "owner"), params)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(send, range(2)))
    assert ids[0] == ids[1]
    with factory() as db:
        assert len(db.scalars(select(A2AInboxMessage).where(A2AInboxMessage.project_id == project_id)).all()) == 1
        assert len(db.scalars(select(Task).where(Task.project_id == project_id)).all()) == 1
    engine.dispose()
