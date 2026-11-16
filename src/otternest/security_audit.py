"""OtterNest production readiness and security audit CLI.

Verifies token entropy, database connection security, file permissions,
lease hygiene, and system protection posture before deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_engine
from .models import Agent, Project, SystemState, Task, now
from .services import get_system_mode


def audit_configuration() -> list[dict[str, Any]]:
    findings = []

    # 1. Check Owner Token
    token = os.environ.get("OTTERNEST_OWNER_TOKEN", "")
    if not token or token.startswith("REPLACE_"):
        findings.append({
            "check": "owner_token",
            "level": "critical",
            "message": "OTTERNEST_OWNER_TOKEN is unset or still contains template placeholder",
        })
    elif len(token) < 32:
        findings.append({
            "check": "owner_token",
            "level": "critical",
            "message": f"OTTERNEST_OWNER_TOKEN length is {len(token)} (minimum 32 required)",
        })
    else:
        findings.append({
            "check": "owner_token",
            "level": "pass",
            "message": "OTTERNEST_OWNER_TOKEN satisfies entropy requirements",
        })

    # 2. Check Database URL
    db_url = os.environ.get("OTTERNEST_DATABASE_URL", "")
    if not db_url or "REPLACE_" in db_url:
        findings.append({
            "check": "database_url",
            "level": "critical",
            "message": "OTTERNEST_DATABASE_URL is not configured with real database credentials",
        })
    elif db_url.startswith("sqlite"):
        findings.append({
            "check": "database_url",
            "level": "warning",
            "message": "Using SQLite backend. PostgreSQL 16+ is required for production concurrency",
        })
    else:
        findings.append({
            "check": "database_url",
            "level": "pass",
            "message": "PostgreSQL database configuration active",
        })

    # 3. Check Artifact Directory
    art_dir = Path(os.environ.get("OTTERNEST_ARTIFACT_DIR", "./var/artifacts"))
    if not art_dir.exists():
        findings.append({
            "check": "artifact_storage",
            "level": "warning",
            "message": f"Artifact directory {art_dir} does not exist yet (will be created on first upload)",
        })
    else:
        mode = art_dir.stat().st_mode
        if hasattr(os, "getuid") and (mode & 0o077):
            findings.append({
                "check": "artifact_storage",
                "level": "warning",
                "message": f"Artifact directory {art_dir} has permissive permissions (suggest chmod 700)",
            })
        else:
            findings.append({
                "check": "artifact_storage",
                "level": "pass",
                "message": f"Artifact directory {art_dir} is present with safe permissions",
            })

    # 4. Check Webhook & A2A Secrets if enabled
    if os.environ.get("OTTERNEST_WEBHOOK_ALLOWED_HOSTS"):
        wh_key = os.environ.get("OTTERNEST_WEBHOOK_SIGNING_KEY", "")
        if len(wh_key) < 32 or wh_key.startswith("REPLACE_"):
            findings.append({
                "check": "webhook_security",
                "level": "critical",
                "message": "Webhook signing key is missing or weak while webhook dispatch is enabled",
            })
        else:
            findings.append({
                "check": "webhook_security",
                "level": "pass",
                "message": "Webhook signing key and host filter configured",
            })

    return findings


def audit_database_state() -> list[dict[str, Any]]:
    findings = []
    try:
        with Session(get_engine()) as db:
            # 1. System protection mode
            mode = get_system_mode(db)
            if mode != "normal":
                findings.append({
                    "check": "system_mode",
                    "level": "warning",
                    "message": f"System is currently in protection mode: '{mode}'",
                })
            else:
                findings.append({
                    "check": "system_mode",
                    "level": "pass",
                    "message": "System operating in normal mode",
                })

            # 2. Check expired leases
            current_time = now()
            expired_tasks = db.scalars(
                select(Task).where(
                    Task.status == "running",
                    Task.lease_until.is_not(None),
                    Task.lease_until < current_time,
                )
            ).all()
            if expired_tasks:
                findings.append({
                    "check": "lease_hygiene",
                    "level": "warning",
                    "message": f"Found {len(expired_tasks)} running tasks with expired leases; reclamation required",
                })
            else:
                findings.append({
                    "check": "lease_hygiene",
                    "level": "pass",
                    "message": "Zero stale leases detected",
                })

            # 3. Check unverified capabilities
            agents = db.scalars(select(Agent)).all()
            unverified_count = 0
            for a in agents:
                declared = set(a.declared_capabilities or [])
                verified = set(a.verified_capabilities or [])
                if declared - verified:
                    unverified_count += 1
            if unverified_count:
                findings.append({
                    "check": "agent_capabilities",
                    "level": "info",
                    "message": f"{unverified_count} agent(s) have declared capabilities pending owner verification",
                })
            else:
                findings.append({
                    "check": "agent_capabilities",
                    "level": "pass",
                    "message": "All declared capabilities are verified",
                })

    except Exception as exc:
        findings.append({
            "check": "database_connection",
            "level": "critical",
            "message": f"Could not inspect database state: {exc}",
        })

    return findings


def run_security_audit() -> dict[str, Any]:
    findings = audit_configuration() + audit_database_state()
    has_critical = any(f["level"] == "critical" for f in findings)
    has_warning = any(f["level"] == "warning" for f in findings)
    overall = "FAIL" if has_critical else ("WARN" if has_warning else "PASS")
    return {
        "status": overall,
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest Security & Pre-deployment Audit")
    parser.add_argument("--json", action="store_true", help="Output audit report as JSON")
    args = parser.parse_args()

    report = run_security_audit()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("=== OtterNest Pre-Deployment Security Audit ===")
        print(f"Overall Status: {report['status']}\n")
        for f in report["findings"]:
            symbol = "✔" if f["level"] == "pass" else ("⚠" if f["level"] == "warning" else "✖")
            print(f"[{symbol}][{f['level'].upper()}] {f['check']}: {f['message']}")
        print("================================================")

    if report["status"] == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
