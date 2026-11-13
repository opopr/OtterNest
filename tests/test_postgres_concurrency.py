"""Integration test: PostgreSQL must serialize competing task claims.

Skipped during fast SQLite unit tests; enabled with OTTERNEST_PG_TEST_URL in CI.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine
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
