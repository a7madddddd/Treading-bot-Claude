-- 0001_initial.sql -- approved persistence schema (design reviewed and
-- approved by the Controller). No strategy/D-0007/Option E business
-- logic is expressed here -- every CHECK below is a basic data-shape
-- guard (enum membership, non-negativity), never a trading rule.
-- active_floor_price/active_floor_source are deliberately NOT columns
-- anywhere -- always derived in Python, never persisted. Trade carries
-- no `status` column -- describe_status() is a pure, non-persisted
-- function. No client_order_id/order_attempts table -- deferred.

CREATE TABLE trades (
    trade_id                        TEXT PRIMARY KEY,
    symbol                          TEXT NOT NULL,
    created_at                      TEXT NOT NULL,

    initial_order_id                TEXT,
    initial_order_status            TEXT NOT NULL DEFAULT 'pending'
        CHECK (initial_order_status IN ('pending','partially_filled','filled','cancelled','expired')),
    initial_order_reconciled        INTEGER NOT NULL DEFAULT 0 CHECK (initial_order_reconciled IN (0,1)),
    original_initial_entry_fill_price REAL CHECK (original_initial_entry_fill_price IS NULL OR original_initial_entry_fill_price > 0),
    initial_filled_shares           INTEGER CHECK (initial_filled_shares IS NULL OR initial_filled_shares >= 0),
    freeze_timestamp                TEXT,

    ladder1_price                   REAL,
    ladder2_price                   REAL,
    original_floor_price            REAL,

    ladder1_proposal_id             TEXT,
    ladder1_filled                  INTEGER NOT NULL DEFAULT 0 CHECK (ladder1_filled IN (0,1)),
    ladder1_fill_order_id           TEXT,
    ladder1_fill_price              REAL,
    ladder1_fill_qty                INTEGER,

    ladder2_proposal_id             TEXT,
    ladder2_filled                  INTEGER NOT NULL DEFAULT 0 CHECK (ladder2_filled IN (0,1)),
    ladder2_fill_order_id           TEXT,
    ladder2_fill_price              REAL,
    ladder2_fill_qty                INTEGER,

    total_shares                    INTEGER NOT NULL DEFAULT 0 CHECK (total_shares >= 0),
    weighted_avg_entry_price        REAL,

    trailing_activated              INTEGER NOT NULL DEFAULT 0 CHECK (trailing_activated IN (0,1)),
    trailing_current_threshold      REAL,
    trailing_floor_price            REAL,
    trailing_last_updated_at        TEXT,

    protective_order_id             TEXT,
    protective_order_stop_price     REAL,
    protective_order_status         TEXT,

    last_broker_poll_at             TEXT,
    last_reconciled_at              TEXT,
    last_error                      TEXT,

    revision                        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_trades_symbol ON trades(symbol);

CREATE TABLE proposals (
    proposal_id                     TEXT PRIMARY KEY,
    trade_id                        TEXT NOT NULL REFERENCES trades(trade_id),
    proposed_action                 TEXT NOT NULL CHECK (proposed_action IN ('initial_entry','ladder_1','ladder_2')),
    symbol                          TEXT NOT NULL,
    candidate_source                TEXT NOT NULL,

    current_price_at_proposal       REAL NOT NULL,
    proposed_entry                  REAL NOT NULL,
    ladder_1_trigger                REAL NOT NULL,
    ladder_1_quantity               INTEGER NOT NULL,
    ladder_2_trigger                REAL NOT NULL,
    ladder_2_quantity               INTEGER NOT NULL,
    floor_trigger                   REAL NOT NULL,
    maximum_position                INTEGER NOT NULL,

    proposal_created_at             TEXT NOT NULL,
    weighted_avg_entry_at_proposal  REAL,
    active_floor_at_proposal        REAL,

    assumptions_json                TEXT NOT NULL,
    risks_json                      TEXT NOT NULL,

    approval_state                  TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_state IN ('pending','approved','rejected','expired')),
    approval_received_at            TEXT,
    approval_expires_at             TEXT,
    decided_by                      TEXT,
    approved_action                 TEXT CHECK (approved_action IS NULL OR approved_action IN ('initial_entry','ladder_1','ladder_2')),
    expired_at                      TEXT
);

CREATE INDEX ix_proposals_trade_action_state ON proposals(trade_id, proposed_action, approval_state);
CREATE INDEX ix_proposals_symbol ON proposals(symbol);

-- Backs "at most one live PENDING per (trade_id, proposed_action)" at
-- the DB level. Deliberately NOT applied to 'approved' -- multiple
-- APPROVED rows may legitimately coexist over time for the same pair;
-- only one is ever D-0007-valid at a time, which is a time-computed
-- fact enforced in Python (plan_save_supersession), never in SQL.
CREATE UNIQUE INDEX ux_proposals_one_live_pending
    ON proposals(trade_id, proposed_action)
    WHERE approval_state = 'pending';

CREATE TABLE trade_snapshots (
    id                               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id                         TEXT NOT NULL REFERENCES trades(trade_id),
    revision                         INTEGER NOT NULL,
    snapshot_at                      TEXT NOT NULL,
    transition                       TEXT NOT NULL,
    full_state_json                  TEXT NOT NULL,
    UNIQUE (trade_id, revision)
);

CREATE TABLE protective_order_history (
    id                                INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id                          TEXT NOT NULL REFERENCES trades(trade_id),
    sequence                          INTEGER NOT NULL,
    order_id                          TEXT NOT NULL,
    stop_price                        REAL NOT NULL CHECK (stop_price > 0),
    status                            TEXT NOT NULL,
    recorded_at                       TEXT NOT NULL,
    UNIQUE (trade_id, sequence)
);
