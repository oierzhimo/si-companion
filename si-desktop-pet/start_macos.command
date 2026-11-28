#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未检测到 python3，请先从 https://www.python.org/downloads/ 或 Homebrew 安装 Python 3.12。"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "正在创建虚拟环境 .venv..."
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q

echo "正在启动 小祀 · Si Companion..."
exec .venv/bin/python main.py "$@"
