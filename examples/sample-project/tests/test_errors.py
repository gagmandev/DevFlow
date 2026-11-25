"""领域异常的语义测试：错误码、继承关系、可序列化。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.errors import (  # noqa: E402
    AccountNotFound,
    DuplicateRequest,
    InsufficientBalance,
    InvalidArgument,
    PointsError,
)


class ErrorCodeTest(unittest.TestCase):
    def test_all_errors_share_base_class(self):
        for cls in (InvalidArgument, AccountNotFound, InsufficientBalance, DuplicateRequest):
            self.assertTrue(issubclass(cls, PointsError), cls.__name__)

    def test_codes_are_stable(self):
        self.assertEqual(InvalidArgument.code, "INVALID_ARGUMENT")
        self.assertEqual(AccountNotFound.code, "ACCOUNT_NOT_FOUND")
        self.assertEqual(InsufficientBalance.code, "INSUFFICIENT_BALANCE")
        self.assertEqual(DuplicateRequest.code, "DUPLICATE_REQUEST")

    def test_message_kept_in_str(self):
        exc = InvalidArgument("user_id 不能为空")
        self.assertIn("user_id", str(exc))
        self.assertEqual(exc.code, "INVALID_ARGUMENT")

    def test_insufficient_balance_carries_numbers(self):
        exc = InsufficientBalance(required=30, available=12)
        self.assertEqual(exc.required, 30)
        self.assertEqual(exc.available, 12)
        self.assertEqual(exc.to_dict()["available"], 12)
        self.assertIn("12", str(exc))

    def test_catchable_as_points_error(self):
        with self.assertRaises(PointsError):
            raise AccountNotFound("u1")


if __name__ == "__main__":
    unittest.main()
