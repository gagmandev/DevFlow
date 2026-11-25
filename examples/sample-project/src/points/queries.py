"""流水查询服务（用户视图 / 运营视图）。

只做参数规整与结果组装，过滤与分页复用 LedgerRepo.query。
"""

from __future__ import annotations

from .repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, LedgerRepo
from .store import JsonStore


class LedgerQuery:
    def __init__(self, store: JsonStore):
        self.repo = LedgerRepo(store)

    def by_user(self, user_id: str, page: int = 1, size: int = DEFAULT_PAGE_SIZE) -> dict:
        return self._pack(self.repo.query(user_id=user_id, page=page, size=size), page, size)

    def all(
        self,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
        user_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        """运营视图：不传 user_id 即全量。"""
        return self._pack(
            self.repo.query(user_id=user_id, start=start, end=end, page=page, size=size),
            page,
            size,
        )

    @staticmethod
    def _pack(result, page: int, size: int) -> dict:
        items, total = result
        return {
            "items": [entry.to_dict() for entry in items],
            "total": total,
            "page": max(1, int(page)),
            "size": max(1, min(MAX_PAGE_SIZE, int(size))),
        }
