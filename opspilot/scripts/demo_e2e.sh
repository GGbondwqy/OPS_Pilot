#!/usr/bin/env bash
# ============================================================
# 端到端演示：注入故障 → 自动调查 → 人工审批 → 执行 → 复验闭环
# 用法：bash scripts/demo_e2e.sh [rabbitmq|stuck-rabbitmq]
#   rabbitmq        正常闭环：审批后恢复成功（默认）
#   stuck-rabbitmq  假恢复演示：执行成功但业务未恢复 → 拦截并升级人工
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
SCENARIO="${1:-rabbitmq}"

PYTHON="python"
if [ -x .venv/bin/python ]; then
    PYTHON=".venv/bin/python"
fi

HOST="${OPSPILOT_HOST:-127.0.0.1}"
PORT="${OPSPILOT_PORT:-8000}"
BASE="http://${HOST}:${PORT}"

echo "==> [1/6] 确认服务健康"
if ! curl -sf "${BASE}/health" >/dev/null; then
    echo "服务未启动。请先运行：bash scripts/start.sh"
    exit 1
fi
curl -s "${BASE}/health" | head -c 200; echo

echo "==> [2/6] 注入故障并发送模拟告警（场景：${SCENARIO}）"
"$PYTHON" -m app.cli simulate --scenario "${SCENARIO}"
sleep 1

echo
echo "==> [3/6] 等待后台自动调查（最多 60 秒）"
INCIDENT=""
for _ in $(seq 1 60); do
    STATUS_LINE=$("$PYTHON" -m app.cli status --limit 1 2>/dev/null | sed -n '3p' || true)
    if echo "${STATUS_LINE}" | grep -qE "NEEDS_APPROVAL|ESCALATED|DIAGNOSED"; then
        INCIDENT=$(echo "${STATUS_LINE}" | awk '{print $1}')
        break
    fi
    sleep 1
done
if [ -z "${INCIDENT}" ]; then
    echo "60 秒内未产生待审批事故，请运行 python -m app.cli status 查看详情"
    exit 1
fi
"$PYTHON" -m app.cli show "${INCIDENT}"

echo
echo "==> [4/6] 查看待审批动作"
APPROVAL=$("$PYTHON" - <<EOF
from app.persistence import repository
from app.persistence.database import SessionLocal
with SessionLocal() as s:
    rows = repository.list_approvals(s, status="PENDING")
    print(rows[0].id if rows else "")
EOF
)
if [ -z "${APPROVAL}" ]; then
    echo "（该事故没有进入审批环节 —— 已按安全策略升级人工，演示结束）"
    exit 0
fi
echo "待审批单 ID：${APPROVAL}"

echo
echo "==> [5/6] 人工审批并执行"
"$PYTHON" -m app.cli approve "${APPROVAL}" --approver demo-operator

echo
echo "==> [6/6] 最终状态"
"$PYTHON" -m app.cli show "${INCIDENT}"
"$PYTHON" -m app.cli status
echo
echo "通知记录：data/notifications.jsonl"
echo "演示完成。"
