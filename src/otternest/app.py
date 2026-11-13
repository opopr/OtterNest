"""OtterNest ASGI entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text

from . import models  # noqa: F401 - load ORM models for schema registry
from .api import router
from .extensions import router as extension_router
from .webhooks import router as webhook_router
from .a2a import router as a2a_router
from .db import get_engine

app = FastAPI(
    title="OtterNest AI Team OS",
    description="Owner-controlled AI team coordination, task leases and authoritative knowledge",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)
app.include_router(router)
app.include_router(extension_router)
app.include_router(webhook_router)
app.include_router(a2a_router)


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready():
    try:
        with get_engine().connect() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Database not ready") from None
    return {"status": "ready"}


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(Path(__file__).with_name("index.html"), media_type="text/html")
