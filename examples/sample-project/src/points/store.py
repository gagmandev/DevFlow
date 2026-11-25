"""JSON 文件存储。

一个进程内可重入锁 + 原子写（临时文件 → fsync → os.replace）。
文件结构固定为::

    {"accounts": {user_id: {...}}, "ledger": [{...}]}
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .errors import PointsError


class StorageError(PointsError):
    """数据文件损坏或无法读写。"""

    code = "STORAGE_ERROR"


def empty_data() -> dict:
    return {"accounts": {}, "ledger": []}


class JsonStore:
    """极简 JSON 存储。读失败不静默清空，直接抛错。"""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def lock(self) -> threading.RLock:
        """业务层用这把锁包住"读-改-写"，避免并发覆盖。"""
        return self._lock

    def read(self) -> dict:
        with self._lock:
            if not self.path.exists():
                return empty_data()
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError as exc:
                raise StorageError(f"读取失败：{self.path}（{exc}）") from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise StorageError(f"数据文件不是合法 JSON：{self.path}（{exc}）") from exc
            if not isinstance(data, dict):
                raise StorageError(f"数据文件结构不对：{self.path}")
            data.setdefault("accounts", {})
            data.setdefault("ledger", [])
            return data

    def write(self, data: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
