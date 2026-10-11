"""Independent watchdog. No model calls, message content, or proxy SMTP transport."""
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import smtplib
import ssl
import subprocess
import tempfile
import time
from email.message import EmailMessage

import httpx

ROOT = pathlib.Path(os.environ.get('PANEL_DIR', pathlib.Path(__file__).resolve().parents[1]))
CONFIG = ROOT / 'data/fault-alerts.json'
STATE = ROOT / 'data/fault-alerts-state.json'
BOT = pathlib.Path(os.environ.get('BOT_DIR', ROOT.parent / 'douyin-friend-ai-bot'))
DEFAULT = dict(enabled=False, sender='', recipient='', auth_code='', failure_minutes=3, repeat_minutes=120, recovery=True)
LABELS = {'service': '机器人服务异常或频繁重启', 'proxy': '代理出口无法连接抖音', 'interlude': '幕间服务未就绪',
          'runner': '机器人持续处理失败或长时间没有完成检查', 'login': '抖音登录可能失效',
          'delivery': '消息生成或发送持续失败', 'monitor': '故障检测本身异常'}


def read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default.copy()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        name = handle.name
        os.chmod(name, 0o600)
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def settings():
    return {**DEFAULT, **read(CONFIG, {})}


def revision(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def public():
    cfg = settings()
    return {'settings': {k: v for k, v in cfg.items() if k != 'auth_code'},
            'has_auth_code': bool(cfg['auth_code']), 'revision': revision(cfg),
            'status': read(STATE, {})}


def validate(body, old):
    required = set(DEFAULT) - {'auth_code'}
    if not isinstance(body, dict) or not required <= body.keys() or set(body) - set(DEFAULT):
        raise ValueError('请提交完整的提醒设置')
    cfg = {**old, **body}
    for key in ['enabled', 'recovery']:
        if type(cfg[key]) is not bool:
            raise ValueError('提醒开关格式不正确')
    for key, lo, hi in [('failure_minutes', 2, 30), ('repeat_minutes', 30, 1440)]:
        if type(cfg[key]) is not int or not lo <= cfg[key] <= hi:
            raise ValueError('持续故障须为 2–30 分钟；重复提醒间隔须为 30–1440 分钟')
    for key in ['sender', 'recipient', 'auth_code']:
        if not isinstance(cfg[key], str):
            raise ValueError('邮箱或授权码格式不正确')
        cfg[key] = cfg[key].strip()
    if not cfg['auth_code']:
        cfg['auth_code'] = old.get('auth_code', '')
    if cfg['sender'] != old.get('sender') and not body.get('auth_code', '').strip():
        cfg['auth_code'] = ''
    if cfg['sender'] and not re.fullmatch(r'[A-Za-z0-9._+-]+@(?:qq\.com|foxmail\.com)', cfg['sender'], re.I):
        raise ValueError('发件邮箱须为 QQ 邮箱或 foxmail 邮箱')
    if cfg['recipient'] and not re.fullmatch(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', cfg['recipient']):
        raise ValueError('请填写有效的收件邮箱')
    if cfg['auth_code'] and not re.fullmatch(r'[A-Za-z0-9]{16}', cfg['auth_code']):
        raise ValueError('请填写 16 位 SMTP 授权码，不是 QQ 登录密码')
    if cfg['enabled'] and not all(cfg[k] for k in ['sender', 'recipient', 'auth_code']):
        raise ValueError('开启前请填写发件邮箱、收件邮箱和 SMTP 授权码')
    return cfg


def send(cfg, title, text):
    if not all(cfg[k] for k in ['sender', 'recipient', 'auth_code']):
        raise ValueError('请先保存邮箱及 SMTP 授权码')
    message = EmailMessage()
    message['From'] = cfg['sender']
    message['To'] = cfg['recipient']
    message['Subject'] = '[抖音机器人] ' + title
    message.set_content(text + '\n\n此邮件不包含聊天内容或密钥。请访问你自行部署的管理面板查看状态。')
    # smtplib connects directly; HTTP_PROXY/HTTPS_PROXY are never consulted.
    smtp = smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=15, context=ssl.create_default_context())
    try:
        smtp.login(cfg['sender'], cfg['auth_code'])
        rejected = smtp.send_message(message)
        if rejected:
            raise smtplib.SMTPRecipientsRefused(rejected)
    finally:
        # SMTP DATA acknowledgement is delivery acceptance. QUIT errors must not trigger duplicates.
        smtp.close()


def safe_error(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'SMTP 认证失败，请检查授权码和 SMTP 服务开关'
    if isinstance(exc, smtplib.SMTPException):
        return '邮件服务器拒绝发送（' + type(exc).__name__ + '）'
    return '连接或发送失败（' + type(exc).__name__ + '）'


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError('System probe failed')
    return result.stdout


def journal_health(entries, now, started):
    """Scope caller to the current service invocation; never treat silence as recovery."""
    rounds = []
    login = 0
    logged_in = 0
    errors = []
    pause_until = 0
    for row in entries:
        stamp = int(row.get('__REALTIME_TIMESTAMP', 0)) / 1e6
        msg = row.get('MESSAGE', '')
        match = re.search(r'第 \d+ 轮完成：.*出错 (\d+)', msg)
        if match:
            rounds.append((stamp, int(match[1])))
        if any(word in msg for word in ['登录态复查失败', '登录已失效', '登录态失效', '登录超时', '请先登录', '需要重新登录', '页面出现安全验证']):
            login = max(login, stamp)
        if '已登录抖音网页版' in msg or '已恢复：成功打开抖音聊天页' in msg:
            logged_in = max(logged_in, stamp)
        if any(word in msg for word in ['打开抖音聊天页失败', '轮处理出错', '轮出现页面级错误', '轮检测到页面崩溃', '读取会话列表失败']):
            errors.append(stamp)
        pause = re.search(r'已连续 \d+ 轮出错，暂停 (\d+) 分钟', msg)
        if pause:
            pause_until = max(pause_until, stamp + int(pause[1]) * 60)
    latest_ok = max([stamp for stamp, count in rounds if count == 0] + [logged_in, started])
    failed = [stamp for stamp, count in rounds if count > 0 and stamp > latest_ok and stamp >= now - 1800]
    failed += [stamp for stamp in errors if stamp > latest_ok and stamp >= now - 1800]
    last_round = max([stamp for stamp, _ in rounds] + [started])
    return {'login': login > max(logged_in, latest_ok),
            'runner': len(failed) >= 3 or pause_until > now or now - last_round > 1200}


def collect(state=None):
    state = state if state is not None else {}
    now = time.time()
    props = dict(line.split('=', 1) for line in command(['systemctl', 'show', 'douyin-friend-ai-bot.service',
                 '-p', 'ActiveState', '-p', 'InvocationID', '-p', 'ActiveEnterTimestampMonotonic', '-p', 'NRestarts']).splitlines() if '=' in line)
    faults = {'service': props.get('ActiveState') != 'active'}
    count = int(props.get('NRestarts') or 0)
    history = [row for row in state.get('restart_history', []) if row[0] >= now - 900]
    if history and count < history[-1][1]:
        history = []
    if not history or count != history[-1][1]:
        history.append([now, count])
    state['restart_history'] = history
    faults['service'] = faults['service'] or count - history[0][1] >= 3
    if props.get('ActiveState') == 'active':
        uptime = time.monotonic() - int(props.get('ActiveEnterTimestampMonotonic') or 0) / 1e6
        raw = command(['journalctl', '_SYSTEMD_INVOCATION_ID=' + props['InvocationID'], '--since=-40min', '-n', '2500', '-o', 'json', '--no-pager'])
        entries = [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
        faults.update(journal_health(entries, now, now - uptime))
    else:
        faults.update(runner=False, login=False)
    config = read(BOT / 'config/config.json', {})
    proxy = config.get('account', {}).get('proxy_server')
    faults['proxy'] = False
    if proxy:
        try:
            with httpx.Client(proxy=proxy, trust_env=False, timeout=12, follow_redirects=False) as client:
                with client.stream('GET', 'https://www.douyin.com/robots.txt') as response:
                    # Any target HTTP response proves transport; a proxy rejection does not.
                    faults['proxy'] = response.status_code == 407
        except Exception:
            faults['proxy'] = True
    try:
        bridge = read(pathlib.Path(os.environ.get('INTERLUDE_DIR', ROOT.parent / 'douyin-interlude')) / 'bridge-config.json', {})
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get('http://127.0.0.1:%d/health' % bridge.get('port', 18765),
                                  headers={'Authorization': 'Bearer ' + bridge['token']})
            response.raise_for_status()
            faults['interlude'] = not bool(response.json().get('ready'))
    except Exception:
        faults['interlude'] = True
    audit = BOT / 'logs/reply_audit.jsonl'
    if audit.exists():
        with audit.open('rb') as handle:
            handle.seek(max(0, audit.stat().st_size - 262144))
            lines = handle.read().decode('utf-8', errors='replace').splitlines()
        results = []
        for line in lines:
            try:
                row = json.loads(line)
                stamp = datetime.datetime.fromisoformat(row['ts']).timestamp()
                event = row.get('event')
                if stamp >= now - 1800 and event in {'ai_error', 'send_error', 'interlude_error', 'empty_reply', 'replied', 'interlude_replied'}:
                    results.append(event in {'replied', 'interlude_replied'})
            except (ValueError, KeyError, TypeError):
                continue
        if results:
            trailing = 0
            for ok in reversed(results):
                if ok:
                    break
                trailing += 1
            if trailing >= 3:
                faults['delivery'] = True
            elif results[-1]:
                faults['delivery'] = False
    return faults


def transition(state, faults, cfg, now, deliver=send):
    incidents = state.setdefault('incidents', {})
    pending = []
    for key, bad in faults.items():
        item = incidents.setdefault(key, {})
        if bad:
            item.setdefault('since', now)
            item.pop('healthy_since', None)
            if now - item['since'] >= cfg['failure_minutes'] * 60 and now - item.get('last_sent', 0) >= cfg['repeat_minutes'] * 60:
                pending.append((key, 'fault'))
        elif item.get('since') is not None:
            item.setdefault('healthy_since', now)
            if now - item['healthy_since'] >= 120:
                if item.get('last_sent') and cfg['recovery']:
                    pending.append((key, 'recovery'))
                else:
                    incidents[key] = {}
    # Global spacing also prevents rapidly flapping or alternating categories from flooding the inbox.
    if pending and cfg['enabled'] and now - state.get('last_attempt', 0) >= 300:
        state['last_attempt'] = now
        lines = [('恢复：' if kind == 'recovery' else '故障：') + LABELS[key] for key, kind in pending]
        try:
            deliver(cfg, '故障状态更新', '\n'.join(lines) + '\n检测时间：' + datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds'))
            state['last_sent'] = now
            state.pop('send_error', None)
            for key, kind in pending:
                if kind == 'recovery':
                    incidents[key] = {}
                else:
                    incidents[key]['last_sent'] = now
        except Exception as exc:
            state['send_error'] = safe_error(exc)
    state.update(checked_at=now, faults=faults, enabled=cfg['enabled'])
    return state


def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (STATE.parent / 'fault-alerts.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cfg = settings()
        state = read(STATE, {})
        try:
            faults = {**state.get('faults', {}), **collect(state)}
            faults['monitor'] = False
        except Exception:
            # Keep last known faults on partial probe failure, never emit a false recovery.
            faults = {**state.get('faults', {}), 'monitor': True}
        atomic(STATE, transition(state, faults, cfg, time.time()))


if __name__ == '__main__':
    main()
