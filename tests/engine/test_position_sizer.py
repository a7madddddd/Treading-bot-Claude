"""Tests for VolatilityPositionSizer (D-0050 Phase 15)."""

import unittest
from datetime import datetime
from engine.research_hub import SymbolResearch
from engine.position_sizer import (
    size_position, SizingConfig, DEFAULT_SIZING,
)


def _r(vol=None):
    return SymbolResearch(symbol="X",
                          collected_at=datetime.utcnow(),
                          volatility_30d_pct=vol)


class TestSizer(unittest.TestCase):
    def test_zero_price_rejected(self):
        self.assertEqual(size_position("X", 0.0).shares, 0)

    def test_negative_price_rejected(self):
        self.assertEqual(size_position("X", -5.0).shares, 0)

    def test_no_vol_data_falls_back_to_dollar_budget(self):
        res = size_position("X", 100.0, research=_r(vol=None))
        self.assertEqual(res.shares, 20)  # $2000 / $100
        self.assertEqual(res.vol_scale, 1.0)
        self.assertIn("fallback", res.reason)

    def test_high_vol_shrinks_position(self):
        low_vol = size_position("X", 100.0, research=_r(vol=20.0))
        high_vol = size_position("X", 100.0, research=_r(vol=80.0))
        self.assertGreater(low_vol.shares, high_vol.shares)

    def test_target_vol_matches_budget_at_target(self):
        """At exact target vol (25%), scale should be 1.0."""
        res = size_position("X", 100.0, research=_r(vol=25.0))
        self.assertAlmostEqual(res.vol_scale, 1.0, places=3)
        self.assertEqual(res.shares, 20)

    def test_extreme_low_vol_clamped(self):
        # vol < min_vol_pct (10%) → clamp
        res = size_position("X", 100.0, research=_r(vol=1.0))
        # scale = 25 / 10 (clamped) = 2.5 → shares = 50
        self.assertEqual(res.shares, 50)

    def test_extreme_high_vol_clamped(self):
        # vol > max_vol_pct (120%) → clamp
        res = size_position("X", 100.0, research=_r(vol=500.0))
        # scale = 25 / 120 (clamped) = 0.208 → shares = 4
        self.assertEqual(res.shares, 4)

    def test_min_shares_floor_respected(self):
        cfg = SizingConfig(target_dollars=10.0, min_shares=1)
        res = size_position("X", 1000.0, research=_r(vol=25.0), config=cfg)
        self.assertEqual(res.shares, cfg.min_shares)

    def test_max_shares_ceiling_respected(self):
        cfg = SizingConfig(target_dollars=1_000_000_000.0,
                            max_shares=100)
        res = size_position("X", 1.0, research=_r(vol=25.0), config=cfg)
        self.assertEqual(res.shares, cfg.max_shares)


if __name__ == "__main__":
    unittest.main()
