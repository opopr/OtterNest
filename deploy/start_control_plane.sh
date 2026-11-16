#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

ENV_FILE="${OTTERNEST_ENV_FILE:-$APP_DIR/.env}"
VENV_DIR="${OTTERNEST_VENV_DIR:-$APP_DIR/.venv}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] 缺少 $ENV_FILE，请先运行 ./deploy/setup_test_server.sh" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

SCHED_PID=""
cleanup() {
  if [[ -n "$SCHED_PID" ]] && kill -0 "$SCHED_PID" 2>/dev/null; then
    kill "$SCHED_PID" 2>/dev/null || true
    wait "$SCHED_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "${OTTERNEST_RUN_SCHEDULER:-1}" == "1" ]]; then
  "$VENV_DIR/bin/otternest-scheduler" --interval 15 &
  SCHED_PID=$!
  echo "[OtterNest] otternest-scheduler 已启动 (PID: $SCHED_PID)"
fi

HOST="${OTTERNEST_HOST:-127.0.0.1}"
PORT="${OTTERNEST_PORT:-8000}"
echo "[OtterNest] 启动 uvicorn 控制平面于 http://${HOST}:${PORT}"
exec "$VENV_DIR/bin/uvicorn" otternest.app:app --host "$HOST" --port "$PORT"
