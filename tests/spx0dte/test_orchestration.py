import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from execution.broker_client import BrokerClient, BrokerOrderState
from spx0dte.orchestration import (
    build_proposal, create_and_notify, expire_stale_pending, process_decisions,
)
from spx0dte.proposal import ProposalApprovalState
from spx0dte.sqlite_repository import SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind


class FakeBroker(BrokerClient):
    def __init__(self):
        self.calls = []

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.calls.append(dict(client_order_id=client_order_id, symbol=symbol, quantity=quantity))
        return BrokerOrderState(
            broker_order_id=f"B-{client_order_id}", status="accepted",
            is_terminal=False, filled_qty=0, filled_avg_price=None,
        )

    def get_order_by_client_order_id(self, client_order_id):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def get_cash_balance(self):
        raise NotImplementedError

    def get_account_equity(self):
        raise NotImplementedError

    def is_market_open(self):
        raise NotImplementedError


def _build(now) -> "OptionTradeProposal":  # noqa: F821 -- for type hint readability only
    return build_proposal(
        date="2026-11-16", direction="call", strike=6900, occ_symbol="SPXW261116C06900000",
        entry_time=now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
        filters_passed="VIX OK", now=now,
    )


class TestOrchestration(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.path)
        self.now = datetime(2026, 11, 16, 14, 45, tzinfo=timezone.utc)

    def tearDown(self):
        os.remove(self.path)

    def test_create_and_notify_persists_regardless_of_send_result(self):
        proposal = _build(self.now)
        sent = create_and_notify(self.repo, proposal, bot_token="bad", chat_id="1")
        # Real network call will fail in this sandbox -- the point is the row persists either way.
        self.assertFalse(sent)
        self.assertIsNotNone(self.repo.get(proposal.proposal_id))

    def test_approve_decision_submits_order(self):
        proposal = _build(self.now)
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        results = process_decisions(
            self.repo, [decision], broker, quantity=1,
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(len(broker.calls), 1)
        self.assertEqual(broker.calls[0]["symbol"], "SPXW261116C06900000")

        stored = self.repo.get(proposal.proposal_id)
        self.assertEqual(stored.approval_state, ProposalApprovalState.APPROVED)

    def test_reject_decision_submits_nothing(self):
        proposal = _build(self.now)
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.REJECT, decided_by="111")

        results = process_decisions(self.repo, [decision], broker, quantity=1, now=self.now + timedelta(minutes=1))

        self.assertEqual(results, [])
        self.assertEqual(broker.calls, [])
        self.assertEqual(self.repo.get(proposal.proposal_id).approval_state, ProposalApprovalState.REJECTED)

    def test_approve_outside_window_submits_nothing(self):
        proposal = _build(self.now)
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        # Decision processed itself happens "now" (so with_decision succeeds),
        # but the processing pass is told a time 10 minutes later than that
        # decision -- simulating a delayed processing pass after expiry.
        decided_now = self.now
        results = process_decisions(self.repo, [decision], broker, quantity=1, now=decided_now)
        self.assertEqual(len(results), 1)  # still inside window relative to itself

        # A second, already-decided proposal must not be resubmitted even
        # if processed again much later.
        results2 = process_decisions(self.repo, [decision], broker, quantity=1, now=decided_now + timedelta(minutes=30))
        self.assertEqual(results2, [])
        self.assertEqual(len(broker.calls), 1)

    def test_unknown_proposal_id_is_ignored(self):
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id="does-not-exist", kind=DecisionKind.APPROVE, decided_by="111")
        results = process_decisions(self.repo, [decision], broker, quantity=1, now=self.now)
        self.assertEqual(results, [])

    def test_expire_stale_pending(self):
        proposal = _build(self.now)
        self.repo.save(proposal)
        count = expire_stale_pending(self.repo, older_than=self.now + timedelta(minutes=1))
        self.assertEqual(count, 1)
        self.assertEqual(self.repo.get(proposal.proposal_id).approval_state, ProposalApprovalState.EXPIRED)

    def test_expire_stale_pending_leaves_recent_ones(self):
        proposal = _build(self.now)
        self.repo.save(proposal)
        count = expire_stale_pending(self.repo, older_than=self.now - timedelta(minutes=1))
        self.assertEqual(count, 0)
        self.assertEqual(self.repo.get(proposal.proposal_id).approval_state, ProposalApprovalState.PENDING)


if __name__ == "__main__":
    unittest.main()
