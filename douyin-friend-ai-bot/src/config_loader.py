# -*- coding: utf-8 -*-
"""配置加载与校验：读取 config/config.json，缺失字段用内置默认值补齐。"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "account": {
        "chat_url": "https://www.douyin.com/chat",
        "storage_state": "state/douyin_storage.json",
        "cookies_file": "state/cookies.json",
        # Firefox 引擎（默认）：抖音新版聊天后端会识破 Chromium 的 CDP 自动化特征，
        # 静默拒绝打开会话；Firefox 实测可用。chromium 仅留给旧环境回退
        "engine": "firefox",
        "driver": "playwright",
        "proxy_server": "",
        # 有头模式（Xvfb 虚拟屏）+ 持久化 profile：减少被识别为脚本的概率
        "headless": False,
        "profile_dir": "state/browser_profile_firefox",
        "user_agent": "",
        "locale": "zh-CN",
        "timezone": "Asia/Shanghai",
        "viewport": {"width": 1536, "height": 864},
        "page_timeout_ms": 15000,
        "login_timeout_seconds": 300,
    },
    "owner": {
        "_说明": "你自己的抖音号（可选但建议填）。填了之后机器人能识别「点开会话时抖音误把本人当对方返回」的情况，不会把你的号当成好友来回复。",
        "douyin_id": "",
        "nickname": "",
    },
    "whitelist": {"friends": [], "exact_match": True},
    "ai": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 1.0,
        "max_tokens": 300,
        # 视觉（思考型）模型单独的 token 预算：reasoning 和正文共用额度，太小正文会空
        "vision_max_tokens": 1000,
        "timeout_seconds": 90,
        "max_retries": 2,
        "vision_model": "",
    },
    "reply": {
        "enabled": True,
        "max_reply_chars": 200,
        "history_messages": 8,
        "typing_delay_seconds": {"min": 2, "max": 7},
        "max_per_friend_per_hour": 30,
        "max_per_friend_per_day": 100,
        "skip_non_text": True,
        "repeat_same_text_after_minutes": 30,
        # 长回复有时拆成几条连发（真人习惯）：满足长度门槛后按概率触发
        "split_long_reply": {
            "enabled": True,
            "min_chars": 30,
            "max_segments": 3,
            "probability": 0.6,
            "gap_seconds": {"min": 2, "max": 5},
        },
        "extra_wait_before_reply_seconds": {"min": 3, "max": 10},
        "images": {"enabled": True, "policy": "vision"},
    },
    "poll": {
        "cycle_min_seconds": 15,
        "cycle_max_seconds": 35,
        "reload_every_cycles": 10,
        "startup_warmup_seconds": 5,
    },
    "prompts": {
        "system_prompt": "",
        # 人设文件（markdown）：配置了就优先于 system_prompt；用 {friend} 占位
        "system_prompt_file": "",
        # 按白名单 name 给特定好友追加备注，例如 {"示例好友": "这是示例备注，请自行修改"}
        "friend_notes": {},
        "user_template": "",
        "image_user_template": "",
    },
    "log": {
        "audit_file": "logs/reply_audit.jsonl",
        "runtime_file": "logs/runtime.log",
        "screenshot_dir": "logs/screenshots",
    },
    "risk": {
        "consecutive_failures_to_pause": 6,
        "pause_minutes_on_captcha": 30,
        "recheck_login_every_cycles": 12,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str | Path, *, require_friends: bool = True, require_ai: bool = True) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")

    raw = path.read_bytes()
    data: dict[str, Any] | None = None
    for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            data = json.loads(raw.decode(enc))
            break
        except Exception:
            continue
    if data is None:
        raise ValueError(f"config 不是合法 JSON: {path}")

    cfg = _deep_merge(DEFAULTS, data)
    validate_config(cfg, require_friends=require_friends, require_ai=require_ai)
    return cfg


def validate_config(cfg: dict[str, Any], *, require_friends: bool = True, require_ai: bool = True) -> None:
    raw_friends = [f for f in cfg.get("whitelist", {}).get("friends", []) if f.get("enabled", True)]
    friends = enabled_friends(cfg)
    if require_friends and not friends:
        raise ValueError("白名单为空：请在 config.json 的 whitelist.friends 里至少填一位好友（必须填对方抖音号 douyin_id；name 只用于定位会话）")
    if require_friends and (len(friends) != len(raw_friends) or any(not friend["name"] or not friend["douyin_id"] or not friend["sec_uid"] for friend in friends)):
        raise ValueError("每位启用的白名单好友都必须填写 name、douyin_id 和 sec_uid；缺少任一身份绑定时机器人不会启动")
    owner_nick, owner_id = owner_identity(cfg)
    if owner_id:
        for friend in friends:
            _fname, _fid = friend_identity(friend)
            if _fid and _fid == owner_id:
                raise ValueError(
                    f"owner.douyin_id（{owner_id}，你自己的抖音号）和 whitelist 里「{_fname or _fid}」重复："
                    "不能把自己加进白名单，否则机器人会尝试回复你自己"
                )
    if require_ai and (not str(cfg["ai"]["api_key"]).strip() or "在这里填" in str(cfg["ai"]["api_key"])):
        raise ValueError("还没填 AI 的 API 密钥：请修改 config.json 里 ai.api_key（默认是 DeepSeek）")
    if require_ai and not str(cfg["ai"]["base_url"]).strip():
        raise ValueError("ai.base_url 不能为空")
    persona_file = str(cfg.get("prompts", {}).get("system_prompt_file") or "").strip()
    if persona_file and not Path(persona_file).exists():
        raise ValueError(f"prompts.system_prompt_file 指向的人设文件不存在: {persona_file}")


# 抖音号在配置里可能出现的字段名（身份以此为准，昵称/备注可随时变）
ID_FIELD_ALIASES = ("douyin_id", "unique_id", "author_id", "account")


def friend_identity(friend: dict[str, Any]) -> tuple[str, str]:
    """从配置项里取出 (聊天页显示名 name, 抖音号 douyin_id)。"""
    name = str(friend.get("name") or "").strip()
    douyin_id = ""
    for key in ID_FIELD_ALIASES:
        value = friend.get(key)
        if value is not None and str(value).strip():
            douyin_id = str(value).strip()
            break
    return name, douyin_id


def enabled_friends(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """返回启用白名单好友；会话标题、抖音号和会话绑定 sec_uid 都必须可用。"""
    out: list[dict[str, Any]] = []
    for f in cfg.get("whitelist", {}).get("friends", []):
        if not f.get("enabled", True):
            continue
        name, douyin_id = friend_identity(f)
        sec_uid = str(f.get("sec_uid") or "").strip()
        if not name and not douyin_id and not sec_uid:
            continue
        out.append({"name": name, "douyin_id": douyin_id, "sec_uid": sec_uid, "enabled": True})
    return out


def owner_identity(cfg: dict[str, Any]) -> tuple[str, str]:
    """返回 (自己的昵称, 自己的抖音号)。

    owner 段存“你自己”的抖音号：点会话时抖音偶尔会把本人误当对方返回，
    机器人用它与拉取到的身份比对，识别出“拉到的是本人”并跳过/改用右侧标题重试。
    """
    owner = cfg.get("owner") or {}
    nickname = str(owner.get("nickname") or "").strip()
    douyin_id = ""
    for key in ID_FIELD_ALIASES:
        value = owner.get(key)
        if value is not None and str(value).strip():
            douyin_id = str(value).strip()
            break
    return nickname, douyin_id
