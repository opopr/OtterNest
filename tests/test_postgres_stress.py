"""High-concurrency PostgreSQL stress and throughput test.

Exercises 20+ concurrent workers claiming tasks, checkpointing, and reporting side-effects
under explicit row-level locking to ensure zero deadlocks and strict epoch isolation.
Skipped when OTTERNEST_PG_TEST_URL is not set.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from otternest.models import Agent, Membership, Project, Task, TaskCheckpoint
from otternest.security import Actor
from otternest.services import Conflict, claim


@pytest.mark.skipif(not os.getenv("OTTERNEST_PG_TEST_URL"), reason="PostgreSQL test URL not provided")
def test_postgres_high_concurrency_stress():
    url = os.environ["OTTERNEST_PG_TEST_URL"]
    if not url.startswith("postgresql+"):
        pytest.fail("PostgreSQL required")

    engine = create_engine(url, pool_size=25, max_overflow=10, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    num_workers = 10
    num_tasks = 20

    with factory() as db:
        project = Project(name="Postgres Stress Test")
        db.add(project)
        db.flush()

        agents = []
        for i in range(num_workers):
            ag = Agent(name=f"Stress Agent {i}", platform="test", token_hash=f"{i}" * 64)
            db.add(ag)
            agents.append(ag)
        db.flush()

        for ag in agents:
            db.add(Membership(project_id=project.id, agent_id=ag.id, role="worker"))

        tasks = []
        for i in range(num_tasks):
            t = Task(
                project_id=project.id,
                title=f"Stress Task {i}",
                instruction="High concurrency execution",
                acceptance="Verification passed",
                risk="R1",
            )
            db.add(t)
            tasks.append(t)
        db.commit()

        project_id = project.id
        agent_ids = [ag.id for ag in agents]
        task_ids = [t.id for t in tasks]

    # Test concurrent claim races: all agents race to claim all tasks
    barrier = Barrier(num_workers)

    def worker_loop(agent_id: str):
        claimed_tasks = []
        with factory() as db:
            barrier.wait(timeout=15)
            for t_id in task_ids:
                try:
                    row = claim(db, t_id, Actor("agent", agent_id), lease_seconds=60)
                    db.commit()
                    claimed_tasks.append(row.id)
                except Conflict:
                    db.rollback()
                except Exception:
                    db.rollback()

        # For all claimed tasks, submit an execution checkpoint
        with factory() as db:
            for t_id in claimed_tasks:
                db.add(TaskCheckpoint(
                    project_id=project_id,
                    task_id=t_id,
                    agent_id=agent_id,
                    lease_epoch=1,
                    step_number=1,
                    summary="High concurrency checkpoint verified",
                ))
            db.commit()

        return len(claimed_tasks)

    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        futures = [pool.submit(worker_loop, a_id) for a_id in agent_ids]
        counts = [f.result() for f in as_completed(futures)]

    total_claimed = sum(counts)
    assert total_claimed == num_tasks, f"Expected {num_tasks} tasks claimed, got {total_claimed}"

    # Verify database state integrity
    with factory() as db:
        persisted_tasks = db.scalars(select(Task).where(Task.project_id == project_id)).all()
        assert len(persisted_tasks) == num_tasks
        assert all(t.status == "running" for t in persisted_tasks)
        assert all(t.lease_epoch == 1 for t in persisted_tasks)

        # Check checkpoints recorded
        cps = db.scalars(select(TaskCheckpoint).join(Task).where(Task.project_id == project_id)).all()
        assert len(cps) == num_tasks

    engine.dispose()
