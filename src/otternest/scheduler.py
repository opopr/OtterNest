"""Small trusted control-plane daemon for safe *routing*, not remote execution.

It never stores a platform credential, executes a job, or assumes a remote Agent
has started work. A polling Agent must still claim with its own credential.
"""
from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_engine
from .models import Project
from .security import Actor
from .services import choose_dispatch_targets

log = logging.getLogger("otternest.scheduler")


def schedule_once(limit_per_project: int = 20, heartbeat_ttl_seconds: int = 180) -> int:
    if not 1 <= limit_per_project <= 100 or not 30 <= heartbeat_ttl_seconds <= 900:
        raise ValueError("Invalid scheduling limits")
    total = 0
    with Session(get_engine()) as db:
        project_ids = db.scalars(select(Project.id).where(Project.status == "active")
                                 .order_by(Project.id)).all()
    for project_id in project_ids:
        with Session(get_engine()) as db:
            routes = choose_dispatch_targets(db, project_id, Actor("owner", "scheduler"),
                                             limit_per_project, heartbeat_ttl_seconds)
            db.commit()
            total += len(routes)
            if routes:
                log.info("routed %s task(s) for project %s", len(routes), project_id)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest trusted project task router")
    parser.add_argument("--once", action="store_true", help="Route a single batch, then exit")
    parser.add_argument("--interval", type=int, default=15, help="Polling interval (seconds, >=5)")
    parser.add_argument("--limit-per-project", type=int, default=20)
    parser.add_argument("--heartbeat-ttl", type=int, default=180)
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("--interval must be at least 5 seconds")
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            schedule_once(args.limit_per_project, args.heartbeat_ttl)
        except Exception:
            log.exception("Task scheduling failed; no remote execution was started")
            if args.once:
                raise
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
