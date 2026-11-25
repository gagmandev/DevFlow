"""积分账户服务。

写路径的关键约束：**余额与流水必须一次落盘**。
所以 `_apply()` 直接构造完整数据快照后调用一次 `store.write()`，
而不是"先存账户再追加流水"——后者在两次写之间崩溃会产生对不上账的数据。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .errors import AccountNotFound, DuplicateRequest, InsufficientBalance, InvalidArgument
from .models import Account, LedgerEntry
from .repository import AccountRepo, LedgerRepo
from .store import JsonStore

CHECKIN_POINTS = 10
CENTS_PER_POINT = 100


class WalletService:
    def __init__(self, store: JsonStore, clock=None):
        self.store = store
        self.clock = clock or datetime.now
        self.accounts = AccountRepo(store)
        self.ledger = LedgerRepo(store)
        self._lock = store.lock()

    # -- 内部 --------------------------------------------------------------

    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _today(self) -> str:
        return self.clock().date().isoformat()

    def _require(self, user_id: str) -> Account:
        if not user_id:
            raise InvalidArgument("user_id 不能为空")
        account = self.accounts.get(user_id)
        if account is None:
            raise AccountNotFound(f"账户不存在：{user_id}")
        return account

    def _apply(self, account: Account, entry: LedgerEntry) -> None:
        """余额变更 + 流水追加，一次写盘。调用方必须已持有锁。"""
        account.balance += entry.amount
        account.version += 1
        data = self.store.read()
        data["accounts"][account.user_id] = account.to_dict()
        data["ledger"].append(entry.to_dict())
        self.store.write(data)

    def _entry(self, user_id: str, type_: str, amount: int, source_id: str, note: str) -> LedgerEntry:
        return LedgerEntry(
            entry_id=uuid4().hex,
            user_id=user_id,
            type=type_,
            amount=amount,
            source_id=source_id,
            created_at=self._now(),
            note=note,
        )

    # -- 对外 --------------------------------------------------------------

    def open_account(self, user_id: str) -> Account:
        if not user_id:
            raise InvalidArgument("user_id 不能为空")
        with self._lock:
            return self.accounts.get_or_create(user_id)

    def check_in(self, user_id: str) -> dict:
        with self._lock:
            account = self._require(user_id)
            source_id = f"checkin:{self._today()}"
            if self.ledger.exists(user_id, "checkin", source_id):
                return {
                    "credited": False,
                    "reason": "already_checked_in",
                    "amount": 0,
                    "balance": account.balance,
                }
            self._apply(
                account,
                self._entry(user_id, "checkin", CHECKIN_POINTS, source_id, "每日签到"),
            )
            return {
                "credited": True,
                "reason": None,
                "amount": CHECKIN_POINTS,
                "balance": account.balance,
            }

    def reward_order(self, user_id: str, amount_cents: int, order_id: str) -> dict:
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
            raise InvalidArgument("amount_cents 必须是整数（单位：分）")
        if amount_cents <= 0:
            raise InvalidArgument(f"金额必须大于 0，收到 {amount_cents}")
        if not order_id:
            raise InvalidArgument("order_id 不能为空")

        points = amount_cents // CENTS_PER_POINT
        with self._lock:
            account = self._require(user_id)
            if points == 0:
                return {
                    "credited": False,
                    "reason": "amount_too_small",
                    "amount": 0,
                    "balance": account.balance,
                }
            if self.ledger.exists(user_id, "order_reward", order_id):
                return {
                    "credited": False,
                    "reason": "duplicate",
                    "amount": 0,
                    "balance": account.balance,
                }
            self._apply(
                account,
                self._entry(user_id, "order_reward", points, order_id, f"订单奖励 {order_id}"),
            )
            return {
                "credited": True,
                "reason": None,
                "amount": points,
                "balance": account.balance,
            }

    def redeem(self, user_id: str, amount: int, source_id: str) -> dict:
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise InvalidArgument("amount 必须是整数")
        if amount <= 0:
            raise InvalidArgument(f"兑换积分必须大于 0，收到 {amount}")
        if not source_id:
            raise InvalidArgument("source_id 不能为空")

        with self._lock:
            account = self._require(user_id)
            if self.ledger.exists(user_id, "redeem", source_id):
                raise DuplicateRequest(f"兑换单 {source_id} 已处理过")
            if account.available < amount:
                raise InsufficientBalance(required=amount, available=account.available)
            self._apply(
                account,
                self._entry(user_id, "redeem", -amount, source_id, f"兑换 {source_id}"),
            )
            return {
                "redeemed": True,
                "amount": amount,
                "balance": account.balance,
                "available": account.available,
            }

    def balance(self, user_id: str) -> dict:
        account = self._require(user_id)
        return {
            "user_id": account.user_id,
            "balance": account.balance,
            "frozen": account.frozen,
            "available": account.available,
        }
