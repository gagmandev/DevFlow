"""积分冻结 / 解冻服务（CR-001）。

冻结不改变 balance，只把可用额度挪到 frozen：
``available = balance - frozen`` 立即生效，兑换自动受约束。

写盘方式与 WalletService._apply 一致：账户与流水一次落盘。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .errors import AccountNotFound, DuplicateRequest, InsufficientBalance, InvalidArgument
from .models import Account, LedgerEntry
from .repository import AccountRepo, LedgerRepo
from .store import JsonStore

FREEZE = "freeze"
UNFREEZE = "unfreeze"


class FreezeService:
    def __init__(self, store: JsonStore, clock=None):
        self.store = store
        self.clock = clock or datetime.now
        self.accounts = AccountRepo(store)
        self.ledger = LedgerRepo(store)
        self._lock = store.lock()

    # -- 内部 --------------------------------------------------------------

    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _require(self, user_id: str) -> Account:
        if not user_id:
            raise InvalidArgument("user_id 不能为空")
        account = self.accounts.get(user_id)
        if account is None:
            raise AccountNotFound(f"账户不存在：{user_id}")
        return account

    def _apply(self, account: Account, entry: LedgerEntry) -> None:
        account.frozen += entry.frozen_delta
        account.version += 1
        data = self.store.read()
        data["accounts"][account.user_id] = account.to_dict()
        data["ledger"].append(entry.to_dict())
        self.store.write(data)

    def _entry(self, user_id: str, type_: str, frozen_delta: int, source_id: str, note: str) -> LedgerEntry:
        return LedgerEntry(
            entry_id=uuid4().hex,
            user_id=user_id,
            type=type_,
            amount=0,
            source_id=source_id,
            created_at=self._now(),
            note=note,
            frozen_delta=frozen_delta,
        )

    @staticmethod
    def _validate(amount: int, source_id: str) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise InvalidArgument("amount 必须是整数")
        if amount <= 0:
            raise InvalidArgument(f"冻结/解冻积分必须大于 0，收到 {amount}")
        if not source_id:
            raise InvalidArgument("source_id 不能为空")

    def _snapshot(self, account: Account) -> dict:
        return {
            "balance": account.balance,
            "frozen": account.frozen,
            "available": account.available,
        }

    # -- 对外 --------------------------------------------------------------

    def freeze(self, user_id: str, amount: int, source_id: str) -> dict:
        self._validate(amount, source_id)
        with self._lock:
            account = self._require(user_id)
            if self.ledger.exists(user_id, FREEZE, source_id):
                raise DuplicateRequest(f"冻结单 {source_id} 已处理过")
            if account.available < amount:
                raise InsufficientBalance(required=amount, available=account.available)
            self._apply(
                account,
                self._entry(user_id, FREEZE, amount, source_id, f"冻结 {source_id}"),
            )
            result = self._snapshot(account)
            result["frozen_delta"] = amount
            return result

    def unfreeze(self, user_id: str, amount: int, source_id: str) -> dict:
        self._validate(amount, source_id)
        with self._lock:
            account = self._require(user_id)
            if self.ledger.exists(user_id, UNFREEZE, source_id):
                raise DuplicateRequest(f"解冻单 {source_id} 已处理过")
            if account.frozen < amount:
                raise InvalidArgument(
                    f"解冻金额 {amount} 超过已冻结 {account.frozen}"
                )
            self._apply(
                account,
                self._entry(user_id, UNFREEZE, -amount, source_id, f"解冻 {source_id}"),
            )
            result = self._snapshot(account)
            result["frozen_delta"] = -amount
            return result

    def frozen_amount(self, user_id: str) -> dict:
        account = self._require(user_id)
        return {"user_id": account.user_id, **self._snapshot(account)}
