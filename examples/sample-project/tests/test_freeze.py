"""冻结 / 解冻服务测试（CR-001）。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.errors import (  # noqa: E402
    AccountNotFound,
    DuplicateRequest,
    InsufficientBalance,
    InvalidArgument,
)
from points.freeze import FreezeService  # noqa: E402
from points.service import WalletService  # noqa: E402
from points.store import JsonStore  # noqa: E402


class FreezeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.wallet = WalletService(self.store)
        self.freeze_service = FreezeService(self.store)
        self.wallet.open_account("u1")
        self.wallet.check_in("u1")  # 10 分

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self):
        return self.store.read()["ledger"]


class FreezeTest(FreezeTestBase):
    def test_freeze_moves_available_to_frozen(self):
        result = self.freeze_service.freeze("u1", 4, "refund-1")
        self.assertEqual(result["balance"], 10)
        self.assertEqual(result["frozen"], 4)
        self.assertEqual(result["available"], 6)
        self.assertEqual(self.wallet.balance("u1")["balance"], 10)

    def test_freeze_insufficient_has_no_side_effect(self):
        before = self.wallet.balance("u1")
        count = len(self.ledger())
        with self.assertRaises(InsufficientBalance) as ctx:
            self.freeze_service.freeze("u1", 11, "refund-1")
        self.assertEqual(ctx.exception.available, 10)
        self.assertEqual(self.wallet.balance("u1"), before)
        self.assertEqual(len(self.ledger()), count)

    def test_duplicate_freeze_rejected(self):
        self.freeze_service.freeze("u1", 3, "refund-1")
        with self.assertRaises(DuplicateRequest):
            self.freeze_service.freeze("u1", 3, "refund-1")
        self.assertEqual(self.freeze_service.frozen_amount("u1")["frozen"], 3)

    def test_freeze_rejects_bad_amount(self):
        with self.assertRaises(InvalidArgument):
            self.freeze_service.freeze("u1", 0, "refund-1")
        with self.assertRaises(InvalidArgument):
            self.freeze_service.freeze("u1", -1, "refund-1")
        with self.assertRaises(InvalidArgument):
            self.freeze_service.freeze("u1", 1, "")

    def test_freeze_unknown_user(self):
        with self.assertRaises(AccountNotFound):
            self.freeze_service.freeze("nobody", 1, "refund-1")

    def test_freeze_ledger_entry_shape(self):
        self.freeze_service.freeze("u1", 4, "refund-1")
        entry = self.ledger()[-1]
        self.assertEqual(entry["type"], "freeze")
        self.assertEqual(entry["amount"], 0)
        self.assertEqual(entry["frozen_delta"], 4)


class UnfreezeTest(FreezeTestBase):
    def setUp(self):
        super().setUp()
        self.freeze_service.freeze("u1", 8, "refund-1")

    def test_partial_unfreeze(self):
        result = self.freeze_service.unfreeze("u1", 3, "unfreeze-1")
        self.assertEqual(result["frozen"], 5)
        self.assertEqual(result["available"], 5)
        self.assertEqual(result["balance"], 10)

    def test_full_unfreeze(self):
        result = self.freeze_service.unfreeze("u1", 8, "unfreeze-1")
        self.assertEqual(result["frozen"], 0)
        self.assertEqual(result["available"], 10)

    def test_unfreeze_more_than_frozen_rejected(self):
        with self.assertRaises(InvalidArgument):
            self.freeze_service.unfreeze("u1", 9, "unfreeze-1")
        self.assertEqual(self.freeze_service.frozen_amount("u1")["frozen"], 8)

    def test_duplicate_unfreeze_rejected(self):
        self.freeze_service.unfreeze("u1", 2, "unfreeze-1")
        with self.assertRaises(DuplicateRequest):
            self.freeze_service.unfreeze("u1", 2, "unfreeze-1")

    def test_reconciliation(self):
        self.freeze_service.unfreeze("u1", 3, "unfreeze-1")
        self.freeze_service.freeze("u1", 1, "refund-2")
        account = self.wallet.balance("u1")
        frozen_total = sum(e["frozen_delta"] for e in self.ledger())
        balance_total = sum(e["amount"] for e in self.ledger())
        self.assertEqual(frozen_total, account["frozen"])
        self.assertEqual(balance_total, account["balance"])


class FreezeAffectsRedeemTest(FreezeTestBase):
    def test_redeem_uses_available_after_freeze(self):
        self.freeze_service.freeze("u1", 6, "refund-1")  # available 4
        with self.assertRaises(InsufficientBalance):
            self.wallet.redeem("u1", 5, "redeem-1")
        self.wallet.redeem("u1", 4, "redeem-2")
        self.assertEqual(self.wallet.balance("u1")["available"], 0)

    def test_unfreeze_restores_spending_power(self):
        self.freeze_service.freeze("u1", 6, "refund-1")
        self.freeze_service.unfreeze("u1", 6, "unfreeze-1")
        self.wallet.redeem("u1", 10, "redeem-1")
        self.assertEqual(self.wallet.balance("u1")["balance"], 0)


if __name__ == "__main__":
    unittest.main()
