# -*- coding: utf-8 -*-
"""审计日志：每条自动回复都落一条 JSONL，方便追溯和排查。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, file_path: str | Path) -> None:
        self.path = Path(file_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, payload: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")