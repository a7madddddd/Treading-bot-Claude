"""Tests for Phase 12 trend / relative-strength / volatility signals."""

import unittest
from datetime import date, datetime, timedelta

from engine.research_hub import SymbolResearch, SymbolResearchHub
from engine.trade_evaluator import (
    TradeEvaluator, EvaluatorConfig, DEFAULT_CONFIG,
    _score_trend, _score_rel_strength, _risk_discount,
)


class _PolyAggsStub:
    """Returns a predictable 100-day price series + SPY benchmark."""
    def __init__(self, series_by_symbol):
        self._series = series_by_symbol

    def get_ticker_snapshot(self, s):
        bars = self._series.get(s, [])
        if not bars:
            return None
        last = bars[-1][1]
        return {"day": {"c": last["c"], "h": last["h"], "l": last["l"],
                         "v": last["v"]}, "prevDay": {}}

    def get_ticker_details(self, s):
        return {"name": f"{s} Co", "sic_description": "Test"}

    def get_news(self, s, limit=10): return []

    def get_aggregates(self, s, m, span, start, end, adjusted=True):
        return self._series.get(s, [])


def _ramp(start_price, end_price, n_days):
    """Build n daily bars climbing linearly from start to end."""
    today = date.today()
    bars = []
    for i in range(n_days):
        t = i / max(1, n_days - 1)
        close = start_price + (end_price - start_price) * t
        bars.append((today - timedelta(days=n_days - 1 - i), {
            "c": close, "h": close * 1.01, "l": close * 0.99,
            "v": 1_000_000, "o": close * 0.995,
            "t": 0,
        }))
    return bars


class TestHistoricalFetch(unittest.TestCase):
    def test_hub_populates_returns_and_vol(self):
        # TSLA climbs 100 → 120 over 100 days; SPY flat at 400
        poly = _PolyAggsStub({
            "TSLA": _ramp(100.0, 120.0, 100),
            "SPY":  _ramp(400.0, 400.0, 100),
        })
        hub = SymbolResearchHub(polygon=poly)
        r = hub.collect("TSLA")
        self.assertIsNotNone(r.return_5d_pct)
        self.assertIsNotNone(r.return_30d_pct)
        self.assertIsNotNone(r.return_90d_pct)
        # 30d return should be positive (climbing)
        self.assertGreater(r.return_30d_pct, 0)
        # rel_strength vs flat SPY must be positive too
        self.assertIsNotNone(r.rel_strength_30d_pct)
        self.assertGreater(r.rel_strength_30d_pct, 0)
        # Volume ratio ~ 1.0 (constant volume)
        self.assertAlmostEqual(r.volume_ratio_30d, 1.0, places=2)
        # Volatility is low (nearly linear) but computed
        self.assertIsNotNone(r.volatility_30d_pct)

    def test_hub_handles_sparse_history(self):
        poly = _PolyAggsStub({"TSLA": _ramp(100, 105, 3)})  # <5 bars
        hub = SymbolResearchHub(polygon=poly)
        r = hub.collect("TSLA")
        # Not enough bars → all return fields stay None
        self.assertIsNone(r.return_5d_pct)
        self.assertIsNone(r.return_30d_pct)

    def test_relative_strength_negative_when_underperforming(self):
        poly = _PolyAggsStub({
            "BAD": _ramp(100.0, 95.0, 100),   # falling 5%
            "SPY": _ramp(400.0, 440.0, 100),  # rising 10%
        })
        hub = SymbolResearchHub(polygon=poly)
        r = hub.collect("BAD")
        self.assertIsNotNone(r.rel_strength_30d_pct)
        self.assertLess(r.rel_strength_30d_pct, 0)

    def test_spy_benchmark_cached_within_hub(self):
        """A second collect() on the same hub must NOT re-fetch SPY."""
        poly = _PolyAggsStub({
            "A": _ramp(100, 110, 100),
            "B": _ramp(100, 115, 100),
            "SPY": _ramp(400, 400, 100),
        })
        calls = {"n": 0}
        orig = poly.get_aggregates
        def _counting(sym, *a, **k):
            if sym == "SPY":
                calls["n"] += 1
            return orig(sym, *a, **k)
        poly.get_aggregates = _counting
        hub = SymbolResearchHub(polygon=poly)
        hub.collect("A")
        hub.collect("B")
        self.assertEqual(calls["n"], 1)  # SPY fetched exactly once


class TestNewScoreComponents(unittest.TestCase):
    def test_trend_zero_when_no_data(self):
        r = SymbolResearch(symbol="X", collected_at=datetime.utcnow())
        self.assertEqual(_score_trend(r, DEFAULT_CONFIG), 0.0)

    def test_trend_positive_on_climbing_returns(self):
        r = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                            return_5d_pct=3.0, return_30d_pct=8.0,
                            return_90d_pct=15.0)
        s = _score_trend(r, DEFAULT_CONFIG)
        self.assertGreater(s, DEFAULT_CONFIG.weight_trend / 2)

    def test_trend_lower_on_falling_returns(self):
        r_up = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                               return_5d_pct=3.0, return_30d_pct=5.0,
                               return_90d_pct=10.0)
        r_dn = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                               return_5d_pct=-3.0, return_30d_pct=-5.0,
                               return_90d_pct=-10.0)
        self.assertGreater(_score_trend(r_up, DEFAULT_CONFIG),
                           _score_trend(r_dn, DEFAULT_CONFIG))

    def test_rel_strength_zero_when_no_data(self):
        r = SymbolResearch(symbol="X", collected_at=datetime.utcnow())
        self.assertEqual(_score_rel_strength(r, DEFAULT_CONFIG), 0.0)

    def test_rel_strength_higher_when_outperforming(self):
        r_hi = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                                rel_strength_30d_pct=5.0)
        r_lo = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                                rel_strength_30d_pct=-3.0)
        self.assertGreater(_score_rel_strength(r_hi, DEFAULT_CONFIG),
                           _score_rel_strength(r_lo, DEFAULT_CONFIG))

    def test_high_volatility_adds_risk_discount(self):
        r_calm = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                                 volatility_30d_pct=30.0)
        r_wild = SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                                 volatility_30d_pct=100.0)
        self.assertEqual(_risk_discount(r_calm, DEFAULT_CONFIG), 0.0)
        self.assertGreater(_risk_discount(r_wild, DEFAULT_CONFIG), 0.0)


if __name__ == "__main__":
    unittest.main()
