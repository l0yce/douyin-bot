#!/usr/bin/env bash
# Ubuntu / Debian server installer. Run from the release checkout as root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash scripts/install.sh'; exit 1; }
command -v apt-get >/dev/null || { echo '此脚本适用于 Ubuntu/Debian；其他系统请手动安装依赖。'; exit 1; }
command -v node >/dev/null || { echo '请先安装 Node.js 20 或以上版本及 npm：https://nodejs.org/'; exit 1; }
node -e 'if(Number(process.versions.node.split(".")[0])<20)process.exit(1)' || { echo '需要 Node.js >= 20'; exit 1; }
command -v npm >/dev/null
apt-get update
apt-get install -y python3 python3-venv python3-dev build-essential xvfb
python3 -c 'import sys; assert sys.version_info >= (3,11), "需要 Python >= 3.11"'
python3 -m venv "$ROOT/douyin-friend-ai-bot/.venv"
"$ROOT/douyin-friend-ai-bot/.venv/bin/pip" install -r "$ROOT/douyin-friend-ai-bot/requirements.txt"
PLAYWRIGHT_BROWSERS_PATH="$ROOT/douyin-friend-ai-bot/ms-playwright" "$ROOT/douyin-friend-ai-bot/.venv/bin/python" -m playwright install --with-deps firefox
python3 -m venv "$ROOT/douyin-bot-panel/venv"
"$ROOT/douyin-bot-panel/venv/bin/pip" install -r "$ROOT/douyin-bot-panel/requirements.txt"
(cd "$ROOT/douyin-interlude" && PUPPETEER_SKIP_DOWNLOAD=true npm ci --omit=dev)
python3 "$ROOT/scripts/init_config.py" --root "$ROOT"
NODE_BIN="$(command -v node)"
python3 - "$ROOT" "$NODE_BIN" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]);node=sys.argv[2]
if any(c in str(root)+node for c in [' ', '\n', '%']):
    raise SystemExit('安装路径不能包含空格、换行或百分号')
for source in (root/'deploy/systemd').glob('*'):
    text=source.read_text().replace('@ROOT@',str(root)).replace('@NODE@',node)
    (Path('/etc/systemd/system')/source.name).write_text(text)
PY
systemctl daemon-reload
systemctl enable --now douyin-bot-xvfb.service douyin-bot-panel.service
echo '安装完成。面板监听 127.0.0.1:8790。机器人和幕间尚未启动。'
echo '下一步：按 README 填写配置、导入 Cookie、发现好友、演练，再启动服务。'
