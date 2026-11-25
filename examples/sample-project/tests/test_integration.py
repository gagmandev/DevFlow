"""端到端链路：开户 → 签到 → 下单奖励 → 兑换 → 查流水 → 查余额。

用真实文件存储，不 mock 任何一层。
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.errors import InsufficientBalance  # noqa: E402
from points.freeze import FreezeService  # noqa: E402
from points.queries import LedgerQuery  # noqa: E402
from points.service import WalletService  # noqa: E402
from points.store import JsonStore  # noqa: E402


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.service = WalletService(self.store)
        self.query = LedgerQuery(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_chain(self):
        # 1. 开户
        account = self.service.open_account("u1")
        self.assertEqual(account.balance, 0)

        # 2. 签到 +10
        self.assertTrue(self.service.check_in("u1")["credited"])

        # 3. 订单 19.99 元 → 19 分
        self.assertEqual(self.service.reward_order("u1", 1999, "order-1")["amount"], 19)

        # 4. 兑换 5 分
        self.service.redeem("u1", 5, "redeem-1")

        # 5. 查余额：10 + 19 - 5 = 24
        balance = self.service.balance("u1")
        self.assertEqual(balance["balance"], 24)
        self.assertEqual(balance["available"], 24)

        # 6. 查流水：3 条，类型与符号正确
        ledger = self.query.by_user("u1")
        self.assertEqual(ledger["total"], 3)
        types = sorted(item["type"] for item in ledger["items"])
        self.assertEqual(types, ["checkin", "order_reward", "redeem"])

        # 7. 对账：流水累计 = 余额
        total = sum(item["amount"] for item in self.store.read()["ledger"])
        self.assertEqual(total, balance["balance"])

    def test_repeat_flow_is_idempotent(self):
        self.service.open_account("u1")
        self.service.check_in("u1")
        self.service.reward_order("u1", 1000, "order-1")

        # 重复调用：签到、奖励都不应再入账
        self.assertFalse(self.service.check_in("u1")["credited"])
        self.assertFalse(self.service.reward_order("u1", 1000, "order-1")["credited"])

        self.assertEqual(self.service.balance("u1")["balance"], 20)
        self.assertEqual(self.query.by_user("u1")["total"], 2)


class FreezeFlowTest(unittest.TestCase):
    """CR-001：退款冻结链路。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.wallet = WalletService(self.store)
        self.freezer = FreezeService(self.store)
        self.query = LedgerQuery(self.store)
        self.wallet.open_account("u1")
        self.wallet.check_in("u1")                    # +10
        self.wallet.reward_order("u1", 1999, "order-1")  # +19 → 29

    def tearDown(self):
        self.tmp.cleanup()

    def test_freeze_blocks_then_unfreeze_restores(self):
        # 冻结 8：balance 不变，available 21
        result = self.freezer.freeze("u1", 8, "refund-1")
        self.assertEqual(result["balance"], 29)
        self.assertEqual(result["frozen"], 8)
        self.assertEqual(result["available"], 21)

        # 兑换 22 超过可用额度
        with self.assertRaises(InsufficientBalance):
            self.wallet.redeem("u1", 22, "redeem-1")

        # 兑换 21 刚好用完可用额度
        self.wallet.redeem("u1", 21, "redeem-2")
        self.assertEqual(self.wallet.balance("u1")["available"], 0)

        # 解冻 8 后可用恢复，再兑换 8
        self.freezer.unfreeze("u1", 8, "unfreeze-1")
        self.wallet.redeem("u1", 8, "redeem-3")
        final = self.wallet.balance("u1")
        self.assertEqual(final["balance"], 0)
        self.assertEqual(final["frozen"], 0)
        self.assertEqual(final["available"], 0)

    def test_two_reconciliations(self):
        self.freezer.freeze("u1", 8, "refund-1")
        self.wallet.redeem("u1", 5, "redeem-1")
        self.freezer.unfreeze("u1", 3, "unfreeze-1")

        ledger = self.store.read()["ledger"]
        balance = self.wallet.balance("u1")
        self.assertEqual(sum(e["amount"] for e in ledger), balance["balance"])
        self.assertEqual(sum(e["frozen_delta"] for e in ledger), balance["frozen"])
        self.assertEqual(balance["available"], balance["balance"] - balance["frozen"])

    def test_ledger_contains_freeze_types(self):
        self.freezer.freeze("u1", 8, "refund-1")
        self.freezer.unfreeze("u1", 8, "unfreeze-1")
        types = [item["type"] for item in self.query.by_user("u1")["items"]]
        self.assertEqual(types.count("freeze"), 1)
        self.assertEqual(types.count("unfreeze"), 1)


if __name__ == "__main__":
    unittest.main()
