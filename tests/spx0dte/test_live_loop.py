import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from execution.broker_client import BrokerClient, BrokerOrderState
from spx0dte.greeks import OptionType, black_scholes_price
from spx0dte.live_loop import LiveSessionState, run_one_tick
from spx0dte.proposal import ProposalApprovalState
from spx0dte.sqlite_repository import SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind


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


class FakeBroker(BrokerClient):
    def __init__(self):
        self.calls = []

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.calls.append(dict(client_order_id=client_order_id, symbol=symbol))
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


def _bar(t: datetime, c: float) -> dict:
    return {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": c}


def _session_start() -> datetime:
    return datetime(2026, 11, 17, 14, 30, tzinfo=timezone.utc)  # 9:30am ET


def _build_breakout_spy_bars(session_start: datetime) -> list:
    """15 flat opening-range bars, then a confirmed call breakout."""
    bars = [_bar(session_start + timedelta(minutes=i), 590.0) for i in range(16)]
    bars.append(_bar(session_start + timedelta(minutes=16), 592.0))  # breaks the flat ORB
    return bars


class TestRunOneTick(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.path)
        self.ratio_provider = FakeRatioProvider(10.0)
        self.broker = FakeBroker()
        self.session_start = _session_start()
        self.now = self.session_start + timedelta(minutes=17)

        spy_bars = _build_breakout_spy_bars(self.session_start)
        expiry = self.now.replace(hour=21, minute=0, second=0, microsecond=0)
        stale_time = self.now - timedelta(minutes=40)
        stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
        stale_price = black_scholes_price(5900, 5920, stale_T, 0.045, 0.15, OptionType.CALL)

        self.client = FakeOptionsClient(
            spy_bars=spy_bars, found_strike=(5920, "SPXW261117C05920000"),
            option_bars=[_bar(stale_time, stale_price)],
        )
        self.state = LiveSessionState()

    def tearDown(self):
        os.remove(self.path)

    def test_first_tick_locks_in_day_pace_and_creates_a_proposal(self):
        result = run_one_tick(
            now=self.now, options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        self.assertIsNotNone(result.dynamic_cap)
        self.assertEqual(result.new_opportunities_seen, 1)
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(result.proposals_rejected_by_filter, 0)

        pending = self.repo.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].occ_symbol, "SPXW261117C05920000")

    def test_same_opportunity_not_processed_twice(self):
        run_one_tick(
            now=self.now, options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        result2 = run_one_tick(
            now=self.now + timedelta(minutes=1), options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        self.assertEqual(result2.new_opportunities_seen, 0)
        self.assertEqual(result2.proposals_created, 0)
        self.assertEqual(len(self.repo.list_pending()), 1)

    def test_concurrency_cap_rejects_a_second_slot(self):
        self.state.position_tracker.max_concurrent = 1
        # Pre-fill the one slot with an unrelated open position spanning this entry time.
        self.state.position_tracker.record_open(
            self.now - timedelta(minutes=5), self.now + timedelta(hours=1),
        )
        result = run_one_tick(
            now=self.now, options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        self.assertEqual(result.proposals_created, 0)
        self.assertEqual(result.proposals_rejected_by_concurrency, 1)

    def test_approve_decision_submits_order_in_same_tick(self):
        run_one_tick(
            now=self.now, options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        proposal = self.repo.list_pending()[0]
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        result2 = run_one_tick(
            now=self.now + timedelta(minutes=1), options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[decision],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        self.assertEqual(result2.orders_submitted, 1)
        self.assertEqual(len(self.broker.calls), 1)
        stored = self.repo.get(proposal.proposal_id)
        self.assertEqual(stored.approval_state, ProposalApprovalState.APPROVED)

    def test_no_bars_yet_does_not_crash(self):
        empty_client = FakeOptionsClient(spy_bars=[], found_strike=None, option_bars=[])
        result = run_one_tick(
            now=self.now, options_client=empty_client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=LiveSessionState(),
        )
        self.assertEqual(result.proposals_created, 0)
        self.assertIsNone(result.dynamic_cap)

    def test_new_day_resets_processed_keys(self):
        run_one_tick(
            now=self.now, options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        self.assertEqual(len(self.state.processed_entry_keys), 1)

        next_day_start = self.session_start + timedelta(days=1)
        next_day_bars = _build_breakout_spy_bars(next_day_start)
        next_day_client = FakeOptionsClient(
            spy_bars=next_day_bars, found_strike=(5920, "SPXW261118C05920000"),
            option_bars=self.client._option_bars,
        )
        next_now = next_day_start + timedelta(minutes=17)
        result = run_one_tick(
            now=next_now, options_client=next_day_client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, pending_decisions=[],
            bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        self.assertEqual(result.new_opportunities_seen, 1)  # not blocked by yesterday's key


if __name__ == "__main__":
    unittest.main()
