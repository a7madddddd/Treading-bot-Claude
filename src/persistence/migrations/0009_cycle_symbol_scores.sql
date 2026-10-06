-- Migration 0009 (D-0082): the per-symbol score breakdown of every
-- evaluation cycle.
--
-- WHY THIS EXISTS, in one sentence: on 2026-10-06 five cycles each
-- evaluated ten symbols and produced zero proposals, and the cause
-- could not be established from the recorded data, because
-- cycle_metrics stores only the cycle TOTAL -- not which symbol scored
-- highest, not which of the seven components earned anything, and not
-- whether a zero meant "no data" or "scored zero".
--
-- Those three unknowns are exactly what turned five hours of
-- diagnosis into eight retracted conclusions. This table removes all
-- three.
--
-- ONE ROW PER SYMBOL PER SCHEDULED CHECK, not just the top one. The
-- question "why did nothing reach 60" is answered by the whole
-- distribution, not by its maximum: a day where the best is 54 and the
-- rest are 50 is a different problem from a day where the best is 54
-- and the rest are 0.
--
-- Append-only. Read by nothing, and no production code branches on it.
-- Keyed on (effective_date, scheduled_slot, symbol) so the seven
-- firings of one scheduled check collapse into one row per symbol,
-- same reasoning as cycle_metrics.
--
-- `sources_succeeded` / `sources_failed` are what make a zero
-- readable. src/engine/trade_evaluator.py documents the ambiguity in
-- its own code -- `_score_fundamentals` says "0 if no data" -- so a
-- component of 0.0 is indistinguishable from a genuine zero unless the
-- source list is stored beside it.

CREATE TABLE cycle_symbol_scores (
    effective_date       TEXT    NOT NULL,
    scheduled_slot       TEXT    NOT NULL,
    symbol               TEXT    NOT NULL,
    cycle_at             TEXT    NOT NULL,
    rank_in_cycle        INTEGER NOT NULL,
    soft_score           REAL    NOT NULL,
    passed_hard_filter   INTEGER NOT NULL CHECK (passed_hard_filter IN (0, 1)),
    hard_filter_reasons  TEXT,

    -- the seven weighted components, exactly as the evaluator emitted
    -- them. NULL means the evaluator did not report that component at
    -- all (a hard-filtered symbol gets an empty breakdown), which is a
    -- different fact from 0.0.
    s_fundamentals       REAL,
    s_technicals         REAL,
    s_momentum           REAL,
    s_news               REAL,
    s_trend              REAL,
    s_rel_strength       REAL,
    s_political          REAL,
    s_risk_discount      REAL,

    -- the inputs that decide whether a 0.0 above is "no data" or "bad"
    sources_succeeded    TEXT,
    sources_failed       TEXT,
    current_price        REAL,
    rsi_14               REAL,
    day_volume           REAL,
    pe_ratio             REAL,

    PRIMARY KEY (effective_date, scheduled_slot, symbol)
);
