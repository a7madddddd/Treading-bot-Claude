-- Migration 0006 (B22): approved-universe-snapshot persistence.
-- One row per D-0048 pipeline run. Snapshots are immutable.
-- Symbol details and metadata live in JSON columns to keep the
-- schema narrow.

CREATE TABLE universe_snapshots (
    snapshot_id               TEXT PRIMARY KEY,
    effective_trading_date    TEXT NOT NULL,
    snapshot_at               TEXT NOT NULL,
    selection_version         TEXT NOT NULL,
    universe_source_version   TEXT NOT NULL,
    identity_mapping_version  TEXT NOT NULL,
    regime_label              TEXT NOT NULL,
    is_empty                  INTEGER NOT NULL CHECK (is_empty IN (0, 1)),
    empty_reason              TEXT,
    symbols_json              TEXT NOT NULL,
    rejection_summary_json    TEXT NOT NULL,
    concentration_json        TEXT NOT NULL,
    data_quality_json         TEXT NOT NULL
);

CREATE INDEX idx_universe_snapshots_effective_date
    ON universe_snapshots (effective_trading_date, snapshot_at DESC);
