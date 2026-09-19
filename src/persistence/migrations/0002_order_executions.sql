-- 0002_order_executions.sql -- OrderExecution persistence (D-0024
-- continued). Every CHECK below is a basic data-shape guard, never a
-- trading/business rule -- matches 0001's own stated principle.
-- Complex cross-field invariants (e.g. "a local pre-broker status
-- must not carry a broker_order_id") are enforced entirely by
-- OrderExecution's own __post_init__, never duplicated here. `status`
-- deliberately has no CHECK -- it is either one of OrderExecution's
-- three local pre-broker constants or an arbitrary broker-reported
-- string, never a closed vocabulary this schema may enumerate.
-- No index -- this table is expected to stay small (few concurrently
-- open executions in a single-symbol paper system), mirroring the
-- same reasoning already accepted for trades.list_active().

CREATE TABLE order_executions (
    execution_id         TEXT PRIMARY KEY,
    proposal_id           TEXT NOT NULL UNIQUE REFERENCES proposals(proposal_id),
    client_order_id       TEXT NOT NULL UNIQUE,
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
