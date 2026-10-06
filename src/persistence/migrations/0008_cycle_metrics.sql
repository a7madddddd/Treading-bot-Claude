-- Migration 0008 (D-0079): one row per watchlist evaluation cycle.
--
-- Purpose, in the Controller's words: "we need to make our calculation
-- for something real, something we already know." The open question is
-- whether the 3-proposals-per-cycle ceiling ever actually binds -- i.e.
-- whether a day exists where many symbols clear the score bar and the
-- cap is what stops them, or whether only two or three ever clear it
-- and the cap has never cost anything. That question cannot be settled
-- from the current data, because the count was never recorded.
--
-- This table records it. It is append-only, additive, and read by
-- nothing: no code branches on these rows. They exist to be queried by
-- the Controller after a month of real cycles, and they ride the daily
-- database backup to git.
--
-- `above_min_score` is the column the question turns on.

CREATE TABLE cycle_metrics (
    cycle_at              TEXT    NOT NULL,
    effective_date        TEXT    NOT NULL,
    candidates_evaluated  INTEGER NOT NULL,
    rejected_hard_filter  INTEGER NOT NULL,
    above_min_score       INTEGER NOT NULL,
    min_score_required    REAL    NOT NULL,
    best_score            REAL,
    proposals_created     INTEGER NOT NULL,
    PRIMARY KEY (cycle_at, effective_date)
);

CREATE INDEX idx_cycle_metrics_date ON cycle_metrics (effective_date);
