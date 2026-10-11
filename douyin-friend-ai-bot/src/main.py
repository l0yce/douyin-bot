import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

# 保证无论从哪个目录运行 python src/main.py，都能找到同目录的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_client import AIError, AIClient
from browser_manager import (
    BrowserSession,
    LoginError,
    cookies_file_path,
    has_any_login_material,
    load_cookie_entries,
    login_by_qr,
)
from config_loader import enabled_friends, load_config, owner_identity
from douyin_chat import DouyinChat
from runtime_log import setup_runtime_logging
from runner import check_login, run_forever

logger = logging.getLogger("douyin.main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抖音白名单好友 AI 自动回复机器人")
    parser.add_argument("--config", default="config/config.json", help="配置文件路径")
    parser.add_argument("--login-qr", action="store_true", help="扫码登录并保存登录态（需要图形界面）")
    parser.add_argument("--import-cookie", metavar="FILE", help="导入 Cookie-Editor 导出的 JSON 到 state/cookies.json")
    parser.add_argument("--check-login", action="store_true", help="只检查登录是否有效后退出")
    parser.add_argument("--list-conversations", action="store_true", help="打印当前会话列表（用于核对白名单名字）")
    parser.add_argument("--discover-friends", action="store_true", help="逐个点开最近会话，打印每个会话的抖音号/名字，用于填白名单（只读不发消息）")
    parser.add_argument("--test-ai", metavar="TEXT", help="用当前 AI 配置试生成一句回复")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    parser.add_argument("--dry-run", action="store_true", help="演练模式：只判断/生成，不真正发送")
    parser.add_argument("--no-ai", action="store_true", help="不调用 AI（配合 --dry-run 仅检查会话）")
    return parser


async def _cmd_check_login(cfg: dict) -> int:
    session = BrowserSession(cfg)
    try:
        await session.start()
        chat = DouyinChat(session.page, timeout_ms=int(cfg["account"].get("page_timeout_ms", 15000)), owner_id=owner_identity(cfg)[1])
        await chat.goto_chat()
        state = await chat.login_state_settled()
        print(f"登录状态：{state}")
        if state == "ok":
            print("✅ 登录有效，可以继续下一步。")
            return 0
        if state == "unknown":
            print("❌ 等待 40 秒仍无法识别页面状态（可能抖音改版或页面异常），需人工排查。")
        else:
            print("❌ 登录无效或出现安全验证，请重新导出 Cookie 后重试。")
        return 1
    except LoginError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        await session.stop()


async def _cmd_list_conversations(cfg: dict) -> int:
    session = BrowserSession(cfg)
    try:
        await session.start()
        chat = DouyinChat(session.page, timeout_ms=int(cfg["account"].get("page_timeout_ms", 15000)), owner_id=owner_identity(cfg)[1])
        await chat.goto_chat()
        await check_login(chat)
        convos = await chat.list_conversations()
        print(f"当前会话列表（共 {len(convos)} 个）：")
        for c in convos:
            print(f"  - {c.get('title')}")
        return 0
    finally:
        await session.stop()

async def _cmd_discover_friends(cfg: dict) -> int:
    session = BrowserSession(cfg)
    try:
        await session.start()
        chat = DouyinChat(session.page, timeout_ms=int(cfg["account"].get("page_timeout_ms", 15000)), owner_id=owner_identity(cfg)[1])
        await chat.goto_chat()
        await check_login(chat)
        print("正在逐个点开最近会话抓取抖音号（只读，不发消息）……")
        items = await chat.discover_conversation_ids()
        print(f"\n共 {len(items)} 个会话。把要自动回复的好友填进 config.json 的 whitelist.friends：")
        for it in items:
            print(f"  - 列表显示名/备注：{it.get('title') or '(无标题)'}")
            print(f"      抖音号 douyin_id：{it.get('douyin_id') or '(未能获取)'}")
            print(f"      会话绑定 sec_uid：{it.get('sec_uid') or '(未能获取)'}")
            if it.get("title") and it.get("douyin_id") and it.get("sec_uid"):
                print("      可复制白名单项：" + json.dumps({"name": it["title"], "douyin_id": it["douyin_id"], "sec_uid": it["sec_uid"], "enabled": True}, ensure_ascii=False))
            print(f"      对方昵称：{it.get('nickname') or ''}     UID：{it.get('uid') or ''}")
            if it.get("note"):
                print(f"      ⚠️ {it.get('note')}")
        return 0
    finally:
        await session.stop()


def _cmd_test_ai(cfg: dict, text: str) -> int:
    ai = AIClient(cfg)
    system = (
        "你是「好友」在抖音上的一位朋友，正在用抖音私信聊天。"
        "请用自然、口语化的中文回复，简短一两句，不要提你是 AI。"
    )
    reply = ai.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": f"对方刚发来：{text}\n\n请自然地回复。"},
    ])
    print(f"AI 模型: {cfg['ai'].get('model')}")
    print(f"拟回复: {reply}")
    return 0


def _cmd_import_cookie(cfg: dict, source: str) -> int:
    try:
        entries = load_cookie_entries(source)
    except Exception as exc:
        logger.error("导入失败：%s", exc)
        return 1
    target = cookies_file_path(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已导入 {len(entries)} 条 Cookie 到 {target}")
    print("接下来建议先运行：python src/main.py --check-login")
    return 0


async def _async_main(args: argparse.Namespace) -> int:
    require_friends = not any((args.import_cookie, args.login_qr, args.check_login, args.list_conversations, args.discover_friends, args.test_ai))
    require_ai = bool(args.test_ai) or not any((args.import_cookie, args.login_qr, args.check_login, args.list_conversations, args.discover_friends, args.no_ai))
    cfg = load_config(args.config, require_friends=require_friends, require_ai=require_ai)
    if args.import_cookie:
        return _cmd_import_cookie(cfg, args.import_cookie)
    if args.test_ai:
        try:
            return _cmd_test_ai(cfg, args.test_ai)
        except AIError as exc:
            logger.error("AI 测试失败：%s", exc)
            return 1

    if args.login_qr:
        timeout = int(cfg["account"].get("login_timeout_seconds", 300))
        await login_by_qr(cfg, timeout_seconds=timeout)
        return 0

    if not has_any_login_material(cfg):
        logger.error("还没有登录凭证。请先运行 --import-cookie 导入 Cookie，或在带界面的电脑上 --login-qr。")
        return 2

    if args.check_login:
        return await _cmd_check_login(cfg)
    if args.list_conversations:
        return await _cmd_list_conversations(cfg)
    if args.discover_friends:
        return await _cmd_discover_friends(cfg)

    # 正式运行 / 演练
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(signum=None, frame=None) -> None:  # noqa: ARG001
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    mode = "演练(dry-run)" if args.dry_run else ("单轮" if args.once else "持续运行")
    friends = enabled_friends(cfg)
    logger.info("启动模式：%s；白名单：%s", mode, "、".join((f["name"] or f["douyin_id"] or "?") for f in friends))
    return await run_forever(
        cfg,
        config_path=args.config,
        once=args.once,
        dry_run=args.dry_run,
        no_ai=args.no_ai,
        stop_event=stop_event,
    )


def main() -> None:
    # Windows 控制台默认 GBK，打印 emoji/中文可能报错；统一改成 UTF-8（Linux 上无影响）
    for _stream_name in ("stdout", "stderr"):
        try:
            getattr(sys, _stream_name).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args()
    # 先保证 logs 目录存在，再用默认配置初始化日志
    default_cfg_path = Path(args.config)
    log_file = "logs/runtime.log"
    if default_cfg_path.exists():
        try:
            probe = load_config(args.config)
            log_file = probe["log"]["runtime_file"]
        except Exception:
            pass
    setup_runtime_logging(log_file)
    try:
        code = asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n已手动停止。")
        code = 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("启动失败：%s", exc)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
