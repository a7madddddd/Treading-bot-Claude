-- 0003_engine_lock.sql -- single-row Engine process lock (Controller-
-- approved: "lightweight SQLite-based Engine lock", see
-- src/engine/lock.py). CHECK (id = 1) bounds this table to at most one
-- row ever, which is what makes a plain INSERT atomic for the "no one
-- holds it yet" case. The "steal a stale lock" case additionally wraps
-- the read-check-and-replace in a single BEGIN IMMEDIATE transaction
-- (see persistence/db.py's transaction()), not expressed in this
-- schema. pid/host are informational/debugging fields only -- liveness
-- is judged solely by heartbeat_at, never by PID-liveness checking.

CREATE TABLE engine_lock (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    pid           INTEGER NOT NULL,
    host          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    heartbeat_at  TEXT NOT NULL
);
