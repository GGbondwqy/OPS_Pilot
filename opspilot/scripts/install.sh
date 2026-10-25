#!/usr/bin/env bash
# ============================================================
# OpsPilot 一键安装脚本（Ubuntu 22.04/24.04 或其他主流 Linux）
# 用法：bash scripts/install.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"

echo "==> [1/5] 检查 Python 环境"
if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3，请先安装：sudo apt install -y python3 python3-venv python3-pip"
    exit 1
fi
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "需要 Python >= 3.10，当前为 $(python3 --version)"
    echo "Ubuntu 22.04/24.04 自带版本满足要求；旧系统建议用 uv 或 pyenv 安装新版本"
    exit 1
fi
echo "    Python $(python3 --version) 满足要求"

echo "==> [2/5] 创建虚拟环境"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

echo "==> [3/5] 安装依赖"
python -m pip install --quiet -r requirements.txt

echo "==> [4/5] 初始化配置"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "    已从模板创建 .env（AI 模型可留空，之后运行 python -m app.cli setup 配置）"
else
    echo "    已存在 .env，保持不变"
fi

echo "==> [5/5] 初始化数据库与知识库"
python -m app.cli init

cat <<EOF

============================================================
 安装完成！接下来三步：

 1. 启动服务：      bash scripts/start.sh
 2. 模拟一次故障：  新开终端运行  python -m app.cli simulate
 3. 查看与审批：    python -m app.cli status
                    python -m app.cli approve <审批单ID> --approver 你的名字

 可选：python -m app.cli setup   # 配置 AI 模型（URL/Key/模型名）
============================================================
EOF
