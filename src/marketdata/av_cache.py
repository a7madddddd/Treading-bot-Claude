"""SQLite-backed TTL cache for Alpha Vantage responses (D-0050 Phase 11).

Why:
  AV free tier = 5 requests/minute, 500/day. Ranking 3 symbols means
  15 AV requests per cycle, which saturates the limit. Daily indicators
  (RSI, MACD, BBANDS, SMA50, SMA200) only change ONCE per trading day,
  so caching them for a few hours is safe and eliminates the rate-
  limit problem for repeated rankings on the same day.

Design:
  - One SQLite file (default: ./av_cache.sqlite, gitignored).
  - Row shape: (cache_key, json_payload, cached_at ISO timestamp).
  - TTL check at read time (default 6 hours for daily indicators).
  - All reads/writes fail-open: a corrupt cache or missing file never
    raises; the caller just makes the fresh API call.
  - Thread-safe via connection-per-call (ok at our volume).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS av_cache (
    cache_key TEXT PRIMARY KEY,
    payload   TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_av_cache_cached_at ON av_cache(cached_at);
"""


class AVCache:
    """Minimal TTL cache. Methods never raise."""

    def __init__(self, db_path: str = "./av_cache.sqlite",
                 default_ttl_seconds: float = 6 * 3600.0) -> None:
        self._path = db_path
        self._ttl = default_ttl_seconds
        self._lock = threading.Lock()
        self._bootstrap()

    def _bootstrap(self) -> None:
        try:
            with self._lock, sqlite3.connect(self._path, timeout=5.0) as c:
                c.executescript(_SCHEMA)
        except Exception:  # noqa: BLE001
            pass

    def get(self, key: str) -> Optional[object]:
        """Returns the cached python object (JSON-decoded) or None if
        missing / expired / corrupt."""
        try:
            with self._lock, sqlite3.connect(self._path, timeout=5.0) as c:
                row = c.execute(
                    "SELECT payload, cached_at FROM av_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
        except Exception:  # noqa: BLE001
            return None
        if not row:
            return None
        payload, cached_at_s = row
        try:
            cached_at = datetime.fromisoformat(cached_at_s)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            if age > self._ttl:
                return None
            return json.loads(payload)
        except Exception:  # noqa: BLE001
            return None

    def set(self, key: str, value: object) -> None:
        """Stores a JSON-encodable value. Any error is swallowed."""
        try:
            text = json.dumps(value, default=_json_default)
        except (TypeError, ValueError):
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock, sqlite3.connect(self._path, timeout=5.0) as c:
                c.execute(
                    "INSERT OR REPLACE INTO av_cache (cache_key, payload, cached_at) "
                    "VALUES (?, ?, ?)",
                    (key, text, now),
                )
        except Exception:  # noqa: BLE001
            pass

    def prune(self) -> int:
        """Removes expired rows. Returns count of removed rows.
        Safe to call; failure returns 0."""
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - self._ttl
            with self._lock, sqlite3.connect(self._path, timeout=5.0) as c:
                cur = c.execute(
                    "DELETE FROM av_cache WHERE "
                    "CAST(strftime('%s', cached_at) AS INTEGER) < ?",
                    (int(cutoff),),
                )
                return cur.rowcount or 0
        except Exception:  # noqa: BLE001
            return 0


def _json_default(obj):
    """Converts non-JSON-native types (date, datetime, tuple) for
    storage. The CachedAlphaVantageSource adapter re-materializes
    them on load."""
    import datetime as _dt
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def make_cache_key(symbol: str, function: str, **params) -> str:
    """Deterministic cache key for an AV call."""
    import hashlib
    p_sorted = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    base = f"AV:{symbol}:{function}:{p_sorted}"
    # Short digest avoids SQLite key explosions on long param strings
    return base[:160] + ":" + hashlib.sha1(base.encode()).hexdigest()[:12]
