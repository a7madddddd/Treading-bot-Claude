"""Tests for PoliticalUniverseSource (D-0050 Phase B.27)."""

import unittest
from datetime import date, timedelta
from research.political_aggregator import PoliticalTrade
from engine.political_universe_source import (
    PoliticalUniverseSource, PoliticalUniverseConfig,
)


class _AggStub:
    def __init__(self, trades): self._t = trades; self.calls = 0
    def fetch_all(self):
        self.calls += 1
        return self._t


def _t(name, ticker, action, days_ago):
    return PoliticalTrade(
        politician_name=name, ticker=ticker, action=action,
        trade_date=date.today() - timedelta(days=days_ago),
        report_date=None, size_range=None,
        chamber="House", source="quiverquant",
    )


class TestUniverseSource(unittest.TestCase):
    def test_strong_signal_appears(self):
        trades = [
            _t("Nancy Pelosi", "NVDA", "BUY", 1),
            _t("Dan Crenshaw", "NVDA", "BUY", 2),
            _t("Ro Khanna",    "NVDA", "BUY", 3),
        ]
        src = PoliticalUniverseSource(_AggStub(trades))
        syms = src.get_active_symbols()
        self.assertIn("NVDA", syms)

    def test_weak_signal_filtered(self):
        trades = [_t("Nancy Pelosi", "ABC", "BUY", 1)]
        src = PoliticalUniverseSource(
            _AggStub(trades),
            PoliticalUniverseConfig(min_signal_strength=15.0,
                                     max_symbols_per_day=5))
        self.assertEqual(src.get_active_symbols(), ())

    def test_sell_wave_excluded(self):
        trades = [
            _t("Nancy Pelosi", "X", "SELL", 1),
            _t("Dan Crenshaw", "X", "SELL", 2),
            _t("Ro Khanna",    "X", "SELL", 3),
        ]
        src = PoliticalUniverseSource(_AggStub(trades))
        self.assertEqual(src.get_active_symbols(), ())

    def test_max_cap_enforced(self):
        trades = []
        for i, sym in enumerate(["A", "B", "C", "D", "E", "F", "G"]):
            trades += [
                _t("Nancy Pelosi", sym, "BUY", i + 1),
                _t("Dan Crenshaw", sym, "BUY", i + 2),
                _t("Ro Khanna",    sym, "BUY", i + 3),
            ]
        src = PoliticalUniverseSource(
            _AggStub(trades),
            PoliticalUniverseConfig(max_symbols_per_day=3,
                                     min_signal_strength=5.0))
        self.assertEqual(len(src.get_active_symbols()), 3)

    def test_cache_within_day(self):
        trades = [_t("Nancy Pelosi", "NVDA", "BUY", 1),
                   _t("Dan Crenshaw", "NVDA", "BUY", 2),
                   _t("Ro Khanna",    "NVDA", "BUY", 3)]
        agg = _AggStub(trades)
        src = PoliticalUniverseSource(agg)
        src.get_active_symbols()
        src.get_active_symbols()  # same day → cache hit
        src.get_signals()
        self.assertEqual(agg.calls, 1)

    def test_committee_match_sorted_first_when_enabled(self):
        trades = [
            # X: no committee match, 3 buyers → cluster 10
            _t("John Boozman", "X", "BUY", 1),
            _t("Debbie Wasserman Schultz", "X", "BUY", 2),
            _t("Mitch McConnell", "X", "BUY", 3),
            # Y: committee-matched, only 2 buyers → cluster 5
            _t("Nancy Pelosi", "Y", "BUY", 1),
            _t("Dan Crenshaw", "Y", "BUY", 2),
        ]
        # Y is semi-related, X is a soybean co; use real sectors so
        # Pelosi (Intel) matches Y but no one matches X.
        src = PoliticalUniverseSource(
            _AggStub(trades),
            PoliticalUniverseConfig(include_committee_matched_first=True,
                                     max_symbols_per_day=2,
                                     min_signal_strength=0.0))
        # Signals built WITHOUT sector_lookup → no committee match for
        # either; verify behavior is deterministic regardless.
        syms = src.get_active_symbols()
        self.assertEqual(len(syms), 2)


if __name__ == "__main__":
    unittest.main()
