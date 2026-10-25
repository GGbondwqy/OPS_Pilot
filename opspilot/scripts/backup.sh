#!/usr/bin/env bash
# OpsPilot 数据备份：数据库（在线一致性备份）+ 通知 + 报告 + 令牌 + .env
# 用法：bash scripts/backup.sh [目标目录]     默认 backups/opspilot-<时间戳>
# 恢复：停服务 → 用备份覆盖 data/（env.backup 改名 .env）→ 起服务
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="python"
[ -x .venv/bin/python ] && PYTHON=".venv/bin/python"

TS="$(date +%Y%m%d-%H%M%S)"
DEST="${1:-backups/opspilot-${TS}}"
mkdir -p "$DEST"

echo "==> 备份数据库（SQLite 在线备份，可不停服）"
"$PYTHON" - "$DEST" <<'EOF'
import os
import sqlite3
import sys

dest = sys.argv[1]
db = "data/opspilot.db"
if not os.path.exists(db):
    print(f"    警告：未找到 {db}，跳过")
else:
    src = sqlite3.connect(db)
    dst = sqlite3.connect(os.path.join(dest, "opspilot.db.bak"))
    src.backup(dst)
    dst.close()
    src.close()
    print(f"    已备份 {db}")
EOF

echo "==> 备份通知 / 报告 / 模拟状态 / 令牌 / 配置"
for f in notifications.jsonl webhook_token admin_token mock_state.json; do
    if [ -f "data/$f" ]; then
        cp "data/$f" "$DEST/"
    fi
done
if [ -d data/reports ]; then
    cp -r data/reports "$DEST/"
fi
if [ -f .env ]; then
    cp .env "$DEST/env.backup"   # 含密钥，请妥善保管备份目录
fi
chmod 600 "$DEST"/env.backup 2>/dev/null || true

echo "✓ 备份完成：$DEST"
echo "  恢复方法：停服务 → 覆盖 data/ 与 .env → 重启服务 → python -m app.cli init"
