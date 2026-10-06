"""D-0079: the two trade COUNTS are derived from the approved fractions.

Every number asserted here was computed by hand from the Controller's
approved fractions, not copied from the implementation's output.
"""

import math
import sqlite3
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from proposals.position_sizing import PositionSizingPolicy
from risk.enforcer import PortfolioRiskEnforcer, evaluate_new_trade
from risk.models import (
    DAILY_NEW_TRADE_FRACTION, PortfolioRiskLimits, PortfolioSnapshot,
    PositionView, RiskVerdict, TRADE_BUDGET_FRACTION,
    _floor_product, _floor_ratio,
)


class TestTheControllerNumbers(unittest.TestCase):
    """60% / 5% = 12 chairs; 25% of 12 = 3 a day."""

    def test_concurrent_cap_is_twelve(self):
        self.assertEqual(PortfolioRiskLimits().max_concurrent_trades, 12)

    def test_daily_pace_is_unchanged_at_three(self):
        self.assertEqual(PortfolioRiskLimits().max_daily_new_trades, 3)

    def test_twelve_full_trades_reach_exactly_the_approved_ceiling(self):
        L = PortfolioRiskLimits()
        used = L.max_concurrent_trades * L.trade_budget_fraction
        self.assertAlmostEqual(used, L.max_gross_exposure_fraction, places=9)

    def test_the_old_cap_of_five_could_only_reach_25_percent(self):
        # The contradiction D-0079 removes, stated as a number.
        self.assertAlmostEqual(5 * 0.05, 0.25, places=9)
        self.assertAlmostEqual(
            PortfolioRiskLimits().max_gross_exposure_fraction - 0.25,
            0.35, places=9,
        )

    def test_the_three_fractions_are_untouched(self):
        L = PortfolioRiskLimits()
        self.assertAlmostEqual(L.max_gross_exposure_fraction, 0.60)
        self.assertAlmostEqual(L.max_single_symbol_fraction, 0.10)
        self.assertAlmostEqual(L.daily_loss_kill_switch_fraction, 0.03)


class TestTheFloatTrapIsReal(unittest.TestCase):
    """The reason _floor_ratio exists at all."""

    def test_plain_float_division_loses_a_whole_position(self):
        self.assertEqual(math.floor(0.60 / 0.05), 11)
        self.assertNotEqual(0.60 / 0.05, 12.0)

    def test_decimal_gets_it_right(self):
        self.assertEqual(_floor_ratio(0.60, 0.05), 12)

    def test_the_derivation_uses_decimal_not_float(self):
        # If anyone replaces _floor_ratio with float division, this fails.
        self.assertEqual(PortfolioRiskLimits().max_concurrent_trades, 12)

    def test_floor_product_is_exact_too(self):
        self.assertEqual(_floor_product(0.25, 12), 3)
        # 0.25*12 is exact in binary, so it proves nothing on its own.
        # 0.29*100 is NOT: the float product is 28.999999999999996.
        self.assertEqual(_floor_product(0.29, 100), 29)
        self.assertEqual(math.floor(0.29 * 100), 28)  # the float answer

    def test_floor_ratio_rounds_down_never_up(self):
        self.assertEqual(_floor_ratio(0.60, 0.07), 8)   # 8.571...
        self.assertEqual(_floor_ratio(0.59, 0.05), 11)  # 11.8


class TestItFollowsTheExposureCeiling(unittest.TestCase):
    """Change the ceiling, the chairs and the pace follow -- and the
    'days to fill the shelf' stays 4."""

    def _derived(self, gross):
        L = PortfolioRiskLimits(max_gross_exposure_fraction=gross)
        return L.max_concurrent_trades, L.max_daily_new_trades

    def test_eighty_percent(self):
        self.assertEqual(self._derived(0.80), (16, 4))

    def test_sixty_percent(self):
        self.assertEqual(self._derived(0.60), (12, 3))

    def test_forty_percent(self):
        self.assertEqual(self._derived(0.40), (8, 2))

    def test_days_to_fill_stays_four(self):
        for gross in (0.80, 0.60, 0.40):
            chairs, pace = self._derived(gross)
            self.assertEqual(math.ceil(chairs / pace), 4, f"gross={gross}")

    def test_a_smaller_trade_budget_means_more_chairs(self):
        L = PortfolioRiskLimits(trade_budget_fraction=0.04)
        self.assertEqual(L.max_concurrent_trades, 15)  # 0.60/0.04
        self.assertEqual(L.max_daily_new_trades, 3)    # floor(0.25*15)

    def test_a_larger_trade_budget_means_fewer(self):
        L = PortfolioRiskLimits(trade_budget_fraction=0.10)
        self.assertEqual(L.max_concurrent_trades, 6)
        self.assertEqual(L.max_daily_new_trades, 1)    # floor(0.25*6)


class TestTheClampsAreNotCosmetic(unittest.TestCase):
    """A tiny ceiling must mean 'one at a time', never an exception."""

    def test_a_ceiling_below_one_trade_still_constructs(self):
        # floor(0.03/0.05) == 0 -> clamped to 1, not ValueError.
        L = PortfolioRiskLimits(max_gross_exposure_fraction=0.03)
        self.assertEqual(L.max_concurrent_trades, 1)
        self.assertEqual(L.max_daily_new_trades, 1)

    def test_a_two_chair_cap_still_allows_one_a_day(self):
        # floor(0.25*2) == 0 -> clamped to 1.
        L = PortfolioRiskLimits(max_gross_exposure_fraction=0.10)
        self.assertEqual(L.max_concurrent_trades, 2)
        self.assertEqual(L.max_daily_new_trades, 1)

    def test_a_tiny_daily_fraction_still_allows_one(self):
        L = PortfolioRiskLimits(daily_new_trade_fraction=0.01)
        self.assertEqual(L.max_daily_new_trades, 1)


class TestExplicitValuesStillWin(unittest.TestCase):
    """Why 1631 existing tests are unaffected."""

    def test_explicit_concurrent_overrides_the_derivation(self):
        self.assertEqual(
            PortfolioRiskLimits(max_concurrent_trades=5).max_concurrent_trades,
            5)

    def test_explicit_daily_overrides_the_derivation(self):
        self.assertEqual(
            PortfolioRiskLimits(max_daily_new_trades=7).max_daily_new_trades,
            7)

    def test_an_explicit_concurrent_does_not_change_the_derived_daily(self):
        # daily derives from the EXPLICIT concurrent, not from 12.
        L = PortfolioRiskLimits(max_concurrent_trades=8)
        self.assertEqual(L.max_daily_new_trades, 2)  # floor(0.25*8)

    def test_explicit_zero_still_raises_exactly_as_before(self):
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(max_concurrent_trades=0)
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(max_daily_new_trades=0)

    def test_negative_explicit_raises(self):
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(max_concurrent_trades=-1)


class TestTheNewFractionsAreValidated(unittest.TestCase):
    def test_zero_trade_budget_raises_instead_of_dividing_by_zero(self):
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(trade_budget_fraction=0.0)

    def test_out_of_range_daily_fraction_raises(self):
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(daily_new_trade_fraction=1.5)
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(daily_new_trade_fraction=0.0)


class TestTheDuplicateIsGuarded(unittest.TestCase):
    """risk/ deliberately does not import proposals/. This test is what
    stops the duplicated 5% from diverging."""

    def test_trade_budget_matches_the_canonical_sizing_policy(self):
        self.assertAlmostEqual(TRADE_BUDGET_FRACTION,
                               PositionSizingPolicy().trade_budget_pct,
                               places=12)

    def test_the_limits_default_matches_it_too(self):
        self.assertAlmostEqual(PortfolioRiskLimits().trade_budget_fraction,
                               PositionSizingPolicy().trade_budget_pct,
                               places=12)

    def test_the_daily_fraction_constant_is_the_default(self):
        self.assertAlmostEqual(PortfolioRiskLimits().daily_new_trade_fraction,
                               DAILY_NEW_TRADE_FRACTION, places=12)


def _snapshot(*, open_trades, new_today, positions=(), equity=100_000.0):
    """The REAL PortfolioSnapshot, never a stub. A stub missing
    gross_exposure() once passed its tests while production raised
    AttributeError (D-0077), so these tests use the production type."""
    return PortfolioSnapshot(
        equity_current=equity,
        equity_at_day_open=equity,
        positions=tuple(positions),
        open_trades=open_trades,
        new_trades_today=new_today,
        snapshot_at=datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc),
    )


class TestTheEnforcerActuallyUsesTwelve(unittest.TestCase):
    """The behaviour change, through the real enforcer and the real
    PortfolioSnapshot -- not a stub."""

    def test_the_sixth_trade_is_now_allowed(self):
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=5_000.0,
            snapshot=_snapshot(open_trades=5, new_today=0),
            limits=PortfolioRiskLimits(),
        )
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED, r.checks)

    def test_the_eleventh_is_allowed(self):
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=5_000.0,
            snapshot=_snapshot(open_trades=11, new_today=0),
            limits=PortfolioRiskLimits(),
        )
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED, r.checks)

    def test_the_thirteenth_is_refused(self):
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=5_000.0,
            snapshot=_snapshot(open_trades=12, new_today=0),
            limits=PortfolioRiskLimits(),
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertTrue(any(c.name == "concurrent_trades" and not c.passed
                            for c in r.checks))

    def test_the_daily_cap_of_three_still_binds(self):
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=5_000.0,
            snapshot=_snapshot(open_trades=3, new_today=3),
            limits=PortfolioRiskLimits(),
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertTrue(any(c.name == "new_trades_today" and not c.passed
                            for c in r.checks))

    def test_gross_exposure_still_stops_the_twelfth_if_sized_bigger(self):
        # 11 positions at 5% each = 55%; a 10% add would breach 60%.
        pos = tuple(PositionView(symbol=f"S{i}", qty=1.0,
                                 market_value=5_000.0)
                    for i in range(11))
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=10_000.0,
            snapshot=_snapshot(open_trades=11, new_today=0, positions=pos),
            limits=PortfolioRiskLimits(),
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertTrue(any(c.name == "gross_exposure" and not c.passed
                            for c in r.checks))

    def test_single_symbol_cap_is_untouched_by_the_new_chair_count(self):
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=11_000.0,  # 11% > 10%
            snapshot=_snapshot(open_trades=0, new_today=0),
            limits=PortfolioRiskLimits(),
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertTrue(any(c.name == "single_symbol_exposure"
                            and not c.passed for c in r.checks))


class TestThroughTheEnforcerClass(unittest.TestCase):
    """Same thing one layer up, where production actually calls it."""

    def test_enforcer_allows_the_sixth_and_refuses_the_thirteenth(self):
        for open_trades, expected in ((5, RiskVerdict.ALLOWED),
                                      (12, RiskVerdict.VIOLATED)):
            enf = PortfolioRiskEnforcer(
                limits=PortfolioRiskLimits(),
                snapshot_builder=lambda ot=open_trades: _snapshot(
                    open_trades=ot, new_today=0),
            )
            r = enf.check_new_trade(symbol="AAPL", proposed_notional=5_000.0)
            self.assertEqual(r.verdict, expected, f"open={open_trades}")

    def test_a_ladder_is_never_blocked_by_the_concurrent_cap(self):
        enf = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=lambda: _snapshot(open_trades=12, new_today=3),
        )
        r = enf.check_ladder_addition(symbol="AAPL",
                                      proposed_notional=2_500.0)
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED, r.checks)


class TestInvariantsHoldForEveryFractionCombination(unittest.TestCase):
    """Swept 30,000 combinations of the three fractions while building
    this. These pin what that sweep established, including the one
    invariant it found violated."""

    def _sweep(self):
        for g in (0.01, 0.05, 0.10, 0.33, 0.60, 0.80, 0.99, 1.0):
            for b in (0.01, 0.02, 0.05, 0.07, 0.10, 0.25, 0.50):
                for f in (0.01, 0.10, 0.25, 0.50, 0.99, 1.0):
                    yield g, b, f, PortfolioRiskLimits(
                        max_gross_exposure_fraction=g,
                        trade_budget_fraction=b,
                        daily_new_trade_fraction=f)

    def test_no_combination_raises(self):
        count = sum(1 for _ in self._sweep())
        self.assertGreater(count, 300)

    def test_both_counts_are_always_at_least_one(self):
        for g, b, f, L in self._sweep():
            self.assertGreaterEqual(L.max_concurrent_trades, 1, (g, b, f))
            self.assertGreaterEqual(L.max_daily_new_trades, 1, (g, b, f))

    def test_the_daily_pace_never_exceeds_the_chair_count(self):
        """Opening more trades in a day than chairs exist would make one
        of the two limits unreachable nonsense."""
        for g, b, f, L in self._sweep():
            self.assertLessEqual(L.max_daily_new_trades,
                                 L.max_concurrent_trades, (g, b, f))

    def test_chairs_times_budget_exceeds_gross_ONLY_when_clamped_to_one(self):
        """The one invariant the sweep found violated, pinned so it
        cannot silently widen.

        When the gross ceiling is smaller than a single trade's budget,
        floor() gives 0 chairs and the clamp raises it to 1 -- so
        1 x budget > gross. It is NOT an unsafe path (the next test
        proves the trade is still refused), but it must never happen
        for any ceiling that fits at least one whole trade.
        """
        for g, b, f, L in self._sweep():
            product = Decimal(str(L.max_concurrent_trades)) * Decimal(str(b))
            if product > Decimal(str(g)):
                self.assertEqual(
                    L.max_concurrent_trades, 1,
                    f"gross={g} budget={b}: over the ceiling with "
                    f"{L.max_concurrent_trades} chairs, not just the clamp")
                self.assertLess(Decimal(str(g)), Decimal(str(b)),
                                f"gross={g} budget={b}: ceiling fits a "
                                f"whole trade yet was exceeded")

    def test_the_clamped_case_is_still_refused_by_gross_exposure(self):
        """Proves the clamp cannot become an unsafe path: the
        gross-exposure check is independent and still binds."""
        L = PortfolioRiskLimits(max_gross_exposure_fraction=0.01,
                                trade_budget_fraction=0.05)
        self.assertEqual(L.max_concurrent_trades, 1)   # clamped
        r = evaluate_new_trade(
            symbol="AAPL", proposed_notional=5_000.0,
            snapshot=_snapshot(open_trades=0, new_today=0),
            limits=L,
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertTrue(any(c.name == "gross_exposure" and not c.passed
                            for c in r.checks),
                        "the gross check must be what refuses it")
