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
class SymbolScore:
    """One symbol's full evaluation inside one scheduled check.

    D-0082. Exists because `cycle_metrics` stores the cycle TOTAL only,
    and on 2026-10-06 that was not enough to establish why five cycles
    of ten symbols produced zero proposals: the total says nobody
    reached 60, and nothing else.

    `components` is the evaluator's `score_breakdown` verbatim. An
    ABSENT key and a 0.0 value are different facts and are stored
    differently (NULL vs 0.0), because a hard-filtered symbol gets an
    empty breakdown while a scored symbol can legitimately earn zero.

    `sources_succeeded` / `sources_failed` are what make a 0.0
    readable at all -- `trade_evaluator._score_fundamentals` documents
    its own ambiguity ("0 if no data"), so the component alone cannot
    say whether the data was missing or bad.
    """

    symbol: str
    rank_in_cycle: int
    soft_score: float
    passed_hard_filter: bool
    hard_filter_reasons: tuple
    components: dict
    sources_succeeded: tuple
    sources_failed: tuple
    current_price: Optional[float]
    rsi_14: Optional[float]
    day_volume: Optional[float]
    pe_ratio: Optional[float]


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
    symbols: tuple = ()
    """D-0082: one SymbolScore per evaluated symbol, in ranked order.
    Empty on the unscored (evaluator-failure) path."""

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


_SYMBOL_SQL = (
    "INSERT OR REPLACE INTO cycle_symbol_scores ("
    "  effective_date, scheduled_slot, symbol, cycle_at, rank_in_cycle,"
    "  soft_score, passed_hard_filter, hard_filter_reasons,"
    "  s_fundamentals, s_technicals, s_momentum, s_news, s_trend,"
    "  s_rel_strength, s_political, s_risk_discount,"
    "  sources_succeeded, sources_failed, current_price, rsi_14,"
    "  day_volume, pe_ratio"
    ") VALUES (" + ",".join(["?"] * 22) + ")"
)

_COMPONENT_KEYS = ("fundamentals", "technicals", "momentum", "news",
                   "trend", "rel_str", "political", "risk")
"""The keys trade_evaluator puts in score_breakdown. A key that is
ABSENT is written as NULL, not 0.0 -- see SymbolScore."""


def record_symbol_scores(conn: sqlite3.Connection, m: CycleMetrics) -> None:
    """One row per evaluated symbol for this scheduled check."""
    if not m.symbols:
        return
    slot = scheduled_slot_for(m.cycle_at)
    day = m.effective_date.isoformat()
    at = m.cycle_at.isoformat()
    rows = []
    for s in m.symbols:
        comps = [None if k not in s.components else float(s.components[k])
                 for k in _COMPONENT_KEYS]
        rows.append((
            day, slot, s.symbol, at, int(s.rank_in_cycle),
            float(s.soft_score), 1 if s.passed_hard_filter else 0,
            "; ".join(s.hard_filter_reasons) or None,
            *comps,
            ",".join(s.sources_succeeded) or None,
            ",".join(s.sources_failed) or None,
            None if s.current_price is None else float(s.current_price),
            None if s.rsi_14 is None else float(s.rsi_14),
            None if s.day_volume is None else float(s.day_volume),
            None if s.pe_ratio is None else float(s.pe_ratio),
        ))
    conn.executemany(_SYMBOL_SQL, rows)


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
            record_symbol_scores(conn, m)
        except Exception as exc:  # noqa: BLE001 - never break a cycle
            print(f"[cycle-metrics] not recorded: "
                  f"{type(exc).__name__}: {exc}", flush=True)

    return _record
