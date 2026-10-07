"""P-041: one alert per symbol per market-data outage, not one per tick.

`_check_floor_trigger` runs for every ACTIVE trade on every
reconciliation tick (30 s). On 2026-10-05, five open positions during a
rate-limit outage produced 10 CRITICAL messages a minute -- 600 an hour
-- all identical. A Controller buried under 600 copies stops reading the
channel, and the next message after that is a Floor execution or a
submission failure.

Also covers P-042's second half: `Engine._notify` must REPORT whether
the message was delivered instead of discarding the result.
"""

import unittest
from datetime import datetime, timezone
from typing import List

from engine.engine import Engine
from marketdata.source import MarketDataUnavailableError
from notifications.service import (
    INotificationService, NotificationEvent, NotificationLevel,
    NotificationResult,
)

from .test_engine import (
    FakeBrokerClient, FakeMarketDataSource, RecordingNotifier,
    _active_trade, _make_engine, _now, _repos,
)


class _FailingMarketData(FakeMarketDataSource):
    """Raises for every symbol until `healed` is set."""

    def __init__(self):
        super().__init__()
        self.healed = False
        self.calls = 0

    def get_last_trade(self, symbol: str) -> float:
        self.calls += 1
        if self.healed:
            return 100.0
        raise MarketDataUnavailableError(f"HTTP 429 for {symbol}")


class _RefusingNotifier(INotificationService):
    def __init__(self):
        self.events: List[NotificationEvent] = []

    def send(self, event: NotificationEvent) -> NotificationResult:
        self.events.append(event)
        return NotificationResult(success=False, attempts=3,
                                  status_code=400, error="text too long")


def _outage_events(notifier):
    return [e for e in notifier.events
            if e.event == "market_data_unavailable"]


def _recovery_events(notifier):
    return [e for e in notifier.events
            if e.event == "market_data_recovered"]


class TestOutageAlertIsSentOnce(unittest.TestCase):
    def setUp(self):
        self.repos = _repos()
        self.md = _FailingMarketData()
        self.engine, _, _, _, self.notifier, _ = _make_engine(
            *self.repos, market_data=self.md)
        _active_trade(self.repos[0], trade_id="T-1", symbol="TSLA")
        self.engine.start(now=_now())
        self.notifier.events.clear()

    def test_the_first_outage_alerts_immediately(self):
        self.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(self.notifier)), 1)

    def test_the_first_alert_is_critical(self):
        self.engine.run_reconciliation_tick(now=_now())
        self.assertIs(_outage_events(self.notifier)[0].level,
                      NotificationLevel.CRITICAL)

    def test_twenty_ticks_still_produce_one_alert(self):
        # 20 ticks at 30s is ten minutes of outage. Before the fix this
        # was 20 identical CRITICAL messages for ONE position.
        for _ in range(20):
            self.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(self.notifier)), 1)

    def test_the_alert_PROMISES_no_repeats_and_an_all_clear(self):
        """P-041's point is the promise, not its exact wording: the
        Controller must be told that silence from here is expected and
        that one all-clear will close it, or he cannot tell a quiet
        channel from a dead one. D-0085 reworded this when grouping
        moved from per-symbol to per-cause, so the test now checks the
        two facts rather than one word."""
        self.engine.run_reconciliation_tick(now=_now())
        message = _outage_events(self.notifier)[0].message
        self.assertIn("all-clear", message)
        self.assertTrue(
            "grouped" in message or "suppressed" in message,
            msg=f"the alert must say further alerts will not repeat: "
                f"{message!r}")

    def test_the_price_source_is_still_polled_every_tick(self):
        # Suppressing the ALERT must not suppress the CHECK. The engine
        # must keep trying, so it notices the moment data returns.
        before = self.md.calls
        for _ in range(5):
            self.engine.run_reconciliation_tick(now=_now())
        self.assertGreater(self.md.calls, before + 4)


class TestRecoveryAllClear(unittest.TestCase):
    def setUp(self):
        self.repos = _repos()
        self.md = _FailingMarketData()
        self.engine, _, _, _, self.notifier, _ = _make_engine(
            *self.repos, market_data=self.md)
        _active_trade(self.repos[0], trade_id="T-1", symbol="TSLA")
        self.engine.start(now=_now())
        self.notifier.events.clear()

    def test_recovery_sends_exactly_one_all_clear(self):
        self.engine.run_reconciliation_tick(now=_now())
        self.md.healed = True
        for _ in range(5):
            self.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_recovery_events(self.notifier)), 1)

    def test_no_all_clear_without_a_preceding_outage(self):
        self.md.healed = True
        for _ in range(5):
            self.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(_recovery_events(self.notifier), [])

    def test_a_second_outage_after_recovery_alerts_again(self):
        # The point of the all-clear: state is reset, so a NEW outage is
        # a new alert rather than being swallowed by the old one.
        self.engine.run_reconciliation_tick(now=_now())
        self.md.healed = True
        self.engine.run_reconciliation_tick(now=_now())
        self.md.healed = False
        self.engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(self.notifier)), 2)

    def test_the_all_clear_is_important_not_critical(self):
        self.engine.run_reconciliation_tick(now=_now())
        self.md.healed = True
        self.engine.run_reconciliation_tick(now=_now())
        self.assertIs(_recovery_events(self.notifier)[0].level,
                      NotificationLevel.IMPORTANT)


class TestDeliveryIsReported(unittest.TestCase):
    def test_notify_returns_true_when_delivered(self):
        repos = _repos()
        engine, _, _, _, _, _ = _make_engine(*repos)
        self.assertTrue(engine._notify(
            level=NotificationLevel.IMPORTANT, event="t", message="m"))

    def test_notify_returns_false_when_refused(self):
        repos = _repos()
        engine, _, _, _, _, _ = _make_engine(
            *repos, notifier=_RefusingNotifier())
        self.assertFalse(engine._notify(
            level=NotificationLevel.IMPORTANT, event="t", message="m"))

    def test_a_refused_send_is_written_to_stdout(self):
        # stdout is systemd's logs/engine.log, and it is the ONLY
        # channel left when the notification channel is what is broken.
        import contextlib, io as _io
        repos = _repos()
        engine, _, _, _, _, _ = _make_engine(
            *repos, notifier=_RefusingNotifier())
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            engine._notify(level=NotificationLevel.IMPORTANT,
                           event="proposal_awaiting_approval", message="m")
        printed = buf.getvalue()
        self.assertIn("notify-failed", printed)
        self.assertIn("proposal_awaiting_approval", printed)
        self.assertIn("400", printed)

    def test_a_refused_send_never_raises(self):
        # CLAUDE.md §6: a failed notification must never escalate into
        # retrying a trade or blocking execution.
        repos = _repos()
        md = FakeMarketDataSource({"TSLA": 100.0})
        engine, _, _, _, _, _ = _make_engine(
            *repos, market_data=md, notifier=_RefusingNotifier())
        _active_trade(repos[0], trade_id="T-1", symbol="TSLA")
        engine.start(now=_now())
        engine.run_reconciliation_tick(now=_now())  # must not raise


if __name__ == "__main__":
    unittest.main()


class TestP043Escalation(unittest.TestCase):
    """P-043: a persistent outage must be reported as a STATE ("this
    position is currently unprotected"), distinct from the EVENT ("an
    outage started") that P-041 covers. It fires once, so it cannot
    re-create the flood P-041 removed."""

    def setUp(self):
        self.repos = _repos()
        self.md = _FailingMarketData()
        self.engine, _, _, _, self.notifier, _ = _make_engine(
            *self.repos, market_data=self.md)
        _active_trade(self.repos[0], trade_id="T-1", symbol="TSLA")
        self.engine.start(now=_now())
        self.notifier.events.clear()

    def _escalations(self):
        return [e for e in self.notifier.events
                if e.event == "protection_unevaluated"]

    def _tick(self, n):
        for _ in range(n):
            self.engine.run_reconciliation_tick(now=_now())

    def test_a_short_outage_does_not_escalate(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES - 1)
        self.assertEqual(self._escalations(), [])

    def test_it_escalates_at_the_threshold(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES)
        self.assertEqual(len(self._escalations()), 1)

    def test_it_escalates_only_once_however_long_the_outage_lasts(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES * 5)
        self.assertEqual(len(self._escalations()), 1)

    def test_the_message_says_the_position_is_unchecked(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES)
        body = self._escalations()[0].message
        self.assertIn("OPEN and UNCHECKED", body)
        self.assertIn("NOT been evaluated", body)

    def test_the_message_states_that_nothing_is_sold_automatically(self):
        # The Controller must not fear that a data outage could trigger
        # a blind sell.
        self._tick(Engine.OUTAGE_ESCALATION_MISSES)
        self.assertIn("Nothing will be sold automatically",
                      self._escalations()[0].message)

    def test_it_is_critical(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES)
        self.assertIs(self._escalations()[0].level,
                      NotificationLevel.CRITICAL)

    def test_recovery_resets_the_counter(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES - 1)
        self.md.healed = True
        self.engine.run_reconciliation_tick(now=_now())
        self.md.healed = False
        self._tick(Engine.OUTAGE_ESCALATION_MISSES - 1)
        self.assertEqual(self._escalations(), [],
                         "a healed gap must not carry misses forward")

    def test_a_second_long_outage_escalates_again(self):
        self._tick(Engine.OUTAGE_ESCALATION_MISSES)
        self.md.healed = True
        self.engine.run_reconciliation_tick(now=_now())
        self.md.healed = False
        self._tick(Engine.OUTAGE_ESCALATION_MISSES)
        self.assertEqual(len(self._escalations()), 2)

    def test_threshold_is_five_minutes_at_the_30s_interval(self):
        self.assertEqual(Engine.OUTAGE_ESCALATION_MISSES * 30, 300)


class _PerSymbolMarketData(FakeMarketDataSource):
    """Fails only the symbols in `failing`, so an incident can be opened
    and closed symbol by symbol -- which is how 2026-10-07 actually
    unfolded: four symbols refused minutes apart, each recovering before
    the next failed."""

    def __init__(self, failing, status="429"):
        super().__init__()
        self.failing = set(failing)
        self.status = status

    def get_last_trade(self, symbol: str) -> float:
        if symbol in self.failing:
            raise MarketDataUnavailableError(
                f"provider returned HTTP {self.status} for {symbol!r}")
        return 100.0


class TestOneIncidentCostsOneMessage(unittest.TestCase):
    """D-0085. On 2026-10-07 four symbols hit the SAME rate limit in the
    same seven minutes and the Controller received nine messages for
    what was one event: the daily universe refresh saturating the shared
    account quota."""

    def _engine_with(self, symbols, md):
        repos = _repos()
        engine, _, _, _, notifier, _ = _make_engine(*repos, market_data=md)
        for i, sym in enumerate(symbols):
            _active_trade(repos[0], trade_id=f"T-{i}", symbol=sym)
        engine.start(now=_now())
        notifier.events.clear()
        return engine, notifier

    def test_four_symbols_one_cause_produce_ONE_alert(self):
        md = _PerSymbolMarketData(["QQQ", "AMZN", "GOOGL", "TSLA"])
        engine, notifier = self._engine_with(
            ["QQQ", "AMZN", "GOOGL", "TSLA"], md)
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(notifier)), 1)

    def test_and_ONE_all_clear_naming_every_affected_symbol(self):
        md = _PerSymbolMarketData(["QQQ", "AMZN", "GOOGL", "TSLA"])
        engine, notifier = self._engine_with(
            ["QQQ", "AMZN", "GOOGL", "TSLA"], md)
        engine.run_reconciliation_tick(now=_now())
        md.failing.clear()
        engine.run_reconciliation_tick(now=_now())

        recoveries = _recovery_events(notifier)
        self.assertEqual(len(recoveries), 1)
        for sym in ("QQQ", "AMZN", "GOOGL", "TSLA"):
            with self.subTest(sym=sym):
                self.assertIn(sym, recoveries[0].message)
        self.assertIn("Affected (4)", recoveries[0].message)

    def test_the_whole_episode_costs_two_messages_not_nine(self):
        md = _PerSymbolMarketData(["QQQ", "AMZN", "GOOGL", "TSLA"])
        engine, notifier = self._engine_with(
            ["QQQ", "AMZN", "GOOGL", "TSLA"], md)
        for _ in range(14):      # seven minutes at the 30s tick
            engine.run_reconciliation_tick(now=_now())
        md.failing.clear()
        engine.run_reconciliation_tick(now=_now())
        total = len(_outage_events(notifier)) + len(_recovery_events(notifier))
        self.assertEqual(total, 2)

    def test_a_symbol_joining_an_OPEN_incident_adds_no_message(self):
        md = _PerSymbolMarketData(["QQQ"])
        engine, notifier = self._engine_with(["QQQ", "AMZN"], md)
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(notifier)), 1)
        md.failing.add("AMZN")           # same cause, joins silently
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(notifier)), 1)

    def test_a_DIFFERENT_cause_still_gets_its_own_alert(self):
        """The failure mode grouping could introduce: a delisted symbol
        going quiet inside an open rate-limit incident. It must not."""
        repos = _repos()

        class _TwoCauses(FakeMarketDataSource):
            def get_last_trade(self, symbol: str) -> float:
                if symbol == "QQQ":
                    raise MarketDataUnavailableError(
                        "provider returned HTTP 429 for 'QQQ'")
                if symbol == "ZZZZ":
                    raise MarketDataUnavailableError(
                        "symbol 'ZZZZ' not found by data provider (HTTP 404)")
                return 100.0

        engine, _, _, _, notifier, _ = _make_engine(
            *repos, market_data=_TwoCauses())
        _active_trade(repos[0], trade_id="T-1", symbol="QQQ")
        _active_trade(repos[0], trade_id="T-2", symbol="ZZZZ")
        engine.start(now=_now())
        notifier.events.clear()

        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_outage_events(notifier)), 2)

    def test_a_recovery_is_NEVER_silent_even_with_lost_state(self):
        """If incident state is missing -- an outage that opened before
        this change, or a restart mid-incident -- the all-clear must
        still be sent. Silence is the one outcome a recovery may never
        have."""
        md = _PerSymbolMarketData(["QQQ"])
        engine, notifier = self._engine_with(["QQQ"], md)
        engine.run_reconciliation_tick(now=_now())
        engine._symbol_incident.clear()
        engine._incident_symbols.clear()
        md.failing.clear()
        engine.run_reconciliation_tick(now=_now())
        self.assertEqual(len(_recovery_events(notifier)), 1)

    def test_the_per_symbol_UNPROTECTED_escalation_is_NOT_grouped(self):
        """The message the Controller actually acts on stays per symbol
        and ungrouped -- grouping it would hide which position is
        exposed."""
        md = _PerSymbolMarketData(["QQQ", "AMZN"])
        engine, notifier = self._engine_with(["QQQ", "AMZN"], md)
        for _ in range(engine.OUTAGE_ESCALATION_MISSES + 1):
            engine.run_reconciliation_tick(now=_now())
        escalations = [e for e in notifier.events
                       if e.event == "protection_unevaluated"]
        self.assertEqual({e.symbol for e in escalations}, {"QQQ", "AMZN"})
