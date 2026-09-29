"""Persistent scheduler daemon (B29 / D-0023).

A stdlib-only, TZ-aware daemon that fires registered `ScheduledJob`s
at their approved wall-clock times, DST-safe. No external scheduler
dependency (APScheduler, cron, systemd timers) is required -- the
process itself is the schedule host.

Responsibilities:
  1. Given a set of ScheduledJobs, compute the earliest next-fire
     moment across all of them.
  2. Sleep until that moment (interruptible on SIGINT / SIGTERM).
  3. Fire the job's callable in a try/except so a single job's
     exception can never kill the daemon.
  4. Record the fire outcome (time, job name, ok/error, error text)
     to an injected sink so operator observability survives restart.
  5. Loop.

Non-responsibilities (deliberate):
  - Does not decide which times are "approved" -- the caller wires
    the ScheduleSpec.
  - Does not enforce D-0006 US market holidays -- flagged gap
    (docs/architecture/overview.md), separate concern.
  - Does not persist "did this exact firing already happen?" state.
    The daemon is idempotent per fire in the sense that a job's
    callable is expected to be re-entrant safe (D-0018 recovery
    contract). Under-fire (missed a slot because the process was
    down) is a data question for the recovery layer, not the
    scheduler.
"""

from __future__ import annotations

import signal
import threading
import time as time_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

from scheduler.next_fire import ScheduleSpec, next_fire_at


Clock = Callable[[], datetime]
"""A function returning the current tz-aware datetime. Injected so
tests can drive a fake clock deterministically."""


def _real_utc_clock() -> datetime:
    return datetime.now(timezone.utc)


class FireSink(Protocol):
    """Where fire outcomes go (persistent log). A real implementation
    writes to the SQLite state store; tests can use a list-appender."""

    def record(self, event: "FireEvent") -> None: ...


@dataclass(frozen=True)
class FireEvent:
    job_name: str
    scheduled_at: datetime  # the wall-clock time the daemon aimed at
    fired_at: datetime      # actual clock time when callable started
    ok: bool
    error: Optional[str]    # exception class + message; None on ok


@dataclass(frozen=True)
class ScheduledJob:
    """A named callable plus a schedule spec. The callable takes the
    scheduled datetime (so the job can log/branch on which slot it
    is) and returns nothing. Exceptions are caught by the daemon."""

    name: str
    spec: ScheduleSpec
    fn: Callable[[datetime], None]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("job name must be non-empty")


class ListFireSink:
    """In-memory sink for tests and short-lived processes."""

    def __init__(self) -> None:
        self._events: List[FireEvent] = []

    def record(self, event: FireEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> Tuple[FireEvent, ...]:
        return tuple(self._events)


class SchedulerDaemon:
    """Persistent scheduler loop.

    Usage:
        d = SchedulerDaemon(jobs=[...], sink=my_sink)
        d.run_forever()   # blocks until stop() or SIGINT/SIGTERM

    `run_until(...)` is a bounded-loop variant for tests; a real
    process calls `run_forever()`.
    """

    def __init__(
        self,
        jobs: Sequence[ScheduledJob],
        *,
        sink: Optional[FireSink] = None,
        clock: Clock = _real_utc_clock,
        sleep_fn: Callable[[float], None] = time_mod.sleep,
        install_signal_handlers: bool = True,
    ) -> None:
        if not jobs:
            raise ValueError("at least one ScheduledJob required")
        names = [j.name for j in jobs]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate job names: {names!r}")
        self._jobs: Tuple[ScheduledJob, ...] = tuple(jobs)
        self._sink = sink if sink is not None else ListFireSink()
        self._clock = clock
        self._sleep_fn = sleep_fn
        self._stop_event = threading.Event()
        if install_signal_handlers:
            self._install_signal_handlers()

    @property
    def sink(self) -> FireSink:
        return self._sink

    def stop(self) -> None:
        """Request graceful shutdown at the next wakeup boundary."""

        self._stop_event.set()

    def run_forever(self) -> None:
        """Loop until stop() or a fatal signal is received."""

        while not self._stop_event.is_set():
            self._tick_once()

    def run_until(self, deadline: datetime) -> None:
        """Bounded-loop variant: exits when the clock passes `deadline`.
        Used in tests with a fake clock so we can assert exact fires."""

        while not self._stop_event.is_set():
            now = self._clock()
            if now >= deadline:
                return
            self._tick_once(deadline=deadline)

    # ------------------------------------------------------------------

    def _tick_once(self, *, deadline: Optional[datetime] = None) -> None:
        now = self._clock()
        job, fire_at = self._earliest_next(now)
        if deadline is not None and fire_at > deadline:
            # Sleep out the remainder against the deadline so run_until
            # exits promptly. Compute against the clock, not wall-clock.
            gap = (deadline - now).total_seconds()
            if gap > 0:
                self._sleep_fn(gap)
            return
        gap = (fire_at - now).total_seconds()
        if gap > 0:
            # Chunked sleep so stop() can interrupt within 1s.
            end = now.timestamp() + gap
            while not self._stop_event.is_set():
                remaining = end - self._clock().timestamp()
                if remaining <= 0:
                    break
                self._sleep_fn(min(1.0, remaining))
            if self._stop_event.is_set():
                return
        self._fire(job, scheduled_at=fire_at)

    def _earliest_next(
        self, now: datetime,
    ) -> Tuple[ScheduledJob, datetime]:
        best: Optional[Tuple[ScheduledJob, datetime]] = None
        for job in self._jobs:
            when = next_fire_at(now, job.spec)
            if best is None or when < best[1]:
                best = (job, when)
        assert best is not None  # __init__ enforces >=1 job
        return best

    def _fire(
        self, job: ScheduledJob, *, scheduled_at: datetime,
    ) -> None:
        fired_at = self._clock()
        try:
            job.fn(scheduled_at)
            self._sink.record(FireEvent(
                job_name=job.name, scheduled_at=scheduled_at,
                fired_at=fired_at, ok=True, error=None,
            ))
        except Exception as exc:  # noqa: BLE001 -- deliberate;
            # daemon must survive job faults.
            self._sink.record(FireEvent(
                job_name=job.name, scheduled_at=scheduled_at,
                fired_at=fired_at, ok=False,
                error=f"{type(exc).__name__}: {exc}",
            ))

    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):  # noqa: ARG001
            self.stop()

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except ValueError:
            # signal() only works on the main thread of the main
            # interpreter. If we're loaded in a thread (e.g. in
            # some test harnesses), silently skip -- the daemon
            # still works, just without signal-based shutdown.
            pass
