"""Local HDSI transport. Browser operations remain in the existing runner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from config_loader import enabled_friends
from reply_engine import _within_rate_limit, message_kind, message_fingerprint
from state_store import msg_fingerprint
from conversation_context import with_conversation_context, outgoing_snapshot, manual_outgoing_changed, context_settings

logger = logging.getLogger("douyin.interlude")
# Queue polling is frequent; retain HTTP errors without one INFO line per poll.
logging.getLogger("httpx").setLevel(logging.WARNING)
_last_drain = 0.0


class BridgeError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def enabled(cfg):
    return cfg.get("interlude", {}).get("enabled") is True


def _request(cfg, method, path, body=None):
    try:
        settings = json.loads(Path(cfg["interlude"]["connection_file"]).read_text(encoding="utf-8"))
        url = "http://127.0.0.1:%d%s" % (settings.get("port", 18765), path)
        with httpx.Client(timeout=35, trust_env=False) as client:
            response = client.request(method, url, json=body,
                headers={"Authorization": "Bearer " + settings["token"]})
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise BridgeError("幕间桥接请求失败 (%s)" % type(exc).__name__,
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None) from exc


async def native_vision_enabled(cfg):
    return bool((await asyncio.to_thread(_request, cfg, "GET", "/health")).get("visionEnabled"))


async def recover_pending(cfg, friend, store, state_key):
    """Replay an uncertain POST with the original event/body before reading new input."""
    pending = store.get(state_key, "interlude_submission", {})
    payload = pending.get("payload")
    if not payload or store.get(state_key, "last_replied_fp") == pending.get("fingerprint"):
        return
    if payload.get("senderId") != str(friend["sec_uid"]):
        raise BridgeError("待重试消息的联系人身份不一致")
    result = await asyncio.to_thread(_request, cfg, "POST", "/inbound", payload)
    if result.get("status") not in ("accepted", "unknown", "submitting"):
        raise BridgeError("此前消息尚未被幕间系统接收，保留原消息待重试")
    store.update(state_key, **pending["acknowledged_state"], interlude_profile_submitted=True,
                 interlude_last_accepted_at=datetime.now().astimezone().isoformat(),
                 interlude_event_id=pending["eventId"],
                 interlude_submission={k:v for k,v in pending.items() if k not in ("payload", "acknowledged_state")})


async def submit(cfg, friend, store, state_key, decision, content_override=None, image_sources=None, messages=None):
    state = store.friend_state(state_key)
    # Stable across HTTP timeout/restart; only a new observed message gets a new id.
    pending = state.get("interlude_submission", {})
    if pending.get("fingerprint") != decision.fingerprint or state.get("last_replied_fp") == decision.fingerprint:
        pending = {"eventId": uuid.uuid4().hex, "fingerprint": decision.fingerprint,
            "occurredAt": datetime.now().astimezone().isoformat()}
        store.set(state_key, "interlude_submission", pending)
    content = content_override or decision.incoming_text
    settings = context_settings(cfg)
    if messages is not None and settings["enabled"]:
        content = with_conversation_context(content, messages, decision.selected_messages,
                                            max_items=settings["history_messages"])
    notes = cfg.get("prompts", {}).get("friend_notes", {})
    note = str(notes.get(friend.get("name"), "") or "").strip()
    # Background knowledge is explicitly labeled; it is not fabricated dialogue.
    if note and not state.get("interlude_profile_submitted"):
        content = "[管理员提供的该参与者背景备注：%s]\n[当前用户消息]\n%s" % (note, content)
    payload = {
        "eventId": pending["eventId"], "senderId": str(friend["sec_uid"]),
        "senderName": friend["name"], "content": content, "occurredAt": pending["occurredAt"],
        "imageSources": image_sources or [],
    }
    acknowledged = decision.acknowledged_state(state)
    if messages is not None:
        acknowledged.update(interlude_outgoing_snapshot=outgoing_snapshot(messages),
                            interlude_automated_since_submission=[])
    pending.update(payload=payload, acknowledged_state=acknowledged)
    store.set(state_key, "interlude_submission", pending)
    result = await asyncio.to_thread(_request, cfg, "POST", "/inbound", payload)
    if result.get("status") not in ("accepted", "unknown", "submitting"):
        raise BridgeError("幕间系统未接收消息，保留待处理状态")
    store.update(state_key, **pending["acknowledged_state"], interlude_profile_submitted=True,
        interlude_last_accepted_at=datetime.now().astimezone().isoformat(),
        interlude_event_id=pending["eventId"],
        interlude_submission={k:v for k,v in pending.items() if k not in ("payload", "acknowledged_state")})
    logger.info("幕间系统已接收消息（生成和发送由后台队列处理）")
    return result


async def _receipt(cfg, store, key, delivery_id, status, error=""):
    # Write before HTTP: if the response is lost, only the ACK is retried, never send_text.
    receipt = {"deliveryId": delivery_id, "status": status, "error": error}
    receipts = dict(store.get(key, "interlude_receipts", {}) or {})
    receipts[delivery_id] = receipt
    store.set(key, "interlude_receipts", receipts)
    try:
        await asyncio.to_thread(_request, cfg, "POST", "/result", receipt)
    except BridgeError as exc:
        if exc.status_code not in (404, 409):
            raise
        logger.warning("幕间发送回执已失去关联，保留本地发送记录供核查：%s", delivery_id)
    receipts.pop(delivery_id, None)
    store.set(key, "interlude_receipts", receipts)


async def drain(chat, cfg, store, audit, dry_run=False, force=False):
    global _last_drain
    if not enabled(cfg) or dry_run or not cfg.get("reply", {}).get("enabled", True):
        return
    if not force and time.monotonic() - _last_drain < 2:
        return
    _last_drain = time.monotonic()
    try:
        # Recover lost ACKs without repeating browser sends.
        for key, state in store.snapshot()["friends"].items():
            for receipt in list((state.get("interlude_receipts") or {}).values()):
                await _receipt(cfg, store, key, receipt["deliveryId"], receipt["status"], receipt.get("error", ""))
        deliveries = (await asyncio.to_thread(_request, cfg, "GET", "/outbox")).get("deliveries", [])
    except BridgeError as exc:
        logger.warning("%s", exc)
        return
    friends = {str(f["sec_uid"]): f for f in enabled_friends(cfg)}
    for item in deliveries:
        friend = friends.get(item.get("senderId"))
        if not friend:
            continue
        key = str(friend["douyin_id"])
        delivery_id = item["deliveryId"]
        if item.get("expiresAt", 0) - time.time() * 1000 < 15000:
            try:
                await _receipt(cfg, store, key, delivery_id, "permanent-failed", "Delivery too close to deadline")
            except BridgeError:
                pass
            continue
        ok, reason = _within_rate_limit(store.friend_state(key), cfg["reply"], datetime.now().astimezone())
        if not ok:
            try:
                await _receipt(cfg, store, key, delivery_id, "permanent-failed", reason)
            except BridgeError:
                pass
            continue
        claimed = False
        attempted = False
        try:
            # All three original identity checks execute on every delivery.
            await chat.open_friend(friend, exact=bool(cfg["whitelist"].get("exact_match", True)))
            messages = await chat.read_messages(limit=200)
            if not messages:
                await _receipt(cfg, store, key, delivery_id, "permanent-failed", "Conversation unavailable before delivery")
                continue
            if context_settings(cfg)["cancel_on_manual"] and manual_outgoing_changed(messages,
                    store.get(key, "interlude_outgoing_snapshot", None),
                    store.get(key, "interlude_automated_since_submission", [])):
                await _receipt(cfg, store, key, delivery_id, "permanent-failed", "Account owner sent a message after generation started")
                audit.log({"event": "interlude_manual_takeover", "friend": friend["name"], "delivery_id": delivery_id,
                    "reason": "检测到本账号在生成期间发送了新消息，旧回复已取消；等待对方下一条消息后重新生成。"})
                logger.info("账号在生成期间有新的发送记录，已撤销幕间旧回复，等待对方新消息")
                continue
            if messages and not messages[0].get("from_me"):
                latest = messages[0]
                expected = store.get(key, "interlude_last_incoming_fp", "")
                kind = message_kind(latest)
                current = message_fingerprint(latest)
                if current != expected:
                    await _receipt(cfg, store, key, delivery_id, "permanent-failed", "New incoming message superseded this delivery")
                    continue
            await asyncio.to_thread(_request, cfg, "POST", "/claim", {"deliveryId": delivery_id})
            claimed = True
            # A persisted claim/attempt is never replayed after restart.
            store.set(key, "interlude_last_attempt", delivery_id)
            attempted = True
            await chat.send_text(item["content"])
            now = datetime.now().astimezone().isoformat()
            events = list(store.get(key, "replied_at", []) or []) + [now]
            day_events = list(store.get(key, "replied_at_day", []) or []) + [now]
            store.update(key, last_replied_at=now, last_reply_text=item["content"][:200],
                replied_at=events[-100:], replied_at_day=day_events[-1000:], interlude_last_sent=delivery_id,
                interlude_automated_since_submission=(list(store.get(key, "interlude_automated_since_submission", [])) + [item["content"]])[-100:])
            audit.log({"event": "interlude_replied", "friend": friend["name"],
                "delivery_id": delivery_id, "reply": item["content"], "verified_sec_uid": True})
            await _receipt(cfg, store, key, delivery_id, "sent")
            logger.info("幕间消息已发送并回传回执")
        except Exception as exc:
            # If sending may have happened, keep the claim for manual inspection.
            # Never turn an ambiguous browser failure into an automatic duplicate.
            if claimed and attempted:
                if store.get(key, "interlude_last_sent") != delivery_id:
                    audit.log({"event": "interlude_delivery_unknown", "delivery_id": delivery_id,
                        "reason": type(exc).__name__})
                logger.warning("幕间投递结果待核实：%s", type(exc).__name__)
            else:
                try:
                    await _receipt(cfg, store, key, delivery_id, "permanent-failed", "Recipient verification or claim failed")
                except BridgeError:
                    pass
                logger.warning("幕间投递未通过身份核验或队列领取：%s", type(exc).__name__)
