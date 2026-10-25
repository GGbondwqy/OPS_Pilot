#!/usr/bin/env bash
# 启动 OpsDoctor API（前台运行；Ctrl+C 停止）
# 用法：bash scripts/start.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    echo "尚未安装，请先运行：bash scripts/install.sh"
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

HOST="${OPSDOCTOR_HOST:-127.0.0.1}"
PORT="${OPSDOCTOR_PORT:-8000}"
echo "OpsDoctor 启动中：http://${HOST}:${PORT}/health （API 文档 /docs）"
exec python -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
