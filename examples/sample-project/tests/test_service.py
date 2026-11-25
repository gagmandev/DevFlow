"""账户服务测试：用真实临时存储，不 mock。"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.errors import (  # noqa: E402
    AccountNotFound,
    DuplicateRequest,
    InsufficientBalance,
    InvalidArgument,
)
from points.service import WalletService  # noqa: E402
from points.store import JsonStore  # noqa: E402


class FakeClock:
    def __init__(self, moment: datetime):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs) -> None:
        self.moment += timedelta(**kwargs)


class ServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.clock = FakeClock(datetime(2026, 11, 20, 9, 0, 0))
        self.service = WalletService(self.store, clock=self.clock)
        self.service.open_account("u1")

    def tearDown(self):
        self.tmp.cleanup()

    def ledger_count(self):
        return len(self.store.read()["ledger"])


class OpenAccountTest(ServiceTestBase):
    def test_idempotent(self):
        again = self.service.open_account("u1")
        self.assertEqual(again.balance, 0)
        self.assertEqual(len(self.store.read()["accounts"]), 1)

    def test_rejects_empty_user_id(self):
        with self.assertRaises(InvalidArgument):
            self.service.open_account("")


class CheckInTest(ServiceTestBase):
    def test_first_check_in_credits_ten(self):
        result = self.service.check_in("u1")
        self.assertTrue(result["credited"])
        self.assertEqual(result["balance"], 10)
        self.assertEqual(self.ledger_count(), 1)

    def test_same_day_second_check_in_does_nothing(self):
        self.service.check_in("u1")
        result = self.service.check_in("u1")
        self.assertFalse(result["credited"])
        self.assertEqual(result["reason"], "already_checked_in")
        self.assertEqual(self.service.balance("u1")["balance"], 10)
        self.assertEqual(self.ledger_count(), 1)

    def test_next_day_credits_again(self):
        self.service.check_in("u1")
        self.clock.advance(days=1)
        result = self.service.check_in("u1")
        self.assertTrue(result["credited"])
        self.assertEqual(result["balance"], 20)

    def test_check_in_unknown_user(self):
        with self.assertRaises(AccountNotFound):
            self.service.check_in("nobody")


class RewardTest(ServiceTestBase):
    def test_rounding_down(self):
        self.assertEqual(self.service.reward_order("u1", 1999, "o1")["amount"], 19)
        self.assertEqual(self.service.reward_order("u1", 100, "o2")["amount"], 1)
        result = self.service.reward_order("u1", 99, "o3")
        self.assertFalse(result["credited"])
        self.assertEqual(result["reason"], "amount_too_small")
        self.assertEqual(self.service.balance("u1")["balance"], 20)

    def test_duplicate_order_not_credited_twice(self):
        self.service.reward_order("u1", 500, "o1")
        result = self.service.reward_order("u1", 500, "o1")
        self.assertFalse(result["credited"])
        self.assertEqual(result["reason"], "duplicate")
        self.assertEqual(self.service.balance("u1")["balance"], 5)
        self.assertEqual(self.ledger_count(), 1)

    def test_invalid_amount(self):
        with self.assertRaises(InvalidArgument):
            self.service.reward_order("u1", 0, "o1")
        with self.assertRaises(InvalidArgument):
            self.service.reward_order("u1", -100, "o1")


class RedeemTest(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.service.reward_order("u1", 2000, "o1")  # 20 分

    def test_redeem_success(self):
        result = self.service.redeem("u1", 5, "r1")
        self.assertTrue(result["redeemed"])
        self.assertEqual(result["balance"], 15)

    def test_insufficient_has_no_side_effect(self):
        before = self.service.balance("u1")
        count = self.ledger_count()
        with self.assertRaises(InsufficientBalance) as ctx:
            self.service.redeem("u1", 21, "r1")
        self.assertEqual(ctx.exception.available, 20)
        self.assertEqual(self.service.balance("u1"), before)
        self.assertEqual(self.ledger_count(), count)

    def test_redeem_to_zero_allowed(self):
        result = self.service.redeem("u1", 20, "r1")
        self.assertEqual(result["balance"], 0)
        with self.assertRaises(InsufficientBalance):
            self.service.redeem("u1", 1, "r2")

    def test_duplicate_source_rejected(self):
        self.service.redeem("u1", 5, "r1")
        with self.assertRaises(DuplicateRequest):
            self.service.redeem("u1", 5, "r1")
        self.assertEqual(self.service.balance("u1")["balance"], 15)

    def test_redeem_rejects_non_positive(self):
        with self.assertRaises(InvalidArgument):
            self.service.redeem("u1", 0, "r1")


class BalanceTest(ServiceTestBase):
    def test_shape_and_math(self):
        self.service.check_in("u1")
        balance = self.service.balance("u1")
        self.assertEqual(balance["balance"], 10)
        self.assertEqual(balance["frozen"], 0)
        self.assertEqual(balance["available"], balance["balance"] - balance["frozen"])

    def test_unknown_user(self):
        with self.assertRaises(AccountNotFound):
            self.service.balance("nobody")

    def test_balance_equals_ledger_sum(self):
        self.service.check_in("u1")
        self.service.reward_order("u1", 1999, "o1")
        self.service.redeem("u1", 4, "r1")
        total = sum(item["amount"] for item in self.store.read()["ledger"])
        self.assertEqual(self.service.balance("u1")["balance"], total)


if __name__ == "__main__":
    unittest.main()
