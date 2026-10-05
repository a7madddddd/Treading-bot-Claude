"""D-0077: the two portfolio trade-count limits respond to how much of
the market the day's research actually saw.

Controller-approved 2026-10-05, after P-055 showed the original lever
was inert: the universe publishes 10 candidates but only 3 new trades a
day are ever permitted, so scaling the candidate list changed nothing.
The cap was always the binding constraint.

Rounding is DOWN, by the Controller's decision.
"""

import sqlite3
import unittest
from datetime import date

from risk.dynamic_limits import (
    BOOTSTRAP_MIN_SAMPLES, baseline_from_history, compute_scale, decide,
    load_recent_enriched, scale_limits,
)
from risk.enforcer import PortfolioRiskEnforcer
from risk.models import PortfolioRiskLimits, PortfolioSnapshot, PositionView


BASE = PortfolioRiskLimits()          # 3 new/day, 5 concurrent
NORMAL = [11_000] * BOOTSTRAP_MIN_SAMPLES


class TestTheControllerNumbers(unittest.TestCase):
    """The worked examples the Controller and Claude agreed on."""

    def _new_and_concurrent(self, today, history=None):
        d = decide(base=BASE, today_enriched=today,
                   history=history if history is not None else NORMAL)
        if not d.trading_allowed:
            return 0, 0
        return d.limits.max_daily_new_trades, d.limits.max_concurrent_trades

    def test_a_normal_day_changes_nothing(self):
        self.assertEqual(self._new_and_concurrent(11_000), (3, 5))

    def test_three_quarters_of_the_market(self):
        self.assertEqual(self._new_and_concurrent(9_000), (2, 4))

    def test_half_the_market_allows_one_new_trade(self):
        self.assertEqual(self._new_and_concurrent(5_000), (1, 2))

    def test_a_quarter_of_the_market_allows_none(self):
        self.assertEqual(self._new_and_concurrent(3_000), (0, 0))

    def test_rounding_is_down_never_up(self):
        # 3 x 0.99 = 2.97 -> 2, not 3.
        d = decide(base=BASE, today_enriched=10_890, history=NORMAL)
        self.assertEqual(d.limits.max_daily_new_trades, 2)


class TestItNeverLoosensTheApprovedLimits(unittest.TestCase):
    def test_seeing_more_than_normal_does_not_raise_the_cap(self):
        d = decide(base=BASE, today_enriched=20_000, history=NORMAL)
        self.assertEqual(d.scale, 1.0)
        self.assertEqual(d.limits.max_daily_new_trades, 3)
        self.assertEqual(d.limits.max_concurrent_trades, 5)

    def test_scale_is_clamped_at_one(self):
        self.assertEqual(compute_scale(99_999, 11_000), 1.0)

    def test_the_exposure_fractions_are_untouched(self):
        d = decide(base=BASE, today_enriched=5_000, history=NORMAL)
        self.assertEqual(d.limits.max_gross_exposure_fraction,
                         BASE.max_gross_exposure_fraction)
        self.assertEqual(d.limits.max_single_symbol_fraction,
                         BASE.max_single_symbol_fraction)
        self.assertEqual(d.limits.daily_loss_kill_switch_fraction,
                         BASE.daily_loss_kill_switch_fraction)


class TestNoHistoryChangesNothing(unittest.TestCase):
    def test_empty_history_leaves_the_approved_limits(self):
        d = decide(base=BASE, today_enriched=5_000, history=[])
        self.assertEqual(d.scale, 1.0)
        self.assertEqual(d.limits.max_daily_new_trades, 3)
        self.assertTrue(d.trading_allowed)

    def test_an_unknown_today_changes_nothing(self):
        d = decide(base=BASE, today_enriched=None, history=NORMAL)
        self.assertEqual(d.scale, 1.0)
        self.assertEqual(d.limits.max_daily_new_trades, 3)


class TestBootstrapUsesTheMaximum(unittest.TestCase):
    """P-054 §4: a median of one value is that value, so a degraded first
    run would define its own baseline and score 1.00. A maximum cannot be
    dragged down by a bad day."""

    def test_few_samples_use_the_maximum(self):
        baseline, source = baseline_from_history([11_000, 5_000, 4_000])
        self.assertEqual(baseline, 11_000)
        self.assertEqual(source, "maximum")

    def test_a_degraded_day_cannot_define_the_baseline(self):
        # Without the maximum rule, history=[5_000] would make a second
        # 5_000 day score 1.00 and pass with full limits.
        d = decide(base=BASE, today_enriched=5_000, history=[11_000, 5_000])
        self.assertEqual(d.limits.max_daily_new_trades, 1)

    def test_enough_samples_switch_to_the_median(self):
        baseline, source = baseline_from_history([11_000] * 20)
        self.assertEqual(source, "median")
        self.assertEqual(baseline, 11_000)

    def test_the_median_ignores_a_short_outage(self):
        # Three bad days inside twenty must not move 'normal'.
        history = [11_000] * 17 + [5_000, 4_000, 3_000]
        baseline, source = baseline_from_history(history)
        self.assertEqual(source, "median")
        self.assertEqual(baseline, 11_000)

    def test_the_median_does_follow_a_real_market_change(self):
        # A sustained change fills the window and is accepted.
        baseline, _ = baseline_from_history([9_000] * 20)
        self.assertEqual(baseline, 9_000)


class TestTheHistoryWindow(unittest.TestCase):
    """P-054 §1 and §2: distinct DATES, not rows, and never including the
    day being judged."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE universe_snapshots ("
            " effective_trading_date TEXT, snapshot_at TEXT,"
            " data_quality_json TEXT)")

    def _add(self, day, at, enriched):
        self.conn.execute(
            "INSERT INTO universe_snapshots VALUES (?,?,?)",
            (day, at, f'[["enriched_with_features", {enriched}]]'))

    def test_many_rows_on_one_date_count_once(self):
        # The Controller's VM held 7 snapshots across 3 dates.
        for i, n in enumerate((5_000, 6_000, 11_000)):
            self._add("2026-10-01", f"2026-10-01T{10+i}:00", n)
        self._add("2026-10-02", "2026-10-02T10:00", 10_000)
        got = load_recent_enriched(self.conn,
                                   before_date=date(2026, 10, 3), limit=20)
        self.assertEqual(len(got), 2)

    def test_the_latest_snapshot_of_a_date_wins(self):
        self._add("2026-10-01", "2026-10-01T06:00", 11_000)
        self._add("2026-10-01", "2026-10-01T15:00", 4_000)
        got = load_recent_enriched(self.conn,
                                   before_date=date(2026, 10, 2), limit=20)
        self.assertEqual(got, (4_000,))

    def test_today_is_never_in_its_own_baseline(self):
        self._add("2026-10-05", "2026-10-05T06:00", 5_000)
        self._add("2026-10-04", "2026-10-04T06:00", 11_000)
        got = load_recent_enriched(self.conn,
                                   before_date=date(2026, 10, 5), limit=20)
        self.assertEqual(got, (11_000,))

    def test_rows_without_the_counter_are_skipped(self):
        # The 7 snapshots that existed before D-0068 carry no counter.
        self.conn.execute(
            "INSERT INTO universe_snapshots VALUES (?,?,?)",
            ("2026-10-01", "2026-10-01T06:00", "[]"))
        self._add("2026-10-02", "2026-10-02T06:00", 11_000)
        got = load_recent_enriched(self.conn,
                                   before_date=date(2026, 10, 3), limit=20)
        self.assertEqual(got, (11_000,))

    def test_the_limit_is_respected(self):
        for d in range(1, 29):
            self._add(f"2026-09-{d:02d}", f"2026-09-{d:02d}T06:00", 11_000)
        got = load_recent_enriched(self.conn,
                                   before_date=date(2026, 10, 1), limit=20)
        self.assertEqual(len(got), 20)


class TestZeroIsExpressible(unittest.TestCase):
    """P-054 §3: PortfolioRiskLimits validates both counts as >= 1, so a
    scaled zero cannot live inside it. Writing the obvious code would
    have raised ValueError on exactly the case that must refuse."""

    def test_zero_never_constructs_an_invalid_limits_object(self):
        limits, allowed = scale_limits(BASE, 0.1)
        self.assertFalse(allowed)
        self.assertGreaterEqual(limits.max_daily_new_trades, 1)

    def test_zero_is_carried_as_trading_not_allowed(self):
        d = decide(base=BASE, today_enriched=1_000, history=NORMAL)
        self.assertFalse(d.trading_allowed)

    def test_the_reason_names_both_numbers(self):
        d = decide(base=BASE, today_enriched=1_000, history=NORMAL)
        self.assertIn("1000", d.reason.replace(",", ""))
        self.assertIn("11000", d.reason.replace(",", ""))


def _Snap(open_trades=0, new_today=0):
    """The REAL PortfolioSnapshot, not a stub -- a stub missing
    gross_exposure() would have passed these tests while the production
    path raised AttributeError."""
    return PortfolioSnapshot(
        equity_current=100_000.0, equity_at_day_open=100_000.0,
        positions=(), open_trades=open_trades, new_trades_today=new_today,
    )


class TestEnforcerIntegration(unittest.TestCase):
    def _enforcer(self, provider=None, snap=None):
        return PortfolioRiskEnforcer(
            limits=BASE, snapshot_builder=lambda: snap or _Snap(),
            limits_provider=provider)

    def test_no_provider_behaves_exactly_as_before(self):
        r = self._enforcer().check_new_trade(symbol="LOW",
                                             proposed_notional=5_000.0)
        self.assertTrue(r.allowed)

    def test_a_blocked_day_refuses_a_new_trade(self):
        d = decide(base=BASE, today_enriched=1_000, history=NORMAL)
        r = self._enforcer(lambda: d).check_new_trade(
            symbol="LOW", proposed_notional=5_000.0)
        self.assertFalse(r.allowed)
        self.assertIn("degraded_universe_no_new_trades",
                      [c.name for c in r.checks])

    def test_a_blocked_day_still_allows_a_LADDER(self):
        # A ladder adds to a position already approved and open. Blocking
        # it would strand an open trade without its ladder.
        d = decide(base=BASE, today_enriched=1_000, history=NORMAL)
        r = self._enforcer(lambda: d).check_ladder_addition(
            symbol="LOW", proposed_notional=2_000.0)
        self.assertTrue(r.allowed)

    def test_a_scaled_cap_refuses_the_fourth_trade_of_a_degraded_day(self):
        d = decide(base=BASE, today_enriched=5_000, history=NORMAL)
        # scale 0.45 -> 1 new trade allowed; one is already open today.
        r = self._enforcer(lambda: d, snap=_Snap(new_today=1)).check_new_trade(
            symbol="LOW", proposed_notional=5_000.0)
        self.assertFalse(r.allowed)

    def test_a_scaled_concurrent_cap_never_closes_anything(self):
        # 5 positions already open, cap scaled to 2: it refuses to ADD,
        # and says so, but nothing in the result asks for an exit.
        d = decide(base=BASE, today_enriched=5_000, history=NORMAL)
        r = self._enforcer(lambda: d, snap=_Snap(open_trades=5)).check_new_trade(
            symbol="LOW", proposed_notional=5_000.0)
        self.assertFalse(r.allowed)
        self.assertNotIn("close", r.checks[0].reason.lower())

    def test_a_raising_provider_falls_back_to_the_approved_limits(self):
        def boom():
            raise RuntimeError("history unreadable")
        r = self._enforcer(boom).check_new_trade(symbol="LOW",
                                                 proposed_notional=5_000.0)
        self.assertTrue(r.allowed)

    def test_a_none_provider_result_falls_back_to_the_approved_limits(self):
        r = self._enforcer(lambda: None).check_new_trade(
            symbol="LOW", proposed_notional=5_000.0)
        self.assertTrue(r.allowed)


if __name__ == "__main__":
    unittest.main()


class TestItShipsInShadowMode(unittest.TestCase):
    """D-0077 ships computed-and-reported, not enforced. The Controller
    sees real numbers for a few weeks before this can cost a position."""

    def _runner_source(self):
        import os
        here = os.path.abspath(os.path.dirname(__file__))
        root = os.path.abspath(os.path.join(here, "..", ".."))
        with open(os.path.join(root, "scripts", "run_paper_session.py"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_default_is_shadow(self):
        src = self._runner_source()
        self.assertIn('choices=("off", "shadow", "active"),\n'
                      '                        default="shadow"', src)

    def test_shadow_returns_no_decision_so_approved_limits_are_enforced(self):
        src = self._runner_source()
        self.assertIn(
            'return decision if args.dynamic_limits == "active" else None',
            src)

    def test_the_engine_is_not_yet_launched_with_active(self):
        import os
        here = os.path.abspath(os.path.dirname(__file__))
        root = os.path.abspath(os.path.join(here, "..", ".."))
        with open(os.path.join(root, "deploy", "engine-run.sh"),
                  encoding="utf-8") as fh:
            self.assertNotIn("--dynamic-limits active", fh.read())
