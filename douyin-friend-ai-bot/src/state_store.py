# -*- coding: utf-8 -*-
"""好友会话状态存储：记录每个白名单好友最近一次已处理的消息指纹，避免重复回复。"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def msg_fingerprint(text: str, ts: str = "") -> str:
    return text_fingerprint(f"{text}|{ts}")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StateStore:
    """state/replied_state.json 的读写封装，带进程内锁，文件损坏时自动重置。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {"version": 1, "friends": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("friends"), dict):
                self._data = raw
        except Exception:
            self._data = {"version": 1, "friends": {}}

    def _save(self) -> None:
        with _LOCK:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def friend_state(self, friend: str) -> dict[str, Any]:
        entry = self._data["friends"].setdefault(friend, {})
        if not isinstance(entry, dict):
            entry = {}
            self._data["friends"][friend] = entry
        return entry

    def get(self, friend: str, key: str, default: Any = None) -> Any:
        return self.friend_state(friend).get(key, default)

    def set(self, friend: str, key: str, value: Any) -> None:
        self.friend_state(friend)[key] = value
        self._save()

    def update(self, friend: str, **kwargs: Any) -> None:
        self.friend_state(friend).update(kwargs)
        self._save()

    def snapshot(self) -> dict[str, Any]:
        with _LOCK:
            return json.loads(json.dumps(self._data, ensure_ascii=False))