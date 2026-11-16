"""Trusted event-driven workflow worker with per-rule durable cursors."""
from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_engine
from .models import Event, Project, WorkflowCursor, WorkflowRule, now
from .security import Actor
from .services import get_system_mode
from .workflow_engine import process_rule_batch

log = logging.getLogger("otternest.workflow_worker")


def process_once(batch_size: int = 100) -> int:
    if not 1 <= batch_size <= 200:
        raise ValueError("batch_size must be 1..200")
    with Session(get_engine()) as db:
        if get_system_mode(db) != "normal":
            return 0
        rule_ids = db.scalars(
            select(WorkflowRule.id)
            .join(Project, Project.id == WorkflowRule.project_id)
            .where(WorkflowRule.enabled.is_(True), Project.status == "active")
            .order_by(WorkflowRule.created_at, WorkflowRule.id)
        ).all()

    processed = 0
    for rule_id in rule_ids:
        with Session(get_engine()) as db:
            if get_system_mode(db) != "normal":
                break
            rule = db.scalar(select(WorkflowRule).where(WorkflowRule.id == rule_id).with_for_update())
            if rule is None or not rule.enabled:
                continue
            project = db.scalar(select(Project).where(Project.id == rule.project_id).with_for_update())
            if project is None or project.status != "active":
                continue
            cursor = db.scalar(
                select(WorkflowCursor).where(WorkflowCursor.rule_id == rule.id).with_for_update()
            )
            if cursor is None:
                # Fail closed on an unexpected missing cursor: do not replay historical events.
                latest = db.scalar(
                    select(Event.id).where(Event.project_id == project.id)
                    .order_by(Event.id.desc()).limit(1)
                ) or 0
                db.add(WorkflowCursor(rule_id=rule.id, last_event_id=latest, updated_at=now()))
                db.commit()
                continue

            events = db.scalars(
                select(Event).where(
                    Event.project_id == project.id,
                    Event.id > cursor.last_event_id,
                ).order_by(Event.id.asc()).limit(batch_size)
            ).all()
            if not events:
                continue
            actor = Actor("owner", "workflow-worker", "system")
            process_rule_batch(db, rule, events, actor)
            cursor.last_event_id = events[-1].id
            cursor.updated_at = now()
            db.commit()
            processed += len(events)
            log.info("processed %s event(s) for workflow rule %s in project %s",
                     len(events), rule.id, project.id)
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest durable event-driven workflow worker")
    parser.add_argument("--once", action="store_true", help="Process one bounded event batch and exit")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds (>=2)")
    parser.add_argument("--batch-size", type=int, default=100, help="Maximum events per rule per pass (1..200)")
    args = parser.parse_args()
    if args.interval < 2:
        parser.error("--interval must be at least 2 seconds")
    if not 1 <= args.batch_size <= 200:
        parser.error("--batch-size must be 1..200")
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            process_once(args.batch_size)
        except Exception:
            log.exception("Workflow worker failed; cursor transaction rolled back")
            if args.once:
                raise
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
