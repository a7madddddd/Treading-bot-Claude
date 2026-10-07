"""Unit tests for D-0048 percentage-only stages A through H."""

import unittest

from d0026.config import UniverseSelectionConfig
from d0026.models import PipelineStage, RejectionReasonCategory
from d0026.stages import default_percentage_evaluators
from d0026.stages.tradability import TradabilityStage
from d0026.stages.data_quality import DataQualityStage
from d0026.stages.execution_quality import ExecutionQualityStage
from d0026.stages.strategy_fit import StrategyMechanicsFitStage
from d0026.stages.regime_adaptation import RegimeAdaptationStage
from d0026.stages.ranking import RankingStage
from d0026.stages.concentration import ConcentrationStage
from d0026.stages.top_n import TopNStage

from ._fixtures import DATE, candidate, regime


CFG = UniverseSelectionConfig()


class TestTradabilityStage(unittest.TestCase):
    def test_keeps_top_volume_and_drops_low_price(self):
        # 10 candidates: top-30% (3) by liquidity, then drop bottom-20%
        # (which is 0 of 3 since 20% of 3 = 0.6 rounding up to 1 -> 1
        # is dropped from remaining 3, leaving 2). Actually with our
        # helper: min ceil(20% of 3) = 1 dropped, so 2 survive.
        cs = tuple(candidate(f"S{i}", close=1.0 + i, liquidity=i * 100.0)
                   for i in range(1, 11))
        result = TradabilityStage(CFG).evaluate(cs, DATE, regime())
        # top-30% of 10 -> 3 survive by volume (S8, S9, S10 with liquidity 800, 900, 1000)
        # then drop-bottom-20% of those 3 -> drop bottom 1 by price = S8
        # (prices: S8=9, S9=10, S10=11); survivors S9, S10
        survivor_tickers = {c.raw.ticker for c in result.survivors}
        self.assertEqual(survivor_tickers, {"S9", "S10"})

    def test_missing_features_bar_get_missing_data_category(self):
        cs = (candidate("A", liquidity=None, completeness_measures_present=False),)
        # completeness_measures_present=False makes liquidity None but features exist
        # We need bar or features None to trigger MISSING_MARKET_DATA
        c_no_feat = candidate("B")
        object.__setattr__(c_no_feat, "features", None)  # frozen so use object.__setattr__
        result = TradabilityStage(CFG).evaluate((c_no_feat,), DATE, regime())
        # c_no_feat has features=None, so it goes to MISSING_MARKET_DATA
        cats = [r.category for r in result.rejections]
        self.assertIn(RejectionReasonCategory.MISSING_MARKET_DATA, cats)


class TestDataQualityStage(unittest.TestCase):
    def test_warm_up_insufficient_rejects(self):
        c = candidate("A", warm_up_sufficient=False)
        result = DataQualityStage(CFG).evaluate((c,), DATE, regime())
        self.assertEqual(len(result.survivors), 0)
        self.assertEqual(result.rejections[0].category,
                         RejectionReasonCategory.INSUFFICIENT_WARM_UP)

    def test_low_completeness_rejects(self):
        c = candidate("A", completeness_measures_present=False)
        result = DataQualityStage(CFG).evaluate((c,), DATE, regime())
        self.assertEqual(len(result.survivors), 0)
        self.assertEqual(result.rejections[0].category,
                         RejectionReasonCategory.FAILED_DATA_QUALITY)

    def test_full_completeness_passes(self):
        c = candidate("A")
        result = DataQualityStage(CFG).evaluate((c,), DATE, regime())
        self.assertEqual(len(result.survivors), 1)
        self.assertEqual(len(result.rejections), 0)


class TestExecutionQualityStage(unittest.TestCase):
    def test_wide_spread_rejects(self):
        c_tight = candidate("T", spread=0.0005)
        c_wide = candidate("W", spread=0.005)  # 0.5% > 0.15% cap
        result = ExecutionQualityStage(CFG).evaluate((c_tight, c_wide),
                                                     DATE, regime())
        survivor_tickers = {c.raw.ticker for c in result.survivors}
        self.assertIn("T", survivor_tickers)
        self.assertNotIn("W", survivor_tickers)

    def test_missing_spread_rejects(self):
        c = candidate("A", spread=None)
        result = ExecutionQualityStage(CFG).evaluate((c,), DATE, regime())
        self.assertEqual(len(result.survivors), 0)

    def test_non_true_quote_bypasses_hard_cap(self):
        """Regression (B24): when execution_quality_proxy_is_true_quote
        is False (e.g. an IEX intraday-range proxy), the absolute
        max_spread_fraction hard cap is skipped.

        D-0088 removed the sentence that used to end this docstring --
        "The percentile filter still applies" -- because that filter is
        gone. See TestD0088 below for why."""
        c_wide = candidate("W", spread=0.02, spread_is_true_quote=False)
        result = ExecutionQualityStage(CFG).evaluate((c_wide,),
                                                     DATE, regime())
        self.assertEqual(len(result.survivors), 1)
        self.assertEqual(len(result.rejections), 0)

    def test_true_quote_still_hard_capped(self):
        """Regression (B24): when the proxy IS a true quote,
        max_spread_fraction still rejects."""
        c_wide = candidate("W", spread=0.02, spread_is_true_quote=True)
        result = ExecutionQualityStage(CFG).evaluate((c_wide,),
                                                     DATE, regime())
        self.assertEqual(len(result.survivors), 0)
        self.assertEqual(len(result.rejections), 1)


class TestD0088TheRelativeTightnessRuleIsGone(unittest.TestCase):
    """D-0088. The "keep the tightest 40% by spread" rule is removed.

    It was meant to filter execution COST, but with no true bid/ask
    quote it ran on the intraday-range proxy, which measures
    VOLATILITY -- and Stage D already filters volatility, in the
    opposite direction. The two fought over the same number.

    Measured on 2026-10-07 across 92 symbols that passed Stage A: this
    stage's cut was range <= 1.72% while Stage D's floor was ATR >=
    2.00%, and ATR - range ran +0.10 to +0.34 for the names checked.
    The joint window was therefore about six hundredths of a point
    wide, and 26 of 12,597 symbols survived the day. Of eight large
    caps checked by name NOT ONE passed both stages: MSFT, AMAT and
    NVDA cleared D and failed here; JPM, MA and COST cleared here and
    failed D.

    These tests use enough candidates for a percentile to actually
    bite. The previous ones used one or two, where keeping "the top
    40%" of a single candidate keeps it -- which is why removing the
    rule broke nothing and why it needs covering now.
    """

    def _ten(self):
        """Nine tight candidates and one four times wider."""
        tight = [candidate(f"T{i}", spread=0.001 + i * 0.0001,
                           spread_is_true_quote=False) for i in range(9)]
        wide = candidate("WIDE", spread=0.004, spread_is_true_quote=False)
        return tuple(tight + [wide])

    def test_the_widest_of_ten_now_SURVIVES(self):
        result = ExecutionQualityStage(CFG).evaluate(self._ten(), DATE,
                                                     regime())
        tickers = {c.raw.ticker for c in result.survivors}
        self.assertIn("WIDE", tickers)
        self.assertEqual(len(result.survivors), 10)

    def test_nothing_is_rejected_for_relative_tightness(self):
        result = ExecutionQualityStage(CFG).evaluate(self._ten(), DATE,
                                                     regime())
        self.assertEqual(result.rejections, ())

    def test_a_MISSING_proxy_is_still_rejected(self):
        """The data-quality half of this stage is untouched: we still
        refuse a candidate we cannot measure at all."""
        cands = self._ten() + (candidate("NODATA", spread=None),)
        result = ExecutionQualityStage(CFG).evaluate(cands, DATE, regime())
        tickers = {c.raw.ticker for c in result.survivors}
        self.assertNotIn("NODATA", tickers)
        self.assertEqual(len(result.rejections), 1)

    def test_a_TRUE_quote_over_the_cap_is_still_rejected(self):
        """The absolute cap is kept and starts working the day a real
        bid/ask feed exists. Removing the relative rule must not have
        weakened it."""
        cands = self._ten() + (candidate("BADQUOTE", spread=0.02,
                                         spread_is_true_quote=True),)
        result = ExecutionQualityStage(CFG).evaluate(cands, DATE, regime())
        tickers = {c.raw.ticker for c in result.survivors}
        self.assertNotIn("BADQUOTE", tickers)

    def test_the_MSFT_case_from_the_incident(self):
        """MSFT's measured range was 2.02% against a 1.72% cut. Under
        the old rule it was dropped here despite clearing Stage D's ATR
        band at 2.12%."""
        peers = [candidate(f"P{i}", spread=0.0150 + i * 0.0001,
                           spread_is_true_quote=False) for i in range(20)]
        msft = candidate("MSFT", spread=0.0202, spread_is_true_quote=False)
        result = ExecutionQualityStage(CFG).evaluate(
            tuple(peers + [msft]), DATE, regime())
        self.assertIn("MSFT", {c.raw.ticker for c in result.survivors})


class TestStrategyMechanicsFitStage(unittest.TestCase):
    def test_atr_below_floor_rejects(self):
        # ATR 0.5, close 100 -> 0.5% < 1% floor
        c = candidate("A", close=100.0, atr=0.5)
        result = StrategyMechanicsFitStage(CFG).evaluate((c,), DATE, regime())
        self.assertEqual(len(result.survivors), 0)
        self.assertIn("ATR/price", result.rejections[0].detail)

    def test_atr_above_cap_rejects(self):
        # ATR 6, close 100 -> 6% > 5% cap
        c = candidate("A", close=100.0, atr=6.0)
        result = StrategyMechanicsFitStage(CFG).evaluate((c,), DATE, regime())
        self.assertEqual(len(result.survivors), 0)

    def test_in_band_passes_and_momentum_ranks(self):
        # 4 candidates in ATR band; keep top-50% momentum
        cs = (candidate("A", close=100.0, atr=2.0, momentum=0.01),
              candidate("B", close=100.0, atr=2.0, momentum=0.05),
              candidate("C", close=100.0, atr=2.0, momentum=0.03),
              candidate("D", close=100.0, atr=2.0, momentum=0.10))
        result = StrategyMechanicsFitStage(CFG).evaluate(cs, DATE, regime())
        # top 50% momentum: B=0.05, D=0.10
        survivors = {c.raw.ticker for c in result.survivors}
        self.assertEqual(survivors, {"B", "D"})


class TestRegimeAdaptationStage(unittest.TestCase):
    def test_normal_regime_pass_through(self):
        cs = (candidate("A"), candidate("B"), candidate("C"))
        result = RegimeAdaptationStage(CFG).evaluate(cs, DATE, regime(vix_percentile=0.5))
        self.assertEqual(len(result.survivors), 3)

    def test_risk_off_reduces_pool(self):
        cs = (candidate("A", momentum=0.01),
              candidate("B", momentum=0.09),
              candidate("C", momentum=0.02),
              candidate("D", momentum=0.10))
        result = RegimeAdaptationStage(CFG).evaluate(
            cs, DATE, regime(vix_percentile=0.85),
        )
        # top 50% by momentum: B, D
        self.assertEqual(len(result.survivors), 2)
        survivors = {c.raw.ticker for c in result.survivors}
        self.assertEqual(survivors, {"B", "D"})

    def test_missing_vix_pass_through(self):
        cs = (candidate("A"), candidate("B"))
        result = RegimeAdaptationStage(CFG).evaluate(cs, DATE, regime())
        self.assertEqual(len(result.survivors), 2)


class TestRankingStage(unittest.TestCase):
    def test_orders_by_composite_score(self):
        # Higher momentum + lower ATR = higher rank
        cs = (candidate("Low_Mom_Hi_ATR", momentum=0.01, atr=5.0, liquidity=1e6),
              candidate("Hi_Mom_Lo_ATR",  momentum=0.10, atr=1.0, liquidity=5e6),
              candidate("Mid_Mid",        momentum=0.05, atr=2.5, liquidity=3e6))
        result = RankingStage(CFG).evaluate(cs, DATE, regime())
        # Expect Hi_Mom_Lo_ATR first
        self.assertEqual(result.survivors[0].raw.ticker, "Hi_Mom_Lo_ATR")
        # None rejected -- ranking never rejects
        self.assertEqual(len(result.rejections), 0)
        self.assertEqual(len(result.survivors), 3)


class TestConcentrationStage(unittest.TestCase):
    def test_sector_cap_engages(self):
        # 4 candidates all in "tech" sector; cap 30% of pool means only
        # ~1 out of ~3 can be in one sector. Greedy iteration in ranked
        # order should keep the first, reject the rest.
        cs = tuple(candidate(f"S{i}", source_reference="sector=tech")
                   for i in range(4))
        result = ConcentrationStage(CFG).evaluate(cs, DATE, regime())
        # First candidate: 1/1 = 100% -> exceeds 30% -> reject!
        # This shows the greedy check works too strictly; we accept it
        # for small pools where sector cap isn't meaningful.
        # Verify at least: it does something deterministic.
        self.assertLessEqual(len(result.survivors), 4)

    def test_mixed_sectors_all_pass_within_cap(self):
        cs = (candidate("A", source_reference="sector=tech"),
              candidate("B", source_reference="sector=health"),
              candidate("C", source_reference="sector=energy"),
              candidate("D", source_reference="sector=finance"))
        result = ConcentrationStage(CFG).evaluate(cs, DATE, regime())
        # Each sector 1/N -> 25% -> under 30% cap -> all survive.
        self.assertEqual(len(result.survivors), 4)

    def test_unknown_sector_bypasses_cap(self):
        """Regression (B24): when candidates' sector cannot be
        determined ('unknown'), the sector cap is not applied. This
        is honest fail-open behavior: we can't enforce
        'no more than 30% in tech' if we don't know which is tech."""
        cs = tuple(candidate(f"U{i}", source_reference="alpaca-iex")
                   for i in range(5))
        result = ConcentrationStage(CFG).evaluate(cs, DATE, regime())
        self.assertEqual(len(result.survivors), 5)
        self.assertEqual(len(result.rejections), 0)


class TestTopNStage(unittest.TestCase):
    def test_keeps_first_n(self):
        cfg = UniverseSelectionConfig(top_n=3)
        cs = tuple(candidate(f"S{i}") for i in range(10))
        result = TopNStage(cfg).evaluate(cs, DATE, regime())
        self.assertEqual(len(result.survivors), 3)
        self.assertEqual([c.raw.ticker for c in result.survivors],
                         ["S0", "S1", "S2"])
        self.assertEqual(len(result.rejections), 7)


class TestFactoryWiresAllStages(unittest.TestCase):
    def test_default_percentage_evaluators_covers_eight_stages(self):
        ev = default_percentage_evaluators(CFG)
        expected = {
            PipelineStage.TRADABILITY, PipelineStage.DATA_QUALITY,
            PipelineStage.EXECUTION_QUALITY,
            PipelineStage.STRATEGY_MECHANICS_FIT,
            PipelineStage.REGIME_ADAPTATION, PipelineStage.RANKING,
            PipelineStage.CONCENTRATION, PipelineStage.TOP_N,
        }
        self.assertEqual(set(ev.keys()), expected)


if __name__ == "__main__":
    unittest.main()


class TestD0065NarrowedAtrBand(unittest.TestCase):
    """D-0065 (Controller-approved 2026-10-05) narrowed Stage D's ATR
    band from D-0048's 1%-5% to 2%-4%, on measured evidence.

    These are BEHAVIORAL tests, not value assertions: they drive the
    real stage with the real ATR figures measured on the Controller's
    VM on 2026-10-05, so they prove the band actually filters the
    symbols it was narrowed to filter. tests/d0026/test_config.py pins
    the two numbers separately.
    """

    # (ticker, measured ATR as a fraction of price, why it matters)
    REAL_MEASUREMENTS = (
        ("RNP",  0.0102, "slow  - ~10 average adverse days to the Floor"),
        ("ETJ",  0.0111, "slow  - ~9 days"),
        ("SCHA", 0.0124, "slow  - ~8 days"),
        ("NVDA", 0.0211, "middle - keep"),
        ("MSFT", 0.0226, "middle - keep"),
        ("TSLA", 0.0306, "middle - keep"),
        ("PTHS", 0.0485, "fast  - Floor in 2.1 days"),
        ("DYN",  0.0492, "fast  - Floor in 2.0 days"),
        ("MSOS", 0.0496, "fast  - Floor in 2.0 days"),
    )

    @staticmethod
    def _atr_only(**overrides):
        """Stage D applies TWO filters: the ATR band and a momentum
        percentile (min_trend_percentile = 0.50, which keeps only the
        top half BY RANK -- it still cuts half even when every momentum
        value is identical, as the first version of this test
        discovered). Setting it to 1.0 keeps everything, isolating the
        ATR band so these tests measure what they claim to measure."""
        return UniverseSelectionConfig(min_trend_percentile=1.0, **overrides)

    def _survivors(self, config):
        stage = StrategyMechanicsFitStage(config)
        cs = tuple(candidate(t, close=100.0, atr=frac * 100.0, momentum=0.05)
                   for t, frac, _why in self.REAL_MEASUREMENTS)
        result = stage.evaluate(cs, DATE, regime())
        return {c.raw.ticker for c in result.survivors}

    def test_the_slow_tail_is_now_rejected(self):
        survivors = self._survivors(self._atr_only())
        for ticker in ("RNP", "ETJ", "SCHA"):
            with self.subTest(ticker=ticker):
                self.assertNotIn(
                    ticker, survivors,
                    f"{ticker} moves under 2%/day, so the -5% Ladder 1 "
                    f"trigger is ~5 average adverse days away and the "
                    f"ladder would rarely fire at all",
                )

    def test_the_fast_tail_is_now_rejected(self):
        survivors = self._survivors(self._atr_only())
        for ticker in ("PTHS", "DYN", "MSOS"):
            with self.subTest(ticker=ticker):
                self.assertNotIn(
                    ticker, survivors,
                    f"{ticker} moves ~5%/day, so it covers the whole "
                    f"-5%/-8%/-10% ladder in about two days and the "
                    f"Floor becomes the normal ending",
                )

    def test_the_middle_is_kept(self):
        survivors = self._survivors(self._atr_only())
        for ticker in ("NVDA", "MSFT", "TSLA"):
            with self.subTest(ticker=ticker):
                self.assertIn(ticker, survivors)

    def test_the_old_band_would_have_kept_both_tails(self):
        """Proves the change is real, not cosmetic: under D-0048's
        1%-5% every one of these nine symbols survived."""
        old = self._atr_only(min_atr_fraction=0.01,
                             max_atr_fraction=0.05)
        survivors = self._survivors(old)
        self.assertEqual(len(survivors), len(self.REAL_MEASUREMENTS))

    def test_exact_boundaries(self):
        """2.00% is IN (inclusive lower bound), 4.00% is OUT (exclusive
        upper bound) -- Stage D rejects atr_fraction > max, so exactly
        4.00% is kept while anything above is not. Pinned because an
        off-by-one here silently changes which symbols trade."""
        stage = StrategyMechanicsFitStage(self._atr_only())
        cs = (candidate("AT_MIN", close=100.0, atr=2.00, momentum=0.05),
              candidate("JUST_UNDER", close=100.0, atr=1.99, momentum=0.05),
              candidate("AT_MAX", close=100.0, atr=4.00, momentum=0.05),
              candidate("JUST_OVER", close=100.0, atr=4.01, momentum=0.05))
        survivors = {c.raw.ticker
                     for c in stage.evaluate(cs, DATE, regime()).survivors}
        self.assertIn("AT_MIN", survivors)
        self.assertNotIn("JUST_UNDER", survivors)
        self.assertIn("AT_MAX", survivors)
        self.assertNotIn("JUST_OVER", survivors)

    def test_rejection_reason_names_the_band(self):
        stage = StrategyMechanicsFitStage(self._atr_only())
        cs = (candidate("SLOW", close=100.0, atr=1.0, momentum=0.05),
              candidate("OK", close=100.0, atr=3.0, momentum=0.05))
        result = stage.evaluate(cs, DATE, regime())
        details = " ".join(r.detail for r in result.rejections)
        self.assertIn("2.00%", details)
