import unittest
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from engine.decision_source import ControllerDecision, DecisionKind, InMemoryDecisionSource
from engine.engine import Engine
from engine.lock import EngineLock
from engine.schedule import is_d0021_check_time
from engine.watchlist import StaticWatchlistSource
from execution.broker_client import BrokerClient, BrokerOrderState
from execution.service import ExecutionService
from execution.sqlite_repository import SqliteOrderExecutionRepository
from marketdata.source import MarketDataSource, MarketDataUnavailableError
from notifications.service import INotificationService, NotificationEvent, NotificationLevel, NotificationResult
from orchestration.trade_proposal_service import TradeProposalService
from persistence.db import bootstrap_schema, connect
from proposals.models import ApprovalState, FloorContext, TradeAction, approved_strategy_rule_set
from proposals.sqlite_repository import SqliteProposalRepository
from trade.models import InitialOrderStatus, Trade
from trade.sqlite_repository import SqliteTradeRepository


def _now():
    return datetime(2026, 9, 22, 13, 30, 0, tzinfo=timezone.utc)  # a D-0021 check time (08:30 CT)


def _strategy():
    return approved_strategy_rule_set()


class FakeBrokerClient(BrokerClient):
    def __init__(self):
        self._orders: Dict[str, BrokerOrderState] = {}
        self.submit_calls: List[str] = []
        self.submit_limit_prices: Dict[str, float] = {}
        self.cancel_calls: List[str] = []

    def set_order_state(self, client_order_id: str, state: BrokerOrderState) -> None:
        self._orders[client_order_id] = state

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.submit_calls.append(client_order_id)
        self.submit_limit_prices[client_order_id] = limit_price
        state = self._orders.get(client_order_id) or BrokerOrderState(
            broker_order_id=f"B-{client_order_id}", status="accepted", is_terminal=False,
            filled_qty=0, filled_avg_price=None,
        )
        self._orders[client_order_id] = state
        return state

    def get_order_by_client_order_id(self, client_order_id):
        return self._orders.get(client_order_id)

    def cancel_order(self, client_order_id: str) -> None:
        self.cancel_calls.append(client_order_id)

    def get_cash_balance(self) -> float:
        return 100000.0

    def get_account_equity(self) -> float:
        # Tests were originally written against the pre-D-0051 fixed
        # 10/10/20 quantities. $80k equity makes the D-0051 policy
        # (5% / 25% / 25% / 50%) at the standard $100 test entry price
        # compute exactly initial=10, ladder_1=10, ladder_2=20, so
        # existing assertions keep passing without a per-test rewrite.
        return 80000.0


class FakeMarketDataSource(MarketDataSource):
    def __init__(self, prices: Optional[Dict[str, float]] = None):
        self._prices = prices or {}
        self._raise_for: set = set()

    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def raise_unavailable_for(self, symbol: str) -> None:
        self._raise_for.add(symbol)

    def get_last_trade(self, symbol: str) -> float:
        if symbol in self._raise_for:
            self._raise_for.discard(symbol)
            raise MarketDataUnavailableError(f"simulated outage for {symbol}")
        return self._prices[symbol]


class RecordingNotifier(INotificationService):
    def __init__(self):
        self.events: List[NotificationEvent] = []

    def send(self, event: NotificationEvent) -> NotificationResult:
        self.events.append(event)
        return NotificationResult(success=True, attempts=1)


def _repos():
    conn = connect(":memory:")
    bootstrap_schema(conn)
    return (
        SqliteTradeRepository(conn),
        SqliteProposalRepository(conn),
        SqliteOrderExecutionRepository(conn),
        conn,
    )


def _active_trade(trade_repo, *, trade_id="T-1", symbol="TSLA", price=100.0, shares=10, now=None):
    now = now or _now()
    record = trade_repo.save(Trade(trade_id=trade_id, symbol=symbol, created_at=now), now=now)
    frozen = record.trade.freeze_initial_reference(
        order_status=InitialOrderStatus.FILLED, filled_shares=shares, fill_price=price,
        strategy=_strategy(), now=now,
    )
    return trade_repo.update(frozen, expected_revision=record.revision, transition="frozen", now=now)


def _make_engine(
    trade_repo, proposal_repo, execution_repo, conn, *,
    broker=None, market_data=None, decision_source=None, notifier=None,
    watchlist=None, trade_evaluator=None, political_universe_source=None,
):
    broker = broker or FakeBrokerClient()
    market_data = market_data or FakeMarketDataSource()
    decision_source = decision_source or InMemoryDecisionSource()
    notifier = notifier or RecordingNotifier()
    watchlist = watchlist if watchlist is not None else StaticWatchlistSource(())
    trade_proposal_service = TradeProposalService(trade_repo, proposal_repo)
    execution_service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
    lock = EngineLock(conn, pid=1, host="test-host")
    engine = Engine(
        trade_repo=trade_repo, proposal_repo=proposal_repo, execution_repo=execution_repo,
        trade_proposal_service=trade_proposal_service, execution_service=execution_service,
        market_data=market_data, watchlist=watchlist, decision_source=decision_source,
        notifier=notifier, lock=lock, trade_evaluator=trade_evaluator,
        political_universe_source=political_universe_source,
    )
    return engine, broker, market_data, decision_source, notifier, execution_service


class TestD0021Schedule(unittest.TestCase):
    def test_matches_each_of_the_seven_scheduled_times(self):
        # D-0041 realignment: 09:30 through 15:30 ET (same wall-clock
        # moments as the pre-D-0041 08:30-14:30 CT labels).
        for hour in range(9, 16):
            et = datetime(2026, 9, 21, hour, 30, tzinfo=ZoneInfo("America/New_York"))  # Monday
            self.assertTrue(is_d0021_check_time(et), f"expected {hour}:30 ET to match")

    def test_does_not_match_off_schedule_time(self):
        et = datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertFalse(is_d0021_check_time(et))

    def test_does_not_match_weekend(self):
        et = datetime(2026, 9, 19, 9, 30, tzinfo=ZoneInfo("America/New_York"))  # Saturday
        self.assertFalse(is_d0021_check_time(et))

    def test_tolerance_window_absorbs_a_late_wake(self):
        et = datetime(2026, 9, 21, 9, 31, 20, tzinfo=ZoneInfo("America/New_York"))
        self.assertTrue(is_d0021_check_time(et, tolerance_seconds=90))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            is_d0021_check_time(datetime(2026, 9, 21, 9, 30))

    def test_spring_forward_monday_09_30_et_still_matches(self):
        # D-0041 §1 + verification-plan §6: on the Monday following the
        # US spring-forward Sunday, the schedule must still fire at the
        # ET wall-clock time -- the TZ-aware scheduler absorbs the
        # +1-hour shift automatically.
        et = datetime(2027, 3, 15, 9, 30, tzinfo=ZoneInfo("America/New_York"))  # Monday after 2027-03-14
        self.assertTrue(is_d0021_check_time(et))

    def test_fall_back_monday_09_30_et_still_matches(self):
        # Verification-plan §6: on the Monday following the US fall-back
        # Sunday, the schedule must still fire at the ET wall-clock time.
        et = datetime(2026, 11, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))  # Monday after 2026-11-01
        self.assertTrue(is_d0021_check_time(et))

    def test_dst_transitions_shift_the_UTC_moment_even_though_ET_is_stable(self):
        # Demonstrates the drift a fixed-UTC cron would suffer: the exact
        # same 09:30 ET wall-clock hits a *different* UTC instant before
        # and after the spring-forward. A fixed-UTC schedule targeting
        # the winter UTC would fire an hour off the ET wall-clock after
        # the transition -- which is exactly why D-0020/D-0041 forbid
        # fixed-UTC cron for market-anchored routines.
        winter_et = datetime(2027, 3, 8, 9, 30, tzinfo=ZoneInfo("America/New_York"))  # EST
        summer_et = datetime(2027, 3, 15, 9, 30, tzinfo=ZoneInfo("America/New_York"))  # EDT
        # Same ET wall-clock, but UTC moments differ by exactly one hour.
        winter_utc = winter_et.astimezone(ZoneInfo("UTC"))
        summer_utc = summer_et.astimezone(ZoneInfo("UTC"))
        self.assertEqual((winter_utc.hour, winter_utc.minute), (14, 30))  # EST = UTC-5
        self.assertEqual((summer_utc.hour, summer_utc.minute), (13, 30))  # EDT = UTC-4
        # Both, however, ARE valid schedule slots for our TZ-aware check.
        self.assertTrue(is_d0021_check_time(winter_et))
        self.assertTrue(is_d0021_check_time(summer_et))


class TestTriggerDetection(unittest.TestCase):
    def test_ladder1_trigger_creates_a_pending_proposal_and_notifies(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)  # below ladder1 trigger (95.0)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())

        proposals = proposal_repo.list_for_trade("T-1")
        ladder1 = [p for p in proposals if p.proposed_action is TradeAction.LADDER_1]
        self.assertEqual(len(ladder1), 1)
        self.assertEqual(ladder1[0].approval_state, ApprovalState.PENDING)
        self.assertTrue(any(e.event == "proposal_awaiting_approval" for e in notifier.events))

    def test_price_above_trigger_creates_nothing(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, *_rest, notifier, _ = _make_engine(trade_repo, proposal_repo, execution_repo, conn)
        _rest[1].set_price("TSLA", 99.0)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())

        self.assertEqual(proposal_repo.list_for_trade("T-1"), [])

    def test_floor_priority_blocks_ladder_proposal_at_or_below_active_floor(self):
        # Simulate a trailing floor that has ratcheted above Ladder 1's
        # trigger -- D-0011's proposal-creation gate must block it.
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        record = _active_trade(trade_repo, price=100.0)
        trade = record.trade
        activated = trade.activate_trailing(current_price=trade.weighted_avg_entry_price * 1.10, now=_now())
        trade_repo.update(activated, expected_revision=record.revision, transition="trailing", now=_now())

        # Confirm the setup actually raised the active floor above the
        # Ladder 1 trigger, otherwise this test would not exercise the gate.
        current = trade_repo.get("T-1").trade
        self.assertGreaterEqual(current.active_floor_price, current.ladder1_price)

        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", current.ladder1_price - 1.0)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())

        self.assertEqual(
            [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1], []
        )

    def test_market_data_unavailable_is_isolated_and_notified(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, trade_id="T-1", symbol="TSLA", price=100.0)
        _active_trade(trade_repo, trade_id="T-2", symbol="AAPL", price=50.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.raise_unavailable_for("TSLA")
        market_data.set_price("AAPL", 45.0)  # below AAPL's ladder1 trigger
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())

        self.assertTrue(any(e.event == "market_data_unavailable" for e in notifier.events))
        # The unrelated trade (AAPL) must still be processed.
        aapl_proposals = [p for p in proposal_repo.list_for_trade("T-2") if p.proposed_action is TradeAction.LADDER_1]
        self.assertEqual(len(aapl_proposals), 1)


class TestDecisionIntakeAndSubmission(unittest.TestCase):
    def test_approve_decision_then_trigger_check_submits(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())
        proposal = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1][0]

        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        approved = proposal_repo.get(proposal.proposal_id)
        self.assertEqual(approved.approval_state, ApprovalState.APPROVED)

        engine.run_trigger_check(now=_now() + timedelta(seconds=2))
        self.assertEqual(broker.submit_calls, [execution_repo.get_by_proposal_id(proposal.proposal_id).execution.client_order_id])

    def test_approve_ladder1_submits_on_the_same_reconciliation_tick(self):
        """Regression for 2026-09-30 Bug #4: a Controller approval for a
        LADDER_1 proposal used to be recorded on the reconciliation tick
        but its broker submission was deferred to the NEXT D-0021 :30
        tick (up to ~60 min later). In practice every Ladder approval
        expired under D-0007's 5-minute window before submission ever
        ran. This test asserts the broker sees the submit call on the
        same reconciliation tick that drains the decision, mirroring
        the INITIAL_ENTRY path -- no run_trigger_check between."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())  # creates the LADDER_1 proposal
        proposal = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1][0]

        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        # Broker must have received the submission on this reconciliation
        # tick, NOT wait for a subsequent run_trigger_check call.
        exec_record = execution_repo.get_by_proposal_id(proposal.proposal_id)
        self.assertIsNotNone(exec_record,
            "LADDER_1 approval must reach the broker in the same reconciliation tick")
        self.assertEqual(broker.submit_calls, [exec_record.execution.client_order_id])

    def test_approve_initial_entry_submits_on_the_reconciliation_tick(self):
        """Regression for a live-observed bug in the 2026-09-29 paper
        session: an Initial Entry proposal, once approved via Telegram,
        was recorded APPROVED in the proposal store but never reached
        the broker because the per-cycle _process_trade loop skips
        AWAITING_INITIAL_FILL trades. The fix submits an INITIAL_ENTRY
        immediately from _apply_decision (the same reconciliation tick
        that drains the decision queue) so the trade actually reaches
        Alpaca."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())
        trades = trade_repo.list_for_symbol("TSLA")
        self.assertEqual(len(trades), 1)
        trade_id = trades[0].trade.trade_id
        proposal = [p for p in proposal_repo.list_for_trade(trade_id) if p.proposed_action is TradeAction.INITIAL_ENTRY][0]

        # Controller approves. One reconciliation tick must both record
        # the decision AND submit the order to the broker.
        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        approved = proposal_repo.get(proposal.proposal_id)
        self.assertEqual(approved.approval_state, ApprovalState.APPROVED)
        exec_record = execution_repo.get_by_proposal_id(proposal.proposal_id)
        self.assertIsNotNone(exec_record, "Initial Entry approval must reach the broker in the same tick")
        self.assertEqual(broker.submit_calls, [exec_record.execution.client_order_id])

    def test_reject_decision_prevents_submission(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        proposal = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1][0]

        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.REJECT, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        rejected = proposal_repo.get(proposal.proposal_id)
        self.assertEqual(rejected.approval_state, ApprovalState.REJECTED)
        self.assertEqual(broker.submit_calls, [])

    def test_reject_initial_entry_frees_symbol_for_next_watchlist_tick(self):
        """Regression for 2026-09-30 Bug #3: an Initial Entry proposal
        rejected by the Controller left the Trade row in
        AWAITING_INITIAL_FILL status forever, so _check_watchlist's
        "already has an open trade for this symbol" guard permanently
        skipped the symbol. On the fix, a rejected Initial Entry is
        transitioned to ABANDONED and the next watchlist pass MUST
        produce a fresh proposal for the same symbol."""
        from engine.watchlist import StaticWatchlistSource
        from trade.models import describe_status
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())

        # First tick: create the Initial Entry proposal.
        engine.run_trigger_check(now=_now())
        trades = trade_repo.list_for_symbol("TSLA")
        self.assertEqual(len(trades), 1)
        trade_id_before = trades[0].trade.trade_id
        first_proposal = [p for p in proposal_repo.list_for_trade(trade_id_before)
                          if p.proposed_action is TradeAction.INITIAL_ENTRY][0]

        # Controller rejects.
        decisions.submit(ControllerDecision(
            proposal_id=first_proposal.proposal_id,
            kind=DecisionKind.REJECT,
            decided_by="controller",
        ))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        # Trade must now be ABANDONED, not still AWAITING_INITIAL_FILL.
        after = trade_repo.get(trade_id_before)
        self.assertEqual(describe_status(after.trade), "ABANDONED")

        # Next watchlist tick MUST produce a brand-new INITIAL_ENTRY
        # proposal for the same symbol under a new trade_id.
        engine.run_trigger_check(now=_now() + timedelta(seconds=2))
        trades_after = trade_repo.list_for_symbol("TSLA")
        self.assertEqual(len(trades_after), 2,
            "watchlist must open a new trade after rejection frees the symbol")

    def test_decision_for_unknown_proposal_is_notified_not_raised(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        engine._lock.acquire(now=_now())
        decisions.submit(ControllerDecision(proposal_id="NOPE", kind=DecisionKind.APPROVE, decided_by="controller"))

        engine.run_reconciliation_tick(now=_now())  # must not raise

        self.assertTrue(any(e.event == "decision_unknown_proposal" for e in notifier.events))

    def test_confirm_ladder2_decision_applies_partial_fill(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 91.8)  # below Ladder 2 trigger (92.0), within D-0007 band
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())
        proposal = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_2][0]
        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))
        engine.run_trigger_check(now=_now() + timedelta(seconds=2))

        record = execution_repo.get_by_proposal_id(proposal.proposal_id)
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="canceled", is_terminal=True,
                filled_qty=10, filled_avg_price=92.0,
            ),
        )
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=3))

        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.ladder2_filled)
        self.assertTrue(any(e.event == "ladder2_partial_fill_pending_confirmation" for e in notifier.events))

        decisions.submit(
            ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.CONFIRM_LADDER2_PARTIAL_FILL, decided_by="controller")
        )
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=4))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.ladder2_filled)
        self.assertEqual(trade.total_shares, 20)


class TestNoDuplicateSubmission(unittest.TestCase):
    def test_running_trigger_check_twice_after_submission_does_not_resubmit(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        proposal = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1][0]
        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        engine.run_trigger_check(now=_now() + timedelta(seconds=2))
        engine.run_trigger_check(now=_now() + timedelta(seconds=3))  # must not raise or resubmit

        self.assertEqual(len(broker.submit_calls), 1)
        ladder1_proposals = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1]
        self.assertEqual(len(ladder1_proposals), 1)


class TestNotificationDeduplication(unittest.TestCase):
    def test_pending_approval_notified_once_per_engine_lifetime(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        engine.recover(now=_now() + timedelta(seconds=1))
        engine.recover(now=_now() + timedelta(seconds=2))

        pending_notifications = [e for e in notifier.events if e.event == "proposal_awaiting_approval"]
        self.assertEqual(len(pending_notifications), 1)

    def test_restart_re_notifies_once(self):
        # A fresh Engine instance (simulating a restart) has an empty
        # in-memory notified set -- Controller-approved: "re-notifying a
        # still-pending item once after process restart is acceptable."
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine1, broker, market_data, decisions, notifier1, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine1._lock.acquire(now=_now())
        engine1.run_trigger_check(now=_now())
        engine1._lock.release()

        engine2, _, _, _, notifier2, _ = _make_engine(trade_repo, proposal_repo, execution_repo, conn)
        engine2.start(now=_now() + timedelta(minutes=1))

        self.assertTrue(any(e.event == "proposal_awaiting_approval" for e in notifier2.events))


class TestRecoveryTerminalUnappliedExecution(unittest.TestCase):
    def test_recovery_applies_a_terminal_fill_never_applied_to_trade(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        proposal = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1][0]
        decisions.submit(ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="controller"))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))
        engine.run_trigger_check(now=_now() + timedelta(seconds=2))

        record = execution_repo.get_by_proposal_id(proposal.proposal_id)
        terminal = record.execution.record_fill_update(
            filled_qty=_strategy().ladder_1_qty, filled_avg_price=94.0, status="filled", is_terminal=True,
            now=_now() + timedelta(seconds=3),
        )
        execution_repo.update(
            terminal, expected_revision=record.revision, transition="simulated_crash_terminal",
            now=_now() + timedelta(seconds=3),
        )

        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.ladder1_filled)

        engine.recover(now=_now() + timedelta(minutes=5))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.ladder1_filled)


class TestPendingProposalRecoveryAfterRestart(unittest.TestCase):
    def test_pending_proposal_rediscovered_and_notified_on_restart(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine1, broker, market_data, decisions, notifier1, exec_service1 = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine1._lock.acquire(now=_now())
        engine1.run_trigger_check(now=_now())  # creates a PENDING proposal, "notified"
        engine1._lock.release()  # simulate crash-equivalent clean stop without deciding it

        engine2, _, _, _, notifier2, _ = _make_engine(trade_repo, proposal_repo, execution_repo, conn)
        engine2.start(now=_now() + timedelta(minutes=10))

        self.assertTrue(any(e.event == "proposal_awaiting_approval" for e in notifier2.events))
        proposals = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1]
        self.assertEqual(len(proposals), 1)  # not duplicated by recovery


class TestReconciliationIndependentCadence(unittest.TestCase):
    def test_reconciliation_tick_never_creates_a_ladder_proposal(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 90.0)  # well past both ladder triggers
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        self.assertEqual(proposal_repo.list_for_trade("T-1"), [])


class TestWatchlistDrivenTradeCreation(unittest.TestCase):
    def test_watchlist_symbol_with_no_trade_starts_a_new_trade(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())

        trades = trade_repo.list_for_symbol("TSLA")
        self.assertEqual(len(trades), 1)
        from trade.models import describe_status
        self.assertEqual(describe_status(trades[0].trade), "AWAITING_INITIAL_FILL")

        proposals = proposal_repo.list_for_trade(trades[0].trade.trade_id)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].proposed_action, TradeAction.INITIAL_ENTRY)
        self.assertTrue(any(e.event == "proposal_awaiting_approval" for e in notifier.events))

    def test_watchlist_symbol_with_existing_open_trade_is_not_duplicated(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, trade_id="T-1", symbol="TSLA", price=100.0)
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())

        trades = trade_repo.list_for_symbol("TSLA")
        self.assertEqual(len(trades), 1)  # still just the one, pre-existing trade

    def test_watchlist_market_data_unavailable_is_notified_and_skipped(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.raise_unavailable_for("TSLA")
        engine._lock.acquire(now=_now())

        engine.run_trigger_check(now=_now())  # must not raise

        self.assertEqual(trade_repo.list_for_symbol("TSLA"), [])
        self.assertTrue(any(e.event == "market_data_unavailable" for e in notifier.events))


class TestWatchlistWithTradeEvaluator(unittest.TestCase):
    """D-0050 Phase 10: when a TradeEvaluator is attached, only Top-3
    candidates with score >= 60 become proposals; the rest are
    silently dropped."""

    class _FakeEvalResult:
        def __init__(self, symbol, score=0.0, passes=True):
            self.symbol = symbol
            self.soft_score = score
            self.passes_hard_filter = passes
            self.hard_filter_reasons = ["x"] if not passes else []
            self.score_breakdown = {}
            self.research = None

    class _FakeEvaluator:
        def __init__(self, scores: dict, raise_on_rank: bool = False):
            self._scores = scores
            self._raise = raise_on_rank
            self.last_candidates = None

        def rank(self, candidates):
            self.last_candidates = list(candidates)
            if self._raise:
                raise RuntimeError("eval blew up")
            results = []
            for s in candidates:
                score = self._scores.get(s, 0.0)
                passes = score >= 0  # 0 means "not rejected"
                if score < 0:  # convention: negative = hard-filter reject
                    passes = False
                results.append(TestWatchlistWithTradeEvaluator._FakeEvalResult(
                    s, abs(score), passes))
            return sorted(results, key=lambda r: -r.soft_score)

    def test_low_score_candidates_skipped(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("A", "B", "C"))
        evaluator = self._FakeEvaluator({"A": 55.0, "B": 65.0, "C": 70.0})
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
        )
        for s in ("A", "B", "C"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        trades = set()
        for s in ("A", "B", "C"):
            if trade_repo.list_for_symbol(s):
                trades.add(s)
        # A (55) below threshold → skipped. B,C accepted.
        self.assertEqual(trades, {"B", "C"})
        self.assertEqual(sorted(evaluator.last_candidates), ["A", "B", "C"])

    def test_top_n_cap_enforced(self):
        """More than 3 passing symbols → only Top-3 by score become proposals."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("A", "B", "C", "D", "E"))
        evaluator = self._FakeEvaluator({
            "A": 61.0, "B": 70.0, "C": 90.0, "D": 65.0, "E": 80.0,
        })
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
        )
        for s in ("A", "B", "C", "D", "E"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        trades = {s for s in ("A", "B", "C", "D", "E")
                  if trade_repo.list_for_symbol(s)}
        # Top-3 by score: C(90), E(80), B(70). D(65) and A(61) dropped.
        self.assertEqual(trades, {"C", "E", "B"})

    def test_evaluator_raise_falls_back_to_unranked(self):
        """A buggy evaluator must never block the trigger loop."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("A", "B"))
        evaluator = self._FakeEvaluator({}, raise_on_rank=True)
        engine, broker, market_data, decisions, notifier, _ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
        )
        for s in ("A", "B"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        trades = {s for s in ("A", "B") if trade_repo.list_for_symbol(s)}
        self.assertEqual(trades, {"A", "B"})
        self.assertTrue(any(e.event == "evaluator_failed" for e in notifier.events))

    def test_hard_filter_reject_dropped(self):
        """A hard-filter reject must not become a proposal."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("A", "B"))
        evaluator = self._FakeEvaluator({"A": -1, "B": 75.0})  # A rejected
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
        )
        for s in ("A", "B"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        self.assertEqual(trade_repo.list_for_symbol("A"), [])
        self.assertTrue(trade_repo.list_for_symbol("B"))

    def test_no_evaluator_falls_back_to_pre_d0050_flow(self):
        """When no evaluator is attached, every candidate becomes a proposal
        (the pre-D-0050 behaviour must still work)."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("A", "B"))
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist,  # no trade_evaluator
        )
        for s in ("A", "B"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        trades = {s for s in ("A", "B") if trade_repo.list_for_symbol(s)}
        self.assertEqual(trades, {"A", "B"})


class TestPoliticalSignalEndToEnd(unittest.TestCase):
    """Phase B.28 end-to-end: political signal must FLOW from universe
    source → research enrichment → re-evaluation → final rank.
    This test specifically catches the bug where the signal was
    enriched AFTER scoring (so it never actually affected the score)."""

    class _FakeResearch:
        def __init__(self, symbol):
            self.symbol = symbol
            self.political_buys_30d = 0
            self.political_sells_30d = 0
            self.political_recent_names = []
            self.political_cluster_score = 0.0
            self.political_committee_match = False
            self.political_weighted_signal = 0.0
            self.political_sell_wave = False

    class _FakeResult:
        def __init__(self, symbol, score, research, passes=True, reasons=None):
            self.symbol = symbol
            self.soft_score = score
            self.passes_hard_filter = passes
            self.hard_filter_reasons = reasons or []
            self.score_breakdown = {"base": score}
            self.research = research

    class _ReevalCountingEvaluator:
        """Evaluator stub that mirrors the real one: evaluate_research()
        is a pure function of research state, so if political_sell_wave
        is True the result rejects; political_weighted_signal boosts
        the score."""
        def __init__(self, base_scores):
            self._base = base_scores
            self.rank_calls = 0
            self.reeval_calls = 0

        def rank(self, candidates):
            self.rank_calls += 1
            return [
                TestPoliticalSignalEndToEnd._FakeResult(
                    s,
                    self._base.get(s, 50.0),
                    TestPoliticalSignalEndToEnd._FakeResearch(s),
                )
                for s in candidates
            ]

        def evaluate_research(self, research):
            self.reeval_calls += 1
            # Mirror the real hard-filter: sell_wave rejects.
            if research.political_sell_wave:
                return TestPoliticalSignalEndToEnd._FakeResult(
                    research.symbol, 0.0, research,
                    passes=False, reasons=["sell wave"],
                )
            # Mirror the soft score: political_weighted_signal adds up to +15.
            base = 50.0
            political_bonus = min(15.0, research.political_weighted_signal)
            return TestPoliticalSignalEndToEnd._FakeResult(
                research.symbol, base + political_bonus, research,
            )

    class _PoliticalSrcStub:
        def __init__(self, symbols_by_sig):
            # symbols_by_sig: {symbol: TickerPoliticalSignal-like dict}
            self._syms = tuple(symbols_by_sig.keys())
            self._sigs = symbols_by_sig
        def get_active_symbols(self):
            return self._syms
        def get_signals(self):
            class _S: pass
            out = {}
            for sym, data in self._sigs.items():
                s = _S()
                s.politician_buys_30d = data.get("buys", 0)
                s.politician_sells_30d = data.get("sells", 0)
                s.recent_names = data.get("names", [])
                s.cluster_score = data.get("cluster", 0.0)
                s.committee_match = data.get("cmte", False)
                s.weighted_signal = data.get("signal", 0.0)
                s.sell_wave = data.get("sell_wave", False)
                out[sym] = s
            return out

    def test_political_signal_actually_boosts_score(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))  # TSLA from watchlist
        # NVDA gets a strong political signal from the universe source.
        evaluator = self._ReevalCountingEvaluator({"TSLA": 50.0, "NVDA": 50.0})
        political = self._PoliticalSrcStub({
            "NVDA": {"buys": 3, "signal": 15.0, "cmte": True,
                     "names": ["Pelosi", "Crenshaw", "Khanna"]},
        })
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
            political_universe_source=political,
        )
        for s in ("TSLA", "NVDA"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        # NVDA re-evaluated with political signal → score 65 > TSLA's 50.
        # Both open trades should exist (both above 60 cutoff on NVDA path).
        self.assertTrue(trade_repo.list_for_symbol("NVDA"))
        # TSLA's final score was 50 — below MIN_SCORE 60, so no trade.
        self.assertFalse(trade_repo.list_for_symbol("TSLA"))
        # Prove the enrichment loop re-called evaluate_research
        self.assertGreater(evaluator.reeval_calls, 0)

    def test_political_sell_wave_blocks_trade(self):
        """A ranked candidate with 3+ whitelist sellers → sell_wave →
        hard-filter rejects → no proposal is created."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(())  # universe comes from political source
        evaluator = self._ReevalCountingEvaluator({"BADSTOCK": 85.0})
        political = self._PoliticalSrcStub({
            "BADSTOCK": {"buys": 0, "sells": 3, "sell_wave": True,
                         "names": ["Pelosi", "Crenshaw", "Khanna"]},
        })
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
            political_universe_source=political,
        )
        market_data.set_price("BADSTOCK", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        # Even though base score was 85, sell-wave hard-filter rejects.
        self.assertFalse(trade_repo.list_for_symbol("BADSTOCK"))

    def test_political_source_failure_doesnt_block(self):
        """A buggy political universe source must never crash the
        trigger loop — fall back to watchlist-only."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("AAPL",))
        evaluator = self._ReevalCountingEvaluator({"AAPL": 75.0})

        class _Boom:
            def get_active_symbols(self): raise RuntimeError("x")
            def get_signals(self): raise RuntimeError("x")

        engine, broker, market_data, _d, notifier, _ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=watchlist, trade_evaluator=evaluator,
            political_universe_source=_Boom(),
        )
        market_data.set_price("AAPL", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        # AAPL still proposed; political failure logged
        self.assertTrue(trade_repo.list_for_symbol("AAPL"))
        self.assertTrue(any(e.event == "political_universe_failed"
                            for e in notifier.events))


class TestOrphanedTradeRecovery(unittest.TestCase):
    def test_recovery_recreates_missing_initial_entry_proposal(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        # Simulates start_trade()'s documented crash window: Trade
        # persisted, but the process died before its Initial Entry
        # proposal was ever created.
        trade_repo.save(Trade(trade_id="T-1", symbol="TSLA", created_at=_now()), now=_now())
        self.assertEqual(proposal_repo.list_for_trade("T-1"), [])

        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 100.0)

        engine.start(now=_now())

        proposals = proposal_repo.list_for_trade("T-1")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].proposed_action, TradeAction.INITIAL_ENTRY)
        self.assertTrue(any(e.event == "proposal_awaiting_approval" for e in notifier.events))


class TestFloorTriggerDetection(unittest.TestCase):
    def test_floor_not_triggered_above_floor_price(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)  # floor = 90.0
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 91.0)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        self.assertEqual(broker.submit_calls, [])
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 40)

    def test_floor_limit_price_is_one_percent_below_current_price(self):
        # Controller-approved execution range: -1% to -0.5% off the
        # current price at trigger time; only the lower (-1%) boundary
        # is actually submitted as the limit price.
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)  # floor = 90.0
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 88.0)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        self.assertEqual(len(broker.submit_calls), 1)
        submitted_client_order_id = broker.submit_calls[0]
        self.assertEqual(broker.submit_limit_prices[submitted_client_order_id], 87.12)  # 88.0 * 0.99

    def test_floor_full_fill_closes_trade_and_notifies(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)  # floor = 90.0

        class _ImmediateFillBroker(FakeBrokerClient):
            def submit_order(self, **kwargs):
                self.submit_calls.append(kwargs["client_order_id"])
                state = BrokerOrderState(
                    broker_order_id=f"B-{kwargs['client_order_id']}", status="filled", is_terminal=True,
                    filled_qty=kwargs["quantity"], filled_avg_price=kwargs["limit_price"],
                )
                self._orders[kwargs["client_order_id"]] = state
                return state

        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, broker=_ImmediateFillBroker()
        )
        market_data.set_price("TSLA", 88.0)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        self.assertEqual(len(broker.submit_calls), 1)
        trade = trade_repo.get("T-1").trade
        self.assertEqual(trade.total_shares, 0)
        from trade.models import describe_status
        self.assertEqual(describe_status(trade), "CLOSED")
        self.assertTrue(any(e.event == "floor_triggered" for e in notifier.events))
        self.assertTrue(any(e.event == "floor_executed_full" for e in notifier.events))

    def test_floor_partial_fill_auto_applies_and_notifies(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)  # floor = 90.0

        class _PartialFillBroker(FakeBrokerClient):
            def submit_order(self, **kwargs):
                self.submit_calls.append(kwargs["client_order_id"])
                state = BrokerOrderState(
                    broker_order_id=f"B-{kwargs['client_order_id']}", status="canceled", is_terminal=True,
                    filled_qty=25, filled_avg_price=kwargs["limit_price"],
                )
                self._orders[kwargs["client_order_id"]] = state
                return state

        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, broker=_PartialFillBroker()
        )
        market_data.set_price("TSLA", 88.0)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        trade = trade_repo.get("T-1").trade
        # Auto-applied -- no Controller approval was involved.
        self.assertEqual(trade.total_shares, 15)
        self.assertTrue(any(e.event == "floor_executed_partial" for e in notifier.events))

    def test_trailing_floor_ratchet_is_used_automatically_with_no_new_code(self):
        # Verification-only (Controller-approved implementation order,
        # step 7): Trailing Floor's math (Trade.activate_trailing(),
        # already approved and unmodified) already raises
        # trade.active_floor_price -- this proves Floor detection
        # picks up the RATCHETED level automatically, with zero new
        # Engine/ExecutionService code for Trailing Floor itself.
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        record = _active_trade(trade_repo, price=100.0, shares=40)  # original floor = 90.0
        trade = record.trade
        activated = trade.activate_trailing(current_price=trade.weighted_avg_entry_price * 1.10, now=_now())
        trade_repo.update(activated, expected_revision=record.revision, transition="trailing", now=_now())

        current = trade_repo.get("T-1").trade
        self.assertGreater(current.active_floor_price, 90.0)  # ratcheted above the original floor

        class _ImmediateFillBroker(FakeBrokerClient):
            def submit_order(self, **kwargs):
                self.submit_calls.append(kwargs["client_order_id"])
                state = BrokerOrderState(
                    broker_order_id=f"B-{kwargs['client_order_id']}", status="filled", is_terminal=True,
                    filled_qty=kwargs["quantity"], filled_avg_price=kwargs["limit_price"],
                )
                self._orders[kwargs["client_order_id"]] = state
                return state

        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, broker=_ImmediateFillBroker()
        )
        # A price that is BELOW the original floor (90.0) but ABOVE it
        # would previously not trigger under the un-ratcheted floor --
        # here it must trigger, because the ratcheted floor is higher.
        just_below_ratcheted_floor = current.active_floor_price - 0.5
        market_data.set_price("TSLA", just_below_ratcheted_floor)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        self.assertEqual(len(broker.submit_calls), 1)
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 0)

    def test_floor_no_duplicate_submission_while_live_exit_exists(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)  # floor = 90.0
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 88.0)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(broker.submit_calls), 1)

        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=5))
        # No second submission while the first is still live (default
        # FakeBrokerClient response is non-terminal "accepted").
        self.assertEqual(len(broker.submit_calls), 1)


class TestEngineLifecycleErrors(unittest.TestCase):
    def test_run_forever_respects_d0021_gating_and_max_iterations(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 94.6)
        engine._lock.acquire(now=_now())

        # Off-schedule time -- trigger_check must not run even though a
        # trigger condition holds.
        off_schedule = _now().replace(hour=15, minute=0)  # not a D-0021 slot
        state = {"t": off_schedule}

        def off_schedule_clock():
            state["t"] += timedelta(seconds=1)
            return state["t"]

        engine.run_forever(
            reconciliation_interval_seconds=0,
            now_fn=off_schedule_clock,
            sleep_fn=lambda _seconds: None,
            max_iterations=2,
        )
        self.assertEqual(proposal_repo.list_for_trade("T-1"), [])

        # On-schedule time -- trigger_check must run.
        state2 = {"t": _now() - timedelta(seconds=1)}

        def on_schedule_clock():
            state2["t"] += timedelta(seconds=1)
            return state2["t"]

        engine.run_forever(
            reconciliation_interval_seconds=0,
            now_fn=on_schedule_clock,
            sleep_fn=lambda _seconds: None,
            max_iterations=1,
        )
        ladder1 = [p for p in proposal_repo.list_for_trade("T-1") if p.proposed_action is TradeAction.LADDER_1]
        self.assertEqual(len(ladder1), 1)


class TestTrailingFloorWiring(unittest.TestCase):
    """Regression for 2026-10-01 Finding #1: in production the engine
    never called Trade.activate_trailing / Trade.ratchet_trailing, so
    the Controller-approved D-0004/D-0008 trailing floor was dormant
    and every live trade's active floor stayed at original_floor_price
    (-10% of entry) forever. The fix wires both into
    _check_floor_trigger (fast reconciliation cadence), BEFORE the
    active_floor_price read, so the latest trailing floor participates
    in the floor comparison on the same tick."""

    def test_price_reaches_activation_threshold_activates_trailing(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)  # WAE=100, floor=90, trailing OFF
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        # Activation threshold = 100 * 1.10 = 110. Trailing floor on
        # activation = 110 * 0.95 = 104.5.
        market_data.set_price("TSLA", 110.0)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        after = trade_repo.get("T-1").trade
        self.assertTrue(after.trailing_activated)
        self.assertEqual(after.trailing_current_threshold, 110.0)
        self.assertEqual(after.trailing_floor_price, 104.5)
        self.assertEqual(after.active_floor_price, 104.5)  # trailing wins
        self.assertTrue(any(e.event == "trailing_activated" for e in notifier.events))

    def test_price_below_activation_threshold_leaves_trailing_off(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 109.99)
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        after = trade_repo.get("T-1").trade
        self.assertFalse(after.trailing_activated)
        self.assertEqual(after.active_floor_price, 90.0)  # still original

    def test_ratchet_fires_on_each_next_threshold(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        engine._lock.acquire(now=_now())
        # First tick: price hits 110 -> trailing activates (threshold
        # 110, floor 104.5).
        market_data.set_price("TSLA", 110.0)
        engine.run_reconciliation_tick(now=_now())

        # Second tick: price jumps to 121.5 (above 110 * 1.05 = 115.5
        # AND above 115.5 * 1.05 = 121.275). Both ratchet steps must
        # fire in one tick (matches backtesting simulator's while loop).
        market_data.set_price("TSLA", 121.5)
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        after = trade_repo.get("T-1").trade
        # After two ratchets: threshold = 110 * 1.05 * 1.05 = 121.275.
        # New trailing floor = 121.275 * 0.95 = 115.21125 (rounded to
        # 4 decimals by ratchet_trailing).
        self.assertAlmostEqual(after.trailing_current_threshold, 121.275, places=4)
        self.assertAlmostEqual(after.trailing_floor_price, 115.2113, places=4)
        self.assertGreater(
            sum(1 for e in notifier.events if e.event == "trailing_ratcheted"), 0
        )

    def test_trailing_floor_blocks_a_price_drop_now_below_the_lifted_floor(self):
        """End-to-end: activate trailing, let price fall back below the
        trailing floor, assert the Floor fires using the trailing floor
        (not the original one)."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        engine._lock.acquire(now=_now())
        # Tick 1: activate.
        market_data.set_price("TSLA", 110.0)
        engine.run_reconciliation_tick(now=_now())
        # Tick 2: price drops to 104.0 -- above original floor (90),
        # but below the trailing floor (104.5) -> Floor must fire.
        market_data.set_price("TSLA", 104.0)
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        # A protective SELL execution must now exist for this trade.
        sell = execution_repo.get_by_trade_id_and_side("T-1", "sell")
        self.assertIsNotNone(sell,
            "trailing floor must trigger Floor at 104.0 since 104.0 <= 104.5")
        self.assertTrue(any(e.event == "floor_triggered" for e in notifier.events))


class TestInitialEntryAbandonedAfterSubmissionRefusal(unittest.TestCase):
    """Regression for 2026-10-01 Finding #2: an INITIAL_ENTRY approved
    by the Controller but refused by D-0007 revalidation (EXPIRED or
    ±0.5% PRICE_DRIFT) left the Trade in AWAITING_INITIAL_FILL forever,
    permanently blocking the symbol from the next watchlist tick. The
    fix abandons the Trade in the same way the REJECT branch does."""

    def test_price_drift_refusal_abandons_the_trade_and_frees_the_symbol(self):
        from trade.models import describe_status
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        trades = trade_repo.list_for_symbol("TSLA")
        trade_id_before = trades[0].trade.trade_id
        proposal = [p for p in proposal_repo.list_for_trade(trade_id_before)
                    if p.proposed_action is TradeAction.INITIAL_ENTRY][0]

        # Price drifts beyond the D-0007 ±0.5% band before approval is
        # applied. 101.0 is +1.0% off the proposal snapshot of 100.0,
        # safely outside the band so submit_approved_proposal raises
        # SubmissionNotAllowedError.
        market_data.set_price("TSLA", 101.0)
        decisions.submit(ControllerDecision(
            proposal_id=proposal.proposal_id,
            kind=DecisionKind.APPROVE,
            decided_by="controller",
        ))
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        # Trade must now be ABANDONED (NOT still AWAITING_INITIAL_FILL).
        after = trade_repo.get(trade_id_before)
        self.assertEqual(describe_status(after.trade), "ABANDONED")
        self.assertTrue(any(e.event == "initial_entry_abandoned" for e in notifier.events))

        # Next watchlist tick must open a brand-new trade for the same
        # symbol -- symbol was properly freed.
        engine.run_trigger_check(now=_now() + timedelta(seconds=2))
        self.assertEqual(len(trade_repo.list_for_symbol("TSLA")), 2)


class TestInitialEntryRecoveryAfterCrashBetweenRecordAndSubmit(unittest.TestCase):
    """Regression for 2026-10-01 Finding #4: a crash between
    proposal_repo.record_decision and ExecutionService.
    submit_approved_proposal left an APPROVED Initial Entry proposal
    with no execution row and no retry path. The fix adds a recovery
    pass in run_reconciliation_tick that scans for APPROVED
    INITIAL_ENTRY proposals missing an execution and resubmits them."""

    def test_approved_initial_entry_with_no_execution_is_resubmitted(self):
        from proposals.models import approved_strategy_rule_set as _strat
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        watchlist = StaticWatchlistSource(("TSLA",))
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn, watchlist=watchlist
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())  # creates INITIAL_ENTRY proposal
        trade_id = trade_repo.list_for_symbol("TSLA")[0].trade.trade_id
        proposal = [p for p in proposal_repo.list_for_trade(trade_id)
                    if p.proposed_action is TradeAction.INITIAL_ENTRY][0]

        # Simulate the crash scenario: mark the proposal APPROVED
        # directly in the repo (as if record_decision ran), but DO NOT
        # go through _apply_decision so no submission happens.
        proposal_repo.record_decision(
            proposal.proposal_id, approved=True, decided_by="controller",
            decided_at=_now(), action=TradeAction.INITIAL_ENTRY,
        )
        self.assertIsNone(execution_repo.get_by_proposal_id(proposal.proposal_id))

        # Recovery pass must notice and resubmit on the next
        # reconciliation tick.
        engine.run_reconciliation_tick(now=_now() + timedelta(seconds=1))

        exec_record = execution_repo.get_by_proposal_id(proposal.proposal_id)
        self.assertIsNotNone(exec_record,
            "recovery pass must retry an APPROVED Initial Entry with no execution")
        self.assertEqual(broker.submit_calls, [exec_record.execution.client_order_id])
        self.assertTrue(any(e.event == "initial_entry_recovery_submit"
                            for e in notifier.events))


class TestLockOnSeparateConn(unittest.TestCase):
    """Controller-approved 2026-10-01: the engine_lock table lives in
    its own session-ephemeral SQLite file (engine_lock.sqlite), split
    out of paper_session.sqlite so per-tick heartbeat writes never
    dirty the committed data DB. These tests prove the lock-on-its-
    own-conn wiring works end-to-end AND that the data conn stays
    bit-for-bit untouched by lock traffic."""

    def test_lock_on_a_dedicated_conn_leaves_the_data_conn_untouched(self):
        from persistence.db import connect, bootstrap_schema, bootstrap_lock_only_schema
        data_conn = connect(":memory:")
        bootstrap_schema(data_conn)
        lock_conn = connect(":memory:")
        bootstrap_lock_only_schema(lock_conn)

        lock = EngineLock(lock_conn, pid=99, host="test-host")
        now = _now()
        lock.acquire(now=now)
        lock.heartbeat(now=now + timedelta(seconds=1))
        lock.heartbeat(now=now + timedelta(seconds=2))
        lock.heartbeat(now=now + timedelta(seconds=3))

        # The lock conn carries the lock row.
        lock_rows = lock_conn.execute("SELECT COUNT(*) FROM engine_lock").fetchone()[0]
        self.assertEqual(lock_rows, 1)

        # The data conn's engine_lock table (created by bootstrap_schema
        # via migration 0003) exists but has NO rows -- the heartbeat
        # traffic went exclusively to the lock conn.
        data_rows = data_conn.execute("SELECT COUNT(*) FROM engine_lock").fetchone()[0]
        self.assertEqual(data_rows, 0,
            "the lock DB separation must leave the data conn's engine_lock empty")

    def test_bootstrap_lock_only_schema_is_idempotent(self):
        """A fresh container will call bootstrap_lock_only_schema on a
        maybe-existing engine_lock.sqlite -- the IF NOT EXISTS DDL
        must be safe to run twice."""
        from persistence.db import connect, bootstrap_lock_only_schema
        conn = connect(":memory:")
        bootstrap_lock_only_schema(conn)
        bootstrap_lock_only_schema(conn)  # must not raise
        # And the table must still be usable.
        lock = EngineLock(conn, pid=1, host="test")
        lock.acquire(now=_now())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM engine_lock").fetchone()[0], 1)

    def test_bootstrap_lock_only_schema_does_not_create_other_tables(self):
        """Lock DB is deliberately minimal -- it must NOT include
        trades/proposals/order_executions etc."""
        from persistence.db import connect, bootstrap_lock_only_schema
        conn = connect(":memory:")
        bootstrap_lock_only_schema(conn)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertEqual(tables, {"engine_lock"})


class TestDbPersisterCallback(unittest.TestCase):
    """Controller-approved 2026-10-01: at the end of every tick,
    paper_session.sqlite (and ONLY that file) is committed+pushed to
    the branch so a cloud-container reclaim mid-session preserves
    state. The Engine takes an optional db_persister callback; the
    production wiring is in scripts/run_paper_session.py. These tests
    use an in-memory stub to confirm:
      1. The callback IS invoked at the end of both run_trigger_check
         and run_reconciliation_tick.
      2. A raise from the callback NEVER crashes the main loop (the
         CRITICAL notification is emitted and the engine stays alive)."""

    def _engine_with_persister(self, callback):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        from engine.engine import Engine
        from execution.service import ExecutionService
        trade_proposal_service = TradeProposalService(trade_repo, proposal_repo)
        execution_service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        notifier = RecordingNotifier()
        engine = Engine(
            trade_repo=trade_repo, proposal_repo=proposal_repo, execution_repo=execution_repo,
            trade_proposal_service=trade_proposal_service, execution_service=execution_service,
            market_data=FakeMarketDataSource(), watchlist=StaticWatchlistSource(()),
            decision_source=InMemoryDecisionSource(), notifier=notifier,
            lock=EngineLock(conn, pid=1, host="test-host"),
            db_persister=callback,
        )
        engine._lock.acquire(now=_now())
        return engine, notifier

    def test_callback_invoked_once_per_reconciliation_tick(self):
        calls = []
        def cb(now):
            calls.append(now)
        engine, _ = self._engine_with_persister(cb)
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(calls), 1)

    def test_callback_invoked_once_per_trigger_check(self):
        calls = []
        def cb(now):
            calls.append(now)
        engine, _ = self._engine_with_persister(cb)
        engine.run_trigger_check(now=_now())
        self.assertEqual(len(calls), 1)

    def test_callback_failure_is_logged_but_does_not_crash(self):
        def cb(now):
            raise RuntimeError("simulated git-push failure")
        engine, notifier = self._engine_with_persister(cb)
        # Must not raise.
        engine.run_reconciliation_tick(now=_now())
        self.assertTrue(any(e.event == "db_persist_failed" for e in notifier.events))

    def test_no_callback_is_a_silent_no_op(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, _b, _m, _d, _n, _s = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        engine._lock.acquire(now=_now())
        # Must not raise even with db_persister=None (default).
        engine.run_reconciliation_tick(now=_now())
        engine.run_trigger_check(now=_now())


class TestFloorLimitPriceRoundingConsolidated(unittest.TestCase):
    """Regression for 2026-10-01 Finding #3: the Floor limit price was
    previously double-rounded (engine -> 4 decimals, then broker ->
    SEC-compliant precision). The fix removes the engine-side pre-
    rounding and lets the broker-client formatter be the single
    source of truth for precision. Behavior must still be correct for
    both >=$1 and sub-$1 prices."""

    def test_floor_passes_unrounded_limit_price_to_execution_service(self):
        # Trade at $100, price falls to $89 -> Floor fires. Limit =
        # 89 * 0.99 = 88.11 (exact 2 decimals). The important check
        # here is that the limit_price going into the broker is the
        # raw float, and the broker-client's formatter does the SEC-
        # Rule-612 2-decimal quantization in ONE place.
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        engine, broker, market_data, decisions, notifier, exec_service = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn
        )
        market_data.set_price("TSLA", 89.0)  # <= floor at 90.0 -> fire
        engine._lock.acquire(now=_now())

        engine.run_reconciliation_tick(now=_now())

        sell = execution_repo.get_by_trade_id_and_side("T-1", "sell")
        self.assertIsNotNone(sell)
        self.assertEqual(len(broker.submit_calls), 1)
        # 89.0 * (1 - 0.01) = 88.11 exactly; limit must match without
        # engine-side quantization having masked anything.
        limit = broker.submit_limit_prices[sell.execution.client_order_id]
        self.assertAlmostEqual(limit, 88.11, places=6)


if __name__ == "__main__":
    unittest.main()
