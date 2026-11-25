"""仓储层测试。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.errors import InvalidArgument  # noqa: E402
from points.models import Account, LedgerEntry  # noqa: E402
from points.repository import AccountRepo, LedgerRepo  # noqa: E402
from points.store import JsonStore  # noqa: E402


def entry(entry_id, user_id="u1", type="checkin", amount=10, source_id="", at="2026-11-20T10:00:00"):
    return LedgerEntry(
        entry_id=entry_id, user_id=user_id, type=type, amount=amount,
        source_id=source_id, created_at=at,
    )


class RepoTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.accounts = AccountRepo(self.store)
        self.ledger = LedgerRepo(self.store)

    def tearDown(self):
        self.tmp.cleanup()


class AccountRepoTest(RepoTestBase):
    def test_get_missing_returns_none(self):
        self.assertIsNone(self.accounts.get("nobody"))

    def test_get_or_create_is_idempotent(self):
        first = self.accounts.get_or_create("u1")
        second = self.accounts.get_or_create("u1")
        self.assertEqual(first.user_id, second.user_id)
        self.assertEqual(len(self.accounts.list_all()), 1)

    def test_get_or_create_rejects_empty(self):
        with self.assertRaises(InvalidArgument):
            self.accounts.get_or_create("")

    def test_save_increments_version(self):
        account = self.accounts.get_or_create("u1")
        self.assertEqual(account.version, 0)
        account.balance = 20
        self.accounts.save(account)
        self.assertEqual(self.accounts.get("u1").version, 1)
        self.assertEqual(self.accounts.get("u1").balance, 20)


class LedgerRepoTest(RepoTestBase):
    def test_append_and_query_desc(self):
        self.ledger.append(entry("e1", at="2026-11-20T10:00:00"))
        self.ledger.append(entry("e2", at="2026-11-20T11:00:00"))
        items, total = self.ledger.query(user_id="u1")
        self.assertEqual(total, 2)
        self.assertEqual([e.entry_id for e in items], ["e2", "e1"])

    def test_exists(self):
        self.ledger.append(entry("e1", source_id="checkin:2026-11-20"))
        self.assertTrue(self.ledger.exists("u1", "checkin", "checkin:2026-11-20"))
        self.assertFalse(self.ledger.exists("u1", "checkin", "checkin:2026-11-21"))

    def test_page_size_clamped_to_100(self):
        for i in range(120):
            self.ledger.append(entry(f"e{i:03d}"))
        items, total = self.ledger.query(size=1000)
        self.assertEqual(len(items), 100)
        self.assertEqual(total, 120)

    def test_time_range_left_closed_right_open(self):
        self.ledger.append(entry("e1", at="2026-11-20T10:00:00"))
        self.ledger.append(entry("e2", at="2026-11-21T10:00:00"))
        items, _ = self.ledger.query(start="2026-11-20T10:00:00", end="2026-11-21T10:00:00")
        self.assertEqual([e.entry_id for e in items], ["e1"])

    def test_out_of_range_page_returns_empty(self):
        self.ledger.append(entry("e1"))
        items, total = self.ledger.query(page=5, size=20)
        self.assertEqual(items, [])
        self.assertEqual(total, 1)

    def test_filter_by_user(self):
        self.ledger.append(entry("e1", user_id="u1"))
        self.ledger.append(entry("e2", user_id="u2"))
        items, total = self.ledger.query(user_id="u2")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].entry_id, "e2")


if __name__ == "__main__":
    unittest.main()
