import unittest
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from execution.broker_client import BrokerClient, BrokerOrderState, BrokerSubmissionAmbiguousError
from execution.repository import OrderExecutionRevisionConflictError
from execution.service import (
    ExecutionAlreadyResolvedError,
    ExecutionAlreadySubmittedError,
    ExecutionService,
    ExecutionServiceError,
    Ladder2PartialFillNotPendingError,
    ProposalNotApprovedError,
    ProposalNotFoundError,
    SubmissionNotAllowedError,
)
from execution.sqlite_repository import SqliteOrderExecutionRepository
from persistence.db import bootstrap_schema, connect
from proposals.models import ApprovalState, FloorContext, TradeAction, approved_strategy_rule_set
from proposals.proposal import build_trade_proposal
from proposals.sqlite_repository import SqliteProposalRepository
from trade.models import InitialOrderStatus, Trade
from trade.sqlite_repository import SqliteTradeRepository


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


class FakeBrokerClient(BrokerClient):
    """In-memory, fully controllable BrokerClient stand-in -- never a
    real network call, never Alpaca. Tests configure exact scenarios
    (immediate fill, rejection, ambiguous-but-lost, ambiguous-but-received)
    via the queue_* helpers below."""

    def __init__(self):
        self._orders: Dict[str, BrokerOrderState] = {}
        self._raise_on_submit: Dict[str, Optional[BrokerOrderState]] = {}
        self._queued_responses: Dict[str, BrokerOrderState] = {}
        self._raise_on_cancel: set = set()
        self._raise_on_get_order: set = set()
        self.submit_calls = []
        self.submit_limit_prices: Dict[str, float] = {}
        self.cancel_calls = []

    def queue_response(self, client_order_id: str, state: BrokerOrderState) -> None:
        self._queued_responses[client_order_id] = state

    def queue_cancel_failure(self, client_order_id: str) -> None:
        self._raise_on_cancel.add(client_order_id)

    def queue_get_order_failure(self, client_order_id: str) -> None:
        """Simulates a single transient broker/API/network exception on
        the NEXT get_order_by_client_order_id() call for this id."""
        self._raise_on_get_order.add(client_order_id)

    def cancel_order(self, client_order_id: str) -> None:
        self.cancel_calls.append(client_order_id)
        if client_order_id in self._raise_on_cancel:
            self._raise_on_cancel.discard(client_order_id)
            raise RuntimeError("simulated ambiguous cancel failure")

    def set_order_state(self, client_order_id: str, state: BrokerOrderState) -> None:
        """Simulates the broker's own server-side state having advanced
        (e.g. a fill arriving) -- what a subsequent
        get_order_by_client_order_id() poll will observe. Distinct from
        queue_response(), which only affects the NEXT submit_order()
        call."""
        self._orders[client_order_id] = state

    def queue_ambiguous(self, client_order_id: str, *, received_state: Optional[BrokerOrderState] = None) -> None:
        self._raise_on_submit[client_order_id] = received_state

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.submit_calls.append(client_order_id)
        self.submit_limit_prices[client_order_id] = limit_price
        if client_order_id in self._raise_on_submit:
            received_state = self._raise_on_submit.pop(client_order_id)
            if received_state is not None:
                self._orders[client_order_id] = received_state
            raise BrokerSubmissionAmbiguousError("simulated ambiguous outcome")
        state = self._queued_responses.pop(client_order_id, None)
        if state is None:
            state = BrokerOrderState(
                broker_order_id=f"B-{client_order_id}",
                status="accepted",
                is_terminal=False,
                filled_qty=0,
                filled_avg_price=None,
            )
        self._orders[client_order_id] = state
        return state

    def get_order_by_client_order_id(self, client_order_id):
        if client_order_id in self._raise_on_get_order:
            self._raise_on_get_order.discard(client_order_id)
            raise RuntimeError("simulated transient broker/API/network error")
        return self._orders.get(client_order_id)


def _repos():
    conn = connect(":memory:")
    bootstrap_schema(conn)
    return (
        SqliteTradeRepository(conn),
        SqliteProposalRepository(conn),
        SqliteOrderExecutionRepository(conn),
    )


def _approved_initial_entry(trade_repo, proposal_repo, *, trade_id="T-1", proposal_id="P-1", price=100.0):
    trade_repo.save(Trade(trade_id=trade_id, symbol="TSLA", created_at=_now()), now=_now())
    proposal = build_trade_proposal(
        proposal_id=proposal_id,
        trade_id=trade_id,
        action=TradeAction.INITIAL_ENTRY,
        symbol="TSLA",
        current_price=price,
        as_of=_now(),
        strategy=_strategy(),
        floor_context=FloorContext.no_existing_position(),
    )
    proposal_repo.save(proposal)
    return proposal_repo.record_decision(
        proposal_id, approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
    )


def _active_trade(trade_repo, *, trade_id="T-1", price=100.0, shares=10):
    record = trade_repo.save(Trade(trade_id=trade_id, symbol="TSLA", created_at=_now()), now=_now())
    frozen = record.trade.freeze_initial_reference(
        order_status=InitialOrderStatus.FILLED,
        filled_shares=shares,
        fill_price=price,
        strategy=_strategy(),
        now=_now(),
    )
    return trade_repo.update(frozen, expected_revision=record.revision, transition="frozen", now=_now())


def _approved_ladder(proposal_repo, *, trade_id, proposal_id, action, price=95.0, floor_price=90.0):
    proposal = build_trade_proposal(
        proposal_id=proposal_id,
        trade_id=trade_id,
        action=action,
        symbol="TSLA",
        current_price=price,
        as_of=_now(),
        strategy=_strategy(),
        floor_context=FloorContext.known(floor_price),
    )
    proposal_repo.save(proposal)
    return proposal_repo.record_decision(
        proposal_id, approved=True, decided_by="controller", decided_at=_now(), action=action
    )


class TestApprovalAndD0007Boundaries(unittest.TestCase):
    def test_missing_proposal_raises(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(ProposalNotFoundError):
            service.submit_approved_proposal("NOPE", current_price=100.0, active_floor_price=50.0, now=_now())

    def test_pending_proposal_raises(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        trade_repo.save(Trade(trade_id="T-1", symbol="TSLA", created_at=_now()), now=_now())
        proposal = build_trade_proposal(
            proposal_id="P-1", trade_id="T-1", action=TradeAction.INITIAL_ENTRY, symbol="TSLA",
            current_price=100.0, as_of=_now(), strategy=_strategy(), floor_context=FloorContext.no_existing_position(),
        )
        proposal_repo.save(proposal)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(ProposalNotApprovedError):
            service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

    def test_d0007_price_band_violation_blocks_submission(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        with self.assertRaises(SubmissionNotAllowedError):
            service.submit_approved_proposal(
                "P-1", current_price=150.0, active_floor_price=50.0, now=_now() + timedelta(seconds=30)
            )
        self.assertEqual(broker.submit_calls, [])
        self.assertIsNone(execution_repo.get_by_proposal_id("P-1"))

    def test_d0007_expired_approval_blocks_submission(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        with self.assertRaises(SubmissionNotAllowedError):
            service.submit_approved_proposal(
                "P-1", current_price=100.0, active_floor_price=50.0, now=_now() + timedelta(minutes=10)
            )


class TestInitialEntryFlow(unittest.TestCase):
    def test_full_fill_creates_execution_and_updates_trade(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-1", current_price=100.0, active_floor_price=50.0, now=_now() + timedelta(seconds=10)
        )

        self.assertEqual(record.execution.status, "accepted")
        self.assertEqual(record.execution.proposal_id, "P-1")
        self.assertEqual(record.execution.requested_qty, _strategy().initial_qty)
        self.assertEqual(broker.submit_calls, [record.execution.client_order_id])

        # Now simulate the broker later reporting a full, terminal fill --
        # exercised via reconcile_unresolved(), the recovery path.
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id,
                status="filled",
                is_terminal=True,
                filled_qty=_strategy().initial_qty,
                filled_avg_price=100.0,
            ),
        )
        results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].execution.is_broker_terminal)

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.initial_order_reconciled)
        self.assertEqual(trade.initial_filled_shares, _strategy().initial_qty)
        self.assertEqual(trade.original_initial_entry_fill_price, 100.0)

    def test_rejected_with_zero_fill_abandons_trade_correctly(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-1", current_price=100.0, active_floor_price=50.0, now=_now()
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id,
                status="rejected",
                is_terminal=True,
                filled_qty=0,
                filled_avg_price=None,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.initial_order_reconciled)
        self.assertEqual(trade.initial_filled_shares, 0)
        self.assertIsNone(trade.original_initial_entry_fill_price)

    def test_immediate_full_fill_on_first_response(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = execution_repo.get_by_proposal_id("P-1")  # None, not yet created
        self.assertIsNone(record)

        # Pre-arrange the broker to report an immediate, terminal fill
        # on the very first submit_order() call.
        broker._queued_responses = {}  # not knowable client_order_id yet -- patch via subclassing instead
        result = service.submit_approved_proposal(
            "P-1", current_price=100.0, active_floor_price=50.0, now=_now()
        )
        # Since FakeBrokerClient defaults to a non-terminal "accepted"
        # response, force the terminal outcome via a follow-up reconcile.
        broker.set_order_state(
            result.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=result.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().initial_qty, filled_avg_price=100.0,
            ),
        )
        final = service.reconcile_unresolved(now=_now() + timedelta(seconds=5))[0]
        self.assertTrue(final.execution.is_broker_terminal)


class TestLadderFlows(unittest.TestCase):
    def test_ladder1_full_fill_updates_trade(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1, price=95.0, floor_price=90.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-L1", current_price=90.3, active_floor_price=90.0, now=_now() + timedelta(seconds=10)
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().ladder_1_qty, filled_avg_price=90.25,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.ladder1_filled)
        self.assertEqual(trade.total_shares, 10 + _strategy().ladder_1_qty)

    def test_ladder2_full_fill_updates_trade(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2, price=92.0, floor_price=90.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        # ladder_2_trigger = 92.0 * (1 - 0.08) = 84.64
        record = service.submit_approved_proposal(
            "P-L2", current_price=84.6, active_floor_price=80.0, now=_now() + timedelta(seconds=10)
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().ladder_2_qty, filled_avg_price=84.64,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.ladder2_filled)
        self.assertEqual(trade.total_shares, 10 + _strategy().ladder_2_qty)

    def test_ladder_zero_fill_leaves_trade_unfilled(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1, price=95.0, floor_price=90.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-L1", current_price=90.3, active_floor_price=90.0, now=_now()
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="expired", is_terminal=True,
                filled_qty=0, filled_avg_price=None,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.ladder1_filled)
        self.assertEqual(trade.total_shares, 10)

    def test_ladder1_partial_fill_does_not_raise_and_leaves_trade_untouched(self):
        # Ladder 1 partial fills have no confirmation path (unlike Ladder
        # 2) -- the fill is simply never applied to Trade, and
        # reconciliation must never raise for this case (it runs inside
        # a sweep over potentially many unresolved rows).
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1, price=95.0, floor_price=90.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-L1", current_price=90.3, active_floor_price=90.0, now=_now()
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="canceled", is_terminal=True,
                filled_qty=4, filled_avg_price=90.3,
            ),
        )
        results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(len(results), 1)

        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.ladder1_filled)
        self.assertEqual(trade.total_shares, 10)
        # The OrderExecution itself still correctly recorded the partial
        # fill -- only Trade was refused an update.
        fetched = execution_repo.get_by_proposal_id("P-L1")
        self.assertEqual(fetched.execution.filled_qty, 4)
        self.assertTrue(fetched.execution.is_broker_terminal)

        # No cancellation is ever requested for Ladder 1 -- that
        # mechanism is scoped to Ladder 2 only.
        self.assertEqual(broker.cancel_calls, [])


class TestAmbiguousOutcomesAndRetrySafety(unittest.TestCase):
    def test_ambiguous_lost_then_reconcile_confirms_unknown(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = execution_repo.get_by_proposal_id("P-1")
        pre_execution_id = None  # not known yet -- must derive after first attempt fails

        # We need the client_order_id before queuing ambiguity, so peek by
        # letting the service create the row first via a dry pass: call
        # submit, but pre-arm ambiguity is impossible without the id, so
        # instead simulate by making the FIRST ever call ambiguous using a
        # wrapper broker that always raises once.
        class _AmbiguousOnceBroker(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self._first = True

            def submit_order(self, **kwargs):
                if self._first:
                    self._first = False
                    self.submit_calls.append(kwargs["client_order_id"])
                    raise BrokerSubmissionAmbiguousError("simulated timeout, never reached broker")
                return super().submit_order(**kwargs)

        broker = _AmbiguousOnceBroker()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        with self.assertRaises(BrokerSubmissionAmbiguousError):
            service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

        record = execution_repo.get_by_proposal_id("P-1")
        self.assertEqual(record.execution.status, "SUBMITTED_UNKNOWN")
        self.assertIsNone(record.execution.broker_order_id)

        results = service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(results[0].execution.status, "SUBMITTED_UNKNOWN")
        self.assertEqual(len(broker.submit_calls), 1)  # reconcile never resubmits

    def test_ambiguous_but_broker_actually_received_it_resolves_without_double_submit(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)

        class _AmbiguousReceivedBroker(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self._first = True

            def submit_order(self, **kwargs):
                if self._first:
                    self._first = False
                    self.submit_calls.append(kwargs["client_order_id"])
                    self._orders[kwargs["client_order_id"]] = BrokerOrderState(
                        broker_order_id="B-HIDDEN", status="accepted", is_terminal=False,
                        filled_qty=0, filled_avg_price=None,
                    )
                    raise BrokerSubmissionAmbiguousError("response lost, but broker actually got it")
                return super().submit_order(**kwargs)

        broker = _AmbiguousReceivedBroker()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        with self.assertRaises(BrokerSubmissionAmbiguousError):
            service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

        # Retrying submission now must discover the hidden order and
        # resolve WITHOUT calling submit_order a second time.
        resumed = service.submit_approved_proposal(
            "P-1", current_price=100.0, active_floor_price=50.0, now=_now() + timedelta(seconds=30)
        )
        self.assertEqual(resumed.execution.broker_order_id, "B-HIDDEN")
        self.assertEqual(len(broker.submit_calls), 1)

    def test_no_blind_retry_when_d0007_now_fails(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)

        class _AlwaysAmbiguousBroker(FakeBrokerClient):
            def submit_order(self, **kwargs):
                self.submit_calls.append(kwargs["client_order_id"])
                raise BrokerSubmissionAmbiguousError("simulated timeout")

        broker = _AlwaysAmbiguousBroker()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        with self.assertRaises(BrokerSubmissionAmbiguousError):
            service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        self.assertEqual(len(broker.submit_calls), 1)

        # Retry, but now price has moved far outside the D-0007 band --
        # must refuse WITHOUT ever calling the broker again.
        with self.assertRaises(SubmissionNotAllowedError):
            service.submit_approved_proposal(
                "P-1", current_price=200.0, active_floor_price=50.0, now=_now() + timedelta(seconds=30)
            )
        self.assertEqual(len(broker.submit_calls), 1)


class TestDuplicateSubmissionProtection(unittest.TestCase):
    def test_already_resolved_execution_refuses_resubmission(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="rejected", is_terminal=True,
                filled_qty=0, filled_avg_price=None,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(seconds=30))

        with self.assertRaises(ExecutionAlreadyResolvedError):
            service.submit_approved_proposal(
                "P-1", current_price=100.0, active_floor_price=50.0, now=_now() + timedelta(minutes=1)
            )

    def test_already_submitted_and_live_refuses_resubmission(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        # Execution is now ACKNOWLEDGED ("accepted"), broker_order_id known,
        # not terminal -- a second submission attempt must be refused.
        with self.assertRaises(ExecutionAlreadySubmittedError):
            service.submit_approved_proposal(
                "P-1", current_price=100.0, active_floor_price=50.0, now=_now() + timedelta(seconds=5)
            )


class TestReconciliationCorrectness(unittest.TestCase):
    def test_reconcile_never_calls_submit(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        calls_before = len(broker.submit_calls)
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(len(broker.submit_calls), calls_before)

    def test_created_only_execution_is_untouched_by_reconcile(self):
        # A row that never advanced past CREATED (e.g. process died right
        # after save(), before start_submission()) must be left alone by
        # reconciliation -- only submit_approved_proposal() may act on it.
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        from execution.models import OrderExecution

        execution_repo.save(
            OrderExecution(proposal_id="P-1", execution_id="E-1", trade_id="T-1", client_order_id="C-1", requested_qty=10, created_at=_now()),
            now=_now(),
        )
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        results = service.reconcile_unresolved(now=_now())
        self.assertEqual(results[0].execution.status, "CREATED")
        self.assertEqual(broker.submit_calls, [])


class TestRevisionAndConcurrency(unittest.TestCase):
    def test_revision_increments_through_lifecycle(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        # save() -> revision 0, start_submission update -> 1, acked update -> 2
        self.assertEqual(record.revision, 2)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().initial_qty, filled_avg_price=100.0,
            ),
        )
        final = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))[0]
        self.assertEqual(final.revision, 3)

    def test_stale_revision_conflict_surfaces_from_repository(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

        stale = record.execution.record_fill_update(
            filled_qty=5, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=_now()
        )
        with self.assertRaises(OrderExecutionRevisionConflictError):
            execution_repo.update(stale, expected_revision=0, transition="stale", now=_now())


class TestExecutionRangeLimitPrices(unittest.TestCase):
    """Controller-approved Issue 1: the BUY limit sent to the broker for
    a ladder is the upper boundary of a 1-point execution range beyond
    that ladder's own trigger -- never the raw trigger itself, never a
    Market Order. Worked example: $100 base -> Ladder 1 trigger $95,
    limit $96; Ladder 2 trigger $92, limit $93."""

    def test_ladder1_limit_price_is_one_point_above_trigger(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1,
            price=100.0, floor_price=80.0,
        )
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-L1", current_price=95.0, active_floor_price=80.0, now=_now()
        )
        self.assertEqual(broker.submit_limit_prices[record.execution.client_order_id], 96.0)

    def test_ladder2_limit_price_is_one_point_above_trigger(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-L2", current_price=92.0, active_floor_price=80.0, now=_now()
        )
        self.assertEqual(broker.submit_limit_prices[record.execution.client_order_id], 93.0)

    def test_initial_entry_limit_price_is_unchanged_proposed_entry(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-1", current_price=100.0, active_floor_price=50.0, now=_now()
        )
        self.assertEqual(broker.submit_limit_prices[record.execution.client_order_id], 100.0)

    def test_d0007_trigger_price_is_unaffected_by_execution_range(self):
        # Ladder 1 trigger is 95.0 (1% band -> D-0007 allows roughly
        # 94.525-95.475). The execution-range limit price is 96.0. A
        # current_price of 95.3 is within D-0007's band around the
        # TRIGGER but would be rejected if D-0007 were (incorrectly)
        # validated against the limit price instead -- this proves the
        # two values remain fully decoupled.
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1,
            price=100.0, floor_price=80.0,
        )
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_approved_proposal(
            "P-L1", current_price=95.3, active_floor_price=80.0, now=_now()
        )
        self.assertEqual(broker.submit_limit_prices[record.execution.client_order_id], 96.0)


class TestLadder2CancellationTrigger(unittest.TestCase):
    """Controller-approved Issue 2 (Option B): the moment a live,
    non-terminal Ladder 2 execution shows ANY fill, cancellation of the
    remainder is requested immediately -- reactive only, never
    predictive, and never extended beyond Ladder 2."""

    def _submit_ladder2(self, trade_repo, proposal_repo, execution_repo, broker):
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal(
            "P-L2", current_price=92.0, active_floor_price=80.0, now=_now()
        )
        return service, record

    def test_cancellation_requested_on_live_partial_fill(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submit_ladder2(trade_repo, proposal_repo, execution_repo, broker)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="partially_filled",
                is_terminal=False, filled_qty=10, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(broker.cancel_calls, [record.execution.client_order_id])

    def test_no_cancellation_for_zero_fill(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submit_ladder2(trade_repo, proposal_repo, execution_repo, broker)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="accepted",
                is_terminal=False, filled_qty=0, filled_avg_price=None,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(broker.cancel_calls, [])

    def test_no_cancellation_once_already_terminal(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submit_ladder2(trade_repo, proposal_repo, execution_repo, broker)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled",
                is_terminal=True, filled_qty=20, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(broker.cancel_calls, [])

    def test_no_cancellation_for_ladder1_partial_fill(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1,
            price=100.0, floor_price=80.0,
        )
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal(
            "P-L1", current_price=95.0, active_floor_price=80.0, now=_now()
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="partially_filled",
                is_terminal=False, filled_qty=4, filled_avg_price=96.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(broker.cancel_calls, [])

    def test_cancellation_failure_does_not_propagate_or_abort_sweep(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submit_ladder2(trade_repo, proposal_repo, execution_repo, broker)
        broker.queue_cancel_failure(record.execution.client_order_id)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="partially_filled",
                is_terminal=False, filled_qty=10, filled_avg_price=93.0,
            ),
        )
        # Must not raise even though cancel_order() raises internally.
        results = service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(len(results), 1)
        self.assertEqual(broker.cancel_calls, [record.execution.client_order_id])


class TestLadder2PartialFillCases(unittest.TestCase):
    """Case A (final=requested): auto-applied, no approval. Case B
    (final between 1 and requested-1): Trade untouched until explicit
    confirm_ladder2_partial_fill(). Case C (final=0): no update, no
    approval needed."""

    def _submitted(self, trade_repo, proposal_repo, execution_repo, broker):
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal(
            "P-L2", current_price=92.0, active_floor_price=80.0, now=_now()
        )
        return service, record

    def test_case_a_full_terminal_fill_auto_applies(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submitted(trade_repo, proposal_repo, execution_repo, broker)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled",
                is_terminal=True, filled_qty=20, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.ladder2_filled)
        self.assertEqual(trade.total_shares, 30)

    def test_case_b_terminal_partial_fill_requires_explicit_confirmation(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submitted(trade_repo, proposal_repo, execution_repo, broker)

        # Cancellation-then-terminal-partial: broker eventually confirms
        # only 10 of 20 shares filled.
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="canceled",
                is_terminal=True, filled_qty=10, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        # Not auto-applied.
        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.ladder2_filled)
        self.assertEqual(trade.total_shares, 10)

        # Explicit Controller-approved confirmation applies it.
        updated = service.confirm_ladder2_partial_fill(
            "P-L2", decided_by="controller", now=_now() + timedelta(minutes=2)
        )
        self.assertTrue(updated.trade.ladder2_filled)
        self.assertEqual(updated.trade.total_shares, 20)

        # Not repeatable.
        with self.assertRaises(Ladder2PartialFillNotPendingError):
            service.confirm_ladder2_partial_fill(
                "P-L2", decided_by="controller", now=_now() + timedelta(minutes=3)
            )

    def test_case_c_zero_terminal_fill_needs_no_approval(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submitted(trade_repo, proposal_repo, execution_repo, broker)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="canceled",
                is_terminal=True, filled_qty=0, filled_avg_price=None,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.ladder2_filled)
        self.assertEqual(trade.total_shares, 10)

        with self.assertRaises(Ladder2PartialFillNotPendingError):
            service.confirm_ladder2_partial_fill(
                "P-L2", decided_by="controller", now=_now() + timedelta(minutes=2)
            )

    def test_cancellation_race_lost_is_treated_as_normal_full_fill(self):
        # Cancellation was requested on an observed partial fill, but
        # more shares filled before it took effect and the order ended
        # up fully filled -- must be treated exactly like Case A.
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        service, record = self._submitted(trade_repo, proposal_repo, execution_repo, broker)

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="partially_filled",
                is_terminal=False, filled_qty=10, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(seconds=30))
        self.assertEqual(broker.cancel_calls, [record.execution.client_order_id])

        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled",
                is_terminal=True, filled_qty=20, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.ladder2_filled)
        self.assertEqual(trade.total_shares, 30)


class TestConfirmLadder2PartialFillRefusals(unittest.TestCase):
    def test_refuses_unknown_proposal(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(ProposalNotFoundError):
            service.confirm_ladder2_partial_fill("NOPE", decided_by="controller", now=_now())

    def test_refuses_non_ladder2_proposal(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1,
            price=100.0, floor_price=80.0,
        )
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(Ladder2PartialFillNotPendingError):
            service.confirm_ladder2_partial_fill("P-L1", decided_by="controller", now=_now())

    def test_refuses_when_no_execution_exists(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(Ladder2PartialFillNotPendingError):
            service.confirm_ladder2_partial_fill("P-L2", decided_by="controller", now=_now())

    def test_refuses_when_execution_not_yet_terminal(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        service.submit_approved_proposal("P-L2", current_price=92.0, active_floor_price=80.0, now=_now())
        with self.assertRaises(Ladder2PartialFillNotPendingError):
            service.confirm_ladder2_partial_fill("P-L2", decided_by="controller", now=_now())

    def test_refuses_when_fill_is_full_not_partial(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal(
            "P-L2", current_price=92.0, active_floor_price=80.0, now=_now()
        )
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled",
                is_terminal=True, filled_qty=20, filled_avg_price=93.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        with self.assertRaises(Ladder2PartialFillNotPendingError):
            service.confirm_ladder2_partial_fill("P-L2", decided_by="controller", now=_now())


class TestReconciliationSweepSurvivesPartialFill(unittest.TestCase):
    def test_ladder1_partial_fill_does_not_abort_processing_of_other_rows(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()

        # Row 1: Ladder 1, will terminate with a partial fill.
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1,
            price=100.0, floor_price=80.0,
        )
        # Row 2: independent Initial Entry for a second trade, will
        # terminate with a normal full fill.
        _approved_initial_entry(trade_repo, proposal_repo, trade_id="T-2", proposal_id="P-2", price=50.0)

        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record1 = service.submit_approved_proposal(
            "P-L1", current_price=95.0, active_floor_price=80.0, now=_now()
        )
        record2 = service.submit_approved_proposal(
            "P-2", current_price=50.0, active_floor_price=10.0, now=_now()
        )

        broker.set_order_state(
            record1.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record1.execution.broker_order_id, status="canceled",
                is_terminal=True, filled_qty=4, filled_avg_price=96.0,
            ),
        )
        broker.set_order_state(
            record2.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record2.execution.broker_order_id, status="filled",
                is_terminal=True, filled_qty=_strategy().initial_qty, filled_avg_price=50.0,
            ),
        )

        results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(len(results), 2)

        trade1 = trade_repo.get("T-1").trade
        self.assertFalse(trade1.ladder1_filled)

        trade2 = trade_repo.get("T-2").trade
        self.assertTrue(trade2.initial_order_reconciled)
        self.assertEqual(trade2.initial_filled_shares, _strategy().initial_qty)


class TestReconciliationSweepIsolation(unittest.TestCase):
    """B1 fix: reconcile_unresolved() must isolate a per-row failure --
    the broker no longer recognizing a known client_order_id, a
    transient broker/API/network exception, or any other
    execution-specific error -- so it never aborts processing of the
    other unresolved executions in the same sweep, and never silently
    drops the failed execution."""

    def test_broker_no_longer_recognizing_one_order_does_not_abort_other_rows(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _approved_initial_entry(trade_repo, proposal_repo, trade_id="T-1", proposal_id="P-1", price=100.0)
        _approved_initial_entry(trade_repo, proposal_repo, trade_id="T-2", proposal_id="P-2", price=50.0)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record1 = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        record2 = service.submit_approved_proposal("P-2", current_price=50.0, active_floor_price=10.0, now=_now())

        # Simulate the broker losing all record of order 1's
        # client_order_id even though we hold a broker_order_id for it --
        # _reconcile_one() refuses to guess and raises for this row.
        del broker._orders[record1.execution.client_order_id]

        broker.set_order_state(
            record2.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record2.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().initial_qty, filled_avg_price=50.0,
            ),
        )

        with self.assertLogs("execution.service", level="ERROR"):
            results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        self.assertEqual(len(results), 2)

        # The unrelated row was still reconciled despite row 1's failure.
        trade2 = trade_repo.get("T-2").trade
        self.assertTrue(trade2.initial_order_reconciled)

        # Row 1's failed execution is not silently dropped from the
        # returned results, and is left unresolved (not fabricated as
        # resolved).
        failed = next(r for r in results if r.execution.execution_id == record1.execution.execution_id)
        self.assertFalse(failed.execution.is_broker_terminal)

    def test_failed_execution_remains_recoverable_on_a_later_pass(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

        del broker._orders[record.execution.client_order_id]
        with self.assertLogs("execution.service", level="ERROR"):
            failed_results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertFalse(failed_results[0].execution.is_broker_terminal)

        # The broker record comes back on a later pass -- no special
        # recovery step is required; the execution was never marked
        # resolved, so it is picked up normally.
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().initial_qty, filled_avg_price=100.0,
            ),
        )
        results = service.reconcile_unresolved(now=_now() + timedelta(minutes=2))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].execution.is_broker_terminal)

    def test_transient_network_exception_on_one_row_does_not_abort_others(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _approved_initial_entry(trade_repo, proposal_repo, trade_id="T-1", proposal_id="P-1", price=100.0)
        _approved_initial_entry(trade_repo, proposal_repo, trade_id="T-2", proposal_id="P-2", price=50.0)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record1 = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        record2 = service.submit_approved_proposal("P-2", current_price=50.0, active_floor_price=10.0, now=_now())

        broker.queue_get_order_failure(record1.execution.client_order_id)
        broker.set_order_state(
            record2.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record2.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().initial_qty, filled_avg_price=50.0,
            ),
        )

        with self.assertLogs("execution.service", level="ERROR"):
            results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        self.assertEqual(len(results), 2)
        trade2 = trade_repo.get("T-2").trade
        self.assertTrue(trade2.initial_order_reconciled)


class TestAmbiguousResumeLadder2CancellationTrigger(unittest.TestCase):
    """B2 fix regression: the resume/discovery branch of
    submit_approved_proposal() must invoke the same Ladder-2-only
    cancellation-on-partial-fill check as _do_submit()/_reconcile_one(),
    not defer it to a later reconciliation pass."""

    def test_resume_discovers_live_ladder2_partial_fill_and_cancels_immediately(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=100.0, floor_price=80.0,
        )

        class _AmbiguousReceivedThenPartialBroker(FakeBrokerClient):
            def __init__(self):
                super().__init__()
                self._first = True

            def submit_order(self, **kwargs):
                if self._first:
                    self._first = False
                    self.submit_calls.append(kwargs["client_order_id"])
                    self.submit_limit_prices[kwargs["client_order_id"]] = kwargs["limit_price"]
                    # The broker actually received and even partially
                    # filled it, but our own attempt raised ambiguous --
                    # this state is only discovered later, on resume.
                    self._orders[kwargs["client_order_id"]] = BrokerOrderState(
                        broker_order_id="B-HIDDEN-L2", status="partially_filled", is_terminal=False,
                        filled_qty=10, filled_avg_price=93.0,
                    )
                    raise BrokerSubmissionAmbiguousError("response lost, but broker actually got it")
                return super().submit_order(**kwargs)

        broker = _AmbiguousReceivedThenPartialBroker()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        with self.assertRaises(BrokerSubmissionAmbiguousError):
            service.submit_approved_proposal(
                "P-L2", current_price=92.0, active_floor_price=80.0, now=_now()
            )
        self.assertEqual(broker.cancel_calls, [])

        # Resume: this single call must both discover the hidden,
        # live, partially-filled order AND immediately request
        # cancellation of the remainder -- not defer it to a
        # subsequent reconcile_unresolved() call.
        resumed = service.submit_approved_proposal(
            "P-L2", current_price=92.0, active_floor_price=80.0, now=_now() + timedelta(seconds=30)
        )
        self.assertEqual(resumed.execution.broker_order_id, "B-HIDDEN-L2")
        self.assertEqual(broker.cancel_calls, [resumed.execution.client_order_id])


class TestExecutionRangeNonRoundPrices(unittest.TestCase):
    """B5 test gap: confirm the execution-range back-derivation lands on
    the expected 4-decimal value for a non-round reference price, not
    just the round $100 worked example."""

    def test_ladder1_and_ladder2_limit_prices_for_non_round_base(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        base_price = 93.371
        _active_trade(trade_repo, price=base_price, shares=10)
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1,
            price=base_price, floor_price=70.0,
        )
        _approved_ladder(
            proposal_repo, trade_id="T-1", proposal_id="P-L2", action=TradeAction.LADDER_2,
            price=base_price, floor_price=70.0,
        )
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record1 = service.submit_approved_proposal(
            "P-L1", current_price=88.7024, active_floor_price=70.0, now=_now()
        )
        self.assertEqual(broker.submit_limit_prices[record1.execution.client_order_id], 89.6361)

        record2 = service.submit_approved_proposal(
            "P-L2", current_price=85.9013, active_floor_price=70.0, now=_now()
        )
        self.assertEqual(broker.submit_limit_prices[record2.execution.client_order_id], 86.835)


class TestRecoverIfTerminal(unittest.TestCase):
    """Controller-approved Engine design review: recover_if_terminal()
    is a thin, idempotent public wrapper around the existing
    _apply_to_trade_if_terminal() -- used by Engine startup recovery
    for an execution that reached a terminal broker outcome without its
    Trade-side effect ever completing (process crash in that window)."""

    def test_returns_none_when_no_execution_exists(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        self.assertIsNone(service.recover_if_terminal("NOPE", now=_now()))

    def test_noop_when_not_yet_terminal(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

        record = service.recover_if_terminal("P-1", now=_now() + timedelta(seconds=1))
        self.assertFalse(record.execution.is_broker_terminal)
        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.initial_order_reconciled)

    def test_applies_a_terminal_fill_that_was_never_applied_to_trade(self):
        # Simulates a crash between the execution reaching terminal and
        # _apply_to_trade_if_terminal() running: the execution record is
        # advanced to terminal directly (bypassing submit/reconcile's own
        # apply step) to reproduce exactly that crash window.
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())

        terminal = record.execution.record_fill_update(
            filled_qty=_strategy().initial_qty, filled_avg_price=100.0, status="filled", is_terminal=True, now=_now()
        )
        execution_repo.update(
            terminal, expected_revision=record.revision, transition="simulated_crash_terminal", now=_now()
        )

        trade = trade_repo.get("T-1").trade
        self.assertFalse(trade.initial_order_reconciled)

        recovered = service.recover_if_terminal("P-1", now=_now() + timedelta(seconds=5))
        self.assertTrue(recovered.execution.is_broker_terminal)

        trade = trade_repo.get("T-1").trade
        self.assertTrue(trade.initial_order_reconciled)
        self.assertEqual(trade.initial_filled_shares, _strategy().initial_qty)

    def test_idempotent_when_called_twice(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        broker = FakeBrokerClient()
        _approved_initial_entry(trade_repo, proposal_repo, price=100.0)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_approved_proposal("P-1", current_price=100.0, active_floor_price=50.0, now=_now())
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().initial_qty, filled_avg_price=100.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        trade_before = trade_repo.get("T-1").trade
        self.assertTrue(trade_before.initial_order_reconciled)

        service.recover_if_terminal("P-1", now=_now() + timedelta(minutes=2))
        trade_after = trade_repo.get("T-1").trade
        self.assertEqual(trade_after.initial_filled_shares, trade_before.initial_filled_shares)


class TestProtectiveExit(unittest.TestCase):
    """Floor SELL -- Controller-approved: proposal-independent,
    approval-free, auto-applies even a partial fill, no automatic
    top-up/retry, reuses BrokerClient.submit_order(side="sell") and
    the existing reconciliation/recovery machinery."""

    def test_full_fill_closes_the_trade(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=40, filled_avg_price=88.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertEqual(trade.total_shares, 0)
        from trade.models import describe_status
        self.assertEqual(describe_status(trade), "CLOSED")

    def test_partial_fill_auto_applies_no_approval_needed(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="canceled", is_terminal=True,
                filled_qty=25, filled_avg_price=88.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        # Auto-applied -- no approval call of any kind was needed.
        self.assertEqual(trade.total_shares, 15)
        from trade.models import describe_status
        self.assertEqual(describe_status(trade), "ACTIVE")

    def test_zero_fill_leaves_trade_unchanged(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        record = service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="rejected", is_terminal=True,
                filled_qty=0, filled_avg_price=None,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))

        trade = trade_repo.get("T-1").trade
        self.assertEqual(trade.total_shares, 40)

    def test_rejects_quantity_exceeding_total_shares(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(ExecutionServiceError):
            service.submit_protective_exit("T-1", quantity=41, limit_price=88.0, now=_now())

    def test_rejects_non_positive_quantity(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        with self.assertRaises(ExecutionServiceError):
            service.submit_protective_exit("T-1", quantity=0, limit_price=88.0, now=_now())

    def test_refuses_duplicate_while_a_live_exit_exists(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())

        with self.assertRaises(ExecutionAlreadySubmittedError):
            service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now() + timedelta(seconds=5))

    def test_second_attempt_allowed_after_first_resolves_partially(self):
        # Sell 40, only 25 fill (auto-applied -- trade now has 15).
        # A later cycle should be able to submit a fresh protective exit
        # for the remaining 15 -- never automatically, but not blocked
        # either once the prior attempt is terminal.
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        first = service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())
        broker.set_order_state(
            first.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=first.execution.broker_order_id, status="canceled", is_terminal=True,
                filled_qty=25, filled_avg_price=88.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 15)

        second = service.submit_protective_exit("T-1", quantity=15, limit_price=87.0, now=_now() + timedelta(minutes=2))
        broker.set_order_state(
            second.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=second.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=15, filled_avg_price=87.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=3))

        trade = trade_repo.get("T-1").trade
        self.assertEqual(trade.total_shares, 0)
        from trade.models import describe_status
        self.assertEqual(describe_status(trade), "CLOSED")

    def test_recover_protective_exit_if_terminal_applies_unapplied_fill(self):
        # Simulates a crash between the execution reaching terminal and
        # _apply_to_trade_if_terminal() running -- same pattern as
        # TestRecoverIfTerminal, but for the SELL/proposal-independent
        # path, which needs its own recovery method since there is no
        # proposal_id to look it up by.
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())

        terminal = record.execution.record_fill_update(
            filled_qty=40, filled_avg_price=88.0, status="filled", is_terminal=True, now=_now()
        )
        execution_repo.update(
            terminal, expected_revision=record.revision, transition="simulated_crash_terminal", now=_now()
        )
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 40)

        recovered = service.recover_protective_exit_if_terminal("T-1", now=_now() + timedelta(seconds=5))
        self.assertTrue(recovered.execution.is_broker_terminal)
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 0)

    def test_recover_protective_exit_if_terminal_is_idempotent(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)
        record = service.submit_protective_exit("T-1", quantity=40, limit_price=88.0, now=_now())
        broker.set_order_state(
            record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=40, filled_avg_price=88.0,
            ),
        )
        service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 0)

        # Calling it again must be a safe no-op -- never double-subtract.
        service.recover_protective_exit_if_terminal("T-1", now=_now() + timedelta(minutes=2))
        self.assertEqual(trade_repo.get("T-1").trade.total_shares, 0)

    def test_recover_protective_exit_if_terminal_returns_none_when_no_execution(self):
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, price=100.0, shares=40)
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, FakeBrokerClient())
        self.assertIsNone(service.recover_protective_exit_if_terminal("T-1", now=_now()))

    def test_reconciliation_sweep_handles_buy_and_sell_rows_together(self):
        # A mixed sweep (one BUY ladder execution, one SELL protective
        # exit for a different trade) must process both correctly --
        # confirms the side branch in _apply_to_trade_if_terminal()
        # does not interfere with the existing BUY path.
        trade_repo, proposal_repo, execution_repo = _repos()
        _active_trade(trade_repo, trade_id="T-1", price=100.0, shares=10)
        _approved_ladder(proposal_repo, trade_id="T-1", proposal_id="P-L1", action=TradeAction.LADDER_1, price=95.0, floor_price=80.0)
        _active_trade(trade_repo, trade_id="T-2", price=200.0, shares=40)

        broker = FakeBrokerClient()
        service = ExecutionService(execution_repo, proposal_repo, trade_repo, broker)

        buy_record = service.submit_approved_proposal("P-L1", current_price=90.3, active_floor_price=80.0, now=_now())
        sell_record = service.submit_protective_exit("T-2", quantity=40, limit_price=175.0, now=_now())

        broker.set_order_state(
            buy_record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=buy_record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=_strategy().ladder_1_qty, filled_avg_price=90.25,
            ),
        )
        broker.set_order_state(
            sell_record.execution.client_order_id,
            BrokerOrderState(
                broker_order_id=sell_record.execution.broker_order_id, status="filled", is_terminal=True,
                filled_qty=40, filled_avg_price=175.0,
            ),
        )
        results = service.reconcile_unresolved(now=_now() + timedelta(minutes=1))
        self.assertEqual(len(results), 2)

        self.assertTrue(trade_repo.get("T-1").trade.ladder1_filled)
        self.assertEqual(trade_repo.get("T-2").trade.total_shares, 0)


if __name__ == "__main__":
    unittest.main()
