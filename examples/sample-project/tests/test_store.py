"""存储与数据结构的测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.models import Account, LedgerEntry  # noqa: E402
from points.store import JsonStore, StorageError  # noqa: E402


class ModelTest(unittest.TestCase):
    def test_account_available(self):
        acc = Account(user_id="u1", balance=30, frozen=10)
        self.assertEqual(acc.available, 20)

    def test_account_rejects_float(self):
        with self.assertRaises(TypeError):
            Account(user_id="u1", balance=1.5)

    def test_account_roundtrip(self):
        acc = Account(user_id="u1", balance=5)
        self.assertEqual(Account.from_dict(acc.to_dict()), acc)

    def test_ledger_amount_signed(self):
        entry = LedgerEntry(entry_id="e1", user_id="u1", type="redeem", amount=-5, source_id="o1")
        self.assertEqual(entry.amount, -5)
        self.assertEqual(LedgerEntry.from_dict(entry.to_dict()), entry)

    def test_ledger_rejects_zero(self):
        with self.assertRaises(ValueError):
            LedgerEntry(entry_id="e1", user_id="u1", type="redeem", amount=0, source_id="o1")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "nested" / "data.json"
        self.store = JsonStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_file_returns_empty(self):
        self.assertEqual(self.store.read(), {"accounts": {}, "ledger": []})

    def test_write_creates_parent_and_roundtrips(self):
        self.store.write({"accounts": {"u1": {"user_id": "u1", "balance": 3}}, "ledger": []})
        self.assertTrue(self.path.exists())
        self.assertEqual(self.store.read()["accounts"]["u1"]["balance"], 3)

    def test_no_tmp_file_left(self):
        self.store.write({"accounts": {}, "ledger": []})
        leftovers = list(self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_corrupted_file_raises(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.read()

    def test_written_file_is_valid_json(self):
        self.store.write({"accounts": {"u1": {"user_id": "u1"}}, "ledger": []})
        json.loads(self.path.read_text(encoding="utf-8"))


class LedgerFrozenDeltaTest(unittest.TestCase):
    """CR-001：流水增加 frozen_delta，且必须兼容 v1 历史数据。"""

    def test_frozen_delta_roundtrip(self):
        entry = LedgerEntry(
            entry_id="e1", user_id="u1", type="freeze", amount=0,
            frozen_delta=5, source_id="refund-1",
        )
        self.assertEqual(LedgerEntry.from_dict(entry.to_dict()), entry)
        self.assertEqual(entry.to_dict()["frozen_delta"], 5)

    def test_zero_amount_allowed_with_frozen_delta(self):
        entry = LedgerEntry(
            entry_id="e1", user_id="u1", type="freeze", amount=0, frozen_delta=5,
        )
        self.assertEqual(entry.amount, 0)

    def test_both_zero_rejected(self):
        with self.assertRaises(ValueError):
            LedgerEntry(entry_id="e1", user_id="u1", type="freeze", amount=0, frozen_delta=0)

    def test_legacy_row_without_frozen_delta(self):
        legacy = {
            "entry_id": "old-1",
            "user_id": "u1",
            "type": "checkin",
            "amount": 10,
            "source_id": "checkin:2026-11-20",
            "created_at": "2026-11-20T09:00:00",
            "note": "每日签到",
        }
        entry = LedgerEntry.from_dict(legacy)
        self.assertEqual(entry.frozen_delta, 0)
        self.assertEqual(entry.amount, 10)

    def test_positional_call_still_works(self):
        # v1 的调用方（tests/test_queries.py）用的是位置参数，新字段必须加在末尾
        entry = LedgerEntry("e1", "u1", "checkin", 10, "c1", "2026-11-20T09:00:00", "")
        self.assertEqual(entry.source_id, "c1")
        self.assertEqual(entry.frozen_delta, 0)

    def test_account_with_frozen(self):
        account = Account(user_id="u1", balance=30, frozen=12)
        self.assertEqual(account.available, 18)


if __name__ == "__main__":
    unittest.main()
