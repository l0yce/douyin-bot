# -*- coding: utf-8 -*-
"""主运行循环：定时检查白名单好友的新消息，交给 AI 生成回复并发送。

安全设计：
- 只读取/回复白名单里的人，其它人一律不点开、不回复。
- 带防重复、每小时/每天次数上限、随机间隔，尽量低频像真人。
- 所有动作写入 logs/reply_audit.jsonl 审计日志。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
from typing import Any

from ai_client import AIError, AIClient
from interlude_bridge import BridgeError, enabled as interlude_enabled, submit as interlude_submit, drain as interlude_drain, native_vision_enabled, recover_pending
from audit_logger import AuditLogger
from browser_manager import BrowserSession, LoginError, has_any_login_material
from config_loader import enabled_friends, load_config, owner_identity
from douyin_chat import DouyinChat, DouyinPageError, FriendMismatch, FriendNotFound
from prompts import build_system_prompt, build_user_prompt
from reply_engine import decide_reply, split_decision, message_kind, message_fingerprint
from video_comments import read_comments, reference_text
from state_store import StateStore

logger = logging.getLogger("douyin.runner")

# 低频兜底核验的时间闸门（进程内共享）
_last_unread_fallback_ts = 0.0

# 「点开会话失败」现场的保存节流：同一好友 5 分钟内最多存一次，避免刷屏磁盘
_open_fail_dump_ts: dict[str, float] = {}

# 「点开会话失败」的退避：连续失败的好友冷却 20 分钟内不再反复点击，
# 避免被服务端拒绝时仍每轮徒劳尝试、加重风控嫌疑
_OPEN_FAIL_COOLDOWN_SECONDS = 20 * 60
_open_fail_until: dict[str, float] = {}


class LoginExpired(RuntimeError):
    pass


class _PageCrash(RuntimeError):
    """页面/浏览器崩溃，需要重启会话而不是继续撞同一个错误。"""


# Playwright 报错文案特征：命中即认为页面/浏览器已崩溃
_CRASH_MARKERS = (
    "target crashed",
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser closed",
    "session closed",
)


def _is_crash_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _CRASH_MARKERS)


async def _page_alive(page: Any) -> bool:
    """页面还能执行 JS 就算活着（崩溃后任何 evaluate 都会直接抛错）。"""
    if page is None:
        return False
    try:
        if page.is_closed():
            return False
    except Exception:
        return False
    try:
        await page.evaluate("() => 1")
        return True
    except Exception:
        return False


async def _wait(stop_event: asyncio.Event, seconds: float) -> bool:
    """等待 seconds 秒；期间被要求停止则返回 False。"""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return False
    except asyncio.TimeoutError:
        return True


async def handle_friend(
    *,
    chat: DouyinChat,
    cfg: dict[str, Any],
    friend: dict[str, Any],
    row: Any = None,
    store: StateStore,
    audit: AuditLogger,
    ai: AIClient | None,
    dry_run: bool,
    current_only: bool = False,
) -> dict[str, Any]:
    """处理一位白名单好友：按抖音号核验 -> 读消息 -> 决策 -> (AI 生成 -> 发送)。

    只有点开后接口返回的抖音号与白名单一致，才会读消息/回复；
    找不到会话按“无事发生”，核验不过按出错记录，绝不碰身份不符的人。
    """
    reply_cfg = cfg["reply"]
    cfg_name = str(friend.get("name") or "").strip()
    cfg_id = str(friend.get("douyin_id") or "").strip()
    key = cfg_id or cfg_name or "?"
    result: dict[str, Any] = {"friend": cfg_name or key, "action": "nothing", "reason": ""}

    # 点不开会话的退避冷却：期间不再反复点击这个好友
    if not current_only and _open_fail_until.get(key, 0) > time.monotonic():
        result["reason"] = "会话近期点不开（冷却中，稍后自动重试）"
        return result

    try:
        kwargs = {"current_only": True} if current_only else {}
        identity = await chat.open_friend(friend, exact=bool(cfg["whitelist"].get("exact_match", True)), row=row, **kwargs)
    except FriendMismatch as exc:
        result.update(action="nothing", reason=f"有未读但不是白名单好友（核验不符），跳过: {exc}")
        logger.info("[%s] %s", key, result["reason"])
        return result
    except FriendNotFound as exc:
        # 点不开会话：进入退避冷却，期间不再反复点击这个好友
        _open_fail_until[key] = time.monotonic() + _OPEN_FAIL_COOLDOWN_SECONDS
        result.update(action="nothing", reason=f"会话里没有这位好友，跳过: {exc}")
        logger.info("[%s] %s", key, result["reason"])
        # 点开会话失败时留一份现场（截图/HTML/会话列表），5 分钟内同好友只存一次
        now_ts = time.monotonic()
        if now_ts - _open_fail_dump_ts.get(key, 0) > 300:
            _open_fail_dump_ts[key] = now_ts
            try:
                saved = await chat.save_failure_artifacts(cfg["log"]["screenshot_dir"], f"openfail-{key}")
                if saved:
                    logger.warning("[%s] 已保存点开失败的现场：%s", key, ", ".join(saved))
            except Exception:
                pass
        return result
    except DouyinPageError as exc:
        result.update(action="error", reason=f"打不开会话或核验未通过: {exc}")
        logger.warning("[%s] %s", key, result["reason"])
        return result

    # 日志/提示词用接口返回的真实身份：昵称优先，其次配置名，兜底用抖音号
    result["verified"] = True  # 身份核验通过，供低频兜底判断“确实属于白名单”
    _open_fail_until.pop(key, None)  # 能打开了，清掉退避冷却
    nickname = str((identity or {}).get("nickname") or "").strip()
    actual_id = str((identity or {}).get("unique_id") or (identity or {}).get("short_id") or "").strip()
    result["verified_douyin_id"] = actual_id
    result["verified_sec_uid"] = True
    logger.info("[%s] 身份核验通过：会话 sec_uid 与白名单一致；读取抖音号 %s 与白名单一致", key, actual_id)
    name = nickname or cfg_name or actual_id or key
    result["friend"] = name
    state_key = actual_id or key  # 状态按抖音号记：昵称/备注改了也不串

    try:
        messages = await chat.read_messages(limit=max(200, int(reply_cfg.get("history_messages", 8)) + 6))
        if current_only:
            await chat.open_friend(friend, exact=bool(cfg["whitelist"].get("exact_match", True)), current_only=True)
    except Exception as exc:  # noqa: BLE001
        result.update(action="error", reason=f"读取消息失败: {exc}")
        logger.warning("[%s] %s", name, result["reason"])
        return result

    if not messages:
        result["reason"] = "没有读到消息"
        return result

    if interlude_enabled(cfg) and not dry_run and reply_cfg.get("enabled", True):
        try:
            await recover_pending(cfg, friend, store, state_key)
        except BridgeError as exc:
            result.update(action="error", reason=str(exc))
            audit.log({"event": "interlude_error", **result})
            return result
    state = store.friend_state(state_key)
    if interlude_enabled(cfg) and state.get("interlude_last_accepted_at"):
        state = dict(state)
        state["last_replied_at"] = max(str(state.get("last_replied_at") or ""), state["interlude_last_accepted_at"])
    decision = decide_reply(
        friend=name,
        messages=messages,
        friend_state=state,
        reply_cfg=reply_cfg,
    )
    result["action"] = decision.action
    result["reason"] = decision.reason

    if decision.action != "reply":
        if decision.action == "skip":
            store.set(state_key, "last_replied_fp", decision.fingerprint or "__non_text__")
            logger.info("[%s] %s", name, decision.reason)
        else:
            logger.debug("[%s] %s", name, decision.reason)
        return result

    if not reply_cfg.get("enabled", True):
        result.update(action="nothing", reason="自动回复已关闭（reply.enabled=false），仅监视不发送")
        logger.info("[%s] %s", name, result["reason"])
        return result

    kind = getattr(decision, "kind", "text") or "text"
    incoming = decision.incoming_text
    result["incoming_text"] = incoming
    result["kind"] = kind
    label = "图片消息" if kind == "image" else "分享视频" if kind == "video" else "消息"
    logger.info("[%s] 收到新%s需要回复: %s", name, label, incoming[:60])

    if ai is None:
        result.update(action="dry_reply", reason="未配置 AI，仅提示需要回复")
        result["reply_text"] = "（未配置 AI：这条需要人工回复）"
        audit.log({"event": "decide", **result})
        return result

    # 历史记录：去掉最新那条待回复消息本身（messages 最新在前，去掉第 0 条）
    selected_objects = {id(m) for m in decision.selected_messages}
    history = [m for m in messages if id(m) not in selected_objects]
    system_prompt = build_system_prompt(name, cfg["prompts"])

    # Capture each image independently; unavailable media must not block text.
    image_b64: str | None = None
    media = {}
    image_cfg = reply_cfg.get("images", {})
    images_enabled = image_cfg.get("enabled", True) and image_cfg.get("policy") != "skip"
    if images_enabled:
        for msg in decision.selected_messages:
            if message_kind(msg) in ("image", "sticker") and msg.get("media_token"):
                try:
                    png_bytes = await chat.capture_message_image_png(msg)
                    media[msg["media_token"]] = base64.b64encode(png_bytes).decode("ascii")
                except DouyinPageError as exc:
                    logger.warning("[%s] 图片读取失败，降级为文字处理：%s", name, type(exc).__name__)
                    audit.log({"event": "image_fallback", "friend": name, "stage": "capture",
                               "error_type": type(exc).__name__})

    vision_failed = False

    def unavailable_image(index):
        return ("\n[第%d条为图片或表情包，内容未能识别。请结合可见文字和上下文自然回应；"
                "不要猜测图案、配字或假装看懂，也不要向好友解释模型或接口错误。]" % index)

    async def describe_image(index, data):
        nonlocal vision_failed
        if not vision_failed:
            try:
                observation = await asyncio.to_thread(ai.chat, [
                    {"role": "system", "content": "只描述图片中可见的事实、文字和场景，不猜测身份或不可见内容。"},
                    {"role": "user", "content": "请简洁描述这张图片或表情包，供后续聊天理解。"},
                ], image_b64=data)
                if observation and observation.strip():
                    return "\n[第%d条的视觉观察，可能有误：]\n%s" % (index, observation)
            except AIError as exc:
                logger.warning("[%s] 识图失败，降级为文字处理：%s", name, type(exc).__name__)
            # Stop repeated failed vision calls within this batch, but retry on
            # future messages so a temporary provider failure can recover.
            vision_failed = True
            audit.log({"event": "image_fallback", "friend": name, "stage": "vision"})
        return unavailable_image(index)

    if interlude_enabled(cfg):
        if dry_run:
            result.update(action="dry_reply", reason="dry-run：幕间路径不写入真实剧本、不投递")
            audit.log({"event": "interlude_dry_run", **result})
            return result
        accepted = 0
        try:
            native_vision = bool(media) and await native_vision_enabled(cfg)
            for part in split_decision(decision):
                content = part.incoming_text
                image_sources = []
                media_number = 0
                read_video_comments = False
                for index, msg in enumerate(part.selected_messages, 1):
                    if message_kind(msg) == 'video':
                        comments = await read_comments(chat.page.context, msg.get('video') or {}, reply_cfg.get('video_comments'))
                        content += '\n' + reference_text(comments, index)
                        read_video_comments = True
                        audit.log({'event': 'video_comments', 'friend': name,
                                   'status': comments['status'], 'count': len(comments['comments']),
                                   'cached': comments.get('cached', False)})
                    data = media.get(msg.get("media_token"))
                    if not data:
                        if message_kind(msg) in ("image", "sticker"):
                            content += unavailable_image(index)
                        continue
                    media_number += 1
                    if native_vision:
                        image_sources.append("data:image/png;base64," + data)
                        content += "\n[附图%d对应上述第%d条消息，请结合图片理解。]" % (media_number, index)
                    else:
                        content += await describe_image(index, data)
                if read_video_comments:
                    fresh_messages = await chat.read_messages(limit=200)
                    if not fresh_messages or not messages or message_fingerprint(fresh_messages[0]) != message_fingerprint(messages[0]):
                        result.update(action='nothing', reason='读取视频评论期间聊天有变化，保留消息待下一轮按新上下文处理', accepted_messages=accepted)
                        audit.log({'event': 'video_comments_context_changed', **result})
                        return result
                await interlude_submit(cfg, friend, store, state_key, part, content, image_sources, messages=messages)
                accepted += len(part.selected_messages)
                audit.log({"event": "interlude_submitted", **result,
                           "incoming_text": content, "message_count": len(part.selected_messages),
                           "kind": part.kind, "image_count": len(image_sources)})
            result.update(action="nothing", reason="幕间系统已接收 %d 条消息，等待后台生成/投递" % accepted)
        except (BridgeError, AIError) as exc:
            result.update(action="error", reason=str(exc), accepted_messages=accepted)
            logger.warning("[%s] %s", name, exc)
            audit.log({"event": "interlude_error", **result})
        return result

    # Isolate image understanding from text generation so either a capture or
    # vision failure can still produce a normal text reply.
    for index, msg in enumerate(decision.selected_messages, 1):
        if message_kind(msg) in ("image", "sticker"):
            data = media.get(msg.get("media_token"))
            incoming += await describe_image(index, data) if data else unavailable_image(index)

    user_prompt = build_user_prompt(
        name,
        history,
        incoming,
        cfg["prompts"],
        max_history=int(reply_cfg.get("history_messages", 8)),
        image=False,
    )

    started = time.time()
    chat_messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        reply_text = await asyncio.to_thread(ai.chat, chat_messages, image_b64=image_b64)
    except AIError as exc:
        store.update(state_key, ai_fail_streak=int(state.get("ai_fail_streak", 0) or 0) + 1)
        result.update(action="error", reason=f"AI 生成失败，消息保留待重试: {exc}")
        audit.log({"event": "ai_error", "friend": name, "reason": str(exc)})
        logger.error("[%s] %s", name, result["reason"])
        return result

    # 官方拒答套话兜底：模型安全层输出的固定话术（如「当前输入涉及敏感信息，
    # 让我们换个话题」）一句就暴露 AI，且提示词压不住。检测到立刻重新生成一次；
    # 仍是套话就用本地自然回复顶替，保证这类话术永远发不出去
    if _looks_like_ai_tell(reply_text):
        logger.warning("[%s] AI 输出疑似官方拒答套话：%r —— 立即重新生成", name, reply_text[:80])
        audit.log({
            "event": "ai_tell_blocked", "friend": name,
            "incoming": incoming[:200], "raw": reply_text[:200],
        })
        try:
            retry_text = await asyncio.to_thread(ai.chat, chat_messages, image_b64=image_b64)
        except AIError as exc:
            logger.warning("[%s] 套话重试生成失败：%s，改用本地自然回复", name, exc)
            retry_text = ""
        if not retry_text or _looks_like_ai_tell(retry_text):
            logger.warning("[%s] 重试仍不可用，改用本地自然回复", name)
            retry_text = random.choice(_NATURAL_DEFLICTIONS)
        reply_text = retry_text
    elapsed_ms = int((time.time() - started) * 1000)

    reply_text = _finalize_reply(reply_text, int(reply_cfg.get("max_reply_chars", 200)))
    if not reply_text:
        result.update(action="error", reason="AI 返回内容为空，跳过发送")
        audit.log({"event": "empty_reply", "friend": name, "incoming": incoming[:200]})
        logger.warning("[%s] AI 返回内容为空（清洗后无正文），跳过发送", name)
        return result

    result["reply_text"] = reply_text
    result["ai_model"] = cfg["ai"].get("model", "")

    if dry_run:
        result.update(action="dry_reply", reason="dry-run：只生成不发送")
        audit.log({"event": "dry_reply", **result})
        logger.info("[%s] (dry-run) 拟回复: %s", name, reply_text[:120])
        return result

    # 发送前模拟真人停顿
    delay = _random_range(reply_cfg["typing_delay_seconds"]) + _random_range(
        reply_cfg.get("extra_wait_before_reply_seconds", {"min": 3, "max": 10})
    )
    logger.info("[%s] 模拟停顿 %.1f 秒后发送……", name, delay)
    await asyncio.sleep(delay)

    # 长回复有时拆成几条连发（概率+长度门槛触发，见 _split_reply_segments）
    segments = _split_reply_segments(reply_text, reply_cfg)
    result["segments"] = len(segments)
    if len(segments) > 1:
        logger.info("[%s] 本条回复分 %d 段发送", name, len(segments))

    sent_count = 0
    try:
        for i, seg in enumerate(segments):
            if i:
                gap = _random_range(
                    (reply_cfg.get("split_long_reply") or {}).get("gap_seconds", {"min": 2, "max": 5})
                )
                logger.info("[%s] 第 %d/%d 段 %.1f 秒后发出", name, i + 1, len(segments), gap)
                await asyncio.sleep(gap)
            await chat.send_text(seg)
            sent_count += 1
    except DouyinPageError as exc:
        if sent_count:
            replied_at = list(state.get("replied_at", []))
            replied_at.append(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            store.update(
                state_key,
                **decision.acknowledged_state(store.friend_state(state_key)),
                last_replied_at=replied_at[-1],
                last_reply_text=reply_text[:200],
                replied_at=replied_at[-100:],
                partial_reply=True,
            )
            result["partial_reply"] = True
        result.update(action="error", reason=f"发送失败: {exc}")
        audit.log({
            "event": "send_error", "friend": name, "incoming": incoming[:200],
            "reply": reply_text[:200], "reason": str(exc),
            "sent_segments": sent_count, "segments": len(segments),
        })
        logger.error("[%s] %s（已发出 %d/%d 段）", name, result["reason"], sent_count, len(segments))
        return result

    replied_at = list(state.get("replied_at", []))
    replied_at.append(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    # 一次写盘：指纹/时间戳/限流列表保持原子一致，中途退出也不会出现只记了一半
    store.update(
        state_key,
        ai_fail_streak=0,
        partial_reply=False,
        **decision.acknowledged_state(store.friend_state(state_key)),
        last_replied_at=replied_at[-1],
        last_reply_text=reply_text[:200],
        replied_at=replied_at[-100:],
    )

    result.update(action="replied", reason="已回复", elapsed_ms=elapsed_ms)
    audit.log({"event": "replied", **result})
    logger.info("[%s] 已回复: %s", name, reply_text[:120])
    return result


def _finalize_reply(text: str, max_chars: int) -> str:
    text = text.strip()
    if not text:
        return ""
    if len(text) > max_chars:
        cut = text[:max_chars]
        for punct in "。！？!?…；;":
            idx = cut.rfind(punct)
            if idx >= max_chars * 0.5:
                cut = cut[: idx + 1]
                break
        text = cut
    return text.strip()


def _split_reply_segments(text: str, reply_cfg: dict[str, Any]) -> list[str]:
    """长回复有时拆成几条连发，更像真人。返回 [text] 表示整条发。

    同时满足才分段：长度达到 min_chars、按句末标点能切出至少两段、
    随机命中 probability（“有时候”）。句子太多时按长度合并成不超过
    max_segments 段；只在句末标点处切，绝不从逗号中间硬拆。
    """
    cfg = reply_cfg.get("split_long_reply") or {}
    if not cfg.get("enabled", True):
        return [text]
    min_chars = int(cfg.get("min_chars", 30))
    max_segments = max(2, int(cfg.get("max_segments", 3)))
    probability = float(cfg.get("probability", 0.6))
    if len(text) < min_chars or random.random() >= probability:
        return [text]

    sentences: list[str] = []
    for chunk in re.split(r"(?<=[。！？!?…；;\n])", text):
        # 提示词要求“句号换成空格”：中文与中文之间的空格也是句子边界，一并切开
        for piece in re.split(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", chunk):
            piece = " ".join(piece.split())  # 压平段内换行/多余空白
            if piece:
                sentences.append(piece)
    if len(sentences) < 2:
        return [text]

    if len(sentences) > max_segments:
        target = max(14, -(-len(text) // max_segments))  # ceil 除法

        def _join(a: str, b: str) -> str:
            # 句末已有标点就直接接；空格文风（结尾无标点）用空格接回，避免字粘在一起
            return a + b if a[-1] in "。！？!?…；;" else f"{a} {b}"

        merged: list[str] = []
        cur = ""
        for s in sentences:
            cur = _join(cur, s) if cur else s
            if len(cur) >= target and len(merged) < max_segments - 1:
                merged.append(cur)
                cur = ""
        if cur:
            merged.append(cur)
        if len(merged) > 1 and len(merged[-1]) < 4:  # 只有 1~3 字的碎尾巴才并回上一段
            merged[-2] = _join(merged[-2], merged[-1])
            merged.pop()
        sentences = [s for s in merged if s]

    if len(sentences) < 2:
        return [text]
    return sentences


# 模型/网关官方拒答或 AI 自曝的固定话术特征（出现即拦截重试）。
# 这层安全逻辑在系统提示词之外，提示词写得再细也压不住——宁可代码侧错杀，
# 也绝不让这些话术发出去（一句就暴露是 AI）
_AI_TELL_PATTERNS = (
    "当前输入", "涉及敏感", "敏感信息", "敏感话题", "让我们换个话题",
    "我是AI", "作为AI", "作为一个AI", "AI助手", "人工智能助手", "语言模型",
    "无法回答", "不能回答", "无法提供", "不能提供", "无法协助",
    "违反相关", "相关规定", "内容规范", "安全政策", "内容安全",
)


def _looks_like_ai_tell(text: str) -> bool:
    low = str(text or "").lower()
    return any(p.lower() in low for p in _AI_TELL_PATTERNS)


# 套话兜底用的本地自然回复（模型重试仍吐套话时随机顶替，风格符合聊天习惯）
_NATURAL_DEFLICTIONS = (
    "哈哈 你搁这说啥呢",
    "咋了咋了 火气这么大",
    "行行行 你说了算",
    "你说的都对 哈哈",
    "哎呀 别说这个了",
    "这有啥好说的 换个换个",
    "聊点别的 这话题没劲",
    "嗯嗯 你继续",
)


def _random_range(sec: dict[str, Any]) -> float:
    # 兼容两种键名：{min,max}（typing_delay 等）和 {cycle_min_seconds,cycle_max_seconds}（poll）
    lo = float(sec.get("min", sec.get("cycle_min_seconds", 1)))
    hi = float(sec.get("max", sec.get("cycle_max_seconds", lo + 1)))
    if hi <= lo:
        return lo
    return random.uniform(lo, hi)


def _maybe_reload_config(
    cfg: dict[str, Any],
    ai: AIClient | None,
    config_path: str,
    *,
    no_ai: bool,
) -> tuple[dict[str, Any], AIClient | None]:
    """每轮重读一次配置文件，有变更就热生效，改白名单/频控不用重启。

    重读失败（JSON 写坏、白名单被清空等）时保留当前配置继续运行；
    ai 段有变更时重建 AI 客户端，重建失败则沿用旧客户端并告警。
    """
    try:
        fresh = load_config(config_path, require_ai=not no_ai)
    except Exception as exc:  # noqa: BLE001
        logger.warning("重读配置失败（继续用当前配置）：%s", exc)
        return cfg, ai
    if fresh == cfg:
        return cfg, ai
    changed = [k for k in sorted(set(fresh) | set(cfg)) if fresh.get(k) != cfg.get(k)]
    logger.info("检测到配置变更并热生效：%s", "、".join(changed))
    if not no_ai and fresh.get("ai") != cfg.get("ai"):
        try:
            ai = AIClient(fresh)
        except AIError as exc:
            logger.error("新的 AI 配置不可用（%s），继续沿用旧 AI 客户端", exc)
    return fresh, ai


async def _handle_open_panel_friend(
    *,
    chat: DouyinChat,
    cfg: dict[str, Any],
    friends: list[dict[str, Any]],
    store: StateStore,
    audit: AuditLogger,
    ai: AIClient | None,
    dry_run: bool,
) -> dict[str, Any] | None:
    """核验当前窗口绑定的 sec_uid 和抖音号，直接读取，不依赖红点或昵称。"""
    try:
        identity = await chat.current_panel_identity()
    except Exception as exc:  # noqa: BLE001
        if _is_crash_error(exc):
            raise
        return None
    if not identity:
        now = time.monotonic()
        if now - getattr(chat, "_binding_warning_at", 0) > 60:
            chat._binding_warning_at = now
            logger.warning("当前窗口联系人身份绑定未核实，本轮尚未读取该窗口消息；不能据此判断没有新消息")
        return None
    actual = str(identity.get("unique_id") or identity.get("short_id") or "").strip()
    sec = str(identity.get("sec_uid") or "").strip()
    matched: dict[str, Any] | None = None
    for friend in friends:
        if actual and sec and actual == str(friend.get("douyin_id") or "").strip() and sec == str(friend.get("sec_uid") or "").strip():
            matched = friend
            break
    if matched is None:
        return None
    name = str(matched.get("name") or "").strip()
    logger.debug("当前面板开在白名单好友「%s」身上，直接比对消息指纹", name)
    out = await handle_friend(
        chat=chat, cfg=cfg, friend=matched, store=store,
        audit=audit, ai=ai, dry_run=dry_run, current_only=True,
    )
    out["via_open_panel"] = True
    return out


async def _wait_monitoring_open_panel(*, chat, cfg, store, audit, ai, dry_run, stop_event, seconds):
    """Keep reading the verified open chat during the ordinary polling delay."""
    deadline = time.monotonic() + seconds
    next_check = time.monotonic()
    while time.monotonic() < deadline:
        if not await _wait(stop_event, min(2, max(0.01, deadline - time.monotonic()))):
            return False
        await interlude_drain(chat, cfg, store, audit, dry_run=dry_run)
        if time.monotonic() < next_check:
            continue
        out = None
        try:
            out = await _handle_open_panel_friend(chat=chat, cfg=cfg, friends=enabled_friends(cfg),
                store=store, audit=audit, ai=ai, dry_run=dry_run)
        except Exception as exc:
            if _is_crash_error(exc):
                raise
            logger.warning("检查当前会话失败，保留消息稍后重试：%s", type(exc).__name__)
            out = {"action": "error"}
        # Retry errors more slowly; normal checks do not change reply rate caps.
        next_check = time.monotonic() + (30 if out and out.get("action") == "error" else 5)
    return not stop_event.is_set()


async def process_friends(
    *,
    chat: DouyinChat,
    cfg: dict[str, Any],
    store: StateStore,
    audit: AuditLogger,
    ai: AIClient | None,
    dry_run: bool,
) -> dict[str, int]:
    """红点驱动：只处理“有未读红点”的白名单好友，不再把每个好友都点开一遍。

    0) 红点盲区兜底：右侧面板开在白名单好友身上时，对方再发消息会被立即标记
       已读、左侧永不亮红点——每轮零成本看一眼开着的面板，是白名单好友就直接
       比对消息指纹处理（不点击、不刷新）；
    1) 一次读取左侧会话列表，找出带未读红点的会话行（红点 = 有新消息）；
    2) 白名单好友按“聊天页显示的名字”匹配红点行，点开核验后处理；
    3) 没匹配上的未读行（群聊/陌生人/改了昵称的白名单）：默认不点开、不回复；
       如需防漏，可配置 whitelist.verify_unmatched_unread_interval_seconds(秒)
       开启低频抖音号核验（仍会核验身份，非白名单一律不读不回）。
    没有未读红点、面板也没开在白名单好友身上时，本轮不点开任何会话。
    """
    global _last_unread_fallback_ts
    friends = enabled_friends(cfg)
    random.shuffle(friends)
    stats = {"replied": 0, "dry": 0, "error": 0, "nothing": 0}

    def _tally(out: dict[str, Any]) -> None:
        key = out.get("action")
        if key == "replied":
            stats["replied"] += 1
        elif key == "dry_reply":
            stats["dry"] += 1
        elif key == "error":
            stats["error"] += 1
        else:
            stats["nothing"] += 1

    # An uncertain native submission remains recoverable even after the browser
    # has left that chat and every message has been marked as read.
    if interlude_enabled(cfg) and not dry_run and cfg["reply"].get("enabled", True):
        for friend in friends:
            state_key = str(friend.get("douyin_id") or "")
            pending = store.get(state_key, "interlude_submission", {})
            if not pending.get("payload"):
                continue
            try:
                await recover_pending(cfg, friend, store, state_key)
            except BridgeError as exc:
                logger.warning("恢复已读消息的提交失败，保留原消息：%s", type(exc).__name__)
                stats["error"] += 1

    # 0) 红点盲区兜底（见 _handle_open_panel_friend 的说明）
    try:
        open_out = await _handle_open_panel_friend(
            chat=chat, cfg=cfg, friends=friends, store=store,
            audit=audit, ai=ai, dry_run=dry_run,
        )
        if open_out is not None:
            _tally(open_out)
    except Exception as exc:  # noqa: BLE001
        if _is_crash_error(exc):
            raise  # 页面崩溃，交给主循环重启会话
        logger.warning("处理当前已开面板的白名单好友时出错: %s", exc)
        stats["error"] += 1

    try:
        convs = await chat.list_conversations()
    except Exception as exc:  # noqa: BLE001
        stats["error"] += 1
        logger.warning("读取会话列表失败: %s", exc)
        if _is_crash_error(exc):
            raise  # 页面已崩溃，交给主循环重启会话，而不是每轮空转
        return stats

    unread_rows = [c for c in convs if c.get("unread")]
    if not unread_rows:
        logger.info("会话列表无红点；继续核验当前窗口并检测新消息")
        return stats

    logger.info(
        "检测到 %d 个会话有未读红点：%s",
        len(unread_rows),
        "、".join(f"「{r.get('title') or '?'}」" for r in unread_rows),
    )

    exact = bool(cfg["whitelist"].get("exact_match", True))

    def _title_matches(row_title: str, want: str) -> bool:
        # 归一化空白：抖音标题里可能是普通空格/不断行空格，统一成普通空格再比
        row_title = " ".join((row_title or "").split())
        want = " ".join((want or "").split())
        if not want:
            return False
        return row_title == want if exact else want in row_title

    # 2) 白名单按名字匹配红点行（只记行号，点开前再取一次最新行，避免列表重排后点错行）
    assigned: set[int] = set()
    todo: list[tuple[dict[str, Any], int]] = []
    for friend in friends:
        name = str(friend.get("name") or "").strip()
        if not name:
            continue
        for row in unread_rows:
            if row["index"] in assigned:
                continue
            if not _title_matches(row.get("title") or "", name):
                continue
            assigned.add(row["index"])
            todo.append((friend, row["index"]))

    processed = 0
    for friend, row_index in todo:
        try:
            # 点开前按标题实时定位（带滚动查找）；找不到就跳过，绝不按旧行号盲点，
            # 避免列表重排后点开陌生人/群聊
            fname = str(friend.get("name") or "").strip()
            loc = await chat.row_by_title(fname, exact=exact) if fname else None
            if loc is None:
                logger.info("[%s] 会话行当前没找到（可能在视口外），本轮跳过", fname or friend.get("douyin_id") or "?")
                _tally({"action": "nothing"})
                continue
            out = await handle_friend(
                chat=chat, cfg=cfg, friend=friend, store=store,
                audit=audit, ai=ai, dry_run=dry_run, row=loc,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_crash_error(exc):
                raise  # 页面崩溃，交给主循环重启会话
            logger.exception(
                "[%s] 处理好友时出现未预期异常: %s",
                friend.get("name") or friend.get("douyin_id") or "?", exc,
            )
            out = {"action": "error", "reason": str(exc)}
        _tally(out)
        processed += 1
        if processed < len(todo):
            await asyncio.sleep(random.uniform(2, 6))

    # 3) 低频兜底（可选）：名字没匹配上的未读行，按抖音号核验是不是白名单。
    #    每行只点开一次、抓一次身份，再和白名单抖音号比对，避免反复点开陌生人会话。
    leftover = [r for r in unread_rows if r["index"] not in assigned]
    if leftover:
        id_only_friends = [friend for friend in friends if not str(friend.get("name") or "").strip()]
        interval = float(cfg["whitelist"].get("verify_unmatched_unread_interval_seconds", 0) or 0)
        # ID-only 白名单无法通过会话标题定位；默认每五分钟做一次只读身份核验。
        if interval <= 0 and id_only_friends:
            interval = 300
        now = time.monotonic()
        if interval > 0 and now - _last_unread_fallback_ts >= interval:
            _last_unread_fallback_ts = now
            logger.info("对 %d 个未匹配未读会话做低频抖音号核验……", len(leftover))
            for row in leftover:
                try:
                    loc = await chat.row_at(row["index"])
                    identity = await chat.identify_row(loc, label=str(row.get("title") or ""))
                except Exception as exc:  # noqa: BLE001
                    if _is_crash_error(exc):
                        raise
                    logger.warning("核验未匹配会话行失败: %s", exc)
                    continue
                actual = str((identity or {}).get("unique_id") or (identity or {}).get("short_id") or "").strip()
                matched: dict[str, Any] | None = None
                if actual:
                    for friend in friends:
                        fid = str(friend.get("douyin_id") or "").strip()
                        if fid and fid == actual:
                            matched = friend
                            break
                if matched is None:
                    await asyncio.sleep(random.uniform(1, 3))
                    continue
                try:
                    out = await handle_friend(
                        chat=chat, cfg=cfg, friend=matched, store=store,
                        audit=audit, ai=ai, dry_run=dry_run, row=loc,
                    )
                except Exception as exc:  # noqa: BLE001
                    if _is_crash_error(exc):
                        raise
                    logger.exception("兜底核验处理异常: %s", exc)
                    out = {"action": "error", "reason": str(exc)}
                _tally(out)
                await asyncio.sleep(random.uniform(1, 3))
    return stats


async def check_login(chat: DouyinChat) -> None:
    for attempt in (1, 2):
        # settled：聊天页冷启动要 ~10 秒才渲染出登录标记，unknown 只说明还没渲染完
        state = await chat.login_state_settled()
        if state == "ok":
            return
        if state == "risk":
            raise LoginExpired("页面出现安全验证，机器人已停止（请人工处理后再启动）")
        # unknown / login_required：可能是页面还没加载完，刷新一次再判断一次
        if attempt == 1:
            try:
                await chat.page.reload(wait_until="domcontentloaded")
                await chat.page.wait_for_timeout(3000)
            except Exception:
                pass
    raise LoginExpired("登录态失效：请重新导出抖音 Cookie 更新 state/cookies.json 后重启机器人")


async def _open_chat_until_ok(
    session: BrowserSession,
    cfg: dict[str, Any],
    stop_event: asyncio.Event,
    *,
    restart: bool = False,
    max_attempts: int | None = None,
) -> DouyinChat:
    """打开聊天页并确认登录，失败后固定等 60 秒重建会话再试。

    网络或 ssh 隧道断开时 goto 会一直超时，这里不让机器人退出：
    失败后重建浏览器会话再试，隧道恢复后自动继续。
    登录失效（LoginExpired/LoginError）不在重试范围，照常抛出。
    """
    attempt = 0
    do_restart = restart
    while True:
        try:
            if do_restart:
                try:
                    await session.stop()
                except Exception:  # noqa: BLE001
                    pass
            await session.start()
            chat = DouyinChat(
                session.page,
                timeout_ms=int(cfg["account"].get("page_timeout_ms", 15000)),
                owner_id=owner_identity(cfg)[1],
            )
            await chat.goto_chat()
            await check_login(chat)
            if attempt:
                logger.info("已恢复：成功打开抖音聊天页（此前失败 %d 次）", attempt)
            return chat
        except (LoginExpired, LoginError):
            raise
        except Exception as exc:  # noqa: BLE001
            attempt += 1
            if max_attempts is not None and attempt >= max_attempts:
                raise
            do_restart = True
            wait_seconds = 60  # 失败后固定等 60 秒重建会话重试（不递增，隧道一恢复最多 1 分多钟就能接上）
            msg = (str(exc) or repr(exc)).splitlines()[0]
            if "launch_persistent_context" in msg or "Failed to launch" in msg:
                hint = "浏览器进程启动失败（多半内存不足被系统杀掉，内存空闲后会自动恢复）"
            elif "PROXY" in msg or "NS_ERROR" in msg:
                hint = "代理隧道断开（手机端网络恢复后会自动继续）"
            else:
                hint = "网络或代理隧道可能断开"
            logger.warning(
                "打开抖音聊天页失败（第 %d 次）：%s —— %s，%d 秒后重建会话重试",
                attempt, msg, hint, wait_seconds,
            )
            if not await _wait(stop_event, wait_seconds):
                raise


async def run_forever(
    cfg: dict[str, Any],
    *,
    config_path: str | None = None,
    once: bool = False,
    dry_run: bool = False,
    no_ai: bool = False,
    stop_event: asyncio.Event | None = None,
) -> int:
    stop_event = stop_event or asyncio.Event()
    audit = AuditLogger(cfg["log"]["audit_file"])

    if not has_any_login_material(cfg):
        logger.error("还没有登录凭证：请先看 README 的「登录抖音」章节，准备好 cookies.json 或先运行 --login-qr")
        return 2

    ai: AIClient | None = None
    if not no_ai:
        try:
            ai = AIClient(cfg)
        except AIError as exc:
            if dry_run:
                logger.warning("AI 配置暂不可用（%s），dry-run 将只提示需要回复、不生成内容", exc)
            else:
                logger.error("AI 配置有问题：%s", exc)
                return 2

    store = StateStore("state/replied_state.json")
    session = BrowserSession(cfg)
    def attach_panel_monitor(target):
        async def before_leave():
            await _handle_open_panel_friend(chat=target, cfg=cfg, friends=enabled_friends(cfg),
                store=store, audit=audit, ai=ai, dry_run=dry_run)
        target.before_panel_leave = before_leave
    try:
        # --once 是交互式单轮模式，失败快速退出；持续模式隧道断开时无限退避重试
        chat = await _open_chat_until_ok(session, cfg, stop_event, max_attempts=1 if once else None)
        attach_panel_monitor(chat)
        logger.info("当前会话消息监视已启用：不依赖红点，切换和刷新前检查新消息")
        logger.info("已登录抖音网页版，开始监视白名单好友……")
        warmup = int(cfg["poll"].get("startup_warmup_seconds", 0))
        if warmup > 0:
            logger.info("启动预热 %s 秒……", warmup)
            if not await _wait(stop_event, warmup):
                logger.info("收到停止信号，正在退出……")
                return 0

        cycle = 0
        consecutive_errors = 0
        while True:
            cycle += 1
            if config_path and not once:
                cfg, ai = _maybe_reload_config(cfg, ai, config_path, no_ai=no_ai)
            crashed = False
            stats = {"replied": 0, "dry": 0, "error": 0, "nothing": 0}
            try:
                if not await _page_alive(session.page):
                    raise _PageCrash("页面已崩溃或已关闭")
                if cycle % int(cfg["poll"].get("reload_every_cycles", 8)) == 0:
                    try:
                        # 和 goto_chat 一样给 2 倍超时：页面流量走家庭隧道，30 秒默认值偶尔不够
                        reload_timeout = int(cfg["account"].get("page_timeout_ms", 15000)) * 2
                        await chat.flush_current_panel()
                        await session.page.reload(wait_until="domcontentloaded", timeout=reload_timeout)
                        await chat.page.wait_for_timeout(2000)
                        await check_login(chat)
                    except LoginExpired:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        if _is_crash_error(exc):
                            raise _PageCrash(str(exc)) from exc
                        logger.warning("定时刷新页面失败：%s", exc)

                stats = await process_friends(
                    chat=chat, cfg=cfg, store=store, audit=audit, ai=ai, dry_run=dry_run,
                )
                await interlude_drain(chat, cfg, store, audit, dry_run=dry_run, force=True)
                if stats.get("error", 0) > 0:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0
                logger.info(
                    "第 %d 轮完成：已回复 %d，dry-run %d，跳过/无事 %d，出错 %d",
                    cycle, stats["replied"], stats["dry"], stats["nothing"], stats["error"],
                )
            except LoginExpired:
                raise
            except _PageCrash:
                crashed = True
                stats = {"replied": 0, "dry": 0, "error": 1, "nothing": 0}
                consecutive_errors += 1
                logger.warning("第 %d 轮检测到页面崩溃，将在本轮末重启浏览器会话", cycle)
            except Exception as exc:  # noqa: BLE001
                if _is_crash_error(exc):
                    crashed = True
                    consecutive_errors += 1
                    logger.warning("第 %d 轮出现页面级错误：%s", cycle, exc)
                else:
                    logger.exception("第 %d 轮处理出错：%s", cycle, exc)
                    consecutive_errors += 1
                    stats = {"replied": 0, "dry": 0, "error": 1, "nothing": 0}
                    try:
                        saved = await chat.save_failure_artifacts(cfg["log"]["screenshot_dir"], f"cycle{cycle}")
                        if saved:
                            logger.warning("已保存故障现场：%s", ", ".join(saved))
                    except Exception:
                        pass

            if once:
                logger.info("单轮模式（--once）结束。")
                return 1 if crashed else 0

            if crashed:
                # 崩溃恢复：关掉旧会话重开（隧道断开时助手会带退避重试，不再让机器人退出）
                logger.info("正在重启浏览器会话……")
                chat = await _open_chat_until_ok(session, cfg, stop_event, restart=True)
                attach_panel_monitor(chat)
                logger.info("浏览器会话已重启，继续监视")
                wait_seconds = 15  # 崩溃后快速重试
            else:
                # 出错后多休息一会儿，避免反复撞同一个错误/反复调 AI
                extra = 180 if stats.get("error", 0) > 0 else 0
                wait_seconds = _random_range(cfg["poll"]) + extra
            logger.debug("休息 %.0f 秒后进入下一轮……", wait_seconds)
            try:
                keep_running = await _wait_monitoring_open_panel(
                    chat=chat, cfg=cfg, store=store, audit=audit, ai=ai, dry_run=dry_run,
                    stop_event=stop_event, seconds=wait_seconds,
                )
                if not keep_running:
                    logger.info("收到停止信号，正在退出……")
                    return 0
            except Exception as exc:
                if _is_crash_error(exc):
                    logger.warning("检查当前会话时浏览器已关闭，正在重建会话")
                    chat = await _open_chat_until_ok(session, cfg, stop_event, restart=True)
                    attach_panel_monitor(chat)
                else:
                    logger.warning("当前会话监视出现错误：%s", type(exc).__name__)

            # 连续失败保护：连续多轮出错就长暂停，避免被风控或反复撞同一错误
            risk_cfg = cfg["risk"]
            pause_after = int(risk_cfg.get("consecutive_failures_to_pause", 0))
            if pause_after > 0 and consecutive_errors >= pause_after:
                pause_minutes = int(risk_cfg.get("pause_minutes_on_captcha", 30)) or 30
                logger.warning("已连续 %d 轮出错，暂停 %d 分钟再继续……", consecutive_errors, pause_minutes)
                if not await _wait(stop_event, pause_minutes * 60):
                    logger.info("收到停止信号，正在退出……")
                    return 0
                consecutive_errors = 0

            if not crashed and cycle % int(risk_cfg.get("recheck_login_every_cycles", 12)) == 0:
                try:
                    await check_login(chat)
                except LoginExpired as exc:
                    logger.error("登录态复查失败：%s", exc)
                    return 3
    except LoginExpired as exc:
        logger.error("%s", exc)
        return 3
    except LoginError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("机器人异常退出：%s", exc)
        try:
            if session.page is not None:
                saved = await DouyinChat(session.page).save_failure_artifacts(
                    cfg["log"]["screenshot_dir"], "fatal"
                )
                if saved:
                    logger.warning("已保存故障现场：%s", ", ".join(saved))
        except Exception:
            pass
        return 1
    finally:
        await session.stop()

