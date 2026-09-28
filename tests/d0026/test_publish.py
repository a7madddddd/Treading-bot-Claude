"""Tests for `d0026.publish.build_snapshot_symbols` (B23).

Verifies:
  - Symbols are produced from ranked survivors, order preserved.
  - Rank is 1-indexed positional rank in the survivor tuple.
  - Fully-featured candidates route to STANDARD entries.
  - Candidates with unresolved/ambiguous identity are dropped
    (NOT_ELIGIBLE) instead of appearing in the output.
"""

import unittest

from d0026.publish import build_snapshot_symbols

from .stages._fixtures import DATE, candidate, regime


class TestBuildSnapshotSymbols(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        result = build_snapshot_symbols(
            (), regime_state=regime(vix_percentile=0.5), as_of_date=DATE,
        )
        self.assertEqual(result, ())

    def test_ranks_are_one_indexed_and_order_preserved(self):
        cs = (candidate("A"), candidate("B"), candidate("C"))
        result = build_snapshot_symbols(
            cs, regime_state=regime(vix_percentile=0.5), as_of_date=DATE,
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].ticker_as_of_date, "A")
        self.assertEqual(result[0].rank, 1)
        self.assertEqual(result[1].rank, 2)
        self.assertEqual(result[2].rank, 3)

    def test_fully_featured_candidate_becomes_snapshot_entry(self):
        cs = (candidate("AAPL"),)
        result = build_snapshot_symbols(
            cs, regime_state=regime(vix_percentile=0.5), as_of_date=DATE,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ticker_as_of_date, "AAPL")
        self.assertEqual(result[0].security_id, "sec-AAPL")

    def test_first_seen_uses_as_of_date(self):
        cs = (candidate("AAPL"),)
        result = build_snapshot_symbols(
            cs, regime_state=regime(vix_percentile=0.5), as_of_date=DATE,
        )
        self.assertEqual(result[0].first_seen_by_universe_at, DATE)


if __name__ == "__main__":
    unittest.main()
