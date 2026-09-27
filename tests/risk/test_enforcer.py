import unittest

from risk.enforcer import (
    PortfolioRiskEnforcer, evaluate_ladder_addition, evaluate_new_trade,
)
from risk.models import (
    PortfolioRiskLimits, PortfolioSnapshot, PositionView, RiskVerdict,
)


LIMITS = PortfolioRiskLimits()  # D-0047 defaults


def _snapshot(*, equity_current=50000.0, equity_at_day_open=50000.0,
              positions=(), open_trades=0, new_trades_today=0):
    return PortfolioSnapshot(
        equity_current=equity_current,
        equity_at_day_open=equity_at_day_open,
        positions=positions,
        open_trades=open_trades,
        new_trades_today=new_trades_today,
    )


class TestNewTrade(unittest.TestCase):
    def test_clean_slate_allows(self):
        r = evaluate_new_trade(symbol="TSLA", proposed_notional=4000.0,
                               snapshot=_snapshot(), limits=LIMITS)
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED)

    def test_gross_exposure_cap_engages(self):
        positions = (
            PositionView("AAPL", 10, 20000.0),
            PositionView("SPY", 3, 8500.0),  # 28,500 = 57%
        )
        # adding 4000 -> 32,500 = 65% > 60% cap
        r = evaluate_new_trade(symbol="TSLA", proposed_notional=4000.0,
                               snapshot=_snapshot(positions=positions),
                               limits=LIMITS)
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        reason = r.first_violation_reason()
        self.assertIsNotNone(reason)
        self.assertIn("gross exposure", reason)

    def test_single_symbol_cap_engages(self):
        positions = (PositionView("TSLA", 5, 1800.0),)  # 3.6% of 50K
        # adding 3500 -> 5300 in TSLA = 10.6% > 10% cap
        r = evaluate_new_trade(symbol="TSLA", proposed_notional=3500.0,
                               snapshot=_snapshot(positions=positions),
                               limits=LIMITS)
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertIn("TSLA", r.first_violation_reason())

    def test_concurrent_trades_cap_engages(self):
        # 5 open trades already -> 6th refused
        r = evaluate_new_trade(symbol="AMD", proposed_notional=100.0,
                               snapshot=_snapshot(open_trades=5),
                               limits=LIMITS)
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertIn("open trades", r.first_violation_reason())

    def test_new_trades_today_cap_engages(self):
        r = evaluate_new_trade(symbol="AMD", proposed_notional=100.0,
                               snapshot=_snapshot(new_trades_today=3),
                               limits=LIMITS)
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertIn("new trades opened today", r.first_violation_reason())

    def test_daily_loss_kill_switch_engages(self):
        # 3% loss = 1500 on 50K. 47,000 current = 3000 loss = 6% -> triggers
        r = evaluate_new_trade(
            symbol="AMD", proposed_notional=100.0,
            snapshot=_snapshot(equity_current=47000.0,
                               equity_at_day_open=50000.0),
            limits=LIMITS,
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertIn("kill-switch", r.first_violation_reason())

    def test_boundary_gross_exposure_exact(self):
        # exactly 60% -> allowed
        positions = (PositionView("AAPL", 1, 26000.0),)
        r = evaluate_new_trade(
            symbol="TSLA", proposed_notional=4000.0,
            snapshot=_snapshot(positions=positions), limits=LIMITS,
        )
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED)

    def test_boundary_single_symbol_exact(self):
        # exactly 10% for TSLA -> allowed
        r = evaluate_new_trade(
            symbol="TSLA", proposed_notional=5000.0,
            snapshot=_snapshot(), limits=LIMITS,
        )
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED)


class TestLadderAddition(unittest.TestCase):
    def test_ladder_ignores_concurrent_trades_cap(self):
        # 5 open trades but this is a ladder addition to one of them
        positions = (PositionView("TSLA", 5, 2000.0),)
        r = evaluate_ladder_addition(
            symbol="TSLA", proposed_notional=1000.0,
            snapshot=_snapshot(positions=positions, open_trades=5),
            limits=LIMITS,
        )
        self.assertEqual(r.verdict, RiskVerdict.ALLOWED)

    def test_ladder_still_enforces_single_symbol_cap(self):
        positions = (PositionView("TSLA", 15, 4800.0),)
        r = evaluate_ladder_addition(
            symbol="TSLA", proposed_notional=500.0,  # -> 5300 = 10.6%
            snapshot=_snapshot(positions=positions),
            limits=LIMITS,
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)

    def test_ladder_still_enforces_daily_kill_switch(self):
        r = evaluate_ladder_addition(
            symbol="TSLA", proposed_notional=500.0,
            snapshot=_snapshot(equity_current=47000.0,
                               equity_at_day_open=50000.0),
            limits=LIMITS,
        )
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)


class TestNonPositiveEquity(unittest.TestCase):
    def test_zero_equity_blocks_gross(self):
        r = evaluate_new_trade(
            symbol="X", proposed_notional=1.0,
            snapshot=_snapshot(equity_current=0.0, equity_at_day_open=0.0),
            limits=LIMITS,
        )
        # gross_exposure and single_symbol both fail on non-positive equity;
        # daily_loss "allows" with a note. Overall must be VIOLATED.
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)


class TestEnforcerWrapper(unittest.TestCase):
    def test_wrapper_calls_builder_every_time(self):
        calls = {"n": 0}
        def builder():
            calls["n"] += 1
            return _snapshot()
        enforcer = PortfolioRiskEnforcer(limits=LIMITS,
                                         snapshot_builder=builder)
        enforcer.check_new_trade(symbol="X", proposed_notional=1.0)
        enforcer.check_ladder_addition(symbol="X", proposed_notional=1.0)
        self.assertEqual(calls["n"], 2)

    def test_snapshot_builder_failure_is_fail_closed(self):
        """Fail-closed doctrine: if the snapshot builder raises (Alpaca
        network error, malformed body), the enforcer returns VIOLATED
        with a `snapshot_unavailable` finding. It NEVER propagates the
        raw exception -- that would crash the trigger loop that services
        other trades' Floors."""
        def failing_builder():
            raise RuntimeError("Alpaca /v2/account timed out")
        enforcer = PortfolioRiskEnforcer(limits=LIMITS,
                                         snapshot_builder=failing_builder)

        for check in (
            lambda: enforcer.check_new_trade(symbol="X", proposed_notional=1.0),
            lambda: enforcer.check_ladder_addition(symbol="X", proposed_notional=1.0),
        ):
            r = check()
            self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
            self.assertEqual(len(r.checks), 1)
            self.assertEqual(r.checks[0].name, "snapshot_unavailable")
            self.assertIn("timed out", r.first_violation_reason())


if __name__ == "__main__":
    unittest.main()
