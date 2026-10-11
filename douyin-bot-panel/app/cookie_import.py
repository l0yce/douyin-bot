"""Convert pasted cookies without logging or returning credential contents."""
import json
import math
import re
import time

MAX_BYTES = 1024 * 1024
NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def flag(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise ValueError('Cookie 的布尔字段应为 true 或 false。')


def convert(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError('请先粘贴完整的抖音 Cookies。')
    if len(content.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Cookie 内容超过 1 MB，请只导出抖音网站的 Cookies。')
    raw = content.strip().lstrip('\ufeff')
    if raw.startswith(('[', '{')):
        try:
            entries = json.loads(raw)
        except (ValueError, RecursionError):
            raise ValueError('JSON 格式不完整，请重新复制完整内容。') from None
        if isinstance(entries, dict):
            entries = entries.get('cookies')
        if not isinstance(entries, list):
            raise ValueError('JSON 需要是 Cookie 数组，或包含 cookies 数组的对象。')
    else:
        # DevTools request Cookie header; split only at the first equals sign.
        raw = re.sub(r'^cookie\s*:\s*', '', raw, flags=re.I)
        if '\n' in raw or '\r' in raw:
            raise ValueError('请只复制 Cookie 请求头的值，不要粘贴整段请求头。')
        entries = []
        for part in raw.split(';'):
            if not part.strip():
                continue
            name, sep, value = part.strip().partition('=')
            if not sep:
                raise ValueError('Cookie 字符串需要是 name=value; name2=value2 格式。')
            entries.append({'name': name.strip(), 'value': value, 'secure': True})
    if not entries or len(entries) > 2000:
        raise ValueError('Cookie 数量应为 1 至 2000 条。')
    result, seen = [], set()
    foreign = expired = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Cookie 数组中存在无效条目，请重新导出。')
        name, value = entry.get('name'), entry.get('value')
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(value, str):
            raise ValueError('每条 Cookie 都需要有效的 name 和字符串 value。')
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Cookie 值包含不支持的控制字符，请重新导出。')
        domain = entry.get('domain') or '.douyin.com'
        if not isinstance(domain, str):
            raise ValueError('Cookie domain 格式不正确。')
        host = domain.lower().lstrip('.')
        if host != 'douyin.com' and not host.endswith('.douyin.com'):
            foreign += 1
            continue
        if not re.fullmatch(r'[a-z0-9.-]+', host) or '..' in host:
            raise ValueError('Cookie domain 格式不正确。')
        path = entry.get('path') or '/'
        if not isinstance(path, str) or not path.startswith('/') or any(ord(c) < 32 for c in path):
            raise ValueError('Cookie path 格式不正确。')
        expires = -1
        if not flag(entry.get('session')):
            stamp = entry.get('expirationDate', entry.get('expires'))
            if stamp is not None:
                try:
                    if isinstance(stamp, bool):
                        raise ValueError()
                    stamp = float(stamp)
                    if not math.isfinite(stamp) or stamp < -1 or stamp > 253402300799:
                        raise ValueError()
                    expires = int(stamp) if stamp >= 0 else -1
                except (ValueError, TypeError, OverflowError):
                    raise ValueError('Cookie 过期时间需要是 Unix 秒时间戳。') from None
        if expires >= 0 and expires <= time.time():
            expired += 1
            continue
        key = (name, domain.lower(), path)
        if key in seen:
            raise ValueError('存在相同名称、域名和路径的重复 Cookie，请只粘贴一份完整导出。')
        seen.add(key)
        cookie = {'name': name, 'value': value, 'domain': domain.lower(), 'path': path,
                  'expires': expires, 'httpOnly': flag(entry.get('httpOnly')),
                  'secure': flag(entry.get('secure'))}
        same_site = str(entry.get('sameSite') or '').lower()
        if same_site in ('none', 'no_restriction'):
            cookie.update(sameSite='None', secure=True)
        elif same_site in ('lax', 'strict'):
            cookie['sameSite'] = same_site.capitalize()
        elif same_site not in ('', 'unspecified'):
            raise ValueError('Cookie sameSite 字段不受支持，请重新导出。')
        result.append(cookie)
    if not result:
        raise ValueError('没有可导入的抖音 Cookies，可能已过期或属于其他网站。')
    warnings = []
    if foreign:
        warnings.append(f'已忽略 {foreign} 条其他网站的 Cookies。')
    if expired:
        warnings.append(f'已忽略 {expired} 条已过期 Cookies。')
    if not any(c['name'] in ('sessionid', 'sessionid_ss') for c in result):
        warnings.append('未发现常见登录会话 Cookie，可能复制不完整；建议使用插件导出完整 JSON。')
    return result, warnings
