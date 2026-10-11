# -*- coding: utf-8 -*-
"""按消息身份和已确认窗口选出新消息；保持时间顺序，成功处理后推进游标。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from state_store import msg_fingerprint

# 抖音网页版对某些消息类型（语音/部分卡片等）只显示占位文案，无法看到真实内容。
# 保留类型描述，但不能让模型猜测不可见内容。
DOUYIN_UNSUPPORTED_MARKERS = (
    "暂不支持该消息类型",
    "暂不支持",
    "请到抖音手机端查看",
)

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_UNSUPPORTED = "unsupported"

NON_TEXT_FP = msg_fingerprint("__non_text__")


class Decision:
    def __init__(self, action: str, reason: str, incoming_text: str = "", fingerprint: str = "",
                 kind: str = KIND_TEXT) -> None:
        self.action = action          # reply | skip | nothing
        self.reason = reason
        self.incoming_text = incoming_text
        self.fingerprint = fingerprint
        self.kind = kind              # text | image
        self.selected_messages: list[dict[str, Any]] = []
        self.checkpoint: list[str] = []
        self.latest_fingerprint = fingerprint

    def acknowledged_state(self, state=None) -> dict[str, Any]:
        seen = list((state or {}).get("incoming_seen_ids") or [])
        seen.extend(str(m["message_id"]) for m in self.selected_messages if m.get("message_id"))
        return {"incoming_window": self.checkpoint[-400:],
                "incoming_seen_ids": list(dict.fromkeys(seen))[-400:],
                "last_replied_fp": self.fingerprint,
                "interlude_last_incoming_fp": self.latest_fingerprint}

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "reason": self.reason,
                "incoming_text": self.incoming_text, "fingerprint": self.fingerprint,
                "kind": self.kind}


def _rate_count(events: list[str], window: timedelta, now: datetime) -> int:
    if not events:
        return 0
    cutoff = now - window
    return sum(1 for e in events if _parse(e) >= cutoff)


def _parse(iso: str) -> datetime:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            return dt.astimezone()
        return dt
    except Exception:
        # 必须返回带时区的最小值，否则与 aware 的 cutoff 比较会抛 TypeError
        return datetime.min.replace(tzinfo=timezone.utc)


def message_kind(msg: dict[str, Any]) -> str:
    """判断一条消息是 文字 / 图片 / 不支持(语音卡片等)。"""
    kind = str(msg.get("kind") or "")
    if kind in (KIND_IMAGE, KIND_VIDEO, KIND_UNSUPPORTED, "sticker", "voice", "file", "link", "card", "location", "live"):
        return kind
    text = str(msg.get("text", "")).strip()
    if (not text) or any(m in text for m in DOUYIN_UNSUPPORTED_MARKERS):
        return KIND_UNSUPPORTED
    return KIND_TEXT


def message_fingerprint(msg: dict[str, Any]) -> str:
    """One identity rule for reply decisions and queued delivery verification."""
    if msg.get("message_id"):
        return msg_fingerprint("message:" + str(msg["message_id"]) + "|" + str(bool(msg.get("from_me"))))
    fp = content_fingerprint(msg)
    return msg_fingerprint(fp, str(msg["ts"])) if msg.get("ts") else fp


def content_fingerprint(msg: dict[str, Any]) -> str:
    kind = message_kind(msg)
    if kind == "live":
        live = msg.get("live") or {}
        key = live.get("room_id") or live.get("web_rid") or live.get("url") or live.get("cover") or "|".join(str(live.get(k) or "") for k in ("host", "title", "card_text"))
        return msg_fingerprint("__live__|" + str(key))
    if kind == KIND_VIDEO:
        video = msg.get("video") or {}
        key = (video.get("id") or video.get("url") or video.get("cover") or
               "|".join(str(video.get(k) or "") for k in ("publisher", "title")))
        return msg_fingerprint("__video__|" + str(key))
    if kind in (KIND_IMAGE, "sticker"):
        src = str(msg.get("img_src") or "")
        if src and not src.startswith("data:"):
            parts = urlsplit(src)
            src = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return msg_fingerprint("__" + kind + "__|" + (src or str(msg.get("text") or "unknown-image")))
    if kind == KIND_UNSUPPORTED:
        return msg_fingerprint("__unsupported__|" + str(msg.get("text") or "unknown"))
    if kind != KIND_TEXT:
        return msg_fingerprint("__" + kind + "__|" + str(msg.get("text") or ""))
    return msg_fingerprint(str(msg.get("text", "")).strip())


def _within_rate_limit(friend_state: dict[str, Any], reply_cfg: dict[str, Any], now: datetime) -> tuple[bool, str]:
    hour_events = friend_state.get("replied_at", [])
    if reply_cfg.get("max_per_friend_per_hour", 0) > 0:
        count = _rate_count(hour_events, timedelta(hours=1), now)
        if count >= int(reply_cfg["max_per_friend_per_hour"]):
            return False, "达到每小时回复上限，本轮跳过"
    day_events = friend_state.get("replied_at_day", hour_events)
    if reply_cfg.get("max_per_friend_per_day", 0) > 0:
        count = _rate_count(day_events, timedelta(days=1), now)
        if count >= int(reply_cfg["max_per_friend_per_day"]):
            return False, "达到每天回复上限，本轮跳过"
    return True, ""


def _repeat_window_elapsed(friend_state: dict[str, Any], reply_cfg: dict[str, Any], now: datetime) -> bool:
    """同指纹（相同文字/相同图片）消息过了宽限期就算“新消息”。

    红点出现且最新一条和上次已回复的内容相同，通常是对方真的又发了一句
    一样的话（“?”、“在吗”这类很常见）；但红点未消的界面异常也会造成
    同样现象。用时间窗区分：宽限期内一律视为重复（防连环回复），
    超过 repeat_same_text_after_minutes 分钟才允许再回一次。
    配 0（旧默认）= 永不重复回复同内容。
    """
    minutes = float(reply_cfg.get("repeat_same_text_after_minutes", 0) or 0)
    if minutes <= 0:
        return False
    last_at = _parse(str(friend_state.get("last_replied_at") or ""))
    if last_at.tzinfo is None:
        return False
    return (now - last_at).total_seconds() >= minutes * 60


def _message_text(msg: dict[str, Any]) -> str:
    kind = message_kind(msg)
    text = str(msg.get("text") or "").strip()
    if kind == "text":
        return text
    labels = {"image": "图片", "sticker": "表情包", "voice": "语音", "file": "文件",
              "link": "链接", "card": "分享卡片", "location": "位置", "unsupported": "其他消息"}
    if kind == "live":
        return text or "[好友分享了一个直播间，未进入或观看直播，无法确定内容]"
    if kind == "video":
        return text or "[好友分享了视频，未播放或观看视频]"
    suffix = ""
    if kind in ("voice", "unsupported"):
        suffix = "（网页未提供可读取的实际内容，不要猜测内容）"
    return "[" + labels.get(kind, kind) + "]" + (" " + text if text else "") + suffix


def _make_decision(selected, checkpoint) -> Decision:
    text = "\n".join(_message_text(m) for m in selected)
    if len(selected) > 1:
        text = "[以下是好友按时间顺序发来的 %d 条消息，请一起理解并回应]\n" % len(selected) + text
    # Snapshot length also distinguishes repeated identical messages without DOM ids.
    fp = msg_fingerprint("batch|" + "|".join(checkpoint))
    kind = message_kind(selected[0]) if len(selected) == 1 else "batch"
    d = Decision("reply", "收到 %d 条尚未处理的消息" % len(selected), text, fp, kind)
    d.selected_messages = selected
    d.checkpoint = checkpoint
    d.latest_fingerprint = message_fingerprint(selected[-1])
    return d


def split_decision(decision: Decision, max_images=2, max_chars=24000) -> list[Decision]:
    """Submit ordered chunks within the bridge's media/body bounds; ACK each separately."""
    selected = decision.selected_messages
    # Selected messages can be interleaved with outgoing messages. Locate their
    # positions backwards so repeated equal-content messages keep their occurrences.
    positions = []
    cursor = len(decision.checkpoint)
    for msg in reversed(selected):
        key = ("me:" if msg.get("from_me") else "them:") + message_fingerprint(msg)
        cursor = max(i for i in range(cursor) if decision.checkpoint[i] == key)
        positions.append(cursor)
    positions.reverse()
    parts, group, images, chars = [], [], 0, 0
    for msg, pos in zip(selected, positions):
        media = int(message_kind(msg) in ("image", "sticker") and bool(msg.get("media_token")))
        size = len(_message_text(msg))
        if group and (images + media > max_images or chars + size > max_chars):
            parts.append(_make_decision(group, decision.checkpoint[:last_pos + 1]))
            group, images, chars = [], 0, 0
        group.append(msg)
        images += media
        chars += size
        last_pos = pos
    if group:
        parts.append(_make_decision(group, decision.checkpoint))
    return parts


def decide_reply(*, friend: str, messages: list[dict[str, Any]], friend_state: dict[str, Any],
                 reply_cfg: dict[str, Any], now: datetime | None = None) -> Decision:
    now = now or datetime.now().astimezone()
    if not messages:
        return Decision("nothing", "没有读到任何消息")
    chronological = list(reversed(messages))
    keys = [("me:" if m.get("from_me") else "them:") + message_fingerprint(m) for m in chronological]
    previous = friend_state.get("incoming_window") or []
    boundary = None
    # Find the longest overlap of the last acknowledged window with this read.
    # Earliest match handles a new duplicate '在吗' when the site exposes no id.
    for size in range(min(len(previous), len(keys)), 0, -1):
        suffix = previous[-size:]
        matches = [i + size for i in range(len(keys) - size + 1) if keys[i:i + size] == suffix]
        if matches:
            boundary = matches[0]
            break
    if boundary is None:
        boundary = 0
        # Bootstrap/migration: don't replay old history before our latest reply.
        for i, msg in enumerate(chronological):
            if msg.get("from_me"):
                boundary = i + 1
        old_fp = friend_state.get("last_replied_fp")
        for i in range(boundary, len(chronological)):
            msg = chronological[i]
            if not msg.get("from_me") and old_fp in (message_fingerprint(msg), content_fingerprint(msg)):
                boundary = i + 1
        # Individual stable ids remain deduplicated if a virtualized list loses overlap.
        seen = set(friend_state.get("incoming_seen_ids") or [])
    else:
        seen = set()
    selected = [m for i, m in enumerate(chronological) if i >= boundary and not m.get("from_me")
                and (not m.get("message_id") or str(m["message_id"]) not in seen)]
    if not selected:
        return Decision("nothing", "当前消息已经处理过，等待新消息")
    # Explicit image/video switches remain authoritative. Unknown message types
    # now carry truthful descriptors instead of being silently discarded.
    only_disabled = all((message_kind(m) in ("image", "sticker") and
                         (not reply_cfg.get("images", {}).get("enabled", True)
                          or reply_cfg.get("images", {}).get("policy") == "skip"))
                        or (message_kind(m) == "video" and not reply_cfg.get("videos", {}).get("enabled", True))
                        for m in selected)
    if only_disabled:
        return Decision("nothing", "这些消息类型的回复已在配置中关闭")
    ok, reason = _within_rate_limit(friend_state, reply_cfg, now)
    if not ok:
        return Decision("nothing", reason)
    return _make_decision(selected, keys)
