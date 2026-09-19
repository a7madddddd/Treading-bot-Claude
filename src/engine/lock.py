"""EngineLock -- the lightweight SQLite-based single-owner lock for the
Engine process (Controller-approved: "Use a lightweight SQLite-based
Engine lock. Do not introduce distributed locking or external
infrastructure.").

Approved timing (Controller-approved, this session):
    HEARTBEAT_INTERVAL_SECONDS = 30
    STALE_THRESHOLD_SECONDS = 300  (5 minutes, a 10x margin)

Design (Controller-approved):
    - A single-row table (`id INTEGER PRIMARY KEY CHECK (id = 1)`)
      bounds the table to at most one lock row ever.
    - Acquisition and staleness-steal both happen inside one
      `persistence.db.transaction()` (BEGIN IMMEDIATE ... COMMIT) --
      the SAME primitive every repository in this codebase already
      uses for read-then-conditionally-write correctness. This is what
      makes two processes racing to acquire/steal the lock safe: the
      second transaction's BEGIN IMMEDIATE blocks until the first
      commits, so it always observes the first's already-committed
      result rather than racing against a stale read.
    - Liveness is judged ONLY by `heartbeat_at` -- never by checking
      whether `pid` is still a running process. PID/host are retained
      as informational/debugging fields only, never load-bearing,
      because PID is only meaningful within one host's PID namespace
      and the eventual hosting choice (pre-apply-checklist B5) is
      still undecided -- a heartbeat-only design is portable regardless
      of where this ends up running.
    - No background thread updates the heartbeat (Controller-approved:
      "Do not introduce a background heartbeat thread... The heartbeat
      must be updated cooperatively from the Engine's normal execution
      points."). Callers (the Engine) are responsible for calling
      heartbeat() at the approved points in every cycle.
"""

from __future__ import annotations

import os
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from persistence.db import transaction

HEARTBEAT_INTERVAL_SECONDS = 30.0
STALE_THRESHOLD_SECONDS = 300.0


class EngineLockError(RuntimeError):
    """Base class for EngineLock errors."""


class EngineLockHeldError(EngineLockError):
    """Raised by acquire() when a live (non-stale) lock is already held
    by another owner -- a second Engine process must refuse to start."""


class EngineLockNotHeldError(EngineLockError):
    """Raised by heartbeat() when this instance does not currently hold
    the lock -- either acquire() was never called, or ownership was
    lost (the row is missing or now owned by a different pid), which
    must never be silently ignored: a heartbeat call that can't find
    its own lock row means something is structurally wrong and the
    Engine should stop, not keep running unprotected."""


@dataclass(frozen=True)
class EngineLockState:
    """A read-only snapshot of the current lock row, for
    diagnostics/logging -- never used by acquire()/heartbeat()'s own
    decision logic beyond what those methods already read directly."""

    pid: int
    host: str
    started_at: datetime
    heartbeat_at: datetime


class EngineLock:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        pid: Optional[int] = None,
        host: Optional[str] = None,
        stale_threshold_seconds: float = STALE_THRESHOLD_SECONDS,
    ) -> None:
        self._conn = conn
        self._pid = pid if pid is not None else os.getpid()
        self._host = host if host is not None else socket.gethostname()
        self._stale_threshold = timedelta(seconds=stale_threshold_seconds)
        self._held = False

    def acquire(self, *, now: datetime) -> None:
        """Acquires the lock, atomically, inside one BEGIN IMMEDIATE
        transaction: if no row exists, or the existing row's
        heartbeat_at is older than the stale threshold, this instance
        becomes the owner (stealing a stale row first deletes it).
        Raises EngineLockHeldError if a live row is found. Safe to call
        on a fresh EngineLock instance only -- calling it twice on the
        same instance without an intervening release() re-acquires
        under this same pid, which is harmless but not a supported
        pattern this class optimizes for."""

        with transaction(self._conn) as tconn:
            row = tconn.execute(
                "SELECT pid, host, started_at, heartbeat_at FROM engine_lock WHERE id = 1"
            ).fetchone()
            if row is not None:
                existing_heartbeat = datetime.fromisoformat(row[3])
                if now - existing_heartbeat <= self._stale_threshold:
                    raise EngineLockHeldError(
                        f"engine_lock already held by pid={row[0]} host={row[1]!r} "
                        f"(started_at={row[2]}, last heartbeat {row[3]}) -- refusing to "
                        "start a second Engine"
                    )
                tconn.execute("DELETE FROM engine_lock WHERE id = 1")
            tconn.execute(
                "INSERT INTO engine_lock (id, pid, host, started_at, heartbeat_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (self._pid, self._host, now.isoformat(), now.isoformat()),
            )
        self._held = True

    def heartbeat(self, *, now: datetime) -> None:
        """Updates heartbeat_at for the row owned by THIS instance's
        pid. Called cooperatively by the Engine at multiple points in
        every cycle (Controller-approved: cycle start, after each
        meaningful per-trade step, after reconciliation, before the
        cycle sleeps) -- never from a background thread."""

        if not self._held:
            raise EngineLockNotHeldError("cannot heartbeat -- this instance does not hold the lock")
        with transaction(self._conn) as tconn:
            row = tconn.execute("SELECT pid FROM engine_lock WHERE id = 1").fetchone()
            if row is None or row[0] != self._pid:
                raise EngineLockNotHeldError(
                    "lock row is missing or now owned by a different pid -- this instance "
                    "lost ownership (likely stolen as stale) and must stop"
                )
            tconn.execute("UPDATE engine_lock SET heartbeat_at = ? WHERE id = 1", (now.isoformat(),))

    def release(self) -> None:
        """Clean shutdown: deletes the row, but ONLY if it is still
        owned by this instance's pid -- never deletes a lock that was
        already stolen from it (e.g. after a long stall past the
        staleness threshold). Idempotent: calling release() when this
        instance never held the lock, or already released it, is a
        harmless no-op."""

        if not self._held:
            return
        with transaction(self._conn) as tconn:
            tconn.execute("DELETE FROM engine_lock WHERE id = 1 AND pid = ?", (self._pid,))
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def current_state(self) -> Optional[EngineLockState]:
        row = self._conn.execute(
            "SELECT pid, host, started_at, heartbeat_at FROM engine_lock WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return EngineLockState(
            pid=row[0],
            host=row[1],
            started_at=datetime.fromisoformat(row[2]),
            heartbeat_at=datetime.fromisoformat(row[3]),
        )
