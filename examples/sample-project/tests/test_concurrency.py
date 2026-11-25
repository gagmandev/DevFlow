"""并发扣减验证：20 个线程同时抢 10 分，只能成功 10 次。"""

import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.errors import InsufficientBalance  # noqa: E402
from points.freeze import FreezeService  # noqa: E402
from points.service import WalletService  # noqa: E402
from points.store import JsonStore  # noqa: E402

THREADS = 20
START_BALANCE = 10


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.service = WalletService(self.store)
        self.service.open_account("u1")
        # 先攒到 10 分：签到 10 分
        self.service.check_in("u1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_overspend(self):
        barrier = threading.Barrier(THREADS)
        results = {"ok": 0, "insufficient": 0, "other": 0}
        observed = []
        lock = threading.Lock()

        def worker(index: int):
            barrier.wait()
            try:
                result = self.service.redeem("u1", 1, f"r-{index}")
                with lock:
                    results["ok"] += 1
                    observed.append(result["balance"])
            except InsufficientBalance:
                with lock:
                    results["insufficient"] += 1
            except Exception:
                with lock:
                    results["other"] += 1

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(worker, range(THREADS)))

        self.assertEqual(results["other"], 0, "不应出现非预期异常")
        self.assertEqual(results["ok"], START_BALANCE)
        self.assertEqual(results["insufficient"], THREADS - START_BALANCE)

        final = self.service.balance("u1")
        self.assertEqual(final["balance"], 0)
        self.assertGreaterEqual(final["balance"], 0)
        self.assertTrue(all(balance >= 0 for balance in observed), "任何时刻余额都不能为负")

        # 1 条签到 + 10 条扣减
        ledger = self.store.read()["ledger"]
        self.assertEqual(len(ledger), START_BALANCE + 1)
        self.assertEqual(sum(item["amount"] for item in ledger), 0)

    def test_balance_never_negative_under_load(self):
        """余额 0 时并发扣减，必须全部失败且余额保持 0。"""
        self.service.redeem("u1", START_BALANCE, "drain")
        barrier = threading.Barrier(THREADS)
        failures = []

        def worker(index: int):
            barrier.wait()
            try:
                self.service.redeem("u1", 1, f"x-{index}")
                failures.append("unexpected success")
            except InsufficientBalance:
                pass

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(worker, range(THREADS)))

        self.assertEqual(failures, [])
        self.assertEqual(self.service.balance("u1")["balance"], 0)


class ConcurrentFreezeTest(unittest.TestCase):
    """CR-001：并发冻结不能超冻（frozen 不能超过 balance）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.wallet = WalletService(self.store)
        self.freezer = FreezeService(self.store)
        self.wallet.open_account("u1")
        self.wallet.check_in("u1")  # 10 分

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_over_freeze(self):
        barrier = threading.Barrier(THREADS)
        results = {"ok": 0, "insufficient": 0, "other": 0}
        observed = []
        lock = threading.Lock()

        def worker(index: int):
            barrier.wait()
            try:
                result = self.freezer.freeze("u1", 1, f"f-{index}")
                with lock:
                    results["ok"] += 1
                    observed.append((result["balance"], result["frozen"]))
            except InsufficientBalance:
                with lock:
                    results["insufficient"] += 1
            except Exception:
                with lock:
                    results["other"] += 1

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(worker, range(THREADS)))

        self.assertEqual(results["other"], 0)
        self.assertEqual(results["ok"], START_BALANCE)
        self.assertEqual(results["insufficient"], THREADS - START_BALANCE)

        final = self.wallet.balance("u1")
        self.assertEqual(final["balance"], START_BALANCE)
        self.assertEqual(final["frozen"], START_BALANCE)
        self.assertEqual(final["available"], 0)
        self.assertTrue(all(frozen <= balance for balance, frozen in observed))

        ledger = self.store.read()["ledger"]
        self.assertEqual(len(ledger), START_BALANCE + 1)
        self.assertEqual(sum(e["frozen_delta"] for e in ledger), final["frozen"])
        self.assertEqual(sum(e["amount"] for e in ledger), final["balance"])


if __name__ == "__main__":
    unittest.main()
