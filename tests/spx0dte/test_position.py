import unittest
from datetime import datetime, timezone

from spx0dte.position import OpenPosition


def _make(**overrides) -> OpenPosition:
    defaults = dict(
        proposal_id="SPX-1", occ_symbol="SPXW261117C05900000", direction="call",
        quantity=1, strike=5900, date="2026-11-17",
        entry_time=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc),
        entry_price=1.50, hold_cap_minutes=40.0,
    )
    defaults.update(overrides)
    return OpenPosition(**defaults)


class TestOpenPositionValidation(unittest.TestCase):
    def test_valid_open_position(self):
        p = _make()
        self.assertFalse(p.is_closed)

    def test_rejects_bad_direction(self):
        with self.assertRaises(ValueError):
            _make(direction="up")

    def test_rejects_nonpositive_quantity(self):
        with self.assertRaises(ValueError):
            _make(quantity=0)

    def test_rejects_nonpositive_entry_price(self):
        with self.assertRaises(ValueError):
            _make(entry_price=0)

    def test_open_position_cannot_carry_exit_fields(self):
        with self.assertRaises(ValueError):
            _make(exit_reason="target")


class TestWithExit(unittest.TestCase):
    def test_closes_cleanly(self):
        p = _make()
        exit_time = datetime(2026, 11, 17, 15, 40, tzinfo=timezone.utc)
        closed = p.with_exit(exit_time=exit_time, exit_price=2.5, exit_reason="target")
        self.assertTrue(closed.is_closed)
        self.assertEqual(closed.exit_price, 2.5)
        self.assertEqual(closed.exit_reason, "target")

    def test_cannot_close_twice(self):
        p = _make()
        exit_time = datetime(2026, 11, 17, 15, 40, tzinfo=timezone.utc)
        closed = p.with_exit(exit_time=exit_time, exit_price=2.5, exit_reason="target")
        with self.assertRaises(ValueError):
            closed.with_exit(exit_time=exit_time, exit_price=1.0, exit_reason="stop_loss")

    def test_original_untouched(self):
        p = _make()
        p.with_exit(
            exit_time=datetime(2026, 11, 17, 15, 40, tzinfo=timezone.utc),
            exit_price=2.5, exit_reason="target",
        )
        self.assertFalse(p.is_closed)


if __name__ == "__main__":
    unittest.main()
