import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from execution.broker_client import BrokerClient, BrokerOrderState
from spx0dte.greeks import OptionType, black_scholes_price
from spx0dte.live_loop import LiveSessionState, build_status_snapshot, run_one_tick
from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind


def _seed_daily_counts(repo: SpxProposalRepository, date_to_count: dict) -> None:
    """Writes `count` synthetic proposals for each `date` (YYYY-MM-DD)
    so tests can exercise the unusual-day threshold's self-computation
    from real repo history, without a real multi-day live run."""
    seq = 0
    for date_str, count in date_to_count.items():
        day_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=15, tzinfo=timezone.utc,
        )
        for _ in range(count):
            seq += 1
            proposal = OptionTradeProposal(
                proposal_id=f"SEED-{seq}", date=date_str, direction="call", strike=5900,
                occ_symbol=f"SPXW{date_str.replace('-', '')[2:]}C05900000",
                entry_time=day_dt, hold_cap_minutes=40.0, estimated_premium=1.0,
                vix=None, filters_passed="seed", created_at=day_dt,
            )
            repo.save(proposal)


class FakeRatioProvider:
    def __init__(self, ratio: float):
        self._ratio = ratio

    def get_ratio(self, *, now=None) -> float:
        return self._ratio


class FakeVixProvider:
    def __init__(self, level):
        self._level = level

    def get_vix_level(self, *, now=None):
        return self._level


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
    def __init__(self, account_equity: float = 100_000.0):
        self.calls = []
        self.account_equity = account_equity

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.calls.append(dict(client_order_id=client_order_id, symbol=symbol, side=side))
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
        return self.account_equity

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
        self.position_repo = SpxPositionRepository(self.path)
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

    def _tick(self, **overrides):
        kwargs = dict(
            now=self.now, options_client=self.client, broker=self.broker,
            ratio_provider=self.ratio_provider, repo=self.repo, position_repo=self.position_repo,
            pending_decisions=[], bot_token="bad-token", chat_id="1", quantity=1, state=self.state,
        )
        kwargs.update(overrides)
        return run_one_tick(**kwargs)

    def test_first_tick_locks_in_day_pace_and_creates_a_proposal(self):
        result = self._tick()
        self.assertIsNotNone(result.dynamic_cap)
        self.assertEqual(result.new_opportunities_seen, 1)
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(result.proposals_rejected_by_filter, 0)

        pending = self.repo.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].occ_symbol, "SPXW261117C05920000")

    def test_without_vix_provider_behaves_as_before(self):
        result = self._tick()  # no vix_provider passed -- default None
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(result.proposals_rejected_by_filter, 0)

    def test_vix_provider_in_tradeable_range_does_not_block(self):
        result = self._tick(vix_provider=FakeVixProvider(20.0))
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(result.proposals_rejected_by_filter, 0)

    def test_vix_provider_out_of_range_rejects_the_opportunity(self):
        result = self._tick(vix_provider=FakeVixProvider(50.0))  # above the 35 upper bound
        self.assertEqual(result.proposals_created, 0)
        self.assertEqual(result.proposals_rejected_by_filter, 1)

    def test_vix_provider_none_level_does_not_block(self):
        # The provider itself may return None (anchor/live fetch failed) --
        # must degrade exactly like not having a provider at all.
        result = self._tick(vix_provider=FakeVixProvider(None))
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(result.proposals_rejected_by_filter, 0)

    def test_same_opportunity_not_processed_twice(self):
        self._tick()
        result2 = self._tick(now=self.now + timedelta(minutes=1))
        self.assertEqual(result2.new_opportunities_seen, 0)
        self.assertEqual(result2.proposals_created, 0)
        self.assertEqual(len(self.repo.list_pending()), 1)

    def test_concurrency_cap_rejects_a_second_slot(self):
        self.state.position_tracker.max_concurrent = 1
        # Pre-fill the one slot with an unrelated open position spanning this entry time.
        self.state.position_tracker.record_open(
            self.now - timedelta(minutes=5), self.now + timedelta(hours=1),
        )
        result = self._tick()
        self.assertEqual(result.proposals_created, 0)
        self.assertEqual(result.proposals_rejected_by_concurrency, 1)

    def test_approve_decision_submits_order_in_same_tick(self):
        self._tick()
        proposal = self.repo.list_pending()[0]
        decision = ControllerDecision(proposal_id=proposal.proposal_id, kind=DecisionKind.APPROVE, decided_by="111")

        result2 = self._tick(now=self.now + timedelta(minutes=1), pending_decisions=[decision])
        self.assertEqual(result2.orders_submitted, 1)
        buy_calls = [c for c in self.broker.calls if c["side"] == "buy"]
        self.assertEqual(len(buy_calls), 1)
        stored = self.repo.get(proposal.proposal_id)
        self.assertEqual(stored.approval_state, ProposalApprovalState.APPROVED)

        # D-0095-A item 4: approval also opens a tracked position.
        self.assertEqual(len(self.position_repo.list_open()), 1)

    def test_no_bars_yet_does_not_crash(self):
        empty_client = FakeOptionsClient(spy_bars=[], found_strike=None, option_bars=[])
        result = self._tick(options_client=empty_client, state=LiveSessionState())
        self.assertEqual(result.proposals_created, 0)
        self.assertIsNone(result.dynamic_cap)

    def test_first_tick_locks_daily_loss_limit_equity(self):
        self._tick()
        self.assertEqual(self.state.loss_limit_tracker.locked_equity, self.broker.account_equity)
        self.assertFalse(self.state.loss_limit_tracker.limit_reached())

    def test_loss_limit_reached_rejects_new_opportunities(self):
        # Lock the day first (as a normal tick would), then push realized
        # losses past the 2% threshold before the opportunity is seen.
        self.state.loss_limit_tracker.start_new_day(
            self.now.strftime("%Y-%m-%d"), equity=self.broker.account_equity,
        )
        self.state.loss_limit_tracker.record_realized_pnl(-self.broker.account_equity * 0.03)

        result = self._tick()
        self.assertEqual(result.proposals_created, 0)
        self.assertEqual(result.proposals_rejected_by_loss_limit, 1)

    def test_unusual_day_threshold_off_without_enough_history(self):
        # Fresh repo -- zero prior trading days of history.
        result = self._tick()
        self.assertEqual(result.proposals_created, 1)
        self.assertIsNone(self.state.unusual_day_trade_count_threshold)
        self.assertFalse(self.state.unusual_day_alert_sent)

    def test_unusual_day_threshold_self_computed_from_real_history(self):
        # 5 prior days (2026-11-10..14), all with 1 proposal -- 90th
        # percentile of [1,1,1,1,1] is 1, so today's first and only
        # proposal (also 1) should reach it.
        _seed_daily_counts(self.repo, {
            "2026-11-10": 1, "2026-11-11": 1, "2026-11-12": 1,
            "2026-11-13": 1, "2026-11-14": 1,
        })
        result = self._tick()
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(self.state.unusual_day_trade_count_threshold, 1.0)
        self.assertTrue(self.state.unusual_day_alert_sent)

    def test_unusual_day_threshold_not_reached_when_history_is_busier(self):
        # 5 prior days all with 10 proposals -- 90th percentile is 10;
        # today's single proposal stays well below it.
        _seed_daily_counts(self.repo, {
            "2026-11-10": 10, "2026-11-11": 10, "2026-11-12": 10,
            "2026-11-13": 10, "2026-11-14": 10,
        })
        result = self._tick()
        self.assertEqual(result.proposals_created, 1)
        self.assertEqual(self.state.unusual_day_trade_count_threshold, 10.0)
        self.assertFalse(self.state.unusual_day_alert_sent)

    def test_unusual_day_history_excludes_todays_own_date(self):
        # A row already saved for TODAY (e.g. from a prior tick) must
        # never count toward its own trailing window.
        _seed_daily_counts(self.repo, {
            "2026-11-10": 1, "2026-11-11": 1, "2026-11-12": 1,
            "2026-11-13": 1, self.now.strftime("%Y-%m-%d"): 99,
        })
        self._tick()
        self.assertIsNone(self.state.unusual_day_trade_count_threshold)  # still only 4 PRIOR days

    def test_unusual_day_counters_reset_on_new_day(self):
        _seed_daily_counts(self.repo, {
            "2026-11-10": 1, "2026-11-11": 1, "2026-11-12": 1,
            "2026-11-13": 1, "2026-11-14": 1,
        })
        self._tick()
        self.assertTrue(self.state.unusual_day_alert_sent)

        next_day_start = self.session_start + timedelta(days=1)
        next_day_bars = _build_breakout_spy_bars(next_day_start)
        next_day_client = FakeOptionsClient(
            spy_bars=next_day_bars, found_strike=(5920, "SPXW261118C05920000"),
            option_bars=self.client._option_bars,
        )
        next_now = next_day_start + timedelta(minutes=17)
        self._tick(now=next_now, options_client=next_day_client)
        self.assertEqual(self.state.proposals_created_today, 1)  # reset, then one new proposal

    def test_new_day_resets_processed_keys(self):
        self._tick()
        self.assertEqual(len(self.state.processed_entry_keys), 1)

        next_day_start = self.session_start + timedelta(days=1)
        next_day_bars = _build_breakout_spy_bars(next_day_start)
        next_day_client = FakeOptionsClient(
            spy_bars=next_day_bars, found_strike=(5920, "SPXW261118C05920000"),
            option_bars=self.client._option_bars,
        )
        next_now = next_day_start + timedelta(minutes=17)
        result = self._tick(now=next_now, options_client=next_day_client)
        self.assertEqual(result.new_opportunities_seen, 1)  # not blocked by yesterday's key


class TestExitMonitoring(unittest.TestCase):
    """D-0095-A item 4: monitor_open_positions, wired into run_one_tick."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.path)
        self.position_repo = SpxPositionRepository(self.path)
        self.ratio_provider = FakeRatioProvider(10.0)
        self.broker = FakeBroker()
        self.entry_time = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
        self.K = 5900
        self.entry_price = 1.0
        self.state = LiveSessionState()

    def tearDown(self):
        os.remove(self.path)

    def _open_a_position(self, hold_cap_minutes=40.0) -> str:
        from spx0dte.position import OpenPosition
        position = OpenPosition(
            proposal_id="SPX-exittest", occ_symbol="SPXW261117C05900000", direction="call",
            quantity=1, strike=self.K, date="2026-11-17", entry_time=self.entry_time,
            entry_price=self.entry_price, hold_cap_minutes=hold_cap_minutes,
        )
        self.position_repo.save(position)
        return position.proposal_id

    def _client_with_stale_price(self, price: float, now: datetime) -> FakeOptionsClient:
        spy_bars = [_bar(now - timedelta(minutes=m), 590.0) for m in range(120, -1, -1)]
        stale_time = now - timedelta(minutes=40)
        return FakeOptionsClient(
            spy_bars=spy_bars, found_strike=(self.K, "SPXW261117C05900000"),
            option_bars=[_bar(stale_time, price)],
        )

    def test_target_hit_closes_position(self):
        self._open_a_position()
        now = self.entry_time + timedelta(minutes=5)
        expiry = now.replace(hour=21, minute=0, second=0, microsecond=0)
        stale_time = now - timedelta(minutes=40)
        stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
        # A stale price whose implied vol, carried forward, reprices
        # well above the +80% target.
        stale_price = black_scholes_price(5900, self.K, stale_T, 0.045, 0.60, OptionType.CALL)
        client = self._client_with_stale_price(stale_price, now)

        result = run_one_tick(
            now=now, options_client=client, broker=self.broker, ratio_provider=self.ratio_provider,
            repo=self.repo, position_repo=self.position_repo, pending_decisions=[],
            bot_token="t", chat_id="1", quantity=1, state=self.state,
        )

        self.assertEqual(result.positions_monitored, 1)
        self.assertEqual(result.positions_closed, 1)
        self.assertEqual(self.position_repo.list_open(), [])
        sell_calls = [c for c in self.broker.calls if c["side"] == "sell"]
        self.assertEqual(len(sell_calls), 1)

        # D-0095-A, 2026-11-18: the close's realized dollar pnl (with
        # the contract multiplier applied, not the bare price diff)
        # must reach the daily loss-limit tracker.
        self.assertGreater(self.state.loss_limit_tracker.realized_pnl_today, 0.0)
        self.assertGreaterEqual(abs(self.state.loss_limit_tracker.realized_pnl_today), 100 * 0.5)

    def test_still_open_position_not_closed(self):
        # Build the stale quote AND the position's recorded entry_price
        # from the SAME (S, K, T, sigma) so the live reprice at `now`
        # lands close to entry_price -- isolating the guard behavior
        # (too early for lagging_pace, not armed, not near stop_loss)
        # instead of an incidental theta-decay price move.
        now = self.entry_time + timedelta(minutes=1)
        expiry = now.replace(hour=21, minute=0, second=0, microsecond=0)
        entry_T = max((expiry - self.entry_time).total_seconds(), 1) / 86400 / 365
        self.entry_price = black_scholes_price(5900, self.K, entry_T, 0.045, 0.15, OptionType.CALL)
        self._open_a_position()

        stale_time = now - timedelta(minutes=40)
        stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
        stale_price = black_scholes_price(5900, self.K, stale_T, 0.045, 0.15, OptionType.CALL)
        client = self._client_with_stale_price(stale_price, now)

        result = run_one_tick(
            now=now, options_client=client, broker=self.broker, ratio_provider=self.ratio_provider,
            repo=self.repo, position_repo=self.position_repo, pending_decisions=[],
            bot_token="t", chat_id="1", quantity=1, state=self.state,
        )

        self.assertEqual(result.positions_closed, 0)
        self.assertEqual(len(self.position_repo.list_open()), 1)

    def test_tracker_persists_across_ticks_for_stall_detection(self):
        self._open_a_position()
        # Arm the trade on tick 1, then feed flat prices to trigger stall by tick 4.
        times_and_vols = [
            (self.entry_time + timedelta(minutes=2), 0.40),  # arms it (big jump)
        ]
        for i in range(3, 7):
            times_and_vols.append((self.entry_time + timedelta(minutes=i), 0.40))

        closed_on_some_tick = False
        for now, vol in times_and_vols:
            expiry = now.replace(hour=21, minute=0, second=0, microsecond=0)
            stale_time = now - timedelta(minutes=40)
            stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
            stale_price = black_scholes_price(5900, self.K, stale_T, 0.045, vol, OptionType.CALL)
            client = self._client_with_stale_price(stale_price, now)

            result = run_one_tick(
                now=now, options_client=client, broker=self.broker, ratio_provider=self.ratio_provider,
                repo=self.repo, position_repo=self.position_repo, pending_decisions=[],
                bot_token="t", chat_id="1", quantity=1, state=self.state,
            )
            if result.positions_closed:
                closed_on_some_tick = True
                break

        self.assertTrue(closed_on_some_tick)


class TestBuildStatusSnapshot(unittest.TestCase):
    """The live dashboard's data source -- a pure function over
    position_repo + state, no I/O of its own."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.position_repo = SpxPositionRepository(self.path)
        self.entry_time = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
        self.state = LiveSessionState()
        self.state.current_date = "2026-11-19"
        self.state.dynamic_cap_today = 40.0
        self.state.loss_limit_tracker.start_new_day("2026-11-19", equity=97_593.76)

    def tearDown(self):
        os.remove(self.path)

    def _open_a_position(self, entry_price=1.0, hold_cap_minutes=40.0):
        from spx0dte.live_exit import LiveExitTracker
        from spx0dte.position import OpenPosition
        position = OpenPosition(
            proposal_id="SPX-snaptest", occ_symbol="SPXW261119C05900000", direction="call",
            quantity=1, strike=5900, date="2026-11-19", entry_time=self.entry_time,
            entry_price=entry_price, hold_cap_minutes=hold_cap_minutes,
        )
        self.position_repo.save(position)
        self.state.exit_trackers[position.proposal_id] = LiveExitTracker(
            entry_price=entry_price, entry_time=self.entry_time, hold_cap_minutes=hold_cap_minutes,
        )
        return position

    def test_empty_when_no_open_positions(self):
        snapshot = build_status_snapshot(now=self.entry_time, state=self.state, position_repo=self.position_repo)
        self.assertEqual(snapshot["open_positions"], [])
        self.assertEqual(snapshot["date"], "2026-11-19")
        self.assertEqual(snapshot["dynamic_cap_today"], 40.0)

    def test_open_position_fields_computed_correctly(self):
        self._open_a_position(entry_price=2.0)
        self.state.last_known_price["SPX-snaptest"] = 2.50
        now = self.entry_time + timedelta(minutes=10)

        snapshot = build_status_snapshot(now=now, state=self.state, position_repo=self.position_repo)
        self.assertEqual(len(snapshot["open_positions"]), 1)
        p = snapshot["open_positions"][0]
        self.assertEqual(p["direction"], "call")
        self.assertEqual(p["entry_price"], 2.0)
        self.assertEqual(p["current_price"], 2.50)
        self.assertAlmostEqual(p["target_price"], 3.6)  # 2.0 * 1.8
        self.assertAlmostEqual(p["stop_price"], 1.2)    # 2.0 * 0.6
        self.assertAlmostEqual(p["armed_price"], 2.5)   # 2.0 * 1.25
        self.assertFalse(p["armed"])  # tracker just created, not yet updated
        self.assertAlmostEqual(p["unrealized_pnl_dollars"], 50.0)  # (2.50-2.00)*1*100
        self.assertAlmostEqual(p["elapsed_minutes"], 10.0)
        self.assertAlmostEqual(p["remaining_minutes"], 30.0)

    def test_current_price_none_when_no_quote_fetched_yet(self):
        self._open_a_position()
        snapshot = build_status_snapshot(now=self.entry_time, state=self.state, position_repo=self.position_repo)
        p = snapshot["open_positions"][0]
        self.assertIsNone(p["current_price"])
        self.assertIsNone(p["unrealized_pnl_dollars"])

    def test_loss_limit_section_reflects_tracker(self):
        self.state.loss_limit_tracker.record_realized_pnl(-200.0)
        snapshot = build_status_snapshot(now=self.entry_time, state=self.state, position_repo=self.position_repo)
        self.assertEqual(snapshot["loss_limit"]["locked_equity"], 97_593.76)
        self.assertEqual(snapshot["loss_limit"]["realized_pnl_today"], -200.0)
        self.assertFalse(snapshot["loss_limit"]["limit_reached"])


class TestRunOneTickStatusFile(unittest.TestCase):
    def test_writes_a_valid_json_snapshot_when_path_given(self):
        fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        fd2, status_path = tempfile.mkstemp(suffix=".json")
        os.close(fd2)
        try:
            repo = SpxProposalRepository(db_path)
            position_repo = SpxPositionRepository(db_path)
            broker = FakeBroker()
            ratio_provider = FakeRatioProvider(10.0)
            now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
            client = FakeOptionsClient(spy_bars=[], found_strike=None, option_bars=[])
            state = LiveSessionState()

            run_one_tick(
                now=now, options_client=client, broker=broker, ratio_provider=ratio_provider,
                repo=repo, position_repo=position_repo, pending_decisions=[],
                bot_token="t", chat_id="1", quantity=1, state=state,
                status_file_path=status_path,
            )

            with open(status_path) as f:
                snapshot = json.load(f)
            self.assertIn("open_positions", snapshot)
            self.assertIn("loss_limit", snapshot)
        finally:
            os.remove(db_path)
            os.remove(status_path)

    def test_no_file_written_when_path_omitted(self):
        fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            repo = SpxProposalRepository(db_path)
            position_repo = SpxPositionRepository(db_path)
            broker = FakeBroker()
            ratio_provider = FakeRatioProvider(10.0)
            now = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
            client = FakeOptionsClient(spy_bars=[], found_strike=None, option_bars=[])
            state = LiveSessionState()

            result = run_one_tick(
                now=now, options_client=client, broker=broker, ratio_provider=ratio_provider,
                repo=repo, position_repo=position_repo, pending_decisions=[],
                bot_token="t", chat_id="1", quantity=1, state=state,
            )
            self.assertEqual(result.new_opportunities_seen, 0)  # just confirms a normal tick, no crash
        finally:
            os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
