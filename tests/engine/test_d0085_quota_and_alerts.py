"""D-0085 — the universe refresh must not starve the live engine, and
one incident must cost one message.

On 2026-10-07 the daily universe refresh (one bars request per surviving
symbol, 11,683 on the 2026-10-05 measurement) ran from 06:00 ET and
saturated the broker's shared account rate limit. Between 06:19 and
06:26 the engine was refused with HTTP 429 for QQQ, AMZN, GOOGL and
TSLA in turn while checking their protective Floors, and the Controller
received nine messages for what was a single event. Nothing was written
to disk, so the investigation had to run off pasted Telegram text.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from d0026.alpaca_enricher import (
    AlpacaFeatureEnricherConfigError, DEFAULT_REQUESTS_PER_MINUTE, _Pacer,
)
from engine.engine import _incident_cause
from notifications.service import (
    INotificationService, LoggingNotificationService, NotificationEvent,
    NotificationLevel, NotificationResult,
)


class _FakeClock:
    """Advances only when something sleeps, so the suite never waits."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class TestThePacerLeavesHeadroom(unittest.TestCase):
    def test_the_first_call_never_waits(self):
        clock = _FakeClock()
        _Pacer(150.0, monotonic=clock.monotonic, sleep=clock.sleep).wait()
        self.assertEqual(clock.slept, [])

    def test_calls_are_spaced_to_the_configured_rate(self):
        clock = _FakeClock()
        pacer = _Pacer(60.0, monotonic=clock.monotonic, sleep=clock.sleep)
        for _ in range(4):
            pacer.wait()
        # 60/min = one per second; the first is free, the next three wait.
        self.assertEqual(clock.slept, [1.0, 1.0, 1.0])

    def test_a_slow_caller_is_never_delayed(self):
        """If the work itself already took longer than the interval,
        pacing must add nothing -- otherwise it would double the cost of
        an already-slow run."""
        clock = _FakeClock()
        pacer = _Pacer(60.0, monotonic=clock.monotonic, sleep=clock.sleep)
        pacer.wait()
        clock.t += 5.0          # the request took five seconds
        pacer.wait()
        self.assertEqual(clock.slept, [])

    def test_None_disables_pacing_entirely(self):
        clock = _FakeClock()
        pacer = _Pacer(None, monotonic=clock.monotonic, sleep=clock.sleep)
        for _ in range(10):
            pacer.wait()
        self.assertEqual(clock.slept, [])

    def test_a_nonsense_rate_is_refused_loudly(self):
        for bad in (0, -1, -0.5):
            with self.subTest(bad=bad):
                with self.assertRaises(AlpacaFeatureEnricherConfigError):
                    _Pacer(bad)

    def test_the_default_leaves_real_headroom_for_the_engine(self):
        """The engine needs about 10 requests a minute with five open
        positions. The default must leave several times that under a
        commonly documented 200/min free tier."""
        self.assertLessEqual(DEFAULT_REQUESTS_PER_MINUTE, 160.0)
        self.assertGreaterEqual(200.0 - DEFAULT_REQUESTS_PER_MINUTE, 40.0)

    def test_the_run_still_finishes_before_the_open(self):
        """11,683 requests at the default rate, started by the 06:00 ET
        timer, must finish well before the first 09:30 trigger."""
        minutes = 11683 / DEFAULT_REQUESTS_PER_MINUTE
        self.assertLess(minutes, 210,
                        "a run that overruns 09:30 would leave the day "
                        "with no universe snapshot, and no snapshot "
                        "means no trading at all")


class TestIncidentsAreKeyedByCause(unittest.TestCase):
    def test_the_same_http_status_is_one_incident(self):
        a = _incident_cause("market_data_unavailable",
                            "provider returned HTTP 429 for 'QQQ'")
        b = _incident_cause("market_data_unavailable",
                            "provider returned HTTP 429 for 'TSLA'")
        self.assertEqual(a, b)

    def test_a_DIFFERENT_status_is_a_DIFFERENT_incident(self):
        """A delisted symbol must not hide inside an open rate-limit
        incident -- that is the failure mode grouping could introduce."""
        rate = _incident_cause("market_data_unavailable",
                               "provider returned HTTP 429 for 'QQQ'")
        gone = _incident_cause("market_data_unavailable",
                               "symbol 'ZZZZ' not found (HTTP 404)")
        self.assertNotEqual(rate, gone)

    def test_a_message_with_no_status_falls_back_to_the_event(self):
        self.assertEqual(
            _incident_cause("market_data_unavailable",
                            "network error while fetching last trade"),
            "market_data_unavailable")

    def test_different_events_without_a_status_stay_separate(self):
        self.assertNotEqual(
            _incident_cause("market_data_unavailable", "network error"),
            _incident_cause("broker_unreachable", "network error"))


class _Recorder(INotificationService):
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, event: NotificationEvent) -> NotificationResult:
        self.sent.append(event)
        return NotificationResult(success=True, attempts=1)


class _Exploding(INotificationService):
    def send(self, event: NotificationEvent) -> NotificationResult:
        raise RuntimeError("transport died")


class TestEveryNotificationReachesDisk(unittest.TestCase):
    def _event(self, **kw):
        base = dict(level=NotificationLevel.CRITICAL,
                    event="market_data_unavailable",
                    message="Could not get current price for QQQ",
                    symbol="QQQ",
                    timestamp=datetime(2026, 10, 7, 10, 19,
                                       tzinfo=timezone.utc))
        base.update(kw)
        return NotificationEvent(**base)

    def test_the_line_carries_what_an_investigation_needs(self):
        lines: list = []
        svc = LoggingNotificationService(_Recorder(), write=lines.append)
        svc.send(self._event(message="provider returned HTTP 429 for QQQ"))
        self.assertEqual(len(lines), 1)
        for needle in ("CRITICAL", "market_data_unavailable", "QQQ",
                       "429", "2026-10-07"):
            with self.subTest(needle=needle):
                self.assertIn(needle, lines[0])

    def test_a_multi_line_alert_stays_on_one_greppable_line(self):
        lines: list = []
        svc = LoggingNotificationService(_Recorder(), write=lines.append)
        svc.send(self._event(message="first line\n\nsecond line"))
        self.assertEqual(len(lines), 1)
        self.assertNotIn("\n", lines[0])
        self.assertIn("first line", lines[0])
        self.assertIn("second line", lines[0])

    def test_delivery_still_happens_and_the_result_is_passed_through(self):
        inner = _Recorder()
        result = LoggingNotificationService(inner, write=lambda _: None).send(
            self._event())
        self.assertEqual(len(inner.sent), 1)
        self.assertTrue(result.success)

    def test_the_trace_is_written_BEFORE_delivery_is_attempted(self):
        """So a transport that dies still leaves evidence the event
        occurred -- the whole reason the 429s were invisible."""
        lines: list = []
        svc = LoggingNotificationService(_Exploding(), write=lines.append)
        with self.assertRaises(RuntimeError):
            svc.send(self._event())
        self.assertEqual(len(lines), 1)

    def test_a_broken_logger_never_blocks_a_critical_alert(self):
        def _bad(_line):
            raise OSError("disk full")
        inner = _Recorder()
        result = LoggingNotificationService(inner, write=_bad).send(
            self._event())
        self.assertEqual(len(inner.sent), 1,
                         "logging must never swallow a delivery")
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
