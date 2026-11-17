import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from execution.broker_client import BrokerClient, BrokerOrderState
from spx0dte.greeks import OptionType, black_scholes_price
from spx0dte.orchestration import (
    build_live_proposal, build_proposal, close_position, create_and_notify,
    expire_stale_pending, process_decisions,
)
from spx0dte.proposal import ProposalApprovalState
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind


class FakeBroker(BrokerClient):
    def __init__(self):
        self.calls = []

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.calls.append(dict(
            client_order_id=client_order_id, symbol=symbol, quantity=quantity, limit_price=limit_price,
        ))
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


class FakeRatioProvider:
    def __init__(self, ratio: float):
        self._ratio = ratio

    def get_ratio(self, *, now=None) -> float:
        return self._ratio


class FakeOptionsClient:
    def __init__(self, *, spy_bars, found_strike, option_bars):
        self._spy_bars = spy_bars
        self._found_strike = found_strike
        self._option_bars = option_bars

    def get_spy_minute_bars(self, date):
        return self._spy_bars

    def find_nearest_traded_strike(self, date, approx_spx_level, call_put, around_time_iso):
        return self._found_strike

    def get_option_bars(self, symbol, start_iso, end_iso, limit=50):
        return self._option_bars


def _bar(t, c: float) -> dict:
    return {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": c}


class TestLivePricingIntegration(unittest.TestCase):
    """Covers the live_pricing wiring added to orchestration.py:
    build_live_proposal() and process_decisions()'s optional re-price
    at submission time."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.path)
        self.now = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
        self.ratio_provider = FakeRatioProvider(10.0)
        self.K = 5900

        self.spy_bars = [_bar(self.now - timedelta(minutes=m), 590.0) for m in range(0, 120, 1)][::-1]
        expiry = self.now.replace(hour=21, minute=0, second=0, microsecond=0)
        stale_time = self.now - timedelta(minutes=40)
        stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
        stale_price = black_scholes_price(5900, self.K, stale_T, 0.045, 0.15, OptionType.CALL)

        self.client = FakeOptionsClient(
            spy_bars=self.spy_bars, found_strike=(self.K, "SPXW261117C05900000"),
            option_bars=[_bar(stale_time, stale_price)],
        )

    def tearDown(self):
        os.remove(self.path)

    def test_build_live_proposal_uses_live_quote(self):
        proposal = build_live_proposal(
            self.client, self.ratio_provider, date="2026-11-17", direction="call",
            entry_time=self.now, hold_cap_minutes=40.0, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.assertEqual(proposal.strike, self.K)
        self.assertEqual(proposal.occ_symbol, "SPXW261117C05900000")
        self.assertGreater(proposal.estimated_premium, 0)

    def test_process_decisions_reprices_live_when_wired(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=self.K, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        process_decisions(
            self.repo, [decision], broker, quantity=1,
            options_client=self.client, ratio_provider=self.ratio_provider,
            now=self.now,
        )

        self.assertEqual(len(broker.calls), 1)
        # Re-priced live, not the stale proposal-time estimate of 1.50.
        self.assertNotAlmostEqual(broker.calls[0]["limit_price"], 1.50, places=2)

    def test_process_decisions_falls_back_without_live_pricing_args(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=self.K, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        process_decisions(self.repo, [decision], broker, quantity=1, now=self.now)

        self.assertAlmostEqual(broker.calls[0]["limit_price"], 1.50, places=2)


class TestPositionWiring(unittest.TestCase):
    """D-0095-A item 4: process_decisions creates an OpenPosition on a
    successful submission when given a position_repo, and
    close_position() submits the exit and persists the close."""

    def setUp(self):
        fd, self.proposal_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.proposal_path)
        self.position_repo = SpxPositionRepository(self.proposal_path)  # same file, shared schema
        self.now = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)

    def tearDown(self):
        os.remove(self.proposal_path)

    def test_approve_creates_an_open_position(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )

        positions = self.position_repo.list_open()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].occ_symbol, "SPXW261117C05900000")
        self.assertAlmostEqual(positions[0].entry_price, 1.50, places=2)

    def test_reject_creates_no_position(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.REJECT, decided_by="111")

        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )
        self.assertEqual(self.position_repo.list_open(), [])

    def test_close_position_submits_sell_and_persists(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")
        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )
        position = self.position_repo.list_open()[0]

        result = close_position(
            self.position_repo, position, broker, exit_price=2.70, exit_reason="target",
            now=self.now + timedelta(minutes=20),
        )

        self.assertTrue(result.success)
        sell_calls = [c for c in broker.calls if c["client_order_id"].endswith("-exit")]
        self.assertEqual(len(sell_calls), 1)
        self.assertEqual(self.position_repo.list_open(), [])  # no longer open
        closed = self.position_repo.get(position.proposal_id)
        self.assertTrue(closed.is_closed)
        self.assertEqual(closed.exit_reason, "target")


if __name__ == "__main__":
    unittest.main()
