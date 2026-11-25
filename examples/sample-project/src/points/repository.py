"""仓储层：把 JSON 结构包装成对象操作。

这一层只负责"存取"，不做业务判断（余额够不够、能不能重复入账属于服务层）。
所有写操作都在 ``store.lock()`` 内完成，且一次操作只写一次盘。
"""

from __future__ import annotations

from .errors import InvalidArgument
from .models import Account, LedgerEntry
from .store import JsonStore

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class AccountRepo:
    def __init__(self, store: JsonStore):
        self.store = store

    def get(self, user_id: str) -> Account | None:
        raw = self.store.read()["accounts"].get(user_id)
        return Account.from_dict(raw) if raw else None

    def get_or_create(self, user_id: str) -> Account:
        if not user_id:
            raise InvalidArgument("user_id 不能为空")
        with self.store.lock():
            data = self.store.read()
            raw = data["accounts"].get(user_id)
            if raw:
                return Account.from_dict(raw)
            account = Account(user_id=user_id)
            data["accounts"][user_id] = account.to_dict()
            self.store.write(data)
            return account

    def save(self, account: Account) -> Account:
        with self.store.lock():
            data = self.store.read()
            account.version += 1
            data["accounts"][account.user_id] = account.to_dict()
            self.store.write(data)
            return account

    def list_all(self) -> list[Account]:
        return [Account.from_dict(raw) for raw in self.store.read()["accounts"].values()]


class LedgerRepo:
    def __init__(self, store: JsonStore):
        self.store = store

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        with self.store.lock():
            data = self.store.read()
            data["ledger"].append(entry.to_dict())
            self.store.write(data)
            return entry

    def exists(self, user_id: str, type: str, source_id: str) -> bool:
        for raw in self.store.read()["ledger"]:
            if (
                raw.get("user_id") == user_id
                and raw.get("type") == type
                and raw.get("source_id") == source_id
            ):
                return True
        return False

    def query(
        self,
        user_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[LedgerEntry], int]:
        """按时间倒序返回 ``(当前页, 过滤后总数)``。

        时间区间左闭右开：``start <= created_at < end``。
        """
        page = max(1, int(page))
        size = max(1, min(MAX_PAGE_SIZE, int(size)))

        items = [LedgerEntry.from_dict(raw) for raw in self.store.read()["ledger"]]
        if user_id is not None:
            items = [e for e in items if e.user_id == user_id]
        if start is not None:
            items = [e for e in items if e.created_at >= start]
        if end is not None:
            items = [e for e in items if e.created_at < end]

        items.sort(key=lambda e: (e.created_at, e.entry_id), reverse=True)
        total = len(items)
        offset = (page - 1) * size
        return items[offset : offset + size], total
