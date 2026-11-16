#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

ENV_FILE="${OTTERNEST_ENV_FILE:-$APP_DIR/.env}"
VENV_DIR="${OTTERNEST_VENV_DIR:-$APP_DIR/.venv}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[OtterNest] 未找到 $ENV_FILE，正在从 deploy/env.test.template 复制模板..."
  cp "$APP_DIR/deploy/env.test.template" "$ENV_FILE"
  echo "[OtterNest] 请先编辑 $ENV_FILE 填入真实数据库密码和随机 Token（>=32 字符），然后重新运行本脚本。"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${OTTERNEST_OWNER_TOKEN:-}" || "${OTTERNEST_OWNER_TOKEN}" == REPLACE_* || "${#OTTERNEST_OWNER_TOKEN}" -lt 32 ]]; then
  echo "[ERROR] OTTERNEST_OWNER_TOKEN 缺失、仍为占位符或长度不足 32 字符。" >&2
  echo "生成命令: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"" >&2
  exit 1
fi

if [[ -z "${OTTERNEST_DATABASE_URL:-}" || "${OTTERNEST_DATABASE_URL}" == *REPLACE_* ]]; then
  echo "[ERROR] 请在 $ENV_FILE 中配置有效的 OTTERNEST_DATABASE_URL。" >&2
  exit 1
fi

mkdir -p "${OTTERNEST_ARTIFACT_DIR:-./var/artifacts}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[OtterNest] 创建虚拟环境: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -e ".[dev,mcp]"

echo "[OtterNest] 执行 Alembic 数据库迁移..."
"$VENV_DIR/bin/alembic" upgrade head

echo "[OtterNest] 编译校验与单元测试..."
"$VENV_DIR/bin/python" -m compileall -q src
"$VENV_DIR/bin/pytest" -q

echo "[OtterNest] 初始化与校验完成。可通过 ./deploy/start_control_plane.sh 启动服务。"
