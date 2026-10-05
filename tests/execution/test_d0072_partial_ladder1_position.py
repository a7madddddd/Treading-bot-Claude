"""D-0072: a partial LADDER-1 fill records the shares it actually
bought, without deciding whether the ladder is finished.

Reproduced before the fix (2026-10-05): a 4-share Ladder 1 filling 2
left the broker holding 22 shares while the Trade said 20, so the
protective exit sold 20 and stranded 2 with no floor and no message.

The idempotency tests are the important ones. `recover_if_terminal`
re-applies terminal executions on every engine startup, and its safety
rests on `ladder1_filled` acting as the "already applied" flag -- which
this path deliberately does not set. An incremental implementation would
have added the same fill again on every restart: 20, 22, 24, 26.
"""

import unittest
from datetime import datetime, timezone

from engine.decision_source import ControllerDecision, DecisionKind
from execution.broker_client import BrokerOrderState
from trade.models import TradeAction

from tests.engine.test_engine import (
    FakeBrokerClient, FakeMarketDataSource,
    _active_trade, _make_engine, _now, _repos,
)


INITIAL_SHARES = 20
ENTRY = 250.0


class _Fixture:
    """Drives a real Ladder 1 to a terminal PARTIAL fill through the
    real Engine, ExecutionService, Trade model and SQLite."""

    def __init__(self, *, unfilled=2, action=TradeAction.LADDER_1):
        self.repos = _repos()
        self.trade_repo = self.repos[0]
        self.proposal_repo = self.repos[1]
        self.execution_repo = self.repos[2]
        self.broker = FakeBrokerClient()
        self.md = FakeMarketDataSource({"LOW": ENTRY})
        (self.engine, _, _, self.decisions, self.notifier,
         self.exec_service) = _make_engine(
            *self.repos, broker=self.broker, market_data=self.md)

        _active_trade(self.trade_repo, trade_id="T-1", symbol="LOW",
                      price=ENTRY, shares=INITIAL_SHARES, now=_now())
        self.trade0 = self.trade_repo.get("T-1").trade
        self.engine.start(now=_now())
        self.notifier.events.clear()

        trigger = (self.trade0.ladder1_price if action is TradeAction.LADDER_1
                   else self.trade0.ladder2_price)
        self.md.set_price("LOW", trigger)
        self.engine.run_trigger_check(now=_now())
        props = [p for p in self.proposal_repo.list_for_trade("T-1")
                 if p.proposed_action is action]
        assert props, f"no {action} proposal was created"
        self.proposal = props[-1]

        self.decisions.submit(ControllerDecision(
            proposal_id=self.proposal.proposal_id,
            kind=DecisionKind.APPROVE, decided_by="controller"))
        self.engine.run_reconciliation_tick(now=_now())
        ex = self.execution_repo.get_by_proposal_id(self.proposal.proposal_id)
        self.requested = ex.execution.requested_qty
        self.filled = max(self.requested - unfilled, 1)
        self.fill_price = float(trigger)
        self.broker.set_order_state(ex.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=ex.execution.broker_order_id or "B-1",
                status="canceled", is_terminal=True,
                filled_qty=self.filled, filled_avg_price=self.fill_price,
            ))
        self.notifier.events.clear()
        self.engine.run_reconciliation_tick(now=_now())

    @property
    def trade(self):
        return self.trade_repo.get("T-1").trade

    def events(self):
        return [e.event for e in self.notifier.events]


class TestSharesAreRecorded(unittest.TestCase):
    def setUp(self):
        self.f = _Fixture()

    def test_the_partial_shares_are_in_the_position(self):
        self.assertEqual(self.f.trade.total_shares,
                         INITIAL_SHARES + self.f.filled)

    def test_the_position_matches_what_the_broker_holds(self):
        # The whole point: nothing is left behind when the floor fires.
        self.assertEqual(self.f.trade.total_shares,
                         INITIAL_SHARES + self.f.filled)

    def test_the_ladder_flag_is_NOT_set(self):
        # "Is this ladder finished" stays the Controller's decision.
        self.assertFalse(self.f.trade.ladder1_filled)

    def test_the_weighted_average_is_recomputed_correctly(self):
        expected = round(
            (INITIAL_SHARES * ENTRY + self.f.filled * self.f.fill_price)
            / (INITIAL_SHARES + self.f.filled), 4)
        self.assertEqual(self.f.trade.weighted_avg_entry_price, expected)

    def test_the_weighted_average_actually_moved(self):
        self.assertNotEqual(self.f.trade.weighted_avg_entry_price, ENTRY)

    def test_the_frozen_levels_are_untouched(self):
        # D-0001: ladders and the original floor come from the frozen
        # initial entry and must not move when a ladder fills.
        after = self.f.trade
        self.assertEqual(after.ladder1_price, self.f.trade0.ladder1_price)
        self.assertEqual(after.ladder2_price, self.f.trade0.ladder2_price)
        self.assertEqual(after.original_floor_price,
                         self.f.trade0.original_floor_price)


class TestTheControllerIsTold(unittest.TestCase):
    def setUp(self):
        self.f = _Fixture()

    def test_a_notification_is_sent(self):
        self.assertIn("ladder1_partial_fill_recorded", self.f.events())

    def test_it_is_sent_only_once_however_many_ticks_pass(self):
        for _ in range(10):
            self.f.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(
            self.f.events().count("ladder1_partial_fill_recorded"), 1)

    def test_it_names_both_quantities(self):
        body = [e.message for e in self.f.notifier.events
                if e.event == "ladder1_partial_fill_recorded"][0]
        self.assertIn(str(self.f.requested), body)
        self.assertIn(str(self.f.filled), body)

    def test_it_says_no_approval_is_needed(self):
        body = [e.message for e in self.f.notifier.events
                if e.event == "ladder1_partial_fill_recorded"][0]
        self.assertIn("Nothing to approve", body)


class TestIdempotency(unittest.TestCase):
    """The trap P-047 recorded: this path sets no flag, so the absolute
    derivation is the ONLY thing stopping repeated application."""

    def setUp(self):
        self.f = _Fixture()
        self.expected = INITIAL_SHARES + self.f.filled

    def test_many_reconciliation_ticks_do_not_accumulate(self):
        for _ in range(20):
            self.f.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(self.f.trade.total_shares, self.expected)

    def test_repeated_startup_recovery_does_not_accumulate(self):
        # This is the exact path that would have produced 20, 22, 24, 26.
        for _ in range(5):
            self.f.exec_service.recover_if_terminal(
                self.f.proposal.proposal_id, now=_now())
        self.assertEqual(self.f.trade.total_shares, self.expected)

    def test_a_full_engine_recovery_pass_does_not_accumulate(self):
        for _ in range(3):
            self.f.engine.recover(now=_now())
        self.assertEqual(self.f.trade.total_shares, self.expected)

    def test_the_weighted_average_also_stays_put(self):
        before = self.f.trade.weighted_avg_entry_price
        for _ in range(5):
            self.f.exec_service.recover_if_terminal(
                self.f.proposal.proposal_id, now=_now())
        self.assertEqual(self.f.trade.weighted_avg_entry_price, before)


class TestLadder2IsUntouched(unittest.TestCase):
    """Ladder 2's approved flow -- notify, then require an explicit
    confirmation -- must not change. P-049 records that this leaves the
    same stranding risk there until the Controller presses confirm."""

    def setUp(self):
        self.f = _Fixture(action=TradeAction.LADDER_2)

    def test_a_ladder2_partial_is_not_auto_recorded(self):
        self.assertEqual(self.f.trade.total_shares, INITIAL_SHARES)

    def test_a_ladder2_partial_still_asks_for_confirmation(self):
        self.assertIn("ladder2_partial_fill_pending_confirmation",
                      self.f.events())

    def test_confirming_applies_it_exactly_once(self):
        self.f.exec_service.confirm_ladder2_partial_fill(
            self.f.proposal.proposal_id, decided_by="controller", now=_now())
        self.assertEqual(self.f.trade.total_shares,
                         INITIAL_SHARES + self.f.filled)
        self.assertTrue(self.f.trade.ladder2_filled)

    def test_confirmed_shares_survive_later_recovery_passes(self):
        self.f.exec_service.confirm_ladder2_partial_fill(
            self.f.proposal.proposal_id, decided_by="controller", now=_now())
        expected = INITIAL_SHARES + self.f.filled
        for _ in range(5):
            self.f.engine.recover(now=_now())
        self.assertEqual(self.f.trade.total_shares, expected)


if __name__ == "__main__":
    unittest.main()
