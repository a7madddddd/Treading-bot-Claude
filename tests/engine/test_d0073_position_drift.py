"""D-0073 (P-045): compare the broker's share count with ours and
REPORT any difference. Detection only -- never correction.

P-044 showed why this matters: a partial Ladder 1 left the broker
holding 22 shares while the Trade said 20, and nothing anywhere noticed.
This check would have caught that by itself, and catches every other
cause too -- a manual buy in the Alpaca app, a corporate action, a
broker-side cancellation.
"""

import unittest
from datetime import timedelta

from engine.engine import Engine
from notifications.service import NotificationLevel

from tests.engine.test_engine import (
    FakeMarketDataSource, _active_trade, _make_engine, _now, _repos,
)


class _Position:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = qty


class _Snapshot:
    def __init__(self, positions):
        self.positions = tuple(positions)


class _Builder:
    def __init__(self, positions, raises=False):
        self._positions = positions
        self._raises = raises
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("broker unreachable")
        return _Snapshot(self._positions)


def _engine_with(positions, *, our_shares=20, raises=False, symbol="LOW"):
    repos = _repos()
    md = FakeMarketDataSource({symbol: 250.0})
    builder = _Builder(positions, raises=raises)
    engine, _, _, _, notifier, _ = _make_engine(
        *repos, market_data=md, position_snapshot_builder=builder)
    _active_trade(repos[0], trade_id="T-1", symbol=symbol, price=250.0,
                  shares=our_shares, now=_now())
    engine.start(now=_now())
    notifier.events.clear()
    return engine, notifier, builder, repos


def _events(notifier, name):
    return [e for e in notifier.events if e.event == name]


class TestDriftIsReported(unittest.TestCase):
    def test_matching_counts_produce_no_message(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 20)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(_events(notifier, "position_drift_detected"), [])

    def test_broker_holding_more_is_reported(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 22)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_events(notifier, "position_drift_detected")), 1)

    def test_broker_holding_fewer_is_reported(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 18)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_events(notifier, "position_drift_detected")), 1)

    def test_a_position_the_broker_lost_entirely_is_reported(self):
        engine, notifier, _, _ = _engine_with([])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_events(notifier, "position_drift_detected")), 1)

    def test_the_message_carries_both_numbers(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 22)])
        engine.run_reconciliation_tick(now=_now())
        body = _events(notifier, "position_drift_detected")[0].message
        self.assertIn("22", body)
        self.assertIn("20", body)

    def test_the_message_says_nothing_was_changed(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 22)])
        engine.run_reconciliation_tick(now=_now())
        body = _events(notifier, "position_drift_detected")[0].message
        self.assertIn("Nothing was changed automatically", body)

    def test_it_is_critical(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 22)])
        engine.run_reconciliation_tick(now=_now())
        self.assertIs(_events(notifier, "position_drift_detected")[0].level,
                      NotificationLevel.CRITICAL)


class TestItNeverCorrects(unittest.TestCase):
    def test_our_share_count_is_left_alone(self):
        engine, _, _, repos = _engine_with([_Position("LOW", 22)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(repos[0].get("T-1").trade.total_shares, 20)

    def test_an_untracked_symbol_does_not_create_a_trade(self):
        engine, _, _, repos = _engine_with(
            [_Position("LOW", 20), _Position("NVDA", 7)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(repos[0].list_active()), 1)


class TestUntrackedPositions(unittest.TestCase):
    def test_a_symbol_we_do_not_track_is_reported(self):
        engine, notifier, _, _ = _engine_with(
            [_Position("LOW", 20), _Position("NVDA", 7)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_events(notifier, "position_not_tracked")), 1)

    def test_a_zero_quantity_is_not_reported(self):
        engine, notifier, _, _ = _engine_with(
            [_Position("LOW", 20), _Position("NVDA", 0)])
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(_events(notifier, "position_not_tracked"), [])


class TestItCannotFlood(unittest.TestCase):
    def test_the_same_divergence_is_reported_once(self):
        engine, notifier, _, _ = _engine_with([_Position("LOW", 22)])
        for i in range(30):
            engine.run_reconciliation_tick(
                now=_now() + timedelta(seconds=i * 700))
        self.assertEqual(len(_events(notifier, "position_drift_detected")), 1)

    def test_the_broker_is_not_polled_every_tick(self):
        # P-032: a burst of broker calls left the protective Floor
        # unevaluated on five positions. This check must not reintroduce
        # that cost.
        engine, _, builder, _ = _engine_with([_Position("LOW", 20)])
        for i in range(20):
            engine.run_reconciliation_tick(now=_now() + timedelta(seconds=i * 30))
        self.assertLessEqual(builder.calls, 2)

    def test_the_interval_is_ten_minutes(self):
        self.assertEqual(Engine.POSITION_DRIFT_INTERVAL_SECONDS, 600.0)


class TestItFailsOpen(unittest.TestCase):
    def test_no_builder_wired_means_no_check_and_no_crash(self):
        repos = _repos()
        md = FakeMarketDataSource({"LOW": 250.0})
        engine, _, _, _, notifier, _ = _make_engine(*repos, market_data=md)
        _active_trade(repos[0], trade_id="T-1", symbol="LOW", price=250.0,
                      shares=20, now=_now())
        engine.start(now=_now())
        engine.run_reconciliation_tick(now=_now())  # must not raise
        self.assertEqual(_events(notifier, "position_drift_detected"), [])

    def test_an_unreachable_broker_is_skipped_quietly(self):
        engine, notifier, _, _ = _engine_with([], raises=True)
        engine.run_reconciliation_tick(now=_now())  # must not raise
        self.assertEqual(_events(notifier, "position_drift_detected"), [])


if __name__ == "__main__":
    unittest.main()
