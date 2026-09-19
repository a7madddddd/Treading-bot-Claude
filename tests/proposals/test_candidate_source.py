import unittest
from datetime import date

from proposals.candidate_source import FixedWatchlistCandidateSource


class TestFixedWatchlistCandidateSource(unittest.TestCase):
    def test_returns_exactly_configured_symbols(self):
        source = FixedWatchlistCandidateSource(["TSLA", "DELL", "NVDA"])
        result = source.get_candidate_symbols(date(2026, 9, 17))
        self.assertEqual(result, ("TSLA", "DELL", "NVDA"))

    def test_source_label_is_fixed_watchlist(self):
        source = FixedWatchlistCandidateSource(["TSLA"])
        self.assertEqual(source.source_label, "fixed_watchlist")

    def test_normalizes_case_and_whitespace(self):
        source = FixedWatchlistCandidateSource([" tsla ", "dell"])
        self.assertEqual(source.get_candidate_symbols(date(2026, 9, 17)), ("TSLA", "DELL"))

    def test_same_symbols_returned_regardless_of_date(self):
        source = FixedWatchlistCandidateSource(["TSLA"])
        self.assertEqual(
            source.get_candidate_symbols(date(2026, 1, 1)),
            source.get_candidate_symbols(date(2030, 1, 1)),
        )

    def test_empty_watchlist_rejected(self):
        with self.assertRaises(ValueError):
            FixedWatchlistCandidateSource([])

    def test_blank_symbol_rejected(self):
        with self.assertRaises(ValueError):
            FixedWatchlistCandidateSource(["TSLA", "   "])


if __name__ == "__main__":
    unittest.main()
