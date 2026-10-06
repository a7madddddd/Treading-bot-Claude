-- Migration 0008 (D-0079): one row per SCHEDULED watchlist evaluation.
--
-- Purpose, in the Controller's words: "we need to make our calculation
-- for something real, something we already know." The open question is
-- whether the 3-proposals-per-cycle ceiling ever actually binds -- i.e.
-- whether a day exists where many symbols clear the score bar and the
-- cap is what stops them, or whether only two or three ever clear it
-- and the cap has never cost anything. That question cannot be settled
-- from the current data, because the count was never recorded.
--
-- `above_min_score` is the column the question turns on.
--
-- Append-only and read by nothing: no production code branches on
-- these rows. They exist to be queried by the Controller after a month
-- of real cycles, and they ride the daily database backup to git.
--
-- ONE ROW PER SCHEDULED CHECK, NOT PER FIRING. D-0021 schedules seven
-- checks a day (09:30..15:30 ET). The engine's loop ticks every 30s
-- and is_d0021_check_time() accepts a +/-90s window, so ONE scheduled
-- check fires SEVEN times -- measured, not assumed. Keying on the
-- instant would therefore store 49 rows a day for 7 real checks and
-- inflate every count and average by 7x. The key is
-- (effective_date, scheduled_slot) so the seven firings collapse into
-- one row, last write winning, which is the freshest view of that
-- check.

CREATE TABLE cycle_metrics (
    effective_date        TEXT    NOT NULL,
    scheduled_slot        TEXT    NOT NULL,
    cycle_at              TEXT    NOT NULL,
    candidates_evaluated  INTEGER NOT NULL,
    -- NULLABLE ON PURPOSE. NULL means the candidates were never
    -- SCORED, which is a different fact from "scored and none passed".
    -- The evaluator-failure fallback in Engine._check_watchlist opens a
    -- trade for every candidate without scoring any of them, and
    -- writing 0 here would read as "nothing was good enough" while
    -- trades were in fact opened, silently under-reporting proposals in
    -- any per-day total. Query with `WHERE above_min_score IS NOT NULL`
    -- for only the cycles that actually scored.
    rejected_hard_filter  INTEGER,
    above_min_score       INTEGER,
    min_score_required    REAL    NOT NULL,
    best_score            REAL,
    proposals_created     INTEGER NOT NULL,
    scored                INTEGER NOT NULL CHECK (scored IN (0, 1)),

    PRIMARY KEY (effective_date, scheduled_slot),

    -- `scored` and the NULL score columns encode the same fact, so the
    -- database refuses a row where they disagree. Without this, a
    -- future caller could write scored=1 with a NULL count and the
    -- Controller's query would silently disagree with the flag.
    CHECK (
        (scored = 1 AND above_min_score IS NOT NULL
                    AND rejected_hard_filter IS NOT NULL)
        OR
        (scored = 0 AND above_min_score IS NULL
                    AND rejected_hard_filter IS NULL
                    AND best_score IS NULL)
    )
);
