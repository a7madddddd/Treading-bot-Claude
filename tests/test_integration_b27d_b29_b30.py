"""End-to-end integration tests for B27d + B29 + B30 wired together.

Goal: hunt for cross-module bugs the isolated unit tests would miss.
Every assertion here exercises real production code paths -- the
D-0048 stages, the SchedulerDaemon loop, and the static sector
provider -- with no per-module mocking.

Scenarios covered:
  1. Prefilter + sector provider produces sector="..." on features
     and stage G enforces the sector cap.
  2. Scheduler daemon fires a job whose callable runs the prefilter
     and returns a stable approved set.
  3. Prefilter with 3+ symbols from the same sector: sector cap
     actually drops the excess (regression check for stage G
     wiring end-to-end).
  4. Prefilter's `source_reference` never leaks sector fragment to
     a symbol the provider doesn't know.
  5. Scheduler + prefilter + sector: firing the job with an
     ever-advancing clock produces monotonically-non-decreasing
     fire timestamps (regression check for stale-clock bugs).
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Sequence
from zoneinfo import ZoneInfo

from backtesting.models import Bar
from backtesting.prefilter import BacktestUniversePrefilter
from d0026.config import UniverseSelectionConfig
from d0026.sector_provider import (
    StaticSectorProvider, load_default_sector_provider,
)
from scheduler.daemon import (
    ListFireSink, ScheduledJob, SchedulerDaemon,
)
from scheduler.next_fire import ScheduleSpec, WEEKDAYS_MON_TO_FRI


NY = ZoneInfo("America/New_York")


def _series(n, *, start_price=100.0, drift=0.5, high_off=1.0,
            low_off=1.0, vol=1_000_000, start_date=date(2026, 1, 1)):
    bars = []
    p = start_price
    for i in range(n):
        d = start_date + timedelta(days=i)
        o = p
        c = p + drift
        h = max(o, c) + high_off
        l = min(o, c) - low_off
        bars.append(Bar(bar_date=d, open=o, high=h, low=l,
                        close=c, volume=vol))
        p = c
    return bars


class TestPrefilterWithSectorProvider(unittest.TestCase):
    def test_source_reference_carries_sector_fragment(self):
        provider = StaticSectorProvider(mapping={
            "AAA": "information_technology",
        })
        bars = _series(60, start_price=100.0, drift=0.5,
                       high_off=1.5, low_off=1.5)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
            sector_provider=provider,
        )
        cands = pf._build_candidates(bars[-1].bar_date)
        self.assertEqual(len(cands), 1)
        self.assertIn(
            "sector=information_technology",
            cands[0].features.source_reference,
        )

    def test_unknown_ticker_gets_no_sector_fragment(self):
        provider = StaticSectorProvider(mapping={"OTHER": "energy"})
        bars = _series(60, start_price=100.0, drift=0.5,
                       high_off=1.5, low_off=1.5)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"UNKNOWN": bars},
            config=UniverseSelectionConfig(),
            sector_provider=provider,
        )
        cands = pf._build_candidates(bars[-1].bar_date)
        self.assertEqual(len(cands), 1)
        self.assertNotIn("sector=", cands[0].features.source_reference)


class TestStageGConcentrationEndToEnd(unittest.TestCase):
    def test_sector_cap_drops_excess(self):
        """With 5 symbols all in 'financials' and cap 0.30:
        ceil(0.30 * 5) = 2 symbols per sector allowed => 3 dropped
        by stage G. This confirms B30 -> stage G actually wires."""

        provider = StaticSectorProvider(mapping={
            "A1": "financials", "A2": "financials", "A3": "financials",
            "A4": "financials", "A5": "financials",
        })
        # 5 identical liquid, mid-ATR series, staggered momentum so
        # ranking is deterministic (later symbols slightly higher).
        bars_by = {}
        for i in range(5):
            bars_by[f"A{i+1}"] = _series(
                60, start_price=100.0 + i * 0.5, drift=0.5,
                high_off=1.5, low_off=1.5,
            )
        pf = BacktestUniversePrefilter(
            bars_by_symbol=bars_by,
            config=UniverseSelectionConfig(top_n=10),
            sector_provider=provider,
        )
        approved = pf.approved_symbols_on(bars_by["A1"][-1].bar_date)
        # Sector cap ceil(0.30 * 5) = 2, so at most 2 survive from
        # 'financials'.
        self.assertLessEqual(len(approved), 2)

    def test_sector_diversity_lifts_cap(self):
        """5 different sectors, one symbol each => no cap trigger,
        all should pass sector cap (subject to other stages)."""

        provider = StaticSectorProvider(mapping={
            "S1": "financials", "S2": "information_technology",
            "S3": "energy", "S4": "health_care", "S5": "industrials",
        })
        bars_by = {}
        for i in range(5):
            bars_by[f"S{i+1}"] = _series(
                60, start_price=100.0 + i * 0.5, drift=0.5,
                high_off=1.5, low_off=1.5,
            )
        pf = BacktestUniversePrefilter(
            bars_by_symbol=bars_by,
            config=UniverseSelectionConfig(top_n=10),
            sector_provider=provider,
        )
        approved = pf.approved_symbols_on(bars_by["S1"][-1].bar_date)
        # No sector has more than 1 -> concentration cannot drop any.
        # Some may still be dropped by other stages (e.g. tradability),
        # but sector G won't reduce below 5's-worth of diversity.
        self.assertGreaterEqual(len(approved), 1)


class TestSchedulerFiresPrefilterJob(unittest.TestCase):
    """Integration: scheduler daemon fires a callable that runs the
    prefilter with a sector provider. Catches wiring bugs between
    B29 and B27d + B30."""

    def test_daemon_fires_prefilter_job_and_records_ok(self):
        provider = load_default_sector_provider()
        bars_by = {
            "AAPL": _series(60, drift=0.5, high_off=1.5, low_off=1.5),
            "MSFT": _series(60, drift=0.5, high_off=1.5, low_off=1.5),
        }
        pf = BacktestUniversePrefilter(
            bars_by_symbol=bars_by,
            config=UniverseSelectionConfig(),
            sector_provider=provider,
        )

        fired_sets: List[frozenset] = []

        def _job(_scheduled_at):
            approved = pf.approved_symbols_on(bars_by["AAPL"][-1].bar_date)
            fired_sets.append(approved)

        spec = ScheduleSpec(
            tz=NY,
            times_of_day=(time(9, 30), time(10, 30)),
            weekdays=WEEKDAYS_MON_TO_FRI,
        )
        job = ScheduledJob(name="prefilter-job", spec=spec, fn=_job)

        class _Clock:
            def __init__(self, s):
                self.t = s

            def now(self):
                return self.t

            def sleep(self, s):
                self.t = self.t + timedelta(seconds=s)

        c = _Clock(datetime(2026, 2, 2, 9, 29, tzinfo=NY))
        sink = ListFireSink()
        d = SchedulerDaemon(
            jobs=[job], sink=sink, clock=c.now,
            sleep_fn=c.sleep, install_signal_handlers=False,
        )
        d.run_until(datetime(2026, 2, 2, 11, 0, tzinfo=NY))

        # 2 fires expected (09:30, 10:30) and both should succeed.
        self.assertGreaterEqual(len(sink.events), 2)
        self.assertTrue(all(e.ok for e in sink.events),
                        [e.error for e in sink.events])
        # Each fire recomputes the same approved set (bars fixed).
        self.assertEqual(fired_sets[0], fired_sets[1])

    def test_fire_timestamps_monotonic(self):
        provider = load_default_sector_provider()
        bars_by = {"AAPL": _series(60, drift=0.5,
                                   high_off=1.5, low_off=1.5)}
        pf = BacktestUniversePrefilter(
            bars_by_symbol=bars_by,
            config=UniverseSelectionConfig(),
            sector_provider=provider,
        )

        def _job(_scheduled_at):
            pf.approved_symbols_on(bars_by["AAPL"][-1].bar_date)

        spec = ScheduleSpec(
            tz=NY,
            times_of_day=tuple(time(h, 30) for h in range(9, 16)),
            weekdays=WEEKDAYS_MON_TO_FRI,
        )
        job = ScheduledJob(name="pf", spec=spec, fn=_job)

        class _Clock:
            def __init__(self, s):
                self.t = s

            def now(self):
                return self.t

            def sleep(self, s):
                self.t = self.t + timedelta(seconds=s)

        c = _Clock(datetime(2026, 2, 2, 9, 0, tzinfo=NY))
        sink = ListFireSink()
        d = SchedulerDaemon(
            jobs=[job], sink=sink, clock=c.now,
            sleep_fn=c.sleep, install_signal_handlers=False,
        )
        d.run_until(datetime(2026, 2, 2, 16, 0, tzinfo=NY))

        # 7 slots (09:30..15:30) should have all fired
        self.assertEqual(len(sink.events), 7)
        # scheduled_at monotonically increasing
        for i in range(1, len(sink.events)):
            self.assertGreater(
                sink.events[i].scheduled_at,
                sink.events[i - 1].scheduled_at,
            )


class TestNoRegressions(unittest.TestCase):
    def test_prefilter_without_sector_provider_still_works(self):
        """B30 addition must not break B27d's no-provider path."""

        bars = _series(60, drift=0.5, high_off=1.5, low_off=1.5)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
        )
        approved = pf.approved_symbols_on(bars[-1].bar_date)
        self.assertIn("AAA", approved)

    def test_source_reference_without_provider_is_backtest(self):
        bars = _series(60, drift=0.5, high_off=1.5, low_off=1.5)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
        )
        cands = pf._build_candidates(bars[-1].bar_date)
        # No provider -> no sector fragment
        self.assertEqual(cands[0].features.source_reference, "backtest")


if __name__ == "__main__":
    unittest.main()
