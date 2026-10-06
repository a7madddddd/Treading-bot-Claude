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
    """Appends one row. `INSERT OR REPLACE` because the primary key is
    (cycle_at, effective_date) and a retried cycle at the same instant
    is the same cycle, not a second one."""
    conn.execute(
        "INSERT OR REPLACE INTO cycle_metrics ("
        "  cycle_at, effective_date, candidates_evaluated,"
        "  rejected_hard_filter, above_min_score, min_score_required,"
        "  best_score, proposals_created"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            m.cycle_at.isoformat(),
            m.effective_date.isoformat(),
            int(m.candidates_evaluated),
            None if m.rejected_hard_filter is None
            else int(m.rejected_hard_filter),
            None if m.above_min_score is None else int(m.above_min_score),
            float(m.min_score_required),
            None if m.best_score is None else float(m.best_score),
            int(m.proposals_created),
        ),
    )
    conn.commit()


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
