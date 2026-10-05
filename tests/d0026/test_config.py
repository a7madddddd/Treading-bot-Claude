import unittest

from d0026.config import UniverseSelectionConfig


class TestConfig(unittest.TestCase):
    def test_defaults_match_D0048(self):
        c = UniverseSelectionConfig()
        self.assertEqual(c.min_volume_percentile, 0.30)
        self.assertEqual(c.drop_bottom_price_percentile, 0.20)
        self.assertEqual(c.min_market_cap_percentile, 0.40)
        self.assertEqual(c.min_completeness_fraction, 0.90)
        self.assertEqual(c.max_gap_fraction, 0.05)
        self.assertEqual(c.max_spread_fraction, 0.0015)
        self.assertEqual(c.min_spread_tightness_percentile, 0.40)
        # D-0065 (2026-10-05) narrowed the band from D-0048's 1%-5%.
        # These assertions pin the APPROVED values, so changing them
        # must require a decision entry, never a quiet edit.
        self.assertEqual(c.min_atr_fraction, 0.02)
        self.assertEqual(c.max_atr_fraction, 0.04)
        self.assertEqual(c.min_trend_percentile, 0.50)
        self.assertEqual(c.vix_topmost_percentile, 0.80)
        self.assertEqual(c.momentum_weight, 0.40)
        self.assertEqual(c.quality_weight, 0.30)
        self.assertEqual(c.liquidity_weight, 0.30)
        self.assertEqual(c.max_sector_fraction, 0.30)
        self.assertEqual(c.max_pairwise_correlation, 0.70)
        self.assertEqual(c.top_n, 10)

    def test_weights_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            UniverseSelectionConfig(
                momentum_weight=0.5, quality_weight=0.3, liquidity_weight=0.3,
            )

    def test_ratio_bounds(self):
        with self.assertRaises(ValueError):
            UniverseSelectionConfig(min_volume_percentile=1.5)
        with self.assertRaises(ValueError):
            UniverseSelectionConfig(max_spread_fraction=-0.01)

    def test_atr_band_coherent(self):
        with self.assertRaises(ValueError):
            UniverseSelectionConfig(min_atr_fraction=0.05, max_atr_fraction=0.02)

    def test_top_n_positive(self):
        with self.assertRaises(ValueError):
            UniverseSelectionConfig(top_n=0)


if __name__ == "__main__":
    unittest.main()
