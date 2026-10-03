"""Tests for PortfolioFilter (D-0050 Phase 14)."""

import unittest
from datetime import date, datetime, timedelta
from engine.research_hub import SymbolResearch
from engine.portfolio_filter import (
    PortfolioFilter, PortfolioFilterConfig, DEFAULT_CONFIG,
    _sector_bucket, _pearson,
)


def _r(symbol, sector=None):
    return SymbolResearch(symbol=symbol,
                          collected_at=datetime.utcnow(),
                          sector=sector)


class TestSectorBucket(unittest.TestCase):
    def test_semiconductor_buckets(self):
        self.assertEqual(_sector_bucket("SEMICONDUCTORS & RELATED",
                                         DEFAULT_CONFIG),
                         "Semiconductors")

    def test_computer_buckets(self):
        self.assertEqual(_sector_bucket("ELECTRONIC COMPUTERS",
                                         DEFAULT_CONFIG),
                         "Software/Hardware")

    def test_bank_buckets(self):
        self.assertEqual(_sector_bucket("NATIONAL COMMERCIAL BANKS",
                                         DEFAULT_CONFIG),
                         "Financials")

    def test_none_sector_returns_none(self):
        self.assertIsNone(_sector_bucket(None, DEFAULT_CONFIG))
        self.assertIsNone(_sector_bucket("", DEFAULT_CONFIG))


class TestSectorCap(unittest.TestCase):
    def test_single_sector_cap_drops_second(self):
        pf = PortfolioFilter()
        ranked = [
            ("NVDA", 85, _r("NVDA", "SEMICONDUCTORS")),
            ("AMD",  80, _r("AMD",  "SEMICONDUCTORS")),
            ("JPM",  75, _r("JPM",  "NATIONAL COMMERCIAL BANKS")),
        ]
        out = pf.apply(ranked)
        self.assertTrue(out[0].accepted)   # NVDA kept
        self.assertFalse(out[1].accepted)  # AMD dropped
        self.assertIn("sector-cap", out[1].rejection_reason)
        self.assertTrue(out[2].accepted)   # JPM kept (different sector)

    def test_missing_sector_does_not_block(self):
        pf = PortfolioFilter()
        ranked = [
            ("A", 90, _r("A", None)),
            ("B", 85, _r("B", None)),
        ]
        out = pf.apply(ranked)
        self.assertTrue(all(p.accepted for p in out))


class TestCorrelationCap(unittest.TestCase):
    """Note: correlation is computed on daily LOG RETURNS, not prices.
    Two different monotonic price ramps can still produce positively-
    correlated returns, so tests use variable (non-monotonic) prices."""

    def _step_prices(self, start, step_pattern, base):
        """Build price series by applying a sequence of multiplicative
        steps so returns match step_pattern exactly (log-returns-wise)."""
        import math
        px = [start]
        for s in step_pattern:
            px.append(px[-1] * math.exp(s))
        return [(base + timedelta(days=i), p) for i, p in enumerate(px)]

    def test_perfectly_correlated_second_dropped(self):
        base = date(2026, 1, 1)
        # Both NVDA and AMD get the EXACT SAME daily-return pattern =>
        # ρ(log-returns) = 1.0, well above the 0.70 cap.
        same_pattern = [0.01 if i % 2 == 0 else -0.005 for i in range(40)]
        other_pattern = [-0.01 if i % 2 == 0 else 0.005 for i in range(40)]
        closes = {
            "NVDA": self._step_prices(100.0, same_pattern, base),
            "AMD":  self._step_prices(50.0,  same_pattern, base),
            "JPM":  self._step_prices(200.0, other_pattern, base),
        }
        pf = PortfolioFilter(closes_provider=lambda s: closes.get(s, []))
        ranked = [
            ("NVDA", 85, _r("NVDA", "SEMICONDUCTORS")),
            ("AMD",  80, _r("AMD",  "Totally different")),  # different sector
            ("JPM",  75, _r("JPM",  "BANKS")),
        ]
        out = pf.apply(ranked)
        self.assertTrue(out[0].accepted)
        self.assertFalse(out[1].accepted)
        self.assertIn("correlation", out[1].rejection_reason)
        # JPM has OPPOSITE return pattern → ρ = -1 → passes
        self.assertTrue(out[2].accepted)

    def test_uncorrelated_both_kept(self):
        base = date(2026, 1, 1)
        up_pattern = [0.01 if i % 2 == 0 else -0.005 for i in range(40)]
        down_pattern = [-0.01 if i % 2 == 0 else 0.005 for i in range(40)]
        closes = {
            "A": self._step_prices(100.0, up_pattern, base),
            "B": self._step_prices(100.0, down_pattern, base),
        }
        pf = PortfolioFilter(closes_provider=lambda s: closes.get(s, []))
        ranked = [
            ("A", 85, _r("A", "sector1")),
            ("B", 80, _r("B", "sector2")),
        ]
        out = pf.apply(ranked)
        # Perfectly ANTI-correlated return patterns → ρ = -1 < cap
        self.assertTrue(all(p.accepted for p in out))

    def test_missing_closes_data_does_not_block(self):
        pf = PortfolioFilter(closes_provider=lambda s: [])
        ranked = [
            ("A", 85, _r("A", "sector1")),
            ("B", 80, _r("B", "sector2")),
        ]
        out = pf.apply(ranked)
        self.assertTrue(all(p.accepted for p in out))


class TestPearson(unittest.TestCase):
    def test_perfectly_correlated(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        b = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        self.assertAlmostEqual(_pearson(a, b), 1.0, places=4)

    def test_anti_correlated(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        b = [6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
        self.assertAlmostEqual(_pearson(a, b), -1.0, places=4)

    def test_constant_series_returns_none(self):
        self.assertIsNone(_pearson([1.0]*6, [1.0]*6))

    def test_short_series_returns_none(self):
        self.assertIsNone(_pearson([1.0], [1.0]))


if __name__ == "__main__":
    unittest.main()
