from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from otternest.app import app
from otternest.db import Base, get_session
from otternest import models  # noqa: F401

OWNER = "test-owner-secret-longer-than-32-characters"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("OTTERNEST_OWNER_TOKEN", OWNER)
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def owner_headers():
    return {"Authorization": f"Bearer {OWNER}"}
