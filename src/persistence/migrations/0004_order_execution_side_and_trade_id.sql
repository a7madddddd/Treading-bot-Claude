-- 0004_order_execution_side_and_trade_id.sql -- generalizes
-- order_executions to represent EITHER a proposal-anchored BUY
-- execution (Initial Entry/Ladder 1/Ladder 2, unchanged) OR a
-- proposal-independent protective SELL execution (Floor, new).
--
-- SQLite cannot relax a NOT NULL/FK constraint on an existing column
-- in place, so the table is recreated: proposal_id becomes nullable
-- (SQLite's UNIQUE constraint allows multiple NULLs, so per-proposal
-- uniqueness for BUY rows is preserved while multiple SELL rows
-- across different trades, each with proposal_id NULL, remain valid),
-- trade_id is added as the real, always-present anchor, and side
-- records which direction this execution is. Every existing row is a
-- BUY execution (this schema predates Floor entirely), so the copy
-- backfills side='buy' and derives trade_id from the existing
-- proposals join -- never invented, always read from the real
-- proposals table.

CREATE TABLE order_executions_new (
    execution_id         TEXT PRIMARY KEY,
    proposal_id           TEXT UNIQUE REFERENCES proposals(proposal_id),
    trade_id              TEXT NOT NULL REFERENCES trades(trade_id),
    client_order_id       TEXT NOT NULL UNIQUE,
    side                  TEXT NOT NULL DEFAULT 'buy' CHECK (side IN ('buy', 'sell')),
    broker_order_id       TEXT UNIQUE,
    requested_qty         INTEGER NOT NULL CHECK (requested_qty > 0),
    filled_qty            INTEGER NOT NULL DEFAULT 0 CHECK (filled_qty >= 0),
    filled_avg_price      REAL CHECK (filled_avg_price IS NULL OR filled_avg_price > 0),
    status                TEXT NOT NULL,
    is_broker_terminal    INTEGER NOT NULL DEFAULT 0 CHECK (is_broker_terminal IN (0,1)),
    created_at            TEXT NOT NULL,
    last_broker_poll_at   TEXT,

    revision              INTEGER NOT NULL DEFAULT 0
);

INSERT INTO order_executions_new (
    execution_id, proposal_id, trade_id, client_order_id, side,
    broker_order_id, requested_qty, filled_qty, filled_avg_price,
    status, is_broker_terminal, created_at, last_broker_poll_at, revision
)
SELECT
    oe.execution_id, oe.proposal_id, p.trade_id, oe.client_order_id, 'buy',
    oe.broker_order_id, oe.requested_qty, oe.filled_qty, oe.filled_avg_price,
    oe.status, oe.is_broker_terminal, oe.created_at, oe.last_broker_poll_at, oe.revision
FROM order_executions oe
JOIN proposals p ON p.proposal_id = oe.proposal_id;

DROP TABLE order_executions;

ALTER TABLE order_executions_new RENAME TO order_executions;
