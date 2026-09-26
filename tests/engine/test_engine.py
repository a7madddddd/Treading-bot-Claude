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
    broker=None, market_data=None, decision_source=None, notifier=None, watchlist=None,
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
        notifier=notifier, lock=lock,
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


if __name__ == "__main__":
    unittest.main()
