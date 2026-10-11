#!/usr/bin/env bash
# 只读发现最近私信会话的白名单身份信息；不会调用 AI，也不会发送消息。
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
CONFIG_FILE="$PROJECT_DIR/config/config.json"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "找不到虚拟环境：请先运行 bash scripts/setup_linux.sh" >&2
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "找不到配置文件：$CONFIG_FILE" >&2
  exit 1
fi

cd "$PROJECT_DIR"
echo "只读发现模式：不会发送消息。"
exec "$PYTHON_BIN" src/main.py --config "$CONFIG_FILE" --discover-friends --no-ai
