"""OtterNest ASGI entrypoint."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import models  # noqa: F401 - load ORM models for schema registry
from .api import router
from .extensions import router as extension_router
from .webhooks import router as webhook_router
from .a2a import router as a2a_router
from .a2a_outbound import router as a2a_outbound_router
from .governance import router as governance_router
from .intent import router as intent_router
from .model_planner import router as model_planner_router
from .evidence import router as evidence_router
from .deliberation import router as deliberation_router
from .continuity import router as continuity_router
from .lifecycle import router as lifecycle_router
from .auth import router as auth_router
from .portability import router as portability_router
from .ops import router as ops_router
from .workflow_engine import router as workflow_router
from .analytics import router as analytics_router
from .realtime import router as realtime_router
from .db import get_engine

_LEGACY_HTML = Path(__file__).with_name("index.html")
_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_WEB_DIST = Path(os.environ.get("OTTERNEST_WEB_DIST", str(_DEFAULT_WEB_DIST))).resolve()
_WEB_INDEX = _WEB_DIST / "index.html"
_WEB_ASSETS = _WEB_DIST / "assets"

app = FastAPI(
    title="OtterNest AI Team OS",
    description="Owner-controlled AI team coordination, task leases and authoritative knowledge",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# Cross-origin access is opt-in. Vite development normally uses a same-origin proxy;
# remote previews must explicitly allow their exact browser Origin.
_allowed_origins = [
    origin.strip().rstrip("/")
    for origin in os.environ.get("OTTERNEST_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if "*" in _allowed_origins:
    raise RuntimeError("OTTERNEST_CORS_ORIGINS must list exact Origins; wildcard '*' is not allowed")
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

app.include_router(router)
app.include_router(extension_router)
app.include_router(webhook_router)
app.include_router(a2a_router)
app.include_router(a2a_outbound_router)
app.include_router(governance_router)
app.include_router(intent_router)
app.include_router(model_planner_router)
app.include_router(evidence_router)
app.include_router(deliberation_router)
app.include_router(continuity_router)
app.include_router(lifecycle_router)
app.include_router(auth_router)
app.include_router(portability_router)
app.include_router(ops_router)
app.include_router(workflow_router)
app.include_router(analytics_router)
app.include_router(realtime_router)


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
    # Keep the old console available until all React pages have been migrated.
    if _WEB_INDEX.is_file():
        return FileResponse(_WEB_INDEX, media_type="text/html")
    return FileResponse(_LEGACY_HTML, media_type="text/html")


@app.get("/legacy", include_in_schema=False)
def legacy_console():
    return FileResponse(_LEGACY_HTML, media_type="text/html")


if _WEB_ASSETS.is_dir():
    app.mount("/ui-assets", StaticFiles(directory=_WEB_ASSETS), name="ui-assets")

_assets_dir = Path(__file__).with_name("assets")
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


@app.get("/{frontend_path:path}", include_in_schema=False)
def frontend_fallback(frontend_path: str):
    # API and static misses must remain 404s; only browser routes fall back to React.
    if frontend_path.startswith(("api/", "assets/", "ui-assets/")):
        raise HTTPException(404, "Not found")
    if _WEB_INDEX.is_file():
        return FileResponse(_WEB_INDEX, media_type="text/html")
    raise HTTPException(404, "Not found")
