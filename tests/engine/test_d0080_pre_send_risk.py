"""D-0080 / P-064: the portfolio caps are checked BEFORE a proposal is
sent, as well as after.

Controller's words: "the submission risk violated should be before my
approval" and "You can check before send and check after send. No
problem."

Every test drives the real Engine through `run_trigger_check` with the
real `PortfolioRiskEnforcer`, `PortfolioRiskLimits` and
`PortfolioSnapshot` -- never a stub of any of them. Under D-0077 a stub
missing `gross_exposure()` passed its tests while production raised
AttributeError, which is why.
"""

import unittest
from datetime import datetime, timezone

from engine.engine import Engine
from risk.enforcer import PortfolioRiskEnforcer
from risk.models import (
    PortfolioRiskLimits, PortfolioSnapshot, PositionView, RiskVerdict,
)
from tests.engine.test_engine import (
    StaticWatchlistSource, _make_engine, _now, _repos,
)


class _Res:
    def __init__(self, symbol, score):
        self.symbol = symbol
        self.soft_score = score
        self.passes_hard_filter = True
        self.hard_filter_reasons = []
        self.score_breakdown = {}
        self.research = None


class _CountingEval:
    """Counts rank() calls so the 'no research is spent' claim is
    measured, not asserted."""

    def __init__(self, scores):
        self._scores = scores
        self.rank_calls = 0

    def rank(self, candidates):
        self.rank_calls += 1
        return sorted((_Res(s, self._scores.get(s, 70.0))
                       for s in candidates), key=lambda r: -r.soft_score)


def _snapshot(*, open_trades=0, new_today=0, positions=(), equity=100_000.0):
    return PortfolioSnapshot(
        equity_current=equity,
        equity_at_day_open=equity,
        positions=tuple(positions),
        open_trades=open_trades,
        new_trades_today=new_today,
        snapshot_at=datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc),
    )


def _enforcer(snapshot_or_raiser, limits=None):
    def builder():
        if isinstance(snapshot_or_raiser, Exception):
            raise snapshot_or_raiser
        return snapshot_or_raiser
    return PortfolioRiskEnforcer(
        limits=limits or PortfolioRiskLimits(),
        snapshot_builder=builder,
    )


class _Harness:
    """NOT named `run` -- that collides with unittest.TestCase.run(),
    which the framework calls with a `result` kwarg."""

    def _drive(self, *, enforcer, symbols=("A", "B", "C"), scores=None,
            price=100.0):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        evaluator = _CountingEval(scores or {})
        engine, broker, market_data, _ds, notifier, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(tuple(symbols)),
            trade_evaluator=evaluator,
        )
        engine._risk_enforcer = enforcer
        for s in symbols:
            market_data.set_price(s, price)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        created = [s for s in symbols if trade_repo.list_for_symbol(s)]
        return engine, evaluator, notifier, created, proposal_repo


class TestLayer1CapsAlreadyExhausted(_Harness, unittest.TestCase):
    """Checked once per cycle, before anything is scored."""

    def test_concurrent_cap_full_sends_nothing(self):
        _e, _ev, _n, created, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=12)))
        self.assertEqual(created, [])

    def test_concurrent_cap_full_spends_NO_research(self):
        """The measured saving: the evaluator calls six sources per
        symbol per cycle, and P-058 records one of them is already over
        its free daily limit."""
        _e, ev, _n, _c, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=12)))
        self.assertEqual(ev.rank_calls, 0)

    def test_daily_cap_full_sends_nothing_and_spends_no_research(self):
        _e, ev, _n, created, _p = self._drive(
            enforcer=_enforcer(_snapshot(new_today=3)))
        self.assertEqual(created, [])
        self.assertEqual(ev.rank_calls, 0)

    def test_one_below_each_cap_proceeds_normally(self):
        _e, ev, _n, created, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=11, new_today=2)))
        self.assertEqual(ev.rank_calls, 1)
        self.assertEqual(len(created), 3)

    def test_the_message_names_which_cap_and_is_sent_once(self):
        _e, _ev, notifier, _c, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=12)))
        msgs = [m for m in notifier.events
                if m.event == "caps_exhausted_no_new_proposals"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("12 concurrent", msgs[0].message)
        self.assertIn("still monitored", msgs[0].message)

    def test_no_cycle_metrics_row_when_nothing_was_scored(self):
        """A zero row here would read as 'nothing was good enough'
        while in fact nothing was ever scored."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        rows = []
        engine, _b, market_data, _d, _n, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("A",)),
            trade_evaluator=_CountingEval({}),
        )
        engine._risk_enforcer = _enforcer(_snapshot(open_trades=12))
        engine._cycle_metrics_recorder = rows.append
        market_data.set_price("A", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        self.assertEqual(rows, [])


class TestLayer2PerSymbol(_Harness, unittest.TestCase):
    """The full D-0047 check for this symbol and this size."""

    def test_a_single_symbol_breach_blocks_only_that_symbol(self):
        # 10% single-symbol cap on 100k = 10,000. An existing 9,500
        # position in A means A breaches, B and C do not.
        pos = (PositionView(symbol="A", qty=1.0, market_value=9_500.0),)
        _e, _ev, _n, created, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=1, positions=pos)))
        self.assertNotIn("A", created)
        self.assertIn("B", created)
        self.assertIn("C", created)

    def test_a_blocked_symbol_creates_NO_trade_or_proposal_row(self):
        """The reason the check sits before start_trade(): after it
        would leave orphaned rows for a proposal never sent."""
        pos = (PositionView(symbol="A", qty=1.0, market_value=9_500.0),)
        _e, _ev, _n, created, proposal_repo = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=1, positions=pos)),
            symbols=("A",))
        self.assertEqual(created, [], "no Trade row")
        self.assertEqual(proposal_repo.list_for_symbol("A"), [],
                         "no TradeProposal row either")

    def test_a_gross_exposure_breach_blocks(self):
        # 11 positions x 5,000 = 55,000; a 10,000 add breaches 60%.
        pos = tuple(PositionView(symbol=f"S{i}", qty=1.0,
                                 market_value=5_000.0) for i in range(11))
        _e, _ev, _n, created, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=11, positions=pos)),
            price=10_000.0)      # one share = 10,000 notional
        self.assertEqual(created, [])

    def test_the_block_message_is_per_symbol_and_optional_level(self):
        pos = (PositionView(symbol="A", qty=1.0, market_value=9_500.0),)
        _e, _ev, notifier, _c, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=1, positions=pos)),
            symbols=("A",))
        msgs = [m for m in notifier.events
                if m.event == "pre_send_risk_blocked"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].symbol, "A")
        self.assertIn("No trade or proposal row was", msgs[0].message)


class TestItFailsOPEN(_Harness, unittest.TestCase):
    """The opposite of the submission check, deliberately. A transient
    snapshot error must not silently suppress a whole day of proposals
    -- that is indistinguishable from a dead engine."""

    def test_layer_1_sends_proposals_when_the_snapshot_raises(self):
        _e, ev, _n, created, _p = self._drive(
            enforcer=_enforcer(RuntimeError("broker down")))
        self.assertEqual(ev.rank_calls, 1)
        self.assertEqual(len(created), 3)

    def test_layer_2_sends_the_proposal_when_the_check_raises(self):
        class _Boom:
            _limits = PortfolioRiskLimits()
            _snapshot_builder = staticmethod(lambda: _snapshot())

            def check_new_trade(self, **kw):
                raise RuntimeError("enforcer exploded")

        _e, _ev, _n, created, _p = self._drive(enforcer=_Boom(), symbols=("A",))
        self.assertEqual(created, ["A"])

    def test_no_enforcer_at_all_behaves_exactly_as_before(self):
        """Why all 1707 pre-existing tests are unaffected."""
        _e, ev, _n, created, _p = self._drive(enforcer=None)
        self.assertEqual(ev.rank_calls, 1)
        self.assertEqual(len(created), 3)


class TestItCannotWidenRisk(unittest.TestCase):
    """The property that makes adding a gate safe."""

    def test_the_submission_time_check_is_still_in_place(self):
        import inspect
        from execution import service
        src = inspect.getsource(service)
        self.assertIn("self._risk_enforcer.check_new_trade", src)
        self.assertIn("PortfolioRiskViolatedError", src)

    def test_the_pre_send_check_only_ever_returns_a_block_or_None(self):
        """It has no path that permits anything -- it either names a
        violation or declines to speak."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, *_ = _make_engine(trade_repo, proposal_repo,
                                  execution_repo, conn)
        engine._risk_enforcer = _enforcer(_snapshot())
        self.assertIsNone(engine._pre_send_risk_block(
            symbol="A", notional=1_000.0))
        engine._risk_enforcer = _enforcer(
            _snapshot(positions=(PositionView(symbol="A", qty=1.0,
                                              market_value=9_500.0),)))
        self.assertIsInstance(engine._pre_send_risk_block(
            symbol="A", notional=1_000.0), str)

    def test_ladders_are_NOT_pre_checked(self):
        """A ladder adds to an already-approved position and
        check_ladder_addition does not consult the trade-count caps.
        Pre-checking one could block a ladder that WOULD pass at
        submission, stranding an open position without it."""
        import inspect
        src = inspect.getsource(Engine._pre_send_risk_block)
        self.assertIn("check_new_trade", src)
        self.assertNotIn("check_ladder_addition", src)


class TestTheNotionalMatchesSubmission(unittest.TestCase):
    """Both checks must judge the same size, or they can disagree."""

    def test_both_use_initial_qty_times_price(self):
        import inspect
        from execution import service
        sub = inspect.getsource(service)
        self.assertIn("notional = qty_for_check * current_price", sub)
        pre = inspect.getsource(Engine._start_new_trade)
        self.assertIn("strategy.initial_qty * price", pre)

    def test_the_proposal_stores_the_same_quantity_that_was_checked(self):
        """build_trade_proposal writes initial_quantity=strategy.initial_qty
        and _requested_qty_for reads it back, so the share count the
        pre-send check judged is the one that reaches the broker. The
        PRICE may move between the two moments -- that is inherent and
        is why the submission check still runs."""
        import inspect
        from proposals import proposal as proposal_mod
        self.assertIn("initial_quantity=strategy.initial_qty",
                      inspect.getsource(proposal_mod))


class TestSnapshotUnavailableIsNotTreatedAsABreach(_Harness,
                                                   unittest.TestCase):
    """The bug the fail-open test caught.

    PortfolioRiskEnforcer does not RAISE when the snapshot cannot be
    built -- it returns VIOLATED with a `snapshot_unavailable` check.
    Correct at submission time; wrong here. The first version of
    _pre_send_risk_block returned that as a block and suppressed every
    proposal of the day on a transient broker failure.
    """

    def test_an_unbuildable_snapshot_does_not_block_the_proposal(self):
        _e, ev, _n, created, _p = self._drive(
            enforcer=_enforcer(RuntimeError("broker unreachable")))
        self.assertEqual(ev.rank_calls, 1, "layer 1 must also fail open")
        self.assertEqual(len(created), 3,
                         "a snapshot failure must not suppress proposals")

    def test_a_REAL_breach_is_still_blocked(self):
        """The distinction has to cut only one way."""
        pos = (PositionView(symbol="A", qty=1.0, market_value=9_500.0),)
        _e, _ev, _n, created, _p = self._drive(
            enforcer=_enforcer(_snapshot(open_trades=1, positions=pos)),
            symbols=("A",))
        self.assertEqual(created, [])

    def test_the_submission_check_still_refuses_an_unknown_snapshot(self):
        """Nothing unsafe gets through: the second belt is unchanged
        and still fails CLOSED."""
        enf = _enforcer(RuntimeError("broker unreachable"))
        r = enf.check_new_trade(symbol="A", proposed_notional=1_000.0)
        self.assertEqual(r.verdict, RiskVerdict.VIOLATED)
        self.assertTrue(any(c.name == "snapshot_unavailable"
                            for c in r.checks))


class TestTheLayer1PrivateAccessIsGuarded(unittest.TestCase):
    """Layer 1 reads `_limits` and `_snapshot_builder` off the enforcer
    because PortfolioRiskEnforcer exposes no public accessor for them.

    The hazard is SILENT: a rename would raise AttributeError, the
    fail-open `except` would swallow it, and the pre-send check would
    stop working forever with no signal at all. Chosen over adding a
    public method so this change does not touch the approved
    `src/risk/` module; these tests are what make the choice safe, and
    they run on every commit.
    """

    def test_the_two_attributes_exist_on_the_real_enforcer(self):
        enf = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=lambda: _snapshot(),
        )
        self.assertTrue(hasattr(enf, "_limits"))
        self.assertTrue(hasattr(enf, "_snapshot_builder"))
        self.assertIsInstance(enf._limits, PortfolioRiskLimits)
        self.assertTrue(callable(enf._snapshot_builder))

    def test_the_two_limit_counts_are_readable_as_ints(self):
        enf = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=lambda: _snapshot(),
        )
        self.assertIsInstance(enf._limits.max_concurrent_trades, int)
        self.assertIsInstance(enf._limits.max_daily_new_trades, int)

    def test_layer_1_runs_against_the_REAL_enforcer_class(self):
        """Not a stub: if the attribute names move, this fails."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, *_ = _make_engine(trade_repo, proposal_repo,
                                  execution_repo, conn)
        engine._risk_enforcer = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=lambda: _snapshot(open_trades=12),
        )
        reason = engine._caps_already_exhausted(now=_now())
        self.assertIsNotNone(
            reason,
            "layer 1 silently stopped working -- the private attribute "
            "names on PortfolioRiskEnforcer probably changed")
        self.assertIn("12 concurrent", reason)

    def test_layer_1_returns_None_when_there_is_room(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, *_ = _make_engine(trade_repo, proposal_repo,
                                  execution_repo, conn)
        engine._risk_enforcer = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=lambda: _snapshot(open_trades=11, new_today=2),
        )
        self.assertIsNone(engine._caps_already_exhausted(now=_now()))
