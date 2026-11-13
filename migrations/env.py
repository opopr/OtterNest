"""Database migrations are explicit; never auto-run during API startup."""
from __future__ import annotations

import os
from alembic import context
from sqlalchemy import create_engine, pool
from otternest.db import Base
from otternest import models  # noqa: F401

config = context.config
target_metadata = Base.metadata
url = os.getenv("OTTERNEST_DATABASE_URL")
if not url:
    raise RuntimeError("OTTERNEST_DATABASE_URL is required for migrations")


def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
