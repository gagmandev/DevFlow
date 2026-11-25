"""流水查询服务测试。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from points.models import LedgerEntry  # noqa: E402
from points.queries import LedgerQuery  # noqa: E402
from points.repository import LedgerRepo  # noqa: E402
from points.store import JsonStore  # noqa: E402


class LedgerQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(Path(self.tmp.name) / "data.json")
        self.repo = LedgerRepo(self.store)
        self.query = LedgerQuery(self.store)
        self.repo.append(LedgerEntry("e1", "u1", "checkin", 10, "c1", "2026-11-20T09:00:00", ""))
        self.repo.append(LedgerEntry("e2", "u2", "checkin", 10, "c2", "2026-11-20T10:00:00", ""))
        self.repo.append(LedgerEntry("e3", "u1", "redeem", -5, "r1", "2026-11-20T11:00:00", ""))

    def tearDown(self):
        self.tmp.cleanup()

    def test_by_user_desc(self):
        result = self.query.by_user("u1")
        self.assertEqual([i["entry_id"] for i in result["items"]], ["e3", "e1"])
        self.assertEqual(result["total"], 2)

    def test_all_without_user_id(self):
        result = self.query.all()
        self.assertEqual(result["total"], 3)
        self.assertEqual([i["entry_id"] for i in result["items"]], ["e3", "e2", "e1"])

    def test_time_range_left_closed_right_open(self):
        result = self.query.all(start="2026-11-20T09:00:00", end="2026-11-20T11:00:00")
        self.assertEqual([i["entry_id"] for i in result["items"]], ["e2", "e1"])

    def test_page_out_of_range(self):
        result = self.query.all(page=3, size=2)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 3)

    def test_size_clamped(self):
        result = self.query.all(size=1000)
        self.assertEqual(result["size"], 100)
        self.assertEqual(len(result["items"]), 3)


if __name__ == "__main__":
    unittest.main()
