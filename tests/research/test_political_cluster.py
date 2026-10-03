"""Tests for political clustering + scoring (B.24)."""

import unittest
from datetime import date, timedelta
from research.political_aggregator import PoliticalTrade
from research.political_cluster import build_signals, TickerPoliticalSignal


def _t(name, ticker, action, days_ago):
    return PoliticalTrade(
        politician_name=name, ticker=ticker, action=action,
        trade_date=date.today() - timedelta(days=days_ago),
        report_date=None, size_range=None,
        chamber="House", source="quiverquant",
    )


class TestBuildSignals(unittest.TestCase):
    def test_single_buyer_minimal_signal(self):
        trades = [_t("Nancy Pelosi", "NVDA", "BUY", 2)]
        signals = build_signals(trades)
        self.assertIn("NVDA", signals)
        s = signals["NVDA"]
        self.assertEqual(s.politician_buys_30d, 1)
        self.assertEqual(s.cluster_score, 0.0)   # single buyer → no cluster
        self.assertGreater(s.weighted_signal, 0)  # alpha + maybe cmte

    def test_cluster_of_three_boosts_score(self):
        trades = [
            _t("Nancy Pelosi", "NVDA", "BUY", 2),
            _t("Dan Crenshaw", "NVDA", "BUY", 5),
            _t("Ro Khanna",    "NVDA", "BUY", 7),
        ]
        signals = build_signals(trades)
        s = signals["NVDA"]
        self.assertEqual(s.politician_buys_30d, 3)
        self.assertEqual(s.cluster_score, 10.0)  # (3-1)*5
        self.assertGreater(s.weighted_signal, 10.0)

    def test_old_trades_excluded(self):
        trades = [
            _t("Nancy Pelosi", "NVDA", "BUY", 60),  # outside 30d window
        ]
        self.assertNotIn("NVDA", build_signals(trades))

    def test_sell_wave_detected(self):
        trades = [
            _t("Nancy Pelosi", "NVDA", "SELL", 5),
            _t("Dan Crenshaw", "NVDA", "SELL", 7),
            _t("Ro Khanna",    "NVDA", "SELL", 10),
        ]
        signals = build_signals(trades)
        self.assertTrue(signals["NVDA"].sell_wave)
        self.assertEqual(signals["NVDA"].politician_sells_30d, 3)

    def test_sell_wave_requires_three(self):
        trades = [
            _t("Nancy Pelosi", "NVDA", "SELL", 5),
            _t("Dan Crenshaw", "NVDA", "SELL", 7),
        ]
        self.assertFalse(signals_from(trades)["NVDA"].sell_wave)

    def test_committee_match_boost(self):
        """Pelosi (Intel Cmte) buys semiconductor → committee_match."""
        trades = [_t("Nancy Pelosi", "NVDA", "BUY", 2)]
        signals = build_signals(trades, sector_lookup={
            "NVDA": "SEMICONDUCTORS & RELATED DEVICES",
        })
        self.assertTrue(signals["NVDA"].committee_match)

    def test_unrelated_sector_no_committee_match(self):
        trades = [_t("Nancy Pelosi", "KO", "BUY", 2)]
        signals = build_signals(trades, sector_lookup={
            "KO": "BEVERAGES",
        })
        self.assertFalse(signals["KO"].committee_match)

    def test_weighted_signal_cap(self):
        """5+ buyers → cluster_score = 20, plus bonuses, cap at 25."""
        trades = [
            _t("Nancy Pelosi", "NVDA", "BUY", 1),
            _t("Dan Crenshaw", "NVDA", "BUY", 2),
            _t("Ro Khanna",    "NVDA", "BUY", 3),
            _t("Josh Gottheimer", "NVDA", "BUY", 4),
            _t("Michael McCaul", "NVDA", "BUY", 5),
        ]
        signals = build_signals(trades, sector_lookup={
            "NVDA": "SEMICONDUCTORS & RELATED DEVICES",
        })
        s = signals["NVDA"]
        self.assertEqual(s.cluster_score, 20.0)
        self.assertLessEqual(s.weighted_signal, 25.0)
        self.assertGreaterEqual(s.weighted_signal, 20.0)

    def test_pending_tickers_ignored(self):
        """eDisclosure pending markers must not pollute signal maps."""
        trades = [_t("Nancy Pelosi", "PENDING", "BUY", 1)]
        self.assertEqual(build_signals(trades), {})


def signals_from(trades):
    return build_signals(trades)


if __name__ == "__main__":
    unittest.main()
