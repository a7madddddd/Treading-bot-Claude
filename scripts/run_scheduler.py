#!/usr/bin/env python3
"""Persistent scheduler daemon entry point (B29 / D-0023).

NOT THE PRODUCTION MECHANISM -- read this before relying on it.
=================================================================
This script is a DEMO of SchedulerDaemon. Its job callable
(`_real_slot` below) only prints a line; it invokes no trading logic
whatsoever, and it has never been wired to any.

Production uses two different things, and neither is this file:

  - the D-0021 hourly trigger: the engine runs it in its OWN loop,
    inside scripts/run_paper_session.py, gated by
    engine.schedule.is_d0021_check_time.
  - the daily D-0026 universe refresh: a systemd timer on the VM,
    deploy/universe-refresh.timer.

This header exists because the old one ("wires the D-0021 approved
schedule ... and runs the daemon") read as though scheduling were
handled here. It was not, and during the 2026-10-05 audit that
wording cost real time: the system appeared to have a scheduler while
nothing was refreshing the universe at all. See P-015.

Wires the D-0021 approved schedule (09:30-15:30 ET, weekdays, hourly
on the half-hour) to a placeholder callable and runs the daemon
until SIGINT/SIGTERM.

Usage:
    PYTHONPATH=src python3 scripts/run_scheduler.py [--dry-run]

--dry-run: fires a no-op logger instead of invoking any real
session logic. Safe to run in any environment; useful for verifying
scheduling behavior without contacting Alpaca / Telegram.

Paper-trading only (D-0002). The daemon itself does NOT decide any
trading behavior -- it invokes callables that other, already-approved
modules own.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

_here = os.path.abspath(os.path.dirname(__file__))
_src = os.path.abspath(os.path.join(_here, "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)


def _dry_run_slot(scheduled_at: datetime) -> None:
    ts = scheduled_at.strftime("%Y-%m-%d %H:%M %Z")
    print(f"[scheduler] fire slot {ts}", flush=True)


def _real_slot(scheduled_at: datetime) -> None:
    """Placeholder for the real routine invocation. Wiring the D-0021
    routine into `run_paper_session` is intentionally deferred until
    the Controller approves live scheduling on APPLY. Until then, we
    log and let the operator decide when to swap in the real call."""

    ts = scheduled_at.strftime("%Y-%m-%d %H:%M %Z")
    print(f"[scheduler] D-0021 slot {ts} -- live routine not yet "
          f"wired (paper-only per D-0002)", flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="use a no-op job instead of the real routine")
    args = p.parse_args()

    from scheduler.daemon import ScheduledJob, SchedulerDaemon
    from scheduler.next_fire import (
        ScheduleSpec, WEEKDAYS_MON_TO_FRI,
    )

    d0021_spec = ScheduleSpec(
        tz=ZoneInfo("America/New_York"),
        times_of_day=tuple(time(h, 30) for h in range(9, 16)),
        weekdays=WEEKDAYS_MON_TO_FRI,
    )
    fn = _dry_run_slot if args.dry_run else _real_slot
    job = ScheduledJob(name="d0021-paper-monitor", spec=d0021_spec, fn=fn)

    daemon = SchedulerDaemon(jobs=[job])
    print(f"[scheduler] starting; job='{job.name}' "
          f"times={[t.isoformat() for t in d0021_spec.times_of_day]} "
          f"tz={d0021_spec.tz.key} weekdays=Mon-Fri", flush=True)
    print(f"[scheduler] SIGINT/SIGTERM = graceful shutdown", flush=True)
    try:
        daemon.run_forever()
    except KeyboardInterrupt:
        pass
    print(f"[scheduler] stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
