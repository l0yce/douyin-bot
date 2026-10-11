# -*- coding: utf-8 -*-
"""运行日志：同时输出到控制台和 logs/runtime.log。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE = "%H:%M:%S"


def setup_runtime_logging(file_path: str | Path, console_level: int = logging.INFO) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_FORMAT, _DATE))

    fileh = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(logging.Formatter(_FORMAT))

    # httpcore 每次 AI 调用会产生十几条 DEBUG 连接细节;请求摘要由 httpx 的 INFO 行提供,足够了
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    root.addHandler(console)
    root.addHandler(fileh)