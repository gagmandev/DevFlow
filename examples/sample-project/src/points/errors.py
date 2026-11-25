"""领域异常。

所有对外抛出的错误都继承 :class:`PointsError`，并带一个稳定的 ``code``，
调用方按 code 分支，不靠解析错误文案。
"""

from __future__ import annotations


class PointsError(Exception):
    """积分领域异常基类。"""

    code = "POINTS_ERROR"

    def __init__(self, message: str = "", **details):
        self.message = message or self.code
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict:
        data = {"code": self.code, "message": self.message}
        data.update(self.details)
        return data


class InvalidArgument(PointsError):
    """入参不合法（空 user_id、非正数金额等）。"""

    code = "INVALID_ARGUMENT"


class AccountNotFound(PointsError):
    """账户不存在。"""

    code = "ACCOUNT_NOT_FOUND"


class InsufficientBalance(PointsError):
    """可用积分不足。"""

    code = "INSUFFICIENT_BALANCE"

    def __init__(self, required: int, available: int, message: str | None = None):
        super().__init__(
            message or f"积分不足：需要 {required}，可用 {available}",
            required=required,
            available=available,
        )
        self.required = required
        self.available = available


class DuplicateRequest(PointsError):
    """同一来源单号被重复提交。"""

    code = "DUPLICATE_REQUEST"
