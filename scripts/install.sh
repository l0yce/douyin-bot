#!/usr/bin/env bash
# Linux server installer: apt, dnf and yum. No login data is embedded.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAN=false
MANAGER=auto
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan) PLAN=true; shift ;;
    --package-manager)
      [[ $# -ge 2 ]] || { echo '缺少包管理器名称'; exit 2; }
      MANAGER="$2"; shift 2 ;;
    -h|--help)
      echo '用法：sudo bash scripts/install.sh [--package-manager apt|dnf|yum]'
      echo '预览：bash scripts/install.sh --plan（只显示计划，不修改系统）'
      exit 0 ;;
    *) echo "未知参数：$1"; exit 2 ;;
  esac
done
case "$MANAGER" in auto|apt|dnf|yum) ;; *) echo '包管理器须为 apt、dnf 或 yum'; exit 2 ;; esac
if [[ "$MANAGER" == auto ]]; then
  if command -v apt-get >/dev/null; then MANAGER=apt
  elif command -v dnf >/dev/null; then MANAGER=dnf
  elif command -v yum >/dev/null; then MANAGER=yum
  else echo '未找到 apt、dnf 或 yum，请按 docs/LINUX.md 手动安装依赖。'; exit 1
  fi
fi
run() {
  if $PLAN; then printf 'PLAN'; printf ' %q' "$@"; printf '\n'
  else "$@"
  fi
}
pick_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys;sys.exit(sys.version_info<(3,11))' >/dev/null 2>&1; then
      command -v "$candidate"; return 0
    fi
  done
  return 1
}
echo "Linux 包管理器：$MANAGER"
if $PLAN; then
  echo 'PLAN: 需要 systemd、Node.js >=20 和 npm；安装前检查，未满足时停止。'
else
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash scripts/install.sh'; exit 1; }
[[ "$(uname -s)" == Linux ]] || { echo '此安装器用于 Linux'; exit 1; }
command -v systemctl >/dev/null && [[ -d /run/systemd/system ]] || { echo '需要正在运行的 systemd；容器环境请手动启动各组件。'; exit 1; }
command -v node >/dev/null || { echo '请先安装 Node.js 20 或以上版本及 npm：https://nodejs.org/'; exit 1; }
node -e 'if(Number(process.versions.node.split(".")[0])<20)process.exit(1)' || { echo '需要 Node.js >= 20'; exit 1; }
command -v npm >/dev/null
[[ "$ROOT" != *" "* && "$ROOT" != *$'\n'* && "$ROOT" != *"%"* ]] || { echo '安装路径不能包含空格、换行或百分号'; exit 1; }
fi
if [[ "$MANAGER" == apt ]]; then
  run apt-get update
  run apt-get install -y python3 python3-venv python3-dev build-essential xvfb xauth git curl ca-certificates
else
  run "$MANAGER" install -y gcc gcc-c++ make git curl ca-certificates xorg-x11-server-Xvfb xorg-x11-xauth gtk3 dbus-glib alsa-lib libXt libX11 libXcomposite libXdamage libXfixes libXrandr libXrender libXtst libXcursor libXi libxcb libxkbcommon nss pango cairo fontconfig freetype mesa-libgbm libdrm
  if ! pick_python >/dev/null; then
    run "$MANAGER" install -y python3.11 python3.11-pip
  fi
fi
if $PLAN; then
  echo 'PLAN: 选择 Python >=3.11，不替换系统 Python；创建或复用项目虚拟环境。'
  if [[ "$MANAGER" == apt ]]; then
    echo 'PLAN: playwright install --with-deps firefox'
  else
    echo 'PLAN: playwright install firefox（浏览器系统依赖由上述包管理器安装）'
  fi
  echo 'PLAN: 检查 Firefox 离线启动；安装面板和幕间依赖；初始化配置；写入 systemd 服务。'
  echo 'PLAN: 仅启动虚拟显示和面板；不启动机器人发送消息。'
  exit 0
fi
PYTHON_BIN="$(pick_python)" || { echo '未找到 Python >=3.11。请从系统支持的软件源安装后重试，不要覆盖 /usr/bin/python3。'; exit 1; }
ensure_venv() {
  local directory="$1"
  if [[ -e "$directory" ]]; then
    [[ -x "$directory/bin/python" ]] && "$directory/bin/python" -c 'import sys;assert sys.version_info>=(3,11)' || {
      echo "已有虚拟环境不可用或版本太旧：$directory。请先自行迁移，不会自动删除。"; exit 1;
    }
  else
    "$PYTHON_BIN" -m venv "$directory" || { echo '创建虚拟环境失败，请检查对应版本的 venv/ensurepip 组件。'; exit 1; }
  fi
}
ensure_venv "$ROOT/douyin-friend-ai-bot/.venv"
"$ROOT/douyin-friend-ai-bot/.venv/bin/pip" install -r "$ROOT/douyin-friend-ai-bot/requirements.txt"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/douyin-friend-ai-bot/ms-playwright"
if [[ "$MANAGER" == apt ]]; then
  "$ROOT/douyin-friend-ai-bot/.venv/bin/python" -m playwright install --with-deps firefox
else
  "$ROOT/douyin-friend-ai-bot/.venv/bin/python" -m playwright install firefox
fi
"$ROOT/douyin-friend-ai-bot/.venv/bin/python" "$ROOT/scripts/check_browser.py"
ensure_venv "$ROOT/douyin-bot-panel/venv"
"$ROOT/douyin-bot-panel/venv/bin/pip" install -r "$ROOT/douyin-bot-panel/requirements.txt"
(cd "$ROOT/douyin-interlude" && PUPPETEER_SKIP_DOWNLOAD=true npm ci --omit=dev)
"$PYTHON_BIN" "$ROOT/scripts/init_config.py" --root "$ROOT"
NODE_BIN="$(command -v node)"
"$PYTHON_BIN" - "$ROOT" "$NODE_BIN" <<'PY'
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
echo '下一步：按 README 打开并登录面板，填写配置、导入 Cookies、添加白名单，再从面板启动机器人。'
