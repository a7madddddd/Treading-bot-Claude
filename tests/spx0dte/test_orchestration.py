import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from execution.broker_client import BrokerClient, BrokerClientError, BrokerOrderState
from spx0dte.greeks import OptionType, black_scholes_price
from spx0dte.orchestration import (
    build_live_proposal, build_proposal, close_position, create_and_notify,
    expire_stale_pending, process_decisions,
)
from spx0dte.proposal import ProposalApprovalState
from spx0dte.sizing import ConcurrentPositionTracker
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind


class FakeBroker(BrokerClient):
    def __init__(self, filled_avg_price=None, fail_submit=False, polled_fill_price=None):
        self.calls = []
        # D-0095-A, 2026-11-19: None reproduces the original "no fill price yet"
        # behavior every existing test here relies on; a real test of the
        # real-fill-over-estimate fix sets this to a specific value.
        self.filled_avg_price = filled_avg_price
        # 2026-11-22: simulates the broker rejecting/failing the submission
        # (e.g. a transient BrokerClientError) -- for close_position's own
        # "don't mark closed on a failed submit" fix.
        self.fail_submit = fail_submit
        # 2026-11-22: simulates _resolve_fill_price's follow-up
        # get_order_by_client_order_id call finding a real fill that the
        # original synchronous submit_order response did not carry yet.
        self.polled_fill_price = polled_fill_price

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.calls.append(dict(
            client_order_id=client_order_id, symbol=symbol, quantity=quantity, limit_price=limit_price,
        ))
        if self.fail_submit:
            raise BrokerClientError("simulated broker rejection")
        filled = self.filled_avg_price
        return BrokerOrderState(
            broker_order_id=f"B-{client_order_id}", status="accepted" if filled is None else "filled",
            is_terminal=filled is not None, filled_qty=(quantity if filled is not None else 0),
            filled_avg_price=filled,
        )

    def get_order_by_client_order_id(self, client_order_id):
        # 2026-11-22: _resolve_fill_price's one follow-up check calls this
        # when the submission response had no fill yet. None here means
        # "nothing newer either", preserving every existing test's fallback
        # expectation unchanged; a dedicated test sets `polled_fill_price`
        # to exercise the follow-up poll itself.
        if self.polled_fill_price is None:
            return None
        return BrokerOrderState(
            broker_order_id=f"B-{client_order_id}", status="filled", is_terminal=True,
            filled_qty=1, filled_avg_price=self.polled_fill_price,
        )

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

    def test_approve_uses_real_fill_price_when_broker_reports_one(self):
        # D-0095-A, 2026-11-19: the Controller's real finding -- every live
        # trade opened already showing a loss, traced to recording the
        # submitted estimate instead of the broker's own confirmed fill.
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker(filled_avg_price=1.32)  # real fill cheaper than the 1.50 estimate
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )

        positions = self.position_repo.list_open()
        self.assertAlmostEqual(positions[0].entry_price, 1.32, places=2)  # real fill, not the 1.50 estimate

    def test_approve_falls_back_to_submitted_price_when_no_fill_reported(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker(filled_avg_price=None)  # order accepted, no fill price back yet
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )

        positions = self.position_repo.list_open()
        self.assertAlmostEqual(positions[0].entry_price, 1.50, places=2)  # unchanged fallback behavior

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

    def test_close_position_uses_real_fill_price_when_broker_reports_one(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()  # no fill reported at entry -- recorded entry stays the 1.50 estimate
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")
        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )
        position = self.position_repo.list_open()[0]

        broker.filled_avg_price = 2.95  # the exit's real fill, different from the 2.70 submitted estimate
        close_position(
            self.position_repo, position, broker, exit_price=2.70, exit_reason="target",
            now=self.now + timedelta(minutes=20),
        )

        closed = self.position_repo.get(position.proposal_id)
        self.assertAlmostEqual(closed.exit_price, 2.95, places=2)  # real fill, not the 2.70 estimate

    def test_close_position_falls_back_to_submitted_price_when_no_fill_reported(self):
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

        close_position(
            self.position_repo, position, broker, exit_price=2.70, exit_reason="target",
            now=self.now + timedelta(minutes=20),
        )

        closed = self.position_repo.get(position.proposal_id)
        self.assertAlmostEqual(closed.exit_price, 2.70, places=2)  # unchanged fallback behavior

    def test_close_position_does_not_mark_closed_on_a_failed_submit(self):
        # 2026-11-22 fix: a broker-rejected SELL must not be recorded as a
        # successful close -- the position must stay open/untouched so the
        # caller keeps monitoring it and can retry.
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

        broker.fail_submit = True
        result = close_position(
            self.position_repo, position, broker, exit_price=2.70, exit_reason="target",
            now=self.now + timedelta(minutes=20),
        )

        self.assertFalse(result.success)
        self.assertEqual(len(self.position_repo.list_open()), 1)  # still open
        not_closed = self.position_repo.get(position.proposal_id)
        self.assertFalse(not_closed.is_closed)

    def test_close_position_uses_a_fill_price_found_by_the_follow_up_poll(self):
        # 2026-11-22 fix: the original synchronous submit response normally
        # carries no fill yet -- _resolve_fill_price's one follow-up
        # get_order_by_client_order_id check is what actually finds the
        # real fill in that (typical) case.
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker()  # submit response itself carries no fill (the normal case)
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")
        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )
        position = self.position_repo.list_open()[0]

        broker.polled_fill_price = 3.05  # found only by the follow-up poll, not the submit response
        close_position(
            self.position_repo, position, broker, exit_price=2.70, exit_reason="target",
            now=self.now + timedelta(minutes=20),
        )

        closed = self.position_repo.get(position.proposal_id)
        self.assertAlmostEqual(closed.exit_price, 3.05, places=2)

    def test_approve_uses_a_fill_price_found_by_the_follow_up_poll(self):
        proposal = build_proposal(
            date="2026-11-17", direction="call", strike=5900, occ_symbol="SPXW261117C05900000",
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=1.50, vix=18.0,
            filters_passed="VIX OK", now=self.now,
        )
        self.repo.save(proposal)
        broker = FakeBroker(polled_fill_price=1.41)  # not in the submit response, only the follow-up poll
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        process_decisions(
            self.repo, [decision], broker, quantity=1, position_repo=self.position_repo, now=self.now,
        )

        positions = self.position_repo.list_open()
        self.assertAlmostEqual(positions[0].entry_price, 1.41, places=2)


class TestProcessDecisionsSafetyGates(unittest.TestCase):
    """D-0095-A, 2026-11-19 -- the real incident: two proposals for the
    SAME option contract, approved together, re-priced identically,
    closed identically. Found live during the Controller's full-cycle
    dry run. These gates run at approval/submission time, as defense
    in depth alongside entry_filters/ConcurrentPositionTracker's
    creation-time check (which uses each candidate's PLANNED exit and
    can be stale by the time a human actually approves)."""

    def setUp(self):
        fd, self.proposal_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.proposal_path)
        self.position_repo = SpxPositionRepository(self.proposal_path)
        self.now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)

    def tearDown(self):
        os.remove(self.proposal_path)

    def _approve(self, *, strike, occ_symbol):
        proposal = build_proposal(
            date="2026-11-19", direction="put", strike=strike, occ_symbol=occ_symbol,
            entry_time=self.now, hold_cap_minutes=40.0, estimated_premium=14.0, vix=None,
            filters_passed="OK", now=self.now,
        )
        self.repo.save(proposal)
        return proposal

    def test_duplicate_same_contract_is_not_submitted_twice(self):
        broker = FakeBroker()
        p1 = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        p2 = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        decisions = [
            ControllerDecision(proposal_id=p1.proposal_id, kind=DecisionKind.APPROVE, decided_by="111"),
            ControllerDecision(proposal_id=p2.proposal_id, kind=DecisionKind.APPROVE, decided_by="111"),
        ]
        notes = []

        results = process_decisions(
            self.repo, decisions, broker, quantity=1, position_repo=self.position_repo,
            now=self.now, notes=notes,
        )

        self.assertEqual(len(results), 1)  # only the FIRST actually submitted
        self.assertEqual(len(self.position_repo.list_open()), 1)
        self.assertTrue(any("already open" in n for n in notes))

    def test_concurrency_cap_rechecked_at_submission_time(self):
        from spx0dte.sizing import MAX_CONCURRENT_POSITIONS
        broker = FakeBroker()
        proposals = [
            self._approve(strike=7000 + i * 100, occ_symbol=f"SPXW261119P0{7000+i*100}00")
            for i in range(MAX_CONCURRENT_POSITIONS + 1)
        ]
        decisions = [
            ControllerDecision(proposal_id=p.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")
            for p in proposals
        ]
        notes = []

        results = process_decisions(
            self.repo, decisions, broker, quantity=1, position_repo=self.position_repo,
            now=self.now, notes=notes,
        )

        self.assertEqual(len(results), MAX_CONCURRENT_POSITIONS)  # the cap, not one more
        self.assertEqual(len(self.position_repo.list_open()), MAX_CONCURRENT_POSITIONS)
        self.assertTrue(any("already open" in n and "cap" in n for n in notes))

    def test_reject_releases_the_concurrency_tracker_slot(self):
        # 2026-11-22 fix: a REJECT must free the slot reserved at proposal-
        # creation time, not wait out the proposal's planned hold window.
        broker = FakeBroker()
        p = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        far_exit = self.now + timedelta(minutes=40)
        tracker = ConcurrentPositionTracker(max_concurrent=1)
        tracker.record_open(self.now, far_exit, proposal_id=p.proposal_id)
        self.assertFalse(tracker.can_open(self.now))

        decisions = [ControllerDecision(proposal_id=p.proposal_id, kind=DecisionKind.REJECT, decided_by="111")]
        process_decisions(
            self.repo, decisions, broker, quantity=1, now=self.now, position_tracker=tracker,
        )

        self.assertTrue(tracker.can_open(self.now))  # freed immediately, not at far_exit

    def test_submission_time_skip_releases_the_concurrency_tracker_slot(self):
        # The same-occ_symbol skip (defense in depth) must also release.
        broker = FakeBroker()
        p1 = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        p2 = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        far_exit = self.now + timedelta(minutes=40)
        tracker = ConcurrentPositionTracker(max_concurrent=2)
        tracker.record_open(self.now, far_exit, proposal_id=p1.proposal_id)
        tracker.record_open(self.now, far_exit, proposal_id=p2.proposal_id)

        decisions = [
            ControllerDecision(proposal_id=p1.proposal_id, kind=DecisionKind.APPROVE, decided_by="111"),
            ControllerDecision(proposal_id=p2.proposal_id, kind=DecisionKind.APPROVE, decided_by="111"),
        ]
        process_decisions(
            self.repo, decisions, broker, quantity=1, position_repo=self.position_repo,
            now=self.now, position_tracker=tracker,
        )

        # p1 submitted (slot stays reserved by its real open position); p2's
        # reservation must have been released since it was never submitted.
        self.assertEqual(len(self.position_repo.list_open()), 1)
        tracker.release(p1.proposal_id)  # drop the one real submission's own reservation
        self.assertTrue(tracker.can_open(self.now))  # nothing left reserved

    def test_expire_stale_pending_releases_the_concurrency_tracker_slot(self):
        p = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        far_exit = self.now + timedelta(minutes=40)
        tracker = ConcurrentPositionTracker(max_concurrent=1)
        tracker.record_open(self.now, far_exit, proposal_id=p.proposal_id)
        self.assertFalse(tracker.can_open(self.now))

        count = expire_stale_pending(
            self.repo, older_than=self.now + timedelta(minutes=1), position_tracker=tracker,
        )

        self.assertEqual(count, 1)
        self.assertTrue(tracker.can_open(self.now))

    def test_gates_are_skipped_entirely_without_position_repo(self):
        # Backward compatible: omitting position_repo (e.g. an older
        # caller, or a test) must behave exactly as before these gates existed.
        broker = FakeBroker()
        p1 = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        p2 = self._approve(strike=7200, occ_symbol="SPXW261119P07200000")
        decisions = [
            ControllerDecision(proposal_id=p1.proposal_id, kind=DecisionKind.APPROVE, decided_by="111"),
            ControllerDecision(proposal_id=p2.proposal_id, kind=DecisionKind.APPROVE, decided_by="111"),
        ]

        results = process_decisions(self.repo, decisions, broker, quantity=1, now=self.now)

        self.assertEqual(len(results), 2)  # no position_repo -- no gate, both submitted


if __name__ == "__main__":
    unittest.main()
