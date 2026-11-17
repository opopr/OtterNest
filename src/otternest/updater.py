"""Self-update engine for OtterNest web control plane.

Provides safe owner-only update checks (`git fetch` + commit comparison) and a
detached background update pipeline (`git pull --ff-only`, `pip install`,
`alembic upgrade head`, `npm run build`, and optional service restart).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


class UpdateError(RuntimeError):
    """Raised when an update precondition or step fails."""


def is_web_update_enabled() -> bool:
    return os.environ.get("OTTERNEST_ENABLE_WEB_UPDATE", "").strip().lower() in {"1", "true", "yes", "on"}


def get_repo_root() -> Path:
    env_root = os.environ.get("OTTERNEST_REPO_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    cur = Path(__file__).resolve().parent
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd().resolve()


def get_status_file(repo_root: Path | None = None) -> Path:
    env_path = os.environ.get("OTTERNEST_UPDATE_STATUS_FILE", "").strip()
    if env_path:
        return Path(env_path).resolve()
    root = repo_root or get_repo_root()
    return root / "var" / "update_status.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_cmd(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def check_for_updates(repo_root: Path | None = None, *, fetch: bool = True) -> dict[str, Any]:
    root = repo_root or get_repo_root()
    if not (root / ".git").exists():
        raise UpdateError(f"Repository .git directory not found at {root}")

    rc, branch, err = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], root, timeout=15)
    if rc != 0:
        raise UpdateError(f"Failed to read git branch: {err}")

    rc, current_commit, err = _run_cmd(["git", "rev-parse", "--short", "HEAD"], root, timeout=15)
    if rc != 0:
        raise UpdateError(f"Failed to read current commit: {err}")

    rc, status_out, _ = _run_cmd(["git", "status", "--porcelain"], root, timeout=15)
    working_tree_clean = (rc == 0 and status_out == "")

    fetch_error = ""
    if fetch:
        rc_f, _, err_f = _run_cmd(["git", "fetch", "origin"], root, timeout=30)
        if rc_f != 0:
            fetch_error = err_f or "git fetch origin failed"

    target_ref = f"origin/{branch}" if branch and branch != "HEAD" else "origin/main"
    rc_r, remote_commit, _ = _run_cmd(["git", "rev-parse", "--short", target_ref], root, timeout=15)
    if rc_r != 0:
        target_ref = "origin/main"
        rc_r, remote_commit, _ = _run_cmd(["git", "rev-parse", "--short", target_ref], root, timeout=15)
    if rc_r != 0:
        remote_commit = current_commit

    rc_c, count_out, _ = _run_cmd(["git", "rev-list", "--count", f"HEAD..{target_ref}"], root, timeout=15)
    behind_count = int(count_out) if (rc_c == 0 and count_out.isdigit()) else 0

    recent_commits: list[str] = []
    if behind_count > 0:
        rc_l, log_out, _ = _run_cmd(["git", "log", "--oneline", "-n", "10", f"HEAD..{target_ref}"], root, timeout=15)
        if rc_l == 0 and log_out:
            recent_commits = [line for line in log_out.splitlines() if line.strip()]

    return {
        "enabled": is_web_update_enabled(),
        "repo_root": str(root),
        "branch": branch,
        "target_ref": target_ref,
        "current_commit": current_commit,
        "remote_commit": remote_commit,
        "behind_count": behind_count,
        "update_available": behind_count > 0,
        "working_tree_clean": working_tree_clean,
        "recent_commits": recent_commits,
        "fetch_error": fetch_error,
        "restart_command_configured": bool(os.environ.get("OTTERNEST_RESTART_COMMAND", "").strip()),
        "checked_at": _now_iso(),
    }


def get_update_status(repo_root: Path | None = None) -> dict[str, Any]:
    status_file = get_status_file(repo_root)
    if not status_file.exists():
        return {
            "state": "idle",
            "step": "",
            "logs": [],
            "started_at": None,
            "finished_at": None,
            "error": "",
        }
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return {
            "state": "idle",
            "step": "",
            "logs": [],
            "started_at": None,
            "finished_at": None,
            "error": "",
        }


def _write_status(status_file: Path, data: dict[str, Any]) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def start_update_job(repo_root: Path | None = None) -> dict[str, Any]:
    if not is_web_update_enabled():
        raise UpdateError("Web self-update is disabled. Set OTTERNEST_ENABLE_WEB_UPDATE=true on the server.")
    root = repo_root or get_repo_root()
    status_file = get_status_file(root)
    current = get_update_status(root)
    if current.get("state") == "running":
        raise UpdateError("An update job is already running")

    initial = {
        "state": "running",
        "step": "initializing",
        "logs": [f"[{_now_iso()}] Update job queued"],
        "started_at": _now_iso(),
        "finished_at": None,
        "error": "",
    }
    _write_status(status_file, initial)

    cmd = [
        sys.executable,
        "-m",
        "otternest.updater",
        "--apply",
        "--repo-root",
        str(root),
        "--status-file",
        str(status_file),
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    return initial


def run_apply_pipeline(repo_root: Path, status_file: Path) -> int:
    data = get_update_status(repo_root)
    logs: list[str] = list(data.get("logs") or [])

    def log(step: str, msg: str) -> None:
        data["step"] = step
        logs.append(f"[{_now_iso()}] {msg}")
        data["logs"] = logs[-80:]
        _write_status(status_file, data)

    def fail(step: str, err: str) -> int:
        data["state"] = "failed"
        data["step"] = step
        data["error"] = err
        data["finished_at"] = _now_iso()
        logs.append(f"[{_now_iso()}] FAILED ({step}): {err}")
        data["logs"] = logs[-80:]
        _write_status(status_file, data)
        return 1

    # 1. git pull --ff-only
    log("git_pull", "Pulling latest commits from origin...")
    rc_b, branch, err_b = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root, timeout=15)
    target_branch = branch if (rc_b == 0 and branch and branch != "HEAD") else "main"
    rc, out, err = _run_cmd(["git", "pull", "--ff-only", "origin", target_branch], repo_root, timeout=60)
    if rc != 0:
        return fail("git_pull", err or out or f"git pull failed ({rc})")
    log("git_pull", out or "Repository up to date.")

    # 2. pip install -e . (optional skip for constrained environments)
    if os.environ.get("OTTERNEST_UPDATE_SKIP_PIP", "").strip().lower() not in {"1", "true", "yes"}:
        log("pip_install", "Updating Python package dependencies...")
        rc, out, err = _run_cmd([sys.executable, "-m", "pip", "install", "-e", ".", "--no-deps"], repo_root, timeout=180)
        if rc != 0:
            return fail("pip_install", err or out or f"pip install failed ({rc})")

    # 3. alembic upgrade head
    log("db_migrate", "Running database migrations (alembic upgrade head)...")
    rc, out, err = _run_cmd([sys.executable, "-m", "alembic", "upgrade", "head"], repo_root, timeout=120)
    if rc != 0:
        return fail("db_migrate", err or out or f"alembic upgrade failed ({rc})")
    log("db_migrate", out or "Database migrations complete.")

    # 4. npm run build (if frontend exists)
    npm_exe = shutil.which("npm")
    if (repo_root / "package.json").exists() and (repo_root / "frontend" / "node_modules").exists() and npm_exe:
        log("frontend_build", "Building React frontend (npm run build)...")
        rc, out, err = _run_cmd([npm_exe, "run", "build"], repo_root, timeout=180)
        if rc != 0:
            return fail("frontend_build", err or out or f"frontend build failed ({rc})")
        log("frontend_build", "Frontend build complete.")

    # 5. Mark succeeded before triggering self-restart
    restart_cmd = os.environ.get("OTTERNEST_RESTART_COMMAND", "").strip()
    data["state"] = "succeeded"
    data["step"] = "restart" if restart_cmd else "completed"
    data["finished_at"] = _now_iso()
    logs.append(f"[{_now_iso()}] Update succeeded" + ("; triggering service restart..." if restart_cmd else "."))
    data["logs"] = logs[-80:]
    _write_status(status_file, data)

    if restart_cmd:
        parts = shlex.split(restart_cmd)
        subprocess.Popen(parts, cwd=str(repo_root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest self-updater")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--status-file", default="")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve() if args.repo_root else get_repo_root()
    status_file = Path(args.status_file).resolve() if args.status_file else get_status_file(root)
    if args.apply:
        sys.exit(run_apply_pipeline(root, status_file))


if __name__ == "__main__":
    main()
