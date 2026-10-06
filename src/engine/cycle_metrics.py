"""D-0079: record what each watchlist evaluation cycle actually saw.

WHAT THIS IS FOR
----------------
The engine proposes at most `_TOP_N_PER_CYCLE` (3) symbols per cycle,
from those scoring `>= _MIN_SCORE` (60). Nobody knows whether that
ceiling has ever bound. If a typical cycle has three or four symbols
above 60, the cap has never cost a single trade. If some cycles have
forty, it has.

The Controller asked for the count so the question is settled from
recorded data instead of an opinion. That is this module's entire job.

WHAT THIS IS NOT
----------------
It is not a control input. No production code reads these rows and no
decision branches on them. Writing a row can never change a proposal,
a price, a limit or an order -- and `record_cycle_metrics` swallows
every exception for exactly that reason: a metrics table must never be
able to stop the engine from trading or, worse, from protecting an open
position.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from engine.schedule import D0021_CHECK_TIMES_ET, D0021_TIMEZONE_ET


def scheduled_slot_for(now: datetime) -> str:
    """Which of D-0021's seven scheduled checks this firing belongs to,
    as "HH:MM" in America/New_York.

    This is the grouping key, and it exists because of a measured fact:
    the engine loop ticks every 30s and `is_d0021_check_time()` accepts
    a +/-90s window, so ONE scheduled check calls `run_trigger_check`
    SEVEN times. Keying rows on the instant stored 49 rows a day for 7
    real checks and inflated every count and average by 7x.

    The nearest scheduled time wins. A firing outside every window
    cannot normally reach here (the caller only runs on a check), but
    if one ever does it is still attributed to its closest slot rather
    than silently dropped or crashing.
    """
    et = now.astimezone(D0021_TIMEZONE_ET)
    best = min(
        D0021_CHECK_TIMES_ET,
        key=lambda t: abs(
            (et.replace(hour=t.hour, minute=t.minute,
                        second=0, microsecond=0) - et).total_seconds()
        ),
    )
    return f"{best.hour:02d}:{best.minute:02d}"


@dataclass(frozen=True)
class CycleMetrics:
    """One watchlist evaluation cycle, as it happened."""

    cycle_at: datetime
    effective_date: date
    candidates_evaluated: int
    rejected_hard_filter: Optional[int]
    above_min_score: Optional[int]
    min_score_required: float
    best_score: Optional[float]
    proposals_created: int
    scored: bool = True
    """False when the candidates were never scored -- the
    evaluator-failure fallback. Then `rejected_hard_filter`,
    `above_min_score` and `best_score` are all None, and
    `proposals_created` still carries the real number of trades the
    fallback opened."""


def record_cycle_metrics(conn: sqlite3.Connection, m: CycleMetrics) -> None:
    """Writes the row for this SCHEDULED check.

    `INSERT OR REPLACE` on (effective_date, scheduled_slot): one
    scheduled check fires seven times inside its +/-90s window, and
    these are the same check seen seven times, not seven cycles. The
    last firing wins -- the freshest view of that check.

    NO conn.commit() HERE. The connection is opened with
    isolation_level=None (autocommit), so the INSERT is already durable
    on return. Calling commit() would be a no-op today but would commit
    a CALLER's open `transaction()` block the moment this is ever
    invoked from inside one -- turning a metrics write into a premature
    commit of someone else's half-finished trade state.
    """
    conn.execute(
        "INSERT OR REPLACE INTO cycle_metrics ("
        "  effective_date, scheduled_slot, cycle_at,"
        "  candidates_evaluated, rejected_hard_filter, above_min_score,"
        "  min_score_required, best_score, proposals_created, scored"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            m.effective_date.isoformat(),
            scheduled_slot_for(m.cycle_at),
            m.cycle_at.isoformat(),
            int(m.candidates_evaluated),
            None if m.rejected_hard_filter is None
            else int(m.rejected_hard_filter),
            None if m.above_min_score is None else int(m.above_min_score),
            float(m.min_score_required),
            None if m.best_score is None else float(m.best_score),
            int(m.proposals_created),
            1 if m.scored else 0,
        ),
    )


def make_recorder(conn: sqlite3.Connection):
    """Returns a recorder that never raises.

    The engine calls this on every cycle, including cycles that end in
    a protective Floor evaluation. An exception escaping here -- a
    locked database, a missing table on an un-migrated file, a disk
    full -- would abort the cycle and leave open positions unchecked.
    A lost metrics row costs one data point; a lost cycle can cost a
    position. The asymmetry decides it.
    """

    def _record(m: CycleMetrics) -> None:
        try:
            record_cycle_metrics(conn, m)
        except Exception as exc:  # noqa: BLE001 - never break a cycle
            print(f"[cycle-metrics] not recorded: "
                  f"{type(exc).__name__}: {exc}", flush=True)

    return _record
