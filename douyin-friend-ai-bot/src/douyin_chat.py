# -*- coding: utf-8 -*-
"""抖音网页版私信页的浏览器操作封装（基于 Playwright）。

所有“怎么点页面”的细节都在这一个文件里，选择器集中在 page_selectors.py。
抖音改版导致失效时，优先更新 page_selectors.py，必要时再改这里。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from playwright.async_api import Locator, Page

from page_selectors import (
    CHAT_OPEN_MARKERS,
    CHAT_URL,
    CONVERSATION_ITEM,
    CONVERSATION_ITEM_FALLBACKS,
    CONVERSATION_TITLES,
    CONVERSATION_UNREAD_BADGE,
    FROM_ME_MARKER,
    LOGIN_MARKERS,
    LOGIN_REQUIRED_MARKERS,
    MESSAGE_BOXES,
    MESSAGE_CONTENT,
    MESSAGE_INPUTS,
    MESSAGE_LIST,
    RISK_MARKERS,
    SEARCH_INPUTS,
    SEARCH_RESULT_ITEMS,
    SEND_BUTTONS,
    SEND_FAILURE_MARKERS,
)

logger = logging.getLogger("douyin.chat")

# 只给 douyin_id、没给名字时的逐行核对范围：只扫列表前若干行，够覆盖“刚发来新消息”的好友
_SCAN_ROW_LIMIT = 40


class DouyinPageError(RuntimeError):
    pass


class FriendNotFound(DouyinPageError):
    """会话列表/搜索里找不到这位白名单好友（可能对方还没发过私信）。"""


class FriendMismatch(DouyinPageError):
    """点开会话后抖音号核验不匹配；按安全规则不读取、不回复。"""



async def first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15000) -> Locator:
    per = max(400, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=per)
            return locator
        except Exception:
            continue
    raise DouyinPageError(f"页面上找不到可用元素，已依次尝试: {', '.join(selectors)}")


async def _any_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 2500) -> bool:
    per = max(300, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible():
                return True
            await locator.wait_for(state="visible", timeout=per)
            return True
        except Exception:
            continue
    return False


# 消息正文开头可能出现的时间/日期分隔行（抖音会把它们插在气泡上方），去掉以免污染正文
_TIME_LABEL_RE = re.compile(
    r"^(刚刚|昨天|今天|\d+分钟前|\d+小时前|\d+天前|"
    r"\d{1,2}:\d{2}|\d{2}[/-]\d{2}([/-]\d{2,4})?|\d{1,2}月\d{1,2}日)$"
)


def _clean_message_text(raw: object) -> str:
    lines = [ln.strip() for ln in str(raw or "").splitlines()]
    while lines and _TIME_LABEL_RE.match(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _canon_user(node: dict[str, Any]) -> dict[str, Any]:
    """把不同版本/命名风格（蛇形/驼峰）的用户对象统一成固定键。"""
    def pick(*names: str) -> Any:
        for n in names:
            value = node.get(n)
            if value not in (None, ""):
                return value
        return ""
    return {
        "uid": pick("uid", "user_id", "userId"),
        "sec_uid": pick("sec_uid", "secUid", "sec_user_id"),
        "unique_id": pick("unique_id", "uniqueId"),
        "short_id": pick("short_id", "shortId"),
        "nickname": pick("nickname", "nickName"),
    }


# 从右侧标题组件的 React 内部数据里提取“当前会话对方”的用户对象。
# 新版聊天界面点开会话不再触发 user/info 接口（监听拿不到身份），
# 但标题组件渲染用的就是对方的用户对象——从这里读天然绑定当前会话，
# 改名冒充者也骗不过（对象里带真实抖音号）。
_EXTRACT_PARTNER_JS = """(sel) => {
    const el = document.querySelector(sel);
    if (!el) return {users: [], selfSecUid: ''};
    const fk = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    if (!fk) return {users: [], selfSecUid: ''};
    const propsList = [];
    let fiber = el[fk];
    for (let i = 0; i < 30 && fiber; i++) {
        try {
            const p = fiber.memoizedProps;
            if (p && typeof p === 'object') propsList.push(p);
        } catch (e) {}
        fiber = fiber.return;
    }
    const found = [];
    const walk = (v, d) => {
        if (!v || typeof v !== 'object' || d > 4 || found.length > 8) return;
        if (Array.isArray(v)) { v.forEach(x => walk(x, d + 1)); return; }
        const keys = Object.keys(v).map(k => k.toLowerCase());
        const hasSec = keys.some(k => k === 'sec_uid' || k === 'secuid' || k === 'sec_user_id');
        const hasId = keys.some(k => ['unique_id','uniqueid','short_id','shortid','nickname'].includes(k));
        if (hasSec && hasId) { found.push(v); return; }
        for (const k of Object.keys(v)) { try { walk(v[k], d + 1); } catch (e) {} }
    };
    for (const p of propsList) walk(p, 0);
    let selfSecUid = '';
    try {
        const s = window.userInfoStore && window.userInfoStore.curLoginUserInfo;
        if (s) selfSecUid = String(s.sec_uid || s.secUid || '');
    } catch (e) {}
    return {
        users: found.map(u => ({
            nickname: u.nickname || u.nickName || '',
            unique_id: u.unique_id || u.uniqueId || '',
            short_id: u.short_id || u.shortId || '',
            sec_uid: String(u.sec_uid || u.secUid || ''),
        })),
        selfSecUid: selfSecUid,
    };
}"""


def _extract_user_payload(payload: Any) -> dict[str, Any] | None:
    """从 user/info 接口返回里递归找出“会话对方”的用户对象。

    兼容新旧两种字段命名（蛇形 sec_uid / 驼峰 secUid）：
    2026-09 新版接口（version_code=170400）字段名可能改成驼峰，
    旧版只认蛇形导致解析失败、身份核验永远过不了。
    """
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            keys = {str(k).lower() for k in node.keys()}
            has_sec = bool(keys & {"sec_uid", "secuid", "sec_user_id"})
            has_id = bool(keys & {"unique_id", "uniqueid", "short_id", "shortid", "nickname"})
            if has_sec and has_id:
                found.append(_canon_user(node))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found[-1] if found else None


class DouyinChat:
    def __init__(self, page: Page, timeout_ms: int = 15000, owner_id: str = "") -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        # 登录者本人的抖音号（可选）：用于识别“抖音误把本人当对方返回”
        self.owner_id = str(owner_id or "").strip()
        self._user_info_events: deque[dict[str, Any]] = deque(maxlen=1024)
        # A monotonic watermark must not depend on the bounded cache's length.
        self._user_info_event_count = 0
        self.before_panel_leave = None
        self._flushing_panel = False
        self._install_user_info_hook()

    def _install_user_info_hook(self) -> None:
        """监听抖音 user/info 接口，捕获“当前打开会话”的对方身份（抖音号等）。"""
        extract_failures = 0

        async def _on_response(resp: Any) -> None:
            nonlocal extract_failures
            try:
                url = resp.url or ""
                if "/aweme/v1/web/im/user/info/" not in url:
                    return
                if resp.status != 200:
                    return
                body = await resp.text()
                data = json.loads(body)
                user = _extract_user_payload(data)
                if user:
                    self._user_info_events.append(user)
                    self._user_info_event_count += 1
                else:
                    # 解析不出来时留一次现场键名，方便下次抖音改字段名时定位
                    extract_failures += 1
                    if extract_failures == 1:
                        keys = list(data)[:12] if isinstance(data, dict) else type(data).__name__
                        logger.warning("user/info 响应解析不出身份，顶层键: %s", keys)
            except Exception:  # noqa: BLE001 监听失败只影响身份核验，不影响主流程
                pass

        self.page.on("response", _on_response)

    # ── 登录状态 ──────────────────────────────────────────────
    async def login_state(self) -> str:
        try:
            await self.page.wait_for_timeout(800)
        except Exception:
            return "unknown"
        if await _any_visible(self.page, RISK_MARKERS, 1500):
            return "risk"
        if await _any_visible(self.page, LOGIN_MARKERS, 1500):
            return "ok"
        if await _any_visible(self.page, LOGIN_REQUIRED_MARKERS, 1500):
            return "login_required"
        # 兜底：会话列表/搜索框出现就算已登录
        try:
            count = await self.page.locator(CONVERSATION_ITEM).count()
            if count == 0:
                count = await self.page.locator(CONVERSATION_ITEM_FALLBACKS[0]).count()
            if count > 0:
                return "ok"
        except Exception:
            pass
        return "unknown"

    async def login_state_settled(self, max_wait_ms: int = 40000) -> str:
        """等聊天页渲染完成后再判定登录态。

        抖音聊天页是重前端页面，冷启动要 10 秒上下才渲染出「私信」等标记；
        单次探测会在渲染完成前得到 unknown（误报）。unknown 时继续等，
        ok / login_required / risk 是明确结论，立即返回。
        """
        deadline = asyncio.get_running_loop().time() + max_wait_ms / 1000
        state = "unknown"
        while True:
            state = await self.login_state()
            if state != "unknown":
                return state
            if asyncio.get_running_loop().time() >= deadline:
                return state
            await self.page.wait_for_timeout(3000)

    async def goto_chat(self) -> None:
        await self.page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=self.timeout_ms * 2)
        await self.page.wait_for_timeout(2000)

    # ── 会话列表 ──────────────────────────────────────────────
    async def list_conversations(self) -> list[dict[str, Any]]:
        """返回左侧会话列表：[{index, title, row_text, unread, unread_count}]

        unread=True 表示该会话行有未读红点（新消息）；unread_count 为红点上的数字。
        """
        row_selectors = [CONVERSATION_ITEM, *CONVERSATION_ITEM_FALLBACKS]
        badge_sel = ", ".join(CONVERSATION_UNREAD_BADGE)
        result = await self.page.evaluate(
            r"""([rowSelectors, titleSelectors, badgeSel]) => {
                // 逐个候选选择器尝试，取第一个“有可见行”的；不能做并集，否则行内子元素会被当成会话
                let rows = [];
                for (const sel of rowSelectors) {
                    const cand = [...document.querySelectorAll(sel)];
                    const visible = cand.filter(row => row.offsetParent !== null);
                    if (visible.length) { rows = visible; break; }
                }
                const out = [];
                rows.forEach((row, index) => {
                    const box = row.getBoundingClientRect();
                    if (box.width === 0 && box.height === 0) return;
                    let title = "";
                    for (const sel of titleSelectors) {
                        const el = row.querySelector(sel);
                        if (el && el.innerText && el.innerText.trim()) {
                            title = el.innerText.trim().split("\n")[0].trim();
                            break;
                        }
                    }
                    if (!title) {
                        const lines = (row.innerText || "").split("\n").map(s => s.trim()).filter(Boolean);
                        title = lines[0] || "";
                    }
                    let unread = false;
                    let unread_count = 0;
                    if (badgeSel) {
                        const badge = row.querySelector(badgeSel);
                        if (badge) {
                            unread = true;
                            const m = (badge.innerText || "").trim().match(/\d+/);
                            if (m) unread_count = parseInt(m[0], 10);
                        }
                    }
                    out.push({index, title, row_text: (row.innerText || "").trim(), unread, unread_count});
                });
                return out;
            }""",
            [row_selectors, list(CONVERSATION_TITLES), badge_sel],
        )
        return result or []

    async def _open_row(self, row_locator: Locator) -> None:
        await self.flush_current_panel()
        try:
            await row_locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await row_locator.click(timeout=5000)
        except Exception as exc:
            # 统一包成 DouyinPageError，调用方按“会话打不开”处理而不是整轮崩溃
            raise DouyinPageError(f"点击会话行失败: {exc}") from exc

    async def flush_current_panel(self) -> None:
        """Let the runner consume already-read messages before leaving this panel."""
        if self.before_panel_leave is None or self._flushing_panel:
            return
        self._flushing_panel = True
        try:
            await self.before_panel_leave()
        finally:
            self._flushing_panel = False

    async def open_conversation(self, name: str, exact: bool = True) -> bool:
        """在左侧会话列表里按标题精确点开某个会话；找不到返回 False。"""
        rows = self.page.locator(CONVERSATION_ITEM)
        if await rows.count() == 0:
            rows = self.page.locator(CONVERSATION_ITEM_FALLBACKS[0])
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1000
        while True:
            for index in range(await rows.count()):
                row = rows.nth(index)
                try:
                    if not await row.is_visible():
                        continue
                    text = (await row.inner_text()).strip()
                    first_line = text.splitlines()[0].strip() if text else ""
                except Exception:
                    continue
                # 行内文字 = 名字 + 最近一条消息预览，精确匹配时只比第一行（名字）
                hit = (first_line == name) if exact else (name in text)
                if hit:
                    await self._open_row(row)
                    return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await self.page.wait_for_timeout(800)

    async def _search_and_open(self, name: str) -> bool:
        """用聊天页搜索框找人并点“发消息”，兼容新版聊天页。"""
        await self.flush_current_panel()
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        try:
            await search.fill("")
        except Exception:
            await search.click()
        await self.page.wait_for_timeout(400)
        await search.fill(name)
        await self.page.wait_for_timeout(1600)

        items_sel = ",".join(SEARCH_RESULT_ITEMS)
        item_count = await self.page.locator(items_sel).count()
        for index in range(min(item_count, 8)):
            item = self.page.locator(items_sel).nth(index)
            try:
                if not await item.is_visible():
                    continue
                text = (await item.inner_text()).strip()
                if name not in text:
                    continue
                # 优先点“发消息 / 发私信”按钮
                for btn_text in ("发消息", "发私信"):
                    btn = item.locator(f"text={btn_text}").first
                    try:
                        if await btn.count() and await btn.is_visible():
                            await btn.click(timeout=3000)
                            return True
                    except Exception:
                        continue
                await item.click(timeout=3000)
                return True
            except Exception:
                continue
        return False

    async def _row_participant_sec_uid(self, row: Locator) -> str:
        """读取会话行 React 状态中与该行绑定的对方 sec_uid；不确定则返回空。"""
        values = await row.evaluate(r"""el => {
            const fiberKey = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
            if (!fiberKey) return [];
            const found = new Set(), seen = new Set(); let fiber = el[fiberKey];
            const walk = (value, depth) => {
                if (!value || typeof value !== 'object' || depth > 5 || seen.has(value) || found.size > 1) return;
                seen.add(value);
                const conversation = value.conversation;
                if (conversation && typeof conversation === 'object') {
                    const id = String(conversation.toParticipantSecUserId || conversation._toParticipantSecUserId || '').trim();
                    if (id) found.add(id);
                }
                for (const key of Object.keys(value)) { try { walk(value[key], depth + 1); } catch (_) {} }
            };
            for (let i = 0; i < 15 && fiber; i += 1, fiber = fiber.return) {
                try { walk(fiber.memoizedProps, 0); } catch (_) {}
            }
            return [...found];
        }""")
        return str(values[0]).strip() if isinstance(values, list) and len(values) == 1 else ""

    async def open_friend(
        self, friend: dict[str, Any], exact: bool = True, row: Locator | None = None,
        current_only: bool = False,
    ) -> dict[str, Any]:
        """点开白名单好友会话，并用对方抖音号(douyin_id)核验身份。

        返回核验通过的对方身份 {uid, sec_uid, unique_id, short_id, nickname}。
        - 列表里找不到会话 → FriendNotFound（调用方可当作“无事发生”）
        - 点开后抖音号对不上 → FriendMismatch（绝不读取/回复）

        name 只是“定位会话用的显示名”；身份一律以接口返回的抖音号为准，
        名字/备注改了、或没填名字时，会按抖音号逐个核验最近几个会话来找。
        """
        name = str(friend.get("name") or "").strip()
        douyin_id = str(friend.get("douyin_id") or "").strip()
        sec_uid = str(friend.get("sec_uid") or "").strip()
        label = douyin_id or name or "?"

        # An already-open chat needs no click or new user/info response. Identity
        # must be bound to the current panel, never inferred from its display name.
        if row is None or current_only:
            current = await self.current_panel_identity()
            if current and sec_uid and str(current.get("sec_uid") or "") == sec_uid and douyin_id:
                ok, _ = await self._identity_ok(current, name=name, douyin_id=douyin_id)
                if ok:
                    return current
            if current_only:
                raise FriendMismatch("当前会话身份不匹配或无法核实，本轮不读取消息")

        # 0) 调用方已通过“红点+名字”定位到具体会话行：只点这一行并核验，不扫列表、不搜人。
        if row is not None:
            if not name or not douyin_id or not sec_uid:
                raise FriendMismatch(f"白名单「{label}」缺少 name、douyin_id 或 sec_uid，已跳过")
            row_sec_uid = await self._row_participant_sec_uid(row)
            if row_sec_uid != sec_uid:
                raise FriendMismatch(f"会话行绑定的 sec_uid 与白名单「{label}」不一致，已跳过")
            mark = self._user_info_event_count
            try:
                await self._open_row(row)
                await self._confirm_open(name or label)
                # 等右侧面板真正切到这个人再提取身份：面板切换有延迟时，
                # 立刻提取会读到上一个会话的对方，产生“核验未通过”误判
                # （曾把 当前好友 误读成上一个打开的群聊成员）
                await self._wait_header_matches(name or label, timeout_ms=8000)
            except DouyinPageError as exc:
                raise FriendNotFound(f"点开「{name or label}」的会话失败: {exc}") from exc
            # 新版界面：先从右侧标题组件数据里直接读对方身份（最快最可靠）。
            # 不匹配时不立刻判死——可能面板仍在切换或读到残留数据，
            # 继续走下面的批量身份/标题关联等来源；真点错会话依然会被拦住
            dom_why = ""
            dom_user = await self._partner_identity_from_dom()
            if dom_user is not None:
                ok, why = await self._identity_ok(dom_user, name=name, douyin_id=douyin_id)
                if ok:
                    return dom_user
                dom_why = why
            user = await self._identity_after_open(mark, timeout_ms=6000)
            ok, why = await self._identity_ok(user, name=name, douyin_id=douyin_id)
            if not ok:
                header = (await self._right_header_name()).strip() or name
                alt = await self._partner_for_header(header)
                if alt is not None:
                    ok, why = await self._identity_ok(alt, name=name, douyin_id=douyin_id)
                    if ok:
                        return alt
            if not ok and douyin_id and name:
                # 新版聊天页的身份接口在页面加载时就批量触发（覆盖所有会话的对方），
                # 点开会话行不会发新请求，mark 等待注定等不到——改为在已捕获的身份里
                # 按“抖音号精确匹配 + 会话标题与白名单名一致”双重条件核验
                # （标题一致保证点开的确实是这位好友的会话，防止陌生人重名混入）
                want = " ".join(name.split())
                header_norm = " ".join((await self._right_header_name()).split())
                if header_norm == want:
                    for cand in reversed(self._user_info_events):
                        ok, why = await self._identity_ok(cand, name=name, douyin_id=douyin_id)
                        if ok:
                            return cand
            if not ok:
                # 兜底再耐心等一小段：个别环境下身份响应可能在点开时才迟到触发
                deadline = asyncio.get_running_loop().time() + 9
                while asyncio.get_running_loop().time() < deadline:
                    await self.page.wait_for_timeout(3000)
                    cand = self._first_user_after(mark)
                    if cand is not None:
                        ok, why = await self._identity_ok(cand, name=name, douyin_id=douyin_id)
                        if ok:
                            return cand
                    alt = await self._partner_for_header(
                        (await self._right_header_name()).strip() or name)
                    if alt is not None:
                        ok, why = await self._identity_ok(alt, name=name, douyin_id=douyin_id)
                        if ok:
                            return alt
            if ok:
                return user
            raise FriendMismatch(
                f"点开好友「{label}」后抖音号核验未通过（{dom_why or why}），已跳过（不读取、不回复）"
            )

        await self._scroll_conversation_top()

        # 1) 候选行：优先按“聊天页显示的名字”精确定位；名字没填/没命中但有抖音号时，按抖音号逐行核验
        candidates: list[Locator] = []
        if name:
            candidates = await self._rows_by_title(name, exact=exact)
        if not candidates and douyin_id:
            candidates = (await self._iter_rows())[:_SCAN_ROW_LIMIT]

        if candidates:
            tried: list[str] = []
            for row in candidates:
                row_title = ""
                try:
                    text = (await row.inner_text()).strip()
                    row_title = text.splitlines()[0].strip() if text else ""
                except Exception:
                    pass
                mark = self._user_info_event_count
                try:
                    await self._open_row(row)
                    await self._confirm_open(name or label)
                    # 同上：等面板真正切到这行的人再提取身份，防止读到上一个会话
                    await self._wait_header_matches(name or row_title or label, timeout_ms=8000)
                except DouyinPageError as exc:
                    tried.append(str(exc)[:100])
                    continue
                user = await self._identity_after_open(mark, timeout_ms=5000)
                ok, why = await self._identity_ok(user, name=name, douyin_id=douyin_id)
                if not ok:
                    # 点击“已在打开”的会话时抖音可能返回的是本人，而非对方；
                    # 改用“昵称与右侧标题一致”的对方身份再核验一次
                    header = (await self._right_header_name()).strip() or name or row_title
                    alt = await self._partner_for_header(header)
                    if alt is not None:
                        ok, why = await self._identity_ok(alt, name=name, douyin_id=douyin_id)
                        if ok:
                            return alt
                if ok:
                    return user
                tried.append(why)
            raise FriendMismatch(
                f"点开好友「{label}」后抖音号核验未通过，已跳过（不读取、不回复）。"
                + ("；".join(tried[-3:]) if tried else "")
            )

        # 2) 列表里没有、但给了名字：退回搜索框找人（点开后仍会核验抖音号）
        if name:
            search_mark = self._user_info_event_count
            if await self._search_and_open(name):
                await self._confirm_open(name)
                user = await self._identity_after_open(search_mark, timeout_ms=5000)
                ok, why = await self._identity_ok(user, name=name, douyin_id=douyin_id)
                if not ok:
                    alt = await self._partner_for_header(name)
                    if alt is not None:
                        ok, why = await self._identity_ok(alt, name=name, douyin_id=douyin_id)
                        if ok:
                            return alt
                if ok:
                    return user
                raise FriendMismatch(
                    f"搜索点开「{name}」后抖音号核验未通过（{why}），已跳过（不读取、不回复）"
                )

        raise FriendNotFound(
            f"找不到好友「{label}」的会话：确认对方给你发过私信，"
            "或先运行 python src/main.py --discover-friends 查看对方的抖音号/名字"
        )

    def _newest_user(self) -> dict[str, Any] | None:
        return self._user_info_events[-1] if self._user_info_events else None

    def _first_user_after(self, mark: int) -> dict[str, Any] | None:
        """Return the first retained event newer than this monotonic watermark."""
        if self._user_info_event_count <= mark or not self._user_info_events:
            return None
        oldest_mark = self._user_info_event_count - len(self._user_info_events)
        return self._user_info_events[max(0, mark - oldest_mark)]

    async def _identity_after_open(self, mark: int, timeout_ms: int = 6000) -> dict[str, Any] | None:
        """点击会话后等待新到的 user/info 身份；超时返回 None（通常是点开了已在打开的会话）。"""
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while self._user_info_event_count <= mark:
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await self.page.wait_for_timeout(300)
        return self._user_info_events[-1]

    async def _identity_from_header(self, expected: str) -> dict[str, Any] | None:
        """没等到新接口时的兜底：右侧标题与期望一致，且最近捕获的身份对得上，才沿用。"""
        if not expected:
            return None
        header = (await self._right_header_name()).strip()
        if not header or header != expected:
            return None
        cached = self._newest_user()
        if cached and str(cached.get("nickname") or "").strip() == header:
            return cached
        return None

    async def _partner_for_header(self, header: str) -> dict[str, Any] | None:
        """在已捕获的 user/info 里找昵称与右侧标题一致的最近一条身份（即当前会话的对方）。

        抖音在“重复点击已打开的会话”时可能返回登录者本人而不是对方，
        因此核验身份时先用它兜底，避免把本人误当成白名单好友。
        """
        if not header:
            return None
        want = " ".join(header.split())
        for user in reversed(self._user_info_events):
            if " ".join(str(user.get("nickname") or "").split()) == want:
                return user
        return None

    def _is_self(self, user: dict[str, Any] | None) -> bool:
        """判断接口返回的身份是不是登录者本人（抖音偶尔会把本人误当成会话对方返回）。"""
        if not user or not self.owner_id:
            return False
        actual = str(user.get("unique_id") or user.get("short_id") or "").strip()
        return bool(actual) and actual == self.owner_id

    async def _identity_ok(
        self, user: dict[str, Any] | None, *, name: str, douyin_id: str
    ) -> tuple[bool, str]:
        """核验身份：配了抖音号就以抖音号为准；没配（旧配置）退回名字核验。"""
        if not user:
            return False, "没能捕获到该会话的抖音号信息"
        shown = str(user.get("nickname") or "").strip()
        actual = str(user.get("unique_id") or user.get("short_id") or "").strip()
        if self._is_self(user):
            return False, (
                f"拉到的是本人（{shown}，抖音号 {actual}）：抖音把会话主人当成了对方，"
                "需用右侧标题重新识别真正的对方"
            )
        if douyin_id:
            if actual and actual == douyin_id:
                return True, ""
            return False, (
                f"该会话是「{shown}」（抖音号 {actual or '未知'}），"
                f"与白名单抖音号 {douyin_id} 不一致"
            )
        if name and shown == name:
            return True, ""
        header = await self._right_header_name()
        if name and header == name:
            return True, ""
        return False, (
            f"名字核验不符：右侧标题是「{header or shown or '未知'}」，"
            f"白名单名字是「{name or '未填'}」"
        )

    async def _iter_rows(self) -> list[Locator]:
        """返回左侧会话列表里所有可见行（按页面顺序）。"""
        rows = self.page.locator(CONVERSATION_ITEM)
        if await rows.count() == 0:
            rows = self.page.locator(CONVERSATION_ITEM_FALLBACKS[0])
        out: list[Locator] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            try:
                if await row.is_visible():
                    out.append(row)
            except Exception:
                continue
        return out
    async def _scroll_conversation_top(self) -> None:
        """把左侧会话列表滚回顶部。

        抖音会话列表是虚拟滚动：如果之前点开的会话在列表下方，
        顶部的白名单好友行可能没被渲染，按名字匹配会漏掉而误去翻别的会话。
        找好友前先滚回顶部，确保顶部会话可见。
        """
        try:
            await self.page.evaluate(
                """() => {
                  let best = null;
                  const cands = document.querySelectorAll(
                    '[class*="ConversationList" i], [class*="conversation" i]'
                  );
                  for (const el of cands) {
                    const delta = el.scrollHeight - el.clientHeight;
                    if (delta > 50 && (!best || delta > best.delta)) {
                      best = { el: el, delta: delta };
                    }
                  }
                  if (best) { best.el.scrollTop = 0; return true; }
                  return false;
                }"""
            )
            await self.page.wait_for_timeout(600)
        except Exception:
            pass


    async def _partner_identity_from_dom(self) -> dict[str, Any] | None:
        """从右侧标题组件的 React 数据里提取当前会话对方的身份（拿不到返回 None）。

        新版界面点开会话不触发 user/info，接口监听等不到身份；这个方法直接读
        界面组件渲染用的用户对象，天然绑定当前打开的会话。登录者本人会被排除。
        """
        header = " ".join((await self._right_header_name()).split())
        cands: list[dict[str, Any]] = []
        seen_secs: set[str] = set()
        for selector in (
            '[class*="RightPanelHeadertitle"]',
            '[class*="RightPanelHeaderinfoContainer"]',
            '[class*="RightPanelHeadertitleContainer"]',
        ):
            try:
                data = await self.page.evaluate(_EXTRACT_PARTNER_JS, selector)
            except Exception:
                continue
            if not data or not data.get("users"):
                continue
            self_sec = str(data.get("selfSecUid") or "")
            for raw in data["users"]:
                user = _canon_user(raw)
                sec = str(user.get("sec_uid") or "")
                if not sec or sec in seen_secs:
                    continue
                if self_sec and sec == self_sec:
                    continue  # 登录者本人（fiber 高层会带着）
                if self._is_self(user):
                    continue
                seen_secs.add(sec)
                cands.append(user)
            if cands:
                break
        if not cands:
            return None
        if header:  # 优先昵称和右侧标题一致的那条
            for user in cands:
                if " ".join(str(user.get("nickname") or "").split()) == header:
                    return user
        return cands[0]

    async def row_at(self, index: int) -> Locator:
        """返回会话列表第 index 个可见行的 Locator（index 与 list_conversations 一致）。"""
        rows = await self._iter_rows()
        if index < 0 or index >= len(rows):
            raise DouyinPageError(f"会话行 {index} 不存在（当前只渲染了 {len(rows)} 行）")
        return rows[index]

    async def row_by_title(self, name: str, exact: bool = True) -> Locator | None:
        """点开前按标题实时定位会话行（虚拟滚动列表里目标行可能不在可视区）。

        1) 用 has_text 过滤定位：点击瞬间仍按文本重新解析，列表重排也只会
           “点不到”而不会“点错行”；
        2) 可见行里找不到时滚动列表继续找（虚拟列表只渲染可见行）。
        """
        if not name:
            return None
        want = " ".join(name.split())
        base = self.page.locator(CONVERSATION_ITEM)
        if await base.count() == 0:
            base = self.page.locator(CONVERSATION_ITEM_FALLBACKS[0])
        # 名字里的空格兼容不间断空格(\xa0)：\s 在 unicode 模式下能匹配它
        pattern = re.compile(r"\s+".join(re.escape(p) for p in name.split()), re.IGNORECASE)
        rows = base.filter(has_text=pattern)
        for attempt in range(5):
            for i in range(await rows.count()):
                row = rows.nth(i)
                try:
                    text = (await row.inner_text()).strip()
                except Exception:
                    continue
                first = " ".join(text.splitlines()[0].split()) if text else ""
                hit = (first == want) if exact else (want in " ".join(text.split()))
                if hit:
                    return row
            if not await self._scroll_list_once(down=(attempt % 2 == 0)):
                break
            await self.page.wait_for_timeout(500)
        return None

    async def _scroll_list_once(self, *, down: bool = True) -> bool:
        """把左侧会话列表滚动一屏（虚拟列表要把目标行滚进可视区才渲染）。"""
        try:
            return bool(await self.page.evaluate(
                """(down) => {
                    const cands = [...document.querySelectorAll(
                        '[class*="onversationList"], [class*="onversation"]'
                    )].filter(el => el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 100);
                    if (!cands.length) return false;
                    const best = cands.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
                    const before = best.scrollTop;
                    best.scrollTop += (down ? 1 : -1) * best.clientHeight * 0.8;
                    return best.scrollTop !== before;
                }""", down))
        except Exception:
            return False

    async def identify_row(self, row: Locator, label: str = "") -> dict[str, Any] | None:
        """点开一个会话行并尽力返回“对方”的身份信息（拿不到返回 None）。

        供低频兜底核验用：每行只点开一次，避免对陌生人会话按好友逐个反复点开。
        抖音在重复点击已打开的会话时可能返回本人，这里同样用右侧标题校正。
        """
        mark = self._user_info_event_count
        try:
            await self._open_row(row)
            # 核验兜底专用：收紧确认超时，避免一堆群聊/陌生人行把单轮拖到几分钟
            await self._confirm_open(label or "会话", timeout_ms=12000)
        except DouyinPageError:
            return None
        # 新版界面：优先从组件数据里直接读对方身份
        user = await self._partner_identity_from_dom()
        if user is None:
            user = await self._identity_after_open(mark, timeout_ms=5000)
        header = (await self._right_header_name()).strip() or label
        if user is None or self._is_self(user) or (
            header and " ".join(str(user.get("nickname") or "").split()) != " ".join(header.split())
        ):
            alt = await self._partner_for_header(header)
            if alt is not None and not self._is_self(alt):
                user = alt
        return user


    async def _rows_by_title(self, name: str, exact: bool = True) -> list[Locator]:
        """返回标题和给定名字匹配的会话行（可能有多个同名）。"""
        matched: list[Locator] = []
        for row in await self._iter_rows():
            try:
                text = (await row.inner_text()).strip()
                first_line = text.splitlines()[0].strip() if text else ""
            except Exception:
                continue
            hit = (first_line == name) if exact else (name in text)
            if hit:
                matched.append(row)
        return matched

    async def _right_header_name(self) -> str:
        """读取右侧聊天面板顶部的对方名字（读不到返回空串）。"""
        for selector in (
            '[class*="RightPanelHeadertitle"]',
            '[class*="rightPanelHeadertitle"]',
            '[class*="chatHeaderTitle"]',
            '[class*="ChatHeader"] [class*="title"]',
        ):
            try:
                loc = self.page.locator(selector).first
                if await loc.count() and await loc.is_visible():
                    text = (await loc.inner_text()).strip()
                    if text:
                        # 归一化空白（标题里常混着不换行空格 \xa0，逐字符比会永远不等）
                        return " ".join(text.splitlines()[0].split())
            except Exception:
                continue
        return ""

    async def open_panel_title(self) -> str:
        """当前右侧聊天面板顶部显示的对方名字（没开面板时返回空串）。"""
        return await self._right_header_name()

    async def current_panel_identity(self) -> dict[str, Any] | None:
        """Read the active panel's bound participant without clicking any row."""
        binding = await self.page.evaluate(r"""() => {
            const selectors=['[class*="RightPanelHeadertitle"]','[class*="rightPanelHeadertitle"]',
                '[class*="RightPanelHeaderinfoContainer"]','[class*="RightPanelHeaderconvHeader"]',
                '[class*="chatHeaderTitle"]'];
            const ids=new Set(), profiles=[];
            for(const selector of selectors){
                const el=[...document.querySelectorAll(selector)].find(e=>{const r=e.getBoundingClientRect();return r.width&&r.height;});
                if(!el)continue;
                const pk=Object.keys(el).find(k=>k.startsWith('__reactProps'));
                const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber'));
                const props=pk?[el[pk]]:[];let f=fk&&el[fk];
                for(let n=0;n<8&&f;n++,f=f.return)props.push(f.memoizedProps);
                const seen=new WeakSet();let count=0;
                function walk(v,d){
                    if(!v||typeof v!=='object'||v.nodeType||Array.isArray(v)||seen.has(v)||d>4||count>100)return;
                    seen.add(v);count++;
                    const c=v.conversation || v.curConversation;
                    if(c&&typeof c==='object'){
                        const sec=String(c.toParticipantSecUserId||c._toParticipantSecUserId||'').trim();
                        if(sec)ids.add(sec);
                    }
                    for(const [k,x] of Object.entries(v)){
                        if(['children','ref','_owner','return','child','alternate','sibling','messages','messageList','conversations','conversationList','users','userMap','selfUser','loginUser','currentUser'].includes(k))continue;
                        if(typeof x==='object')walk(x,d+1);
                    }
                }
                for(const p of props){
                    walk(p,0);
                    // Direct header component profile props are scoped to this
                    // rendered header. No nickname/cache-only identity fallback.
                    for(const k of ['opponentUserProfile','user','userInfo','partnerUser','toUserInfo','partner']){
                        const u=p?.[k];
                        if(u&&typeof u==='object'&&(u.sec_uid||u.secUid))profiles.push(u);
                    }
                }
            }
            return {ids:[...ids],profiles:profiles.map(u=>({
                sec_uid:String(u.sec_uid||u.secUid||''),unique_id:String(u.unique_id||u.uniqueId||''),
                short_id:String(u.short_id||u.shortId||''),nickname:String(u.nickname||u.nickName||''),uid:String(u.uid||u.user_id||'')
            }))};
        }""")
        ids = list(dict.fromkeys(str(x) for x in (binding or {}).get("ids", []) if x))
        direct = [_canon_user(x) for x in (binding or {}).get("profiles", [])]
        direct = [x for x in direct if not self._is_self(x)]
        if len(ids) > 1:
            return None  # Mixed/global conversation state is not an active binding.
        if not ids:
            ids = list(dict.fromkeys(str(x.get("sec_uid") or "") for x in direct if x.get("sec_uid")))
        if len(ids) != 1:
            return None
        sec = ids[0]
        if any(x.get("sec_uid") and x.get("sec_uid") != sec for x in direct):
            return None  # Header profile and conversation disagree during a switch.
        candidates = [x for x in direct if x.get("sec_uid") == sec]
        # Cached profile data may fill in an id only after the panel itself supplies
        # the participant binding. It cannot select which conversation is open.
        candidates += [x for x in reversed(self._user_info_events) if str(x.get("sec_uid") or "") == sec]
        dom = await self._partner_identity_from_dom()
        if dom and str(dom.get("sec_uid") or "") == sec:
            candidates.append(dom)
        candidates = [x for x in candidates if not self._is_self(x) and (x.get("unique_id") or x.get("short_id"))]
        actual_ids = {str(x.get("unique_id") or x.get("short_id")) for x in candidates}
        if len(actual_ids) != 1:
            return None
        return candidates[0] if candidates else None

    async def discover_conversation_ids(self, limit: int = 60) -> list[dict[str, Any]]:
        """逐个点开最近会话，抓每个会话对方的抖音号，用于填写白名单（只读，不发消息）。"""
        results: list[dict[str, Any]] = []
        rows = (await self._iter_rows())[:limit]
        for index, row in enumerate(rows):
            row_title = ""
            try:
                text = (await row.inner_text()).strip()
                row_title = text.splitlines()[0].strip() if text else ""
            except Exception:
                pass
            row_sec_uid = await self._row_participant_sec_uid(row)
            mark = self._user_info_event_count
            user: dict[str, Any] | None = None
            try:
                await self._open_row(row)
                await self._confirm_open(row_title or f"会话{index + 1}")
                user = await self._identity_after_open(mark, timeout_ms=5000)
            except Exception:
                user = None
            # 点击“已在打开”的会话时抖音可能返回本人：用右侧标题关联的对方身份校正
            header = (await self._right_header_name()).strip() or row_title
            if user is None or self._is_self(user):
                alt = await self._partner_for_header(header)
                if alt is not None and not self._is_self(alt):
                    user = alt
            elif header and str(user.get("nickname") or "").strip() != header:
                alt = await self._partner_for_header(header)
                if alt is not None and not self._is_self(alt):
                    user = alt
            note = ""
            if not row_sec_uid:
                note = "会话行未提供可绑定的 sec_uid，不能添加到严格白名单"
            elif user is None:
                note = "未能捕获身份"
            elif self._is_self(user):
                note = "抖音返回了本人，且未能从已捕获信息找到对方；建议手动重试该会话"
            is_self = bool(user) and self._is_self(user)
            results.append({
                "index": index,
                "title": row_title,
                "douyin_id": str(user.get("unique_id") or user.get("short_id") or "") if user and not is_self else "",
                "uid": str(user.get("uid") or "") if user and not is_self else "",
                "sec_uid": row_sec_uid,
                "nickname": str(user.get("nickname") or "") if user and not is_self else "",
                "note": note,
            })
            await self.page.wait_for_timeout(600)
        return results

    async def _confirm_open(self, name: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms or self.timeout_ms
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        while True:
            if await self._chat_open(name):
                await self.page.wait_for_timeout(800)
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise DouyinPageError(
                    f"点开「{name}」后未能确认聊天面板已打开（{await self._open_state_probe()}）"
                )
            await self.page.wait_for_timeout(500)

    async def _wait_header_matches(self, expected: str, timeout_ms: int = 8000) -> bool:
        """等右侧面板标题变成 expected（面板真正切换完成）。

        点击会话行后面板切换有延迟：_confirm_open 只确认“有面板开着”，
        不代表已切到目标人；立刻提取身份会读到上一个会话的对方，
        产生“核验未通过”的误判。最多等 timeout_ms，超时不报错
        （交给后续的身份核验兜底）。返回是否匹配上。
        """
        want = " ".join((expected or "").split())
        if not want:
            return False
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            header = " ".join((await self._right_header_name()).split())
            if header and header == want:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await self.page.wait_for_timeout(500)

    async def _open_state_probe(self) -> str:
        """点不开时收集页面关键元素的现状，写进报错便于定位是页面改版还是点击失效。"""
        try:
            return str(await self.page.evaluate(
                """() => {
                    const editors = document.querySelectorAll('[contenteditable="true"]').length;
                    const msgBoxes = document.querySelectorAll('[class*="messageBox"]').length;
                    const e2e = [...document.querySelectorAll('[data-e2e]')]
                        .slice(0, 8).map(e => e.getAttribute('data-e2e')).join(',');
                    const rightCls = [...document.querySelectorAll('[class*="ightPanel"]')]
                        .slice(0, 4).map(e => String(e.className).slice(0, 50)).join(' | ');
                    return `editor=${editors} msgBox=${msgBoxes} e2e=[${e2e}] rightCls=[${rightCls}]`;
                }"""
            ))
        except Exception:
            return "页面无响应"

    async def _chat_open(self, name: str) -> bool:
        if await _any_visible(self.page, CHAT_OPEN_MARKERS, 800):
            return True
        try:
            editor = await first_visible(self.page, MESSAGE_INPUTS, 800)
            if await editor.count() and await editor.is_visible():
                return True
        except Exception:
            pass
        return False

    # ── 读取消息（最新在前） ───────────────────────────────────
    async def read_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        """读取当前已打开会话的消息（最新在前）。

        抖音网页版这个版本的气泡没有 data-index，且 DOM 顺序在不同时刻可能
        “新的在上”或“新的在下”，不能直接信 DOM 顺序。这里改用气泡在页面里
        的垂直位置排序（越靠下越新），并用“气泡在中线左边/右边”判断是谁发的。

        保留每条消息的身份、类型和媒体定位标记，支持连续消息的去重与逐张截图。
        """
        list_sel = ",".join(MESSAGE_LIST)
        box_sel = ",".join(MESSAGE_BOXES)
        data = await self.page.evaluate(
            Path(__file__).with_name("message_reader.js").read_text(encoding="utf-8"),
            [list_sel, box_sel, MESSAGE_CONTENT, limit],
        )
        messages: list[dict[str, Any]] = []
        for item in data or []:
            kind = str(item.get("kind") or "text")
            text = _clean_message_text(item.get("text"))
            video = item.get("video") or {}
            live = item.get("live") or {}
            if kind == "live":
                parts = ["[好友分享了一个直播间]"]
                for field, label, limit_chars in [("host", "直播间主播", 100), ("title", "直播标题", 1200),
                                                 ("status", "卡片显示的直播状态", 100), ("url", "直播间链接", 256)]:
                    if live.get(field):
                        parts.append(label + "：" + str(live[field])[:limit_chars])
                if live.get("card_text"):
                    parts.append("分享卡片可见文字（不是好友说的话）：" + str(live["card_text"])[:1200])
                if not live.get("title"):
                    parts.append("卡片未提供直播标题，无法确定直播的具体内容。")
                parts.append("[以上仅为分享卡片信息，未进入或观看直播。主播名字不是好友说的话，也不是聊天好友的身份；不能据此假装看过直播、知道正在播什么或确认当前开播状态。]")
                text = "\n".join(parts)
            elif kind == "card":
                text = "[好友分享了一张卡片]\n卡片可见信息：" + (text or "网页未提供可读取内容") + "\n[这是分享卡片上的信息，不是好友说的话；卡片中的人名不是聊天好友的身份。未打开卡片查看实际内容，不要猜测。]"
            if kind == "video":
                parts = ["[好友分享了一个视频]"]
                if video.get("card_status") == "unavailable":
                    parts.append("聊天界面的视频卡片提示（不是好友说的话）：" + str(video.get("card_notice") or "视频无法查看")[:100])
                    parts.append("[已从这条消息提取视频编号，可尝试读取评论；卡片提示本身不能证明原视频已删除，也不代表好友无法观看。不要把界面提示当成好友的问题。]")
                if video.get("publisher"):
                    parts.append("视频发布者：" + str(video["publisher"])[:100])
                if video.get("title"):
                    parts.append("视频标题/文案：" + str(video["title"])[:1200])
                if video.get("url"):
                    parts.append("视频链接：" + str(video["url"])[:256])
                if not video.get("title"):
                    parts.append("卡片未提供视频标题或文案，无法确定视频的具体内容。")
                parts.append("[以上是分享卡片信息，未播放或观看视频。发布者名字不是好友说的话，不要据此假装已看过视频。]")
                text = "\n".join(parts)
            messages.append({
                "from_me": bool(item.get("from_me")), "kind": kind,
                "text": text, "img_src": str(item.get("img_src") or ""),
                "video": video if kind == "video" else {}, "ts": str(item.get("ts") or ""),
                "live": live if kind == "live" else {},
                "message_id": str(item.get("message_id") or ""),
                "media_token": str(item.get("media_token") or ""),
            })
        return messages[:limit]

    async def capture_message_image_png(self, message: dict[str, Any]) -> bytes:
        """Capture precisely the media bound to this read, never a newer bubble."""
        token = str(message.get("media_token") or "")
        if not re.fullmatch(r"[a-z0-9]+-\d+", token):
            raise DouyinPageError("这条消息没有可定位的图片或表情包")
        loc = self.page.locator('[data-douyin-reader-media="' + token + '"]')
        try:
            if await loc.count() != 1:
                raise DouyinPageError("图片消息已经移出页面，将在重新读取后重试")
            if await loc.get_attribute("src") != message.get("img_src"):
                # currentSrc can differ from src on responsive images; verify it too.
                current = await loc.evaluate("el => el.currentSrc || el.src")
                if current != message.get("img_src"):
                    raise DouyinPageError("图片元素内容已经改变，将重新读取后重试")
            await loc.scroll_into_view_if_needed(timeout=5000)
            await loc.evaluate("el => el.complete ? Promise.resolve() : new Promise((resolve,reject) => {el.addEventListener('load',resolve,{once:true});el.addEventListener('error',()=>reject(new Error('image load failed')),{once:true});setTimeout(()=>reject(new Error('image load timeout')),5000);})")
            return await loc.screenshot(timeout=8000)
        except Exception as exc:
            raise DouyinPageError("图片或表情包截图失败：" + type(exc).__name__) from exc

    # ── 图片消息截图 ─────────────────────────
    async def capture_newest_image_png(self) -> bytes:
        """把聊天区“最下面（最新）那张图片”截成 PNG 字节，供视觉模型识别。

        网页版图片可能是内嵌 data:image，也可能是外链地址；统一用 Playwright
        元素截图拿真实像素最稳。找不到图片时抛 DouyinPageError。
        """
        selectors = ('img[class*="MessageItemImage"]', 'img[src^="data:image"]')
        for selector in selectors:
            try:
                idx = await self.page.evaluate(
                    r"""([sel]) => {
                        const imgs = [...document.querySelectorAll(sel)].filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                        if (!imgs.length) return -1;
                        let best = 0, by = -Infinity;
                        imgs.forEach((el, i) => {
                            const r = el.getBoundingClientRect();
                            if (r.y > by) { by = r.y; best = i; }
                        });
                        return best;
                    }""",
                    [selector],
                )
                if idx is None or int(idx) < 0:
                    continue
                loc = self.page.locator(selector).nth(int(idx))
                try:
                    await loc.scroll_into_view_if_needed(timeout=5000)
                except Exception:
                    pass
                await self.page.wait_for_timeout(400)
                png = await loc.screenshot()
                if png:
                    return png
            except Exception as exc:  # noqa: BLE001
                raise DouyinPageError(f"图片消息截图失败: {exc}") from exc
        raise DouyinPageError("聊天里没找到图片消息，无法回复")

    # ── 发送文字消息 ──────────────────────────────────────────
    async def send_text(self, text: str) -> None:
        editor = await first_visible(self.page, MESSAGE_INPUTS, self.timeout_ms)
        try:
            await editor.click(timeout=5000)
        except Exception:
            pass
        try:
            await editor.focus()
        except Exception:
            pass
        await self.page.wait_for_timeout(300)
        # Chromium 用 insert_text 瞬间输入；Firefox 等引擎不支持，先试、
        # 文本没进编辑框就用真实按键逐字输入兜底（对所有编辑器引擎通用）
        try:
            await self.page.keyboard.insert_text(text)
        except Exception:
            pass
        if not await self._wait_text_in_editor(text[:40], timeout_ms=4000):
            try:
                await editor.click(timeout=3000)
            except Exception:
                pass
            await self.page.keyboard.type(text, delay=25)
            if not await self._wait_text_in_editor(text[:40], timeout_ms=8000):
                raise DouyinPageError("文字没能写进输入框（页面结构可能变了）")

        await self.page.wait_for_timeout(500)
        await self._trigger_send()
        await self._confirm_sent(text)

    async def _wait_text_in_editor(self, needle: str, timeout_ms: int = 4000) -> bool:
        """等文字出现在输入框里（出现返回 True，超时返回 False）。"""
        try:
            await self.page.wait_for_function(
                r"""([txt, inputSel]) => {
                    const sels = inputSel.split('|').filter(Boolean);
                    const es = [...sels.flatMap(sel => document.querySelectorAll(sel)),
                                ...document.querySelectorAll('[contenteditable="true"]')];
                    return es.some(e => (e.innerText || '').includes(txt));
                }""",
                arg=[needle, "|".join(MESSAGE_INPUTS)],
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    async def _trigger_send(self) -> None:
        for selector in SEND_BUTTONS:
            btn = self.page.locator(selector).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=3000)
                    return
            except Exception:
                continue
        await self.page.keyboard.press("Enter")

    async def _confirm_sent(self, expected_text: str) -> None:
        """发送后确认：新气泡确实出现在聊天区（自己发的、最新一条、内容匹配）。

        和 read_messages 用同一套可靠判断：按气泡在页面上的垂直位置找“最新”，
        用气泡相对聊天区中线偏左/偏右判断是不是自己发的（不再依赖 isFromMe class，
        也不依赖 DOM 顺序），避免“其实发出去了却误报失败”。
        """
        needle = expected_text[:60].replace(" ", "")
        list_sel = ",".join(MESSAGE_LIST)
        box_sel = ",".join(MESSAGE_BOXES)
        try:
            await self.page.wait_for_function(
                r"""([needle, listSel, boxSel, contentSel]) => {
                    const norm = v => (v || '').replace(/[\s\u200b\u200c\u200d\ufeff]+/g, '').trim();
                    const list = document.querySelector(listSel);
                    if (!list) return false;
                    const lr = list.getBoundingClientRect();
                    const centerX = lr.x + lr.width / 2;
                    const items = [];
                    list.querySelectorAll(boxSel).forEach(box => {
                        const br = box.getBoundingClientRect();
                        if (br.width === 0 || br.height === 0) return;
                        const content = box.querySelector(contentSel) || box;
                        const cr = content.getBoundingClientRect();
                        let mine = false;
                        if (cr.width > 0 && cr.width < lr.width * 0.8) {
                            mine = (cr.x + cr.width / 2) > centerX;
                        } else {
                            const img = box.querySelector('img');
                            if (img) {
                                const ir = img.getBoundingClientRect();
                                mine = ir.x > centerX;
                            }
                        }
                        items.push({y: br.y, mine, text: norm(content.innerText)});
                    });
                    // 垂直位置越靠下越新：y 升序后取最后几条（最新），再逐条检查
                    items.sort((a, b) => a.y - b.y);
                    const newest = items.slice(-3).reverse();
                    return newest.some(box => box.mine && box.text.length > 0 &&
                        (needle.length === 0 ||
                         box.text.indexOf(norm(needle)) >= 0 ||
                         norm(needle).indexOf(box.text) >= 0));
                }""",
                arg=[needle, list_sel, box_sel, MESSAGE_CONTENT],
                timeout=15000,
            )
        except Exception as exc:
            raise DouyinPageError("发送后没能确认新消息出现，为避免重复发送不再自动重试") from exc
        # 二次确认：网页可能先把气泡“乐观显示”出来但实际没送达（风控时常见）。
        # 再等几秒，若页面出现“发送失败”提示/重试标记，说明消息其实没发出去，要如实报错。
        await self.page.wait_for_timeout(1500)
        try:
            await self.page.wait_for_timeout(4000)
            for sel in SEND_FAILURE_MARKERS:
                loc = self.page.locator(sel).first
                try:
                    if await loc.count() and await loc.is_visible():
                        raise DouyinPageError("页面出现“发送失败”提示，消息可能未真正送达，不再自动重试")
                except DouyinPageError:
                    raise
                except Exception:
                    continue
        except DouyinPageError:
            raise
        except Exception:
            pass
    # ── 故障现场保存 ──────────────────────────────────────────
    async def save_failure_artifacts(self, folder: str | Path, tag: str) -> list[str]:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        saved = []
        safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", tag).strip("-") or "unknown"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            png = folder / f"{stamp}-{safe}.png"
            await self.page.screenshot(path=str(png), full_page=False)
            saved.append(str(png))
        except Exception:
            pass
        try:
            html = folder / f"{stamp}-{safe}.html"
            html.write_text(await self.page.content(), encoding="utf-8")
            saved.append(str(html))
        except Exception:
            pass
        try:
            convo = await self.list_conversations()
            j = folder / f"{stamp}-{safe}-conversations.json"
            j.write_text(json.dumps(convo, ensure_ascii=False, indent=2), encoding="utf-8")
            saved.append(str(j))
        except Exception:
            pass
        return saved

