"""抖音机器人手机控制面板 API。

只监听 127.0.0.1:8790，由 nginx 反代到 /botpanel/ 并套 HTTPS。
鉴权：Bearer Token（安装时生成，存 douyin-bot-panel/panel_token）。
安全约束：任何接口都不会返回 AI API Key、代理凭据等敏感字段；
写白名单前自动备份 config.json。
"""
import hmac
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

BASE_DIR = Path(os.environ.get("PANEL_DIR", Path(__file__).resolve().parents[1]))
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
TOKEN_FILE = BASE_DIR / "panel_token"

BOT_DIR = Path(os.environ.get("BOT_DIR", BASE_DIR.parent / "douyin-friend-ai-bot"))
sys.path.insert(0, str(BOT_DIR / 'src'))
from video_comments import normalize_settings as video_comment_settings, SETTINGS_DEFAULTS, SETTINGS_BOUNDS
CONFIG_PATH = BOT_DIR / "config" / "config.json"
BACKUP_DIR = BOT_DIR / "config" / "backups"
AUDIT_PATH = BOT_DIR / "logs" / "reply_audit.jsonl"
RUNTIME_PATH = BOT_DIR / "logs" / "runtime.log"
DISCOVER_OUT = DATA_DIR / "discover_latest.txt"

BOT_UNIT = "douyin-friend-ai-bot"
XVFB_UNIT = "douyin-bot-xvfb"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

SEC_UID_RE = re.compile(r"^MS4wLjAB[0-9A-Za-z_-]+$")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
PROCESS_LOCK = threading.Lock()


# ---------------- 鉴权 ----------------

def _generate_token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def load_token() -> str:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        return token if token else _generate_token()
    except FileNotFoundError:
        return _generate_token()


TOKEN = load_token()


async def require_token(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    supplied = auth[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, TOKEN):
        raise HTTPException(status_code=401, detail="访问令牌无效")


# ---------------- 进程/系统工具 ----------------

def run_cmd(args: list, timeout: float = 25, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def systemctl(*args: str) -> tuple:
    try:
        p = run_cmd(["systemctl", *args], timeout=40)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "systemctl 超时"


def unit_properties(unit: str, props: list) -> dict:
    args = ["systemctl", "show", unit, "--no-pager"]
    for name in props:
        args.append(f"--property={name}")
    try:
        p = run_cmd(args)
    except Exception:
        return {}
    kv = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k] = v
    return kv


def unit_uptime_seconds(unit: str) -> Optional[float]:
    p = run_cmd(["systemctl", "show", unit, "--no-pager", "--value",
                 "--property=ActiveEnterTimestampMonotonic"])
    try:
        mono_us = int((p.stdout or "0").strip() or "0")
    except ValueError:
        return None
    if mono_us <= 0:
        return None
    return max(0.0, (time.monotonic() * 1_000_000 - mono_us) / 1_000_000)


def bot_uptime_seconds() -> Optional[float]:
    return unit_uptime_seconds(BOT_UNIT)


# ---------------- 配置读写 ----------------

def read_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def backup_config() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    target = BACKUP_DIR / f"config.json.bak.{stamp}"
    shutil.copy2(CONFIG_PATH, target)
    backups = sorted(BACKUP_DIR.glob("config.json.bak.*"))
    for old in backups[:-20]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


# ---------------- 日志工具 ----------------

def tail_text(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def audit_compact(raw: dict) -> dict:
    keys = ("ts", "event", "action", "friend", "reason", "verified", "verified_douyin_id",
            "verified_sec_uid", "incoming_text", "reply_text", "ai_model", "error", "kind",
            "delivery_id", "elapsed_ms", "sent_segments", "segments", "partial_reply")
    out = {k: raw[k] for k in keys if raw.get(k) is not None}
    for target, source in (("reply_text", "reply"), ("incoming_text", "incoming")):
        if not out.get(target) and isinstance(raw.get(source), str):
            out[target] = raw[source]
    if "verified" not in out and isinstance(raw.get("verified_sec_uid"), bool):
        out["verified"] = raw["verified_sec_uid"]
    event = str(raw.get("event") or "")
    states = {
        "replied": ("sent", "回复已发送"),
        "interlude_replied": ("sent", "幕间回复已发送"),
        "interlude_submitted": ("pending", "幕间已接收，等待生成和发送"),
        "interlude_manual_takeover": ("takeover", "手动接管，旧回复已取消"),
        "interlude_delivery_unknown": ("unknown", "发送结果待核实"),
        "interlude_error": ("error", "幕间处理失败"),
        "ai_error": ("error", "回复生成失败"),
        "image_error": ("error", "图片读取失败"),
        "send_error": ("error", "回复发送失败"),
        "empty_reply": ("error", "模型未生成可发送的回复"),
        "ai_tell_blocked": ("error", "生成内容被过滤，未发送"),
        "dry_reply": ("preview", "预览回复，未发送"),
        "interlude_dry_run": ("preview", "幕间预览，未发送"),
        "interlude_receipt_replayed": ("other", "发送回执已补报"),
    }
    state, label = states.get(event, ("other", "运行记录"))
    if event not in states and raw.get("action") in ("skip", "nothing"):
        state, label = "skipped", "本轮未回复"
    if event == "send_error" and raw.get("sent_segments"):
        label = "部分消息已发送，后续发送失败"
    out.update(feed_state=state, event_label=label)
    return out


def parse_audit_lines(text: str) -> list:
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict):
            entries.append(audit_compact(raw))
    return entries


def last_audit_entry() -> Optional[dict]:
    text = tail_text(AUDIT_PATH, 64 * 1024)
    entries = parse_audit_lines(text)
    return entries[-1] if entries else None


# ---------------- 发现脚本任务 ----------------

_discover_lock = threading.Lock()
_discover_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
}


def _discover_worker():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with DISCOVER_OUT.open("w", encoding="utf-8") as out:
            out.write("只读发现模式：不会发送消息。\n", )
            proc = subprocess.Popen(
                ["bash", str(BOT_DIR / "scripts" / "discover_friends.sh")],
                cwd=str(BOT_DIR), stdout=out, stderr=subprocess.STDOUT)
            code = proc.wait(timeout=900)
        with DISCOVER_OUT.open("a", encoding="utf-8") as out:
            out.write(f"\n[面板] 发现脚本结束，退出码 {code}\n")
        _discover_state["returncode"] = code
    except Exception as exc:  # noqa: BLE001
        try:
            with DISCOVER_OUT.open("a", encoding="utf-8") as out:
                out.write(f"\n[面板] 执行异常：{exc}\n")
        except OSError:
            pass
        _discover_state["returncode"] = -1
    finally:
        _discover_state["running"] = False
        _discover_state["finished_at"] = time.time()


# ---------------- 接口 ----------------

def conversation_settings(cfg):
    raw = cfg.get("reply", {}).get("conversation_context", {})
    raw = raw if isinstance(raw, dict) else {}
    count = raw.get("history_messages", 40)
    count = count if type(count) is int else 40
    return {"enabled": raw.get("enabled", True) is not False,
            "history_messages": max(5, min(count, 100)),
            "cancel_on_manual": raw.get("cancel_on_manual", True) is not False}


@app.get("/api/conversation-context")
async def get_conversation_context(_: None = Depends(require_token)):
    raw = CONFIG_PATH.read_bytes()
    cfg = json.loads(raw)
    return {"settings": conversation_settings(cfg),
            "interlude_enabled": cfg.get("interlude", {}).get("enabled") is True,
            "revision": hashlib.sha256(raw).hexdigest()}


_context_lock = threading.Lock()


@app.get('/api/video-comments')
async def get_video_comments(_: None = Depends(require_token)):
    raw = CONFIG_PATH.read_bytes()
    cfg = json.loads(raw)
    return {'settings': video_comment_settings(cfg.get('reply', {}).get('video_comments')),
            'interlude_enabled': cfg.get('interlude', {}).get('enabled') is True,
            'revision': hashlib.sha256(raw).hexdigest()}


@app.put('/api/video-comments')
async def put_video_comments(body: dict, _: None = Depends(require_token)):
    settings = body.get('settings')
    if not isinstance(settings, dict) or set(settings) != set(SETTINGS_DEFAULTS):
        raise HTTPException(status_code=400, detail='请提交完整的视频评论参考设置')
    if type(settings['enabled']) is not bool:
        raise HTTPException(status_code=400, detail='开关值必须是 true 或 false')
    for key, (low, high) in SETTINGS_BOUNDS.items():
        if type(settings[key]) is not int or not low <= settings[key] <= high:
            raise HTTPException(status_code=400, detail='设置超出允许范围，请检查条数、字数、缓存时间和等待时间')
    with _context_lock:
        raw = CONFIG_PATH.read_bytes()
        if body.get('revision') != hashlib.sha256(raw).hexdigest():
            raise HTTPException(status_code=409, detail='配置已被其他操作修改，请重新读取后再保存')
        cfg = json.loads(raw)
        cfg.setdefault('reply', {})['video_comments'] = settings
        backup = backup_config()
        updated = (json.dumps(cfg, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=CONFIG_PATH.parent, prefix='.video-comments-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(updated)
            os.chmod(temporary, CONFIG_PATH.stat().st_mode & 0o777)
            temporary.replace(CONFIG_PATH)
        finally:
            if temporary and temporary.exists(): temporary.unlink()
    return {'ok': True, 'settings': settings, 'revision': hashlib.sha256(updated).hexdigest(),
            'backup': backup.name, 'message': '已保存，机器人将在下一轮检查消息时应用'}


@app.put("/api/conversation-context")
async def put_conversation_context(body: dict, _: None = Depends(require_token)):
    settings = body.get("settings")
    if not isinstance(settings, dict) or set(settings) != {"enabled", "history_messages", "cancel_on_manual"}:
        raise HTTPException(status_code=400, detail="请提交完整的聊天协作设置")
    if type(settings["enabled"]) is not bool or type(settings["cancel_on_manual"]) is not bool:
        raise HTTPException(status_code=400, detail="开关值必须是 true 或 false")
    if type(settings["history_messages"]) is not int or not 5 <= settings["history_messages"] <= 100:
        raise HTTPException(status_code=400, detail="上下文条数须为 5–100 的整数")
    with _context_lock:
        raw = CONFIG_PATH.read_bytes()
        if body.get("revision") != hashlib.sha256(raw).hexdigest():
            raise HTTPException(status_code=409, detail="配置已被其他操作修改，请重新读取后再保存")
        cfg = json.loads(raw)
        cfg.setdefault("reply", {})["conversation_context"] = settings
        backup_path = backup_config()
        updated = (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=CONFIG_PATH.parent, prefix=".context-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(updated)
            os.chmod(temporary, CONFIG_PATH.stat().st_mode & 0o777)
            temporary.replace(CONFIG_PATH)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return {"ok": True, "settings": settings, "revision": hashlib.sha256(updated).hexdigest(),
            "backup": backup_path.name, "message": "已保存，机器人将在下一轮检查消息时应用"}

@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/status")
async def status(_: None = Depends(require_token)):
    bot = unit_properties(BOT_UNIT, ["ActiveState", "SubState", "UnitFileState"])
    xvfb = unit_properties(XVFB_UNIT, ["ActiveState"])
    try:
        friends = read_config().get("whitelist", {}).get("friends", [])
    except Exception:
        friends = []
    return {
        "bot": {
            "active_state": bot.get("ActiveState", "unknown"),
            "sub_state": bot.get("SubState", ""),
            "unit_file_state": bot.get("UnitFileState", ""),
            "uptime_seconds": bot_uptime_seconds(),
        },
        "xvfb": {"active_state": xvfb.get("ActiveState", "unknown")},
        "whitelist": {
            "total": len(friends),
            "enabled": sum(1 for f in friends if isinstance(f, dict) and f.get("enabled")),
        },
        "last_audit": last_audit_entry(),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/api/bot/{action}")
def bot_action(action: str, _: None = Depends(require_token)):
    with PROCESS_LOCK:
        if _discover_state['running']:
            raise HTTPException(status_code=409, detail='发现脚本正在运行，请结束后再控制机器人。')
        return _bot_action(action)


def _bot_action(action: str):
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(status_code=404, detail="未知操作")
    if action == "start":
        systemctl("start", XVFB_UNIT)
        code, out = systemctl("start", BOT_UNIT)
    elif action == "stop":
        code, out = systemctl("stop", BOT_UNIT)
        systemctl("stop", XVFB_UNIT)
    else:
        code, out = systemctl("restart", BOT_UNIT)
    info = unit_properties(BOT_UNIT, ["ActiveState", "SubState"])
    return {
        "ok": code == 0,
        "output": out[-500:],
        "bot": {
            "active_state": info.get("ActiveState", "unknown"),
            "sub_state": info.get("SubState", ""),
        },
    }


@app.get("/api/bot/journal")
async def bot_journal(lines: int = 40, _: None = Depends(require_token)):
    lines = max(10, min(int(lines), 200))
    p = run_cmd(["journalctl", "-u", BOT_UNIT, "-n", str(lines), "--no-pager", "-o", "short-iso"])
    return PlainTextResponse(p.stdout or p.stderr or "")


@app.get("/api/whitelist")
async def get_whitelist(_: None = Depends(require_token)):
    cfg = read_config()
    friends = cfg.get("whitelist", {}).get("friends", [])
    return {
        "friends": friends,
        "owner_douyin_id": str(cfg.get("owner", {}).get("douyin_id") or ""),
    }


@app.put("/api/whitelist")
async def put_whitelist(body: dict, _: None = Depends(require_token)):
    friends = body.get("friends") if isinstance(body, dict) else None
    if not isinstance(friends, list):
        raise HTTPException(status_code=400, detail="body 需为 {\"friends\": [...]}")
    cfg = read_config()
    owner_id = str(cfg.get("owner", {}).get("douyin_id") or "")
    old_by_id = {
        str(f.get("douyin_id")): f
        for f in cfg.get("whitelist", {}).get("friends", [])
        if isinstance(f, dict)
    }
    seen = set()
    cleaned = []
    for item in friends:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="好友项格式错误")
        name = str(item.get("name") or "").strip()
        douyin_id = str(item.get("douyin_id") or "").strip()
        sec_uid = str(item.get("sec_uid") or "").strip()
        if not name or not douyin_id:
            raise HTTPException(status_code=400, detail=f"缺少 name 或 douyin_id：{name or douyin_id or '空项'}")
        if douyin_id in seen:
            raise HTTPException(status_code=400, detail=f"抖音号重复：{douyin_id}")
        if owner_id and douyin_id == owner_id:
            raise HTTPException(status_code=400, detail="不能把自己的抖音号加入白名单")
        if not sec_uid:
            raise HTTPException(status_code=400, detail=f"「{name}」缺少 sec_uid；请用发现脚本获取，不要手填")
        if not SEC_UID_RE.match(sec_uid):
            raise HTTPException(status_code=400, detail=f"「{name}」的 sec_uid 格式不合法（应以 MS4wLjAB 开头）")
        seen.add(douyin_id)
        merged = dict(old_by_id.get(douyin_id, {}))
        merged.update({
            "name": name,
            "douyin_id": douyin_id,
            "sec_uid": sec_uid,
            "enabled": bool(item.get("enabled", False)),
        })
        cleaned.append(merged)
    backup_path = backup_config()
    cfg.setdefault("whitelist", {})["friends"] = cleaned
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "count": len(cleaned), "backup": backup_path.name}


@app.post("/api/discover/run")
def discover_run(_: None = Depends(require_token)):
    with PROCESS_LOCK, _discover_lock:
        if _discover_state["running"]:
            raise HTTPException(status_code=409, detail="发现脚本正在运行中")
        if not (BOT_DIR / "scripts" / "discover_friends.sh").exists():
            raise HTTPException(status_code=500, detail="找不到发现脚本")
        _discover_state.update(running=True, started_at=time.time(),
                               finished_at=None, returncode=None)
        threading.Thread(target=_discover_worker, daemon=True).start()
    return {"ok": True}


# ---------------- Cookie 导入（不回显凭据） ----------------
import cookie_import


def _cookie_target(value):
    target = Path(value)
    if not target.is_absolute():
        target = BOT_DIR / target
    target = target.resolve()
    if not target.is_relative_to(BOT_DIR.resolve() / 'state'):
        raise HTTPException(status_code=400, detail='登录文件须位于机器人 state 目录，请检查账号配置路径。')
    return target


def _private_write(target, data):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.cookie-import-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.chmod(name, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _save_imported_cookies(cookies, warnings):
    with PROCESS_LOCK, _discover_lock:
        if _discover_state['running']:
            raise HTTPException(status_code=409, detail='发现脚本正在运行，请结束后再导入 Cookies。')
        account = read_config().get('account', {})
        cf = _cookie_target(account.get('cookies_file') or 'state/cookies.json')
        ss = _cookie_target(account.get('storage_state') or 'state/douyin_storage.json')
        marker = cf.with_name(cf.name + '.import-pending')
        if len({cf, ss, marker}) != 3:
            raise HTTPException(status_code=400, detail='Cookie 文件与登录态文件路径不能相同。')
        info = unit_properties(BOT_UNIT, ['ActiveState'])
        if info.get('ActiveState') not in {'active', 'activating', 'reloading', 'inactive', 'failed', 'deactivating'}:
            raise HTTPException(status_code=503, detail='无法确认机器人状态，未导入。请检查服务后重试。')
        if info['ActiveState'] not in {'inactive', 'failed'}:
            code, _ = systemctl('stop', BOT_UNIT)
            if code or unit_properties(BOT_UNIT, ['ActiveState']).get('ActiveState') != 'inactive':
                raise HTTPException(status_code=503, detail='机器人未能停止，未修改 Cookies。请手动停止后重试。')
        originals = {p: p.read_bytes() if p.exists() else None for p in (cf, ss, marker)}
        payloads = {cf: json.dumps(cookies, ensure_ascii=False).encode('utf-8'),
                    ss: json.dumps({'cookies': cookies, 'origins': []}, ensure_ascii=False).encode('utf-8'),
                    marker: b'panel-import-v1\n'}
        try:
            for target, data in payloads.items():
                _private_write(target, data)
        except OSError:
            try:
                for target, data in originals.items():
                    if data is None:
                        target.unlink(missing_ok=True)
                    else:
                        _private_write(target, data)
            except OSError:
                raise HTTPException(status_code=500, detail='导入写入失败且恢复未完成。机器人保持停止，请检查登录文件后再启动。') from None
            raise HTTPException(status_code=500, detail='导入写入失败，已恢复原登录文件。机器人保持停止，请检查磁盘空间和权限。') from None
        return {'ok': True, 'count': len(cookies), 'warnings': warnings,
                'message': f'已转换并导入 {len(cookies)} 条抖音 Cookies。机器人保持停止，请点击“启动机器人”，再查看连接日志确认登录。'}


@app.post('/api/cookies/import')
async def import_cookies(request: Request, _: None = Depends(require_token)):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > cookie_import.MAX_BYTES * 2:
            raise HTTPException(status_code=413, detail='请求过大，请只粘贴抖音网站的 Cookies。')
    try:
        body = json.loads(raw)
        cookies, warnings = cookie_import.convert(body.get('content') if isinstance(body, dict) else None)
    except (ValueError, UnicodeError, RecursionError) as error:
        message = str(error) if type(error) is ValueError else '请求格式不正确，请重新粘贴 Cookies。'
        raise HTTPException(status_code=400, detail=message) from None
    return await asyncio.to_thread(_save_imported_cookies, cookies, warnings)


@app.get("/api/discover")
async def discover_status(_: None = Depends(require_token)):
    output = tail_text(DISCOVER_OUT, 8 * 1024) if DISCOVER_OUT.exists() else ""
    return {**_discover_state, "output": output[-4000:]}


@app.get("/api/logs/runtime")
async def logs_runtime(lines: int = 150, _: None = Depends(require_token)):
    lines = max(20, min(int(lines), 500))
    text = tail_text(RUNTIME_PATH, 256 * 1024)
    return PlainTextResponse("\n".join(text.splitlines()[-lines:]))


@app.get("/api/logs/audit")
async def logs_audit(limit: int = 50, _: None = Depends(require_token)):
    limit = max(5, min(int(limit), 200))
    text = tail_text(AUDIT_PATH, 512 * 1024)
    entries = parse_audit_lines(text)[-limit:]
    entries.reverse()
    return {"entries": entries}


# ---------------- 静态文件 ----------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")


@app.get("/icon.svg")
async def icon():
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


# Added to the existing authenticated FastAPI panel.
import asyncio
import hashlib
import httpx

INTERLUDE_DIR = Path(os.environ.get('INTERLUDE_DIR', BASE_DIR.parent / 'douyin-interlude'))
INTERLUDE_CONFIG = INTERLUDE_DIR / 'bridge-config.json'
STORY_SAVE_LOCK = asyncio.Lock()
STORY_FIELDS = {'characterName':100, 'characterProfile':20000, 'world':8000,
    'relationship':4000, 'supportingCast':8000, 'location':200, 'style':4000, 'startingSituation':8000}

def story_revision(raw):
    return hashlib.sha256(raw.encode()).hexdigest()

def story_read():
    raw = INTERLUDE_CONFIG.read_text(encoding='utf-8')
    settings = json.loads(raw)
    bot = read_config()
    persona = bot.get('prompts', {}).get('system_prompt', '')
    persona_path = bot.get('prompts', {}).get('system_prompt_file')
    if persona_path:
        persona = (BOT_DIR / persona_path).read_text(encoding='utf-8')
    defaults = {'characterName':settings.get('characterName') or bot.get('owner',{}).get('nickname') or '抖音朋友',
        'characterProfile':persona, 'world':'现代现实日常生活，通过抖音文字私信联系。未记录的真实往事、线下见面和对方资料不能凭空当作事实。',
        'relationship':'按实际聊天逐渐建立关系，不凭空假定亲密关系。',
        'supportingCast':'', 'location':'', 'startingSituation':'',
        'style':'中文口语，短句，自然接话。持续叙事只写入后台剧本，发送给用户的内容只包含聊天台词。'}
    defaults.update({k:v for k,v in settings.get('story',{}).items() if k in STORY_FIELDS})
    return raw, settings, {'story':defaults, 'revision':story_revision(raw),
        'autoAdvanceEnabled':settings.get('autoAdvanceEnabled',True),
        'allowProactiveMessages':settings.get('allowProactiveMessages',False),
        'autoAdvanceIntervalMinutes':settings.get('autoAdvanceIntervalMinutes',120)}

async def story_health(settings):
    async with httpx.AsyncClient(trust_env=False,timeout=5) as client:
        response = await client.get('http://127.0.0.1:%d/health' % settings.get('port',18765),
            headers={'Authorization':'Bearer '+settings['token']})
        response.raise_for_status()
        return response.json()

@app.get('/api/story')
async def get_story(_: None = Depends(require_token)):
    try:
        _, settings, public = story_read()
        public['service'] = 'active' if (await story_health(settings)).get('ready') else 'starting'
        return public
    except (OSError, ValueError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail='剧情服务暂不可用，请稍后重试。')

@app.put('/api/story')
async def put_story(body: dict, _: None = Depends(require_token)):
    async with STORY_SAVE_LOCK:
        raw, settings, _ = story_read()
        if body.get('revision') != story_revision(raw):
            raise HTTPException(status_code=409, detail='设定已经在其他页面更新。请重新打开剧情页，再保存修改。')
        values = body.get('story')
        if not isinstance(values,dict) or set(values) != set(STORY_FIELDS):
            raise HTTPException(status_code=400, detail='剧情字段不完整，请刷新页面后重试。')
        for key, limit in STORY_FIELDS.items():
            if not isinstance(values[key],str) or len(values[key]) > limit:
                raise HTTPException(status_code=400, detail='设定内容过长或格式不正确，请缩短后重试。')
        if not values['characterName'].strip() or not values['characterProfile'].strip():
            raise HTTPException(status_code=400, detail='请填写角色名字和角色人设。')
        for key in ('autoAdvanceEnabled','allowProactiveMessages'):
            if type(body.get(key)) is not bool:
                raise HTTPException(status_code=400, detail='运行开关格式不正确。')
        interval = body.get('autoAdvanceIntervalMinutes')
        if type(interval) is not int or not 5 <= interval <= 1440:
            raise HTTPException(status_code=400, detail='生活推进间隔需为 5 至 1440 分钟。')
        try:
            health = await story_health(settings)
        except httpx.HTTPError:
            raise HTTPException(status_code=503,detail='剧情服务暂不可用，未修改设定。')
        if not health.get('ready') or health.get('pending') or health.get('unknown'):
            raise HTTPException(status_code=409,detail='现在有消息正在处理或待核实，请稍后再保存。')
        queue = json.loads((INTERLUDE_DIR/'data/bridge-state.json').read_text())
        if any(item.get('status') in ('submitting','accepted') and item.get('createdAt',0) > time.time()*1000-180000 for item in queue.get('inbox',{}).values()):
            raise HTTPException(status_code=409,detail='角色刚收到消息，请等三分钟再保存，以免打断回复。')
        backup_dir = INTERLUDE_DIR/'backups'
        backup_dir.mkdir(mode=0o700,exist_ok=True)
        backup_file = backup_dir/('story-'+str(time.time_ns())+'.json')
        backup_file.write_text(raw,encoding='utf-8'); backup_file.chmod(0o600)
        settings['story'] = {key:value.strip() for key,value in values.items()}
        for key in ('autoAdvanceEnabled','allowProactiveMessages','autoAdvanceIntervalMinutes'):
            settings[key] = body[key]
        def write(value):
            temp = INTERLUDE_CONFIG.with_suffix('.story-tmp')
            temp.write_text(value,encoding='utf-8'); temp.chmod(0o600); temp.replace(INTERLUDE_CONFIG)
        write(json.dumps(settings,ensure_ascii=False,indent=2))
        try:
            code, _ = await asyncio.to_thread(systemctl,'restart','douyin-interlude')
            if code: raise RuntimeError('Restart failed')
            for _ in range(20):
                await asyncio.sleep(.5)
                try:
                    if (await story_health(settings)).get('ready'): break
                except httpx.HTTPError: pass
            else: raise RuntimeError('Service did not become ready')
        except Exception:
            write(raw)
            await asyncio.to_thread(systemctl,'restart','douyin-interlude')
            raise HTTPException(status_code=503,detail='新设定未能启用，已恢复原配置。请稍后重试。')
        _, _, public = story_read()
        return {**public, 'service':'active', 'saved':True}

# Full HDSI management stays on the panel's existing authentication boundary.
HDSI_ACTIONS={'start','pause','resume','advance','compact','overlay-compact','overlay-clear','schedule-refresh',
    'fact-add','fact-edit','forget','note','cancel-intent','reject-patch','rebase','purge-range','purge-platform','reset','clear-database'}

async def hdsi_request(path,body):
    settings=json.loads(INTERLUDE_CONFIG.read_text())
    async with httpx.AsyncClient(trust_env=False,timeout=250) as client:
        try:
            response=await client.post('http://127.0.0.1:%d%s'%(settings.get('port',18765),path),json=body,
                headers={'Authorization':'Bearer '+settings['token']})
        except httpx.HTTPError:
            raise HTTPException(status_code=503,detail='剧情服务暂不可用。若操作已经开始，请先查看结果，不要重复提交。')
        if not response.is_success:
            detail=response.json().get('error','剧情操作失败。')
            raise HTTPException(status_code=response.status_code,detail=detail)
        return response.json()

@app.get('/api/hdsi/overview')
async def hdsi_overview(_:None=Depends(require_token)):
    result=await hdsi_request('/admin',{'action':'overview'})
    opts=json.loads(INTERLUDE_CONFIG.read_text())
    result['switches']={key:opts.get(key) for key in ['autoAdvanceEnabled','allowProactiveMessages','autoAdvanceIntervalMinutes']}
    result['queue']=await story_health(opts)
    return result

@app.post('/api/hdsi/timeline')
async def hdsi_timeline(body:dict,_:None=Depends(require_token)):
    return await hdsi_request('/admin',{'action':'timeline','args':body})

@app.get('/api/hdsi/config')
async def hdsi_config(_:None=Depends(require_token)):
    async with STORY_SAVE_LOCK:
        data=await hdsi_request('/admin',{'action':'config'})
        data['revision']=story_revision(INTERLUDE_CONFIG.read_text())
        return data

@app.put('/api/hdsi/config')
async def hdsi_save_config(body:dict,_:None=Depends(require_token)):
    async with STORY_SAVE_LOCK:
        raw=INTERLUDE_CONFIG.read_text()
        if body.get('revision')!=story_revision(raw):
            raise HTTPException(status_code=409,detail='设置已更新，请重新读取后保存。')
        saved=await hdsi_request('/full-config',body)
        try:
            code,_=await asyncio.to_thread(systemctl,'restart','douyin-interlude')
            if code:raise RuntimeError('Restart failed')
            for _ in range(30):
                await asyncio.sleep(.5)
                try:
                    if (await story_health(json.loads(INTERLUDE_CONFIG.read_text()))).get('ready'):break
                except httpx.HTTPError:pass
            else:raise RuntimeError('Readiness timeout')
        except Exception:
            temp=INTERLUDE_CONFIG.with_suffix('.restore-tmp');temp.write_text(raw);temp.chmod(0o600);temp.replace(INTERLUDE_CONFIG)
            await asyncio.to_thread(systemctl,'restart','douyin-interlude')
            raise HTTPException(status_code=503,detail='新配置未能启动，已恢复原配置。')
        return {'saved':True,'revision':story_revision(INTERLUDE_CONFIG.read_text())}

@app.post('/api/hdsi/action')
async def hdsi_action(body:dict,_:None=Depends(require_token)):
    if body.get('action') not in HDSI_ACTIONS:
        raise HTTPException(status_code=400,detail='操作无效。')
    return await hdsi_request('/admin',body)

@app.get('/api/hdsi/export')
async def hdsi_export(_:None=Depends(require_token)):
    return await hdsi_request('/admin',{'action':'export'})

@app.get('/api/hdsi/logs')
async def hdsi_logs(_:None=Depends(require_token)):
    result=await asyncio.to_thread(run_cmd,['journalctl','-u','douyin-interlude','-n','120','--no-pager'],25)
    return {'text':result.stdout[-60000:]}


# ---------------- 独立故障邮件提醒 ----------------
import fault_alerts
ALERT_SAVE_LOCK = threading.Lock()
ALERT_TEST_LOCK = threading.Lock()
ALERT_TEST_LAST = 0.0


@app.get('/api/fault-alerts')
def get_fault_alerts(_: None = Depends(require_token)):
    return fault_alerts.public()


@app.put('/api/fault-alerts')
def put_fault_alerts(body: dict, _: None = Depends(require_token)):
    with ALERT_SAVE_LOCK:
        old = fault_alerts.settings()
        if body.get('revision') != fault_alerts.revision(old):
            raise HTTPException(status_code=409, detail='提醒设置已更新，请重新读取再保存')
        try:
            cfg = fault_alerts.validate(body.get('settings'), old)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        fault_alerts.atomic(fault_alerts.CONFIG, cfg)
    return fault_alerts.public()


@app.post('/api/fault-alerts/test')
def test_fault_alerts(_: None = Depends(require_token)):
    global ALERT_TEST_LAST
    with ALERT_TEST_LOCK:
        if time.time() - ALERT_TEST_LAST < 60:
            raise HTTPException(status_code=429, detail='请等待一分钟再测试')
        cfg = fault_alerts.settings()
        if not all(cfg[k] for k in ['sender', 'recipient', 'auth_code']):
            raise HTTPException(status_code=400, detail='请先保存邮箱和 SMTP 授权码')
        ALERT_TEST_LAST = time.time()
        try:
            fault_alerts.send(cfg, '测试邮件', '这是一封由你在管理面板点击发送的测试邮件。收到后即确认本次收信成功。')
        except Exception as exc:
            raise HTTPException(status_code=502, detail=fault_alerts.safe_error(exc))
    return {'ok': True, 'message': '邮件服务器已接受测试邮件，请检查收件箱或垃圾邮件箱'}
