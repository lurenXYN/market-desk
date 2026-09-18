"""FIFO position lots and exec-diary helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class PositionLotsTests(unittest.TestCase):
    """Exercise multi-fill lots and diary persistence against a temp SQLite DB."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmp.name) / "desk.db"
        import market_desk.db as db

        self.db = db
        self._patcher = mock.patch.object(db, "DB_PATH", self.db_path)
        self._patcher.start()
        db.init_db()

    def tearDown(self) -> None:
        self._patcher.stop()
        # SQLite WAL may keep a handle briefly on Windows.
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def test_add_position_creates_lot_and_average(self) -> None:
        first = self.db.add_position(
            "600000", "浦发", 10.0, 100, "首笔", user_id=1
        )
        self.assertEqual(int(first["qty"]), 100)
        self.assertAlmostEqual(float(first["buy_price"]), 10.0)
        second = self.db.add_position(
            "600000", "浦发", 12.0, 100, "加仓", user_id=1
        )
        self.assertEqual(int(second["qty"]), 200)
        self.assertAlmostEqual(float(second["buy_price"]), 11.0)
        lots = self.db.load_lots_for_positions([int(second["id"])], user_id=1)
        rows = lots.get(int(second["id"])) or []
        self.assertEqual(len(rows), 2)
        self.assertEqual(int(rows[0]["qty"]), 100)
        self.assertAlmostEqual(float(rows[0]["buy_price"]), 10.0)
        self.assertEqual(int(rows[1]["qty"]), 100)
        self.assertAlmostEqual(float(rows[1]["buy_price"]), 12.0)

    def test_fifo_trim_consumes_oldest_lot_first(self) -> None:
        pos = self.db.add_position(
            "600000", "浦发", 10.0, 100, "a", user_id=1
        )
        self.db.add_position("600000", "浦发", 12.0, 100, "b", user_id=1)
        trimmed = self.db.trim_position(
            int(pos["id"]), 100, sell_price=11.0, user_id=1
        )
        self.assertIsNotNone(trimmed)
        assert trimmed is not None
        self.assertEqual(int(trimmed["qty"]), 100)
        chunks = trimmed.get("lot_chunks") or []
        self.assertEqual(len(chunks), 1)
        self.assertAlmostEqual(float(chunks[0]["buy_price"]), 10.0)
        self.assertAlmostEqual(float(trimmed["buy_price"]), 12.0)
        lots = self.db.load_lots_for_positions([int(pos["id"])], user_id=1)
        rows = lots.get(int(pos["id"])) or []
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["buy_price"]), 12.0)

    def test_exec_diary_roundtrip(self) -> None:
        row = self.db.add_exec_diary(
            user_id=1,
            side="buy",
            code="600000",
            name="浦发",
            qty=100,
            price=10.5,
            advice={"action": "可买入", "phase": "修复"},
            note="测试",
        )
        self.assertTrue(row.get("id"))
        items = self.db.load_exec_diary(user_id=1, limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["side"], "buy")
        self.assertEqual(items[0]["advice"].get("action"), "可买入")


if __name__ == "__main__":
    unittest.main()
