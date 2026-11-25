"""账户与流水的数据结构。

约定：
- 积分一律是 ``int``，不接受 float / bool；
- 时间一律是本地时间的 ISO 字符串（``datetime.now().isoformat``）；
- 序列化字段名与 JSON 完全一致，不做驼峰转换。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_int(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} 必须是 int，收到 {type(value).__name__}")
    return value


@dataclass
class Account:
    """积分账户。一个 user_id 对应一个账户。"""

    user_id: str
    balance: int = 0
    frozen: int = 0
    version: int = 0
    created_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("user_id 不能为空")
        self.balance = _as_int(self.balance, "balance")
        self.frozen = _as_int(self.frozen, "frozen")
        self.version = _as_int(self.version, "version")

    @property
    def available(self) -> int:
        """可用积分 = 总额 - 冻结。本期 frozen 恒为 0。"""
        return self.balance - self.frozen

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        return cls(
            user_id=data["user_id"],
            balance=_as_int(data.get("balance", 0), "balance"),
            frozen=_as_int(data.get("frozen", 0), "frozen"),
            version=_as_int(data.get("version", 0), "version"),
            created_at=data.get("created_at") or now_iso(),
        )


@dataclass(frozen=True)
class LedgerEntry:
    """一条积分流水。

    ``amount``：对 balance 的影响，带符号（入账为正，扣减为负）。
    ``frozen_delta``：对 frozen 的影响，带符号（冻结为正，解冻为负）。
    两者不能同时为 0；纯冻结流水 ``amount=0`` 是合法的。
    """

    entry_id: str
    user_id: str
    type: str
    amount: int
    source_id: str = ""
    created_at: str = field(default_factory=now_iso)
    note: str = ""
    # 新增字段放在最后：v1 的调用方用位置参数构造，插在中间会静默错位
    frozen_delta: int = 0

    def __post_init__(self) -> None:
        if not self.entry_id:
            raise ValueError("entry_id 不能为空")
        if not self.user_id:
            raise ValueError("user_id 不能为空")
        _as_int(self.amount, "amount")
        _as_int(self.frozen_delta, "frozen_delta")
        if self.amount == 0 and self.frozen_delta == 0:
            raise ValueError("amount 与 frozen_delta 不能同时为 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LedgerEntry":
        return cls(
            entry_id=data["entry_id"],
            user_id=data["user_id"],
            type=data["type"],
            amount=_as_int(data["amount"], "amount"),
            # v1 的历史流水没有这个字段，默认 0
            frozen_delta=_as_int(data.get("frozen_delta", 0), "frozen_delta"),
            source_id=data.get("source_id", ""),
            created_at=data.get("created_at") or now_iso(),
            note=data.get("note", ""),
        )
