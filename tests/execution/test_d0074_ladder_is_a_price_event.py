"""D-0074: a ladder is a PRICE event, not a quantity target.

Controller, 2026-10-05: "the ladder hold in the falling, not the partial
decrease ... we need to buy the existing and we need to notify that the
exist is only x number of shares ... so the next ladder is minus eight
percent", and "we wouldn't want to stop at one of the ladders, because
the share price when it decreases does not wait for us".

So:
  * PARTIAL fill -> buy what the market had, RECORD it, CLOSE the
    ladder, move on to the next level.
  * ZERO fill -> the market did not answer; the ladder stays available
    for a later trigger and never blocks the next level or the floor.

Both were broken before, and both were reproduced:
  * a 4-share Ladder 1 filling 2 left the broker holding 22 while the
    Trade said 20, with no message -- the floor would have stranded 2;
  * a zero fill left the ladder flagged open but permanently stuck, with
    no message at all.
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
        # unfilled=None means a ZERO fill: the market offered nothing.
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
        self.filled = (0 if unfilled is None
                       else max(self.requested - unfilled, 1))
        self.fill_price = float(trigger)
        self.broker.set_order_state(ex.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=ex.execution.broker_order_id or "B-1",
                status="canceled", is_terminal=True,
                filled_qty=self.filled,
                filled_avg_price=(self.fill_price if self.filled else None),
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

    def test_the_ladder_is_CLOSED(self):
        # D-0074: the price event happened and D-0034 forfeits the
        # remainder permanently, so the ladder is over and is recorded
        # as over. Leaving it False described a ladder that was still
        # available when nothing further could ever happen to it.
        self.assertTrue(self.f.trade.ladder1_filled)

    def test_the_weighted_average_is_recomputed_correctly(self):
        # _apply_ladder_fill does the averaging in full precision and
        # does NOT round -- asserting a rounded value here would be
        # testing a number this code never produces.
        expected = ((INITIAL_SHARES * ENTRY
                     + self.f.filled * self.f.fill_price)
                    / (INITIAL_SHARES + self.f.filled))
        self.assertAlmostEqual(self.f.trade.weighted_avg_entry_price,
                               expected, places=6)

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
        self.assertIn("ladder_partially_filled", self.f.events())

    def test_it_is_sent_only_once_however_many_ticks_pass(self):
        for _ in range(10):
            self.f.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(
            self.f.events().count("ladder_partially_filled"), 1)

    def test_it_names_both_quantities(self):
        body = [e.message for e in self.f.notifier.events
                if e.event == "ladder_partially_filled"][0]
        self.assertIn(str(self.f.requested), body)
        self.assertIn(str(self.f.filled), body)

    def test_it_says_no_approval_is_needed(self):
        body = [e.message for e in self.f.notifier.events
                if e.event == "ladder_partially_filled"][0]
        self.assertIn("Nothing to approve", body)
        self.assertIn("-8%", body)  # names the next level


class TestIdempotency(unittest.TestCase):
    """P-047 recorded the trap: recover_if_terminal re-applies terminal
    executions on every startup. Closing the ladder restores the normal
    guard -- ladderN_filled -- so these pin that it actually holds."""

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


class TestLadder2BehavesTheSameWay(unittest.TestCase):
    """D-0074 supersedes D-0034 sections 4 and 7: both ladders now
    behave identically. No confirmation is required for either, because
    there is nothing left to decide -- D-0034 already forfeits the
    remainder permanently."""

    def setUp(self):
        self.f = _Fixture(action=TradeAction.LADDER_2)

    def test_a_ladder2_partial_is_recorded_too(self):
        self.assertEqual(self.f.trade.total_shares,
                         INITIAL_SHARES + self.f.filled)

    def test_the_ladder2_flag_is_closed(self):
        self.assertTrue(self.f.trade.ladder2_filled)

    def test_it_reports_the_same_way(self):
        self.assertIn("ladder_partially_filled", self.f.events())

    def test_the_message_points_at_the_floor_not_another_ladder(self):
        body = [e.message for e in self.f.notifier.events
                if e.event == "ladder_partially_filled"][0]
        self.assertIn("-10%", body)

    def test_it_survives_repeated_recovery_passes(self):
        expected = INITIAL_SHARES + self.f.filled
        for _ in range(5):
            self.f.engine.recover(now=_now())
        self.assertEqual(self.f.trade.total_shares, expected)

    def test_confirming_is_now_refused_as_already_applied(self):
        from execution.service import Ladder2PartialFillNotPendingError
        with self.assertRaises(Ladder2PartialFillNotPendingError):
            self.f.exec_service.confirm_ladder2_partial_fill(
                self.f.proposal.proposal_id, decided_by="controller",
                now=_now())


if __name__ == "__main__":
    unittest.main()


class TestZeroFillKeepsTheLadderAvailable(unittest.TestCase):
    """Controller, 2026-10-05: "if they didn't have any shares to buy
    ... no problem, we will continue ... we wouldn't want to stop at one
    of the ladders, because the share price when it decreases does not
    wait for us."

    Reproduced before the fix: a zero fill left the ladder flagged open
    but permanently stuck -- the APPROVED proposal blocked every new
    one, and five further trigger checks at the trigger price produced
    no new chance to buy, with no notification at all.
    """

    def setUp(self):
        self.f = _Fixture(unfilled=None)

    def test_nothing_was_bought(self):
        self.assertEqual(self.f.trade.total_shares, INITIAL_SHARES)

    def test_the_ladder_stays_open(self):
        self.assertFalse(self.f.trade.ladder1_filled)

    def test_the_controller_is_told(self):
        self.assertIn("ladder_filled_nothing", self.f.events())

    def test_the_message_says_it_can_be_tried_again(self):
        body = [e.message for e in self.f.notifier.events
                if e.event == "ladder_filled_nothing"][0]
        self.assertIn("stays open", body)
        self.assertIn("-8%", body)

    def test_told_only_once_per_attempt(self):
        for _ in range(10):
            self.f.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(self.f.events().count("ladder_filled_nothing"), 1)

    def test_a_new_attempt_becomes_possible(self):
        # The heart of it: the stale APPROVED proposal must stop
        # blocking the next one.
        before = len([p for p in self.f.proposal_repo.list_for_trade("T-1")
                      if p.proposed_action is TradeAction.LADDER_1])
        self.f.md.set_price("LOW", self.f.trade0.ladder1_price)
        self.f.engine.run_trigger_check(now=_now())
        after = len([p for p in self.f.proposal_repo.list_for_trade("T-1")
                     if p.proposed_action is TradeAction.LADDER_1])
        self.assertGreater(after, before)

    def test_ladder_2_is_not_blocked_by_a_dead_ladder_1(self):
        self.f.md.set_price("LOW", self.f.trade0.ladder2_price)
        self.f.engine.run_trigger_check(now=_now())
        l2 = [p for p in self.f.proposal_repo.list_for_trade("T-1")
              if p.proposed_action is TradeAction.LADDER_2]
        self.assertTrue(l2, "Ladder 2 must still be reachable")

    def test_the_floor_is_not_blocked_either(self):
        # The floor is evaluated independently of any ladder state.
        self.f.md.set_price("LOW", self.f.trade0.original_floor_price - 1)
        self.f.engine.run_reconciliation_tick(now=_now())
        self.assertTrue(any(e.event.startswith("floor_")
                            for e in self.f.notifier.events))
