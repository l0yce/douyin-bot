# -*- coding: utf-8 -*-
"""浏览器与登录态管理。

两种登录方式（二选一，推荐方式一）：
1. 电脑浏览器导出 Cookie JSON（Cookie-Editor 插件，JSON 数组格式）-> 放到 state/cookies.json
2. 用 --login-qr 在带界面的电脑上扫码登录一次，自动保存登录态到 state/douyin_storage.json

主程序启动时优先用 storage_state；没有则读 cookies_file。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger("douyin.browser")

# 优先使用项目自带的浏览器（ms-playwright/），不依赖外部环境变量；
# 已设置 PLAYWRIGHT_BROWSERS_PATH 时尊重用户的选择。
# 仅 Linux 生效：Windows/macOS 用 Playwright 自带的下载目录（项目里的是 Linux 版二进制）
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BROWSERS = _REPO_ROOT / "ms-playwright"
if _DEFAULT_BROWSERS.exists() and os.name == "posix":
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_DEFAULT_BROWSERS))


class LoginError(RuntimeError):
    pass


# 小内存服务器（<2GB）必备的 Chromium 启动参数：限制渲染进程数、
# 关掉 GPU/扩展/后台节流，并给每个渲染进程的 V8 堆设上限，
# 显著降低 OOM 被杀（"Target crashed"）的概率。可在 config 的
# account.chromium_flags 里整体覆盖。
DEFAULT_CHROMIUM_FLAGS = (
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--mute-audio",
    "--disable-extensions",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--renderer-process-limit=2",
    "--js-flags=--max-old-space-size=256",
    "--disk-cache-size=33554432",
)

# 抖音新版聊天后端会识破 Chromium 的自动化特征（CDP）并静默拒绝打开会话
# （表现为：点击会话行后右侧面板永远空着）。Firefox 引擎不走 CDP，实测可用。
FIREFOX_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0"
)


# 本进程启动的 Xvfb（stop 时收掉，避免孤儿进程越积越多）
_XVFB_PROC: subprocess.Popen | None = None


def ensure_display() -> bool:
    """没有图形环境时自动拉起 Xvfb 虚拟屏（有头模式需要）。已有 DISPLAY 则原样使用。

    有头 Chrome 的指纹比 headless shell 真实得多，抖音对 headless 环境会限制
    IM 数据下发（表现为会话列表一直「暂无会话」），所以服务器上必须用有头模式。
    注意：必须在 Playwright 驱动进程启动之前调用——驱动启动时会固化环境变量，
    之后再改 os.environ 的 DISPLAY 是传不到浏览器进程的。
    """
    if os.name != "posix":
        return True  # Windows/macOS 桌面自带显示器，有头浏览器直接开窗口
    if os.environ.get("DISPLAY"):
        return True
    for num in range(99, 110):
        try:
            proc = subprocess.Popen(
                ["Xvfb", f":{num}", "-screen", "0", "1536x864x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("系统里没有 Xvfb：有头模式需要先安装（yum install xorg-x11-server-Xvfb 或 apt install xvfb）")
            return False
        # 等虚拟屏就绪：进程活着且 unix socket 已出现，才算启动成功
        deadline = time.time() + 5
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break  # 这个显示号起不来（可能被残留锁占用），换下一个
            if Path(f"/tmp/.X11-unix/X{num}").exists():
                ready = True
                break
            time.sleep(0.5)
        if ready:
            os.environ["DISPLAY"] = f":{num}"
            global _XVFB_PROC
            _XVFB_PROC = proc
            logger.info("已启动 Xvfb 虚拟屏 DISPLAY=:%d", num)
            return True
        try:  # 没就绪：收掉半死进程再试下一个显示号
            proc.terminate()
        except Exception:
            pass
    logger.error("没能启动 Xvfb 虚拟屏")
    return False


# ── Cookie 转换 ──────────────────────────────────────────────


def _convert_cookie_json(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name") or "value" not in entry:
            continue
        cookie: dict[str, Any] = {
            "name": entry["name"],
            "value": entry["value"],
            "domain": entry.get("domain") or ".douyin.com",
            "path": entry.get("path") or "/",
        }
        if entry.get("expirationDate") is not None and entry.get("session") is False:
            cookie["expires"] = int(entry["expirationDate"])
        elif entry.get("expires") is not None:
            cookie["expires"] = int(entry["expires"])
        else:
            cookie["expires"] = -1
        cookie["httpOnly"] = bool(entry.get("httpOnly", False))
        cookie["secure"] = bool(entry.get("secure", False))
        same_site = str(entry.get("sameSite") or "").lower()
        if same_site in ("no_restriction", "none"):
            cookie["sameSite"] = "None"
        elif same_site == "strict":
            cookie["sameSite"] = "Strict"
        elif same_site == "lax":
            cookie["sameSite"] = "Lax"
        out.append(cookie)
    return out


def load_cookie_entries(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise LoginError(f"找不到 Cookie 文件: {p}")
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if not isinstance(data, list) or not data:
        raise LoginError(f"Cookie 文件格式不对：{p} 需要是 Cookie-Editor 导出的 JSON 数组")
    return _convert_cookie_json(data)


def storage_state_path(cfg: dict[str, Any]) -> Path:
    return Path(cfg["account"]["storage_state"])


def cookies_file_path(cfg: dict[str, Any]) -> Path:
    return Path(cfg["account"]["cookies_file"])


def has_any_login_material(cfg: dict[str, Any]) -> bool:
    return storage_state_path(cfg).exists() or cookies_file_path(cfg).exists()


# ── 浏览器会话 ───────────────────────────────────────────────


class BrowserSession:
    """管理 playwright/chromium/context/page 的生命周期。"""

    def __init__(self, cfg: dict[str, Any], *, headless: bool | None = None) -> None:
        self.cfg = cfg
        self.headless = bool(cfg["account"].get("headless", True)) if headless is None else headless
        self._pw: Playwright | None = None
        self.browser = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self, *, login_source: str = "auto") -> None:
        # Xvfb 必须在 Playwright 驱动进程启动之前就绪（见 ensure_display 的说明），
        # 否则浏览器进程拿不到 DISPLAY，报 "Missing X server or $DISPLAY"
        if not self.headless and not os.environ.get("DISPLAY"):
            if not ensure_display():
                raise LoginError("无法启动图形环境（Xvfb），有头模式不可用")
        # 驱动选择：patchright 是 Playwright 的隐身补丁分支（修补 CDP 检测痕迹），
        # 用于对抗抖音新版聊天页对 CDP 自动化的前端检测；默认仍是官方 playwright
        driver = str(self.cfg["account"].get("driver") or "playwright").strip().lower()
        if driver == "patchright":
            try:
                from patchright.async_api import async_playwright as _async_playwright
            except ImportError as exc:
                raise LoginError("未安装 patchright：pip install patchright 后重试") from exc
        else:
            from playwright.async_api import async_playwright as _async_playwright
        self._pw = await _async_playwright().start()
        engine = str(self.cfg["account"].get("engine") or "firefox").strip().lower()
        locale = self.cfg["account"].get("locale", "zh-CN")
        timezone = self.cfg["account"].get("timezone", "Asia/Shanghai")
        viewport = self.cfg["account"].get("viewport") or {"width": 1536, "height": 864}
        # 住宅代理（可选）：数据中心 IP 容易被抖音限制 IM 数据/会话打开，
        # 配 account.proxy_server 如 "http://user:pass@host:port" 即可走代理
        proxy_server = str(self.cfg["account"].get("proxy_server") or "").strip()
        proxy = {"server": proxy_server} if proxy_server else None
        ss = storage_state_path(self.cfg)
        cf = cookies_file_path(self.cfg)
        try:
            profile_dir = str(self.cfg["account"].get("profile_dir") or "").strip()
            if profile_dir:
                # 持久化 profile：每次运行是“同一台设备”，不再像新设备反复出现
                Path(profile_dir).mkdir(parents=True, exist_ok=True)
                if engine == "chromium":
                    flags = list(self.cfg["account"].get("chromium_flags") or DEFAULT_CHROMIUM_FLAGS)
                    if not self.headless and "--no-sandbox" not in flags:
                        flags.append("--no-sandbox")
                    self.context = await self._pw.chromium.launch_persistent_context(
                        profile_dir,
                        headless=self.headless,
                        locale=locale,
                        timezone_id=timezone,
                        viewport=viewport,
                        proxy=proxy,
                        args=flags,
                    )
                elif engine == "edge":
                    # 真实 Edge（channel="msedge"）：用的是系统安装的原版 Edge 二进制，
                    # 和真人日常用的浏览器完全一致，指纹真实度最高。
                    # 不做任何 UA/平台伪装——真实值就是最好的伪装。
                    flags = ["--disable-dev-shm-usage",
                             "--renderer-process-limit=2",
                             "--js-flags=--max-old-space-size=256"]
                    if os.name == "posix" and os.geteuid() == 0:
                        flags.append("--no-sandbox")
                    self.context = await self._pw.chromium.launch_persistent_context(
                        profile_dir,
                        channel="msedge",
                        headless=self.headless,
                        locale=locale,
                        timezone_id=timezone,
                        viewport=viewport,
                        proxy=proxy,
                        args=flags,
                    )
                else:
                    # Firefox（默认）：抖音识破不了的非 CDP 引擎。
                    # 沿用实测通过的 Firefox/Windows UA 与平台伪装。
                    ua = str(self.cfg["account"].get("user_agent") or "").strip() or FIREFOX_DEFAULT_UA
                    self.context = await self._pw.firefox.launch_persistent_context(
                        profile_dir,
                        headless=self.headless,
                        locale=locale,
                        timezone_id=timezone,
                        user_agent=ua,
                        viewport=viewport,
                        proxy=proxy,
                    )
                    await self.context.add_init_script(
                        "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});"
                    )
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                # profile 里可能存着旧登录态；cookies.json 是用户刚导的，以它为准刷新
                if login_source in ("auto", "cookies") and cf.exists():
                    await self.context.add_cookies(load_cookie_entries(cf))
                elif login_source in ("auto", "storage") and ss.exists():
                    data = json.loads(ss.read_text(encoding="utf-8"))
                    if data.get("cookies"):
                        await self.context.add_cookies(data["cookies"])
            else:
                # 无 profile 的旧式无痕模式（不推荐）：仅支持 Chromium
                flags = list(self.cfg["account"].get("chromium_flags") or DEFAULT_CHROMIUM_FLAGS)
                self.browser = await self._pw.chromium.launch(headless=self.headless, args=flags, proxy=proxy)
                if login_source == "none":
                    self.context = await self.browser.new_context(locale=locale)
                elif login_source == "storage":
                    if not ss.exists():
                        raise LoginError(f"没有找到登录态文件: {ss}")
                    self.context = await self.browser.new_context(storage_state=str(ss), locale=locale)
                elif login_source == "cookies":
                    if not cf.exists():
                        raise LoginError(f"没有找到 Cookie 文件: {cf}")
                    self.context = await self.browser.new_context(locale=locale)
                    await self.context.add_cookies(load_cookie_entries(cf))
                else:  # auto：优先 storage，其次 cookies
                    if ss.exists():
                        self.context = await self.browser.new_context(storage_state=str(ss), locale=locale)
                    elif cf.exists():
                        self.context = await self.browser.new_context(locale=locale)
                        await self.context.add_cookies(load_cookie_entries(cf))
                    else:
                        raise LoginError(
                            "还没有登录凭证：请先把抖音 Cookie 导出成 JSON 放到 state/cookies.json，"
                            "或在带界面的电脑上运行 --login-qr 扫码登录一次"
                        )
                self.page = await self.context.new_page()
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        for closer in (self._close_page, self._close_context, self._close_browser, self._close_pw):
            try:
                await closer()
            except Exception:
                pass

    async def _close_page(self) -> None:
        if self.page is not None:
            try:
                await self.page.close()
            finally:
                self.page = None

    async def _close_context(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            finally:
                self.context = None

    async def _close_browser(self) -> None:
        if self.browser is not None:
            try:
                await self.browser.close()
            finally:
                self.browser = None

    async def _close_pw(self) -> None:
        global _XVFB_PROC
        if _XVFB_PROC is not None:
            try:
                _XVFB_PROC.terminate()
            except Exception:
                pass
            _XVFB_PROC = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            finally:
                self._pw = None

    async def save_storage(self) -> Path:
        if self.context is None:
            raise LoginError("浏览器还没启动")
        target = storage_state_path(self.cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=str(target))
        return target


# ── 扫码登录 ─────────────────────────────────────────────────


async def login_by_qr(cfg: dict[str, Any], timeout_seconds: int = 300) -> Path:
    """打开有界面的浏览器到抖音聊天页，等用户用手机扫码登录后保存登录态。

    服务器没有图形界面时不要用这个命令；请在带桌面的电脑上登录一次后把
    state/douyin_storage.json（或 cookies.json）传到服务器。
    """
    from douyin_chat import DouyinChat  # 局部导入，避免与其它模块的顶层导入相互依赖

    session = BrowserSession(cfg, headless=False)
    await session.start(login_source="none")  # 不带任何凭证，先扫码
    try:
        page = session.page
        assert page is not None
        await page.goto(cfg["account"]["chat_url"], wait_until="domcontentloaded")
        print("请在自动打开的浏览器里，用手机抖音 App 扫码登录。")
        print(f"等待扫码，最长 {timeout_seconds} 秒……（登录后能看到私信页面即自动保存）")
        deadline = time.time() + timeout_seconds
        # 只建一次 DouyinChat：每次构造都会往 page 上挂一个 response 监听器，
        # 放在循环里会随等待时间越积越多（监听器泄漏）
        chat = DouyinChat(page)
        while time.time() < deadline:
            await page.wait_for_timeout(2500)
            url = page.url
            state = await chat.login_state()
            if state == "ok":
                await page.wait_for_timeout(4000)
                saved = await session.save_storage()
                print(f"登录成功，登录态已保存到: {saved}")
                return saved
            if state == "risk":
                print("页面出现安全验证，请在浏览器里手动完成验证后继续等待……")
            if "/chat" not in url and "/messages" not in url:
                await page.goto(cfg["account"]["chat_url"], wait_until="domcontentloaded")
        raise LoginError(f"等待扫码超时（{timeout_seconds} 秒），请重试")
    finally:
        await session.stop()
