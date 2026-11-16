import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.signal import find_breakout_opportunities


def _points(start: datetime, prices: list, step_seconds: int = 60) -> list:
    return [
        {"datetime": (start + timedelta(seconds=i * step_seconds)).isoformat(), "spx": p}
        for i, p in enumerate(prices)
    ]


class TestFindBreakoutOpportunities(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)

    def test_no_data_returns_empty(self):
        self.assertEqual(find_breakout_opportunities([]), [])

    def test_no_breakout_returns_empty(self):
        # 15 min opening range (900s @ 60s/tick = 15 ticks), then flat.
        orb = [100.0] * 16
        flat = [100.0] * 10
        points = _points(self.start, orb + flat)
        self.assertEqual(find_breakout_opportunities(points, opening_range_minutes=15), [])

    def test_single_call_breakout(self):
        orb = [100.0] * 16  # orb_high = orb_low = 100
        breakout = [101.0, 102.0, 103.0]  # above orb_high, VWAP rises with it
        points = _points(self.start, orb + breakout)
        result = find_breakout_opportunities(points, opening_range_minutes=15)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].direction, "call")

    def test_single_put_breakout(self):
        orb = [100.0] * 16
        breakout = [99.0, 98.0, 97.0]
        points = _points(self.start, orb + breakout)
        result = find_breakout_opportunities(points, opening_range_minutes=15)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].direction, "put")

    def test_multiple_opportunities_require_return_inside_range(self):
        orb = [100.0] * 16
        # breakout up, back inside, breakout up again -> 2 opportunities
        sequence = [101.0, 102.0, 100.0, 101.5, 102.5]
        points = _points(self.start, orb + sequence)
        result = find_breakout_opportunities(points, opening_range_minutes=15)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r.direction == "call" for r in result))

    def test_continued_excursion_counts_once(self):
        orb = [100.0] * 16
        # never returns inside the range -- one continuous excursion
        sequence = [101.0, 102.0, 103.0, 104.0, 105.0]
        points = _points(self.start, orb + sequence)
        result = find_breakout_opportunities(points, opening_range_minutes=15)
        self.assertEqual(len(result), 1)

    def test_vwap_confirmation_rejects_a_weak_second_breakout(self):
        # orb: 16 ticks at 100 -> orb_high = orb_low = 100.
        orb = [100.0] * 16
        # A: price 150, well above orb_high and above TWAP -> confirmed, opportunity #1.
        # B: price 149, same excursion ("call") -> not re-checked.
        # C: price 100, back inside the range -> resets outside_since_last.
        # D: price 101, breaks orb_high again, but by now TWAP (pulled up
        #    by A/B's high prices) is ~105 -- 101 <= TWAP, so this does
        #    NOT confirm as a new call opportunity.
        sequence = [150.0, 149.0, 100.0, 101.0]
        points = _points(self.start, orb + sequence)
        result = find_breakout_opportunities(points, opening_range_minutes=15, require_vwap_confirmation=True)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].price_at_entry, 150.0)

    def test_vwap_confirmation_can_be_disabled(self):
        orb = [100.0] * 16
        breakout = [101.0]
        points = _points(self.start, orb + breakout)
        with_confirmation = find_breakout_opportunities(points, opening_range_minutes=15, require_vwap_confirmation=True)
        without_confirmation = find_breakout_opportunities(points, opening_range_minutes=15, require_vwap_confirmation=False)
        self.assertEqual(len(with_confirmation), 1)
        self.assertEqual(len(without_confirmation), 1)


if __name__ == "__main__":
    unittest.main()
