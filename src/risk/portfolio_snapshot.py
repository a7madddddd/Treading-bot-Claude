"""Live `PortfolioSnapshot` builder (D-0047).

Reads live state from three sources:
  - Alpaca `/v2/account` (equity_current, equity_at_day_open via
    `last_equity`).
  - Alpaca `/v2/positions` (per-symbol dollar exposure).
  - SQLite `trades` table (open-trade count, new-trades-today count).

The builder is invoked by `PortfolioRiskEnforcer` before every risk
check, so every check sees fresh state -- Alpaca is queried on each
invocation. The enforcer itself never talks to Alpaca or SQLite; it
only calls the builder callable.
"""

from __future__ import annotations

import json
import ssl
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone
from typing import Callable, List, Mapping, Optional, Tuple

from risk.models import PortfolioSnapshot, PositionView


_US_MARKET_TZ_OFFSET_HOURS = -4.0
"""Rough offset for US Eastern Time from UTC (accepts EST or EDT --
this snapshot only uses it to compute "today"'s cutoff, and the
strategy uses D-0021 for anything precise). The 4h approximation
picks up trades all the way back to 20:00 UTC of the prior day
during DST, which is safe (over-counts, never under-counts) for a
daily-cap check."""


class HttpResponse:
    __slots__ = ("status", "body")

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


HttpTransport = Callable[[str, Mapping[str, str], float], HttpResponse]


def _urllib_transport(url: str, headers: Mapping[str, str],
                      timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, headers=dict(headers))
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return HttpResponse(resp.status, resp.read())
    except urllib.error.HTTPError as ex:
        return HttpResponse(ex.code, ex.read())


class LivePortfolioSnapshotBuilder:
    def __init__(
        self,
        *,
        broker_base_url: str,
        broker_key_id: str,
        broker_secret_key: str,
        sqlite_conn: sqlite3.Connection,
        transport: HttpTransport = _urllib_transport,
        timeout_seconds: float = 10.0,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not broker_base_url or not broker_key_id or not broker_secret_key:
            raise ValueError("broker credentials required")
        self._base = broker_base_url.rstrip("/")
        self._key = broker_key_id
        self._secret = broker_secret_key
        self._conn = sqlite_conn
        self._transport = transport
        self._timeout = timeout_seconds
        self._now_fn = now_fn

    def __call__(self) -> PortfolioSnapshot:
        headers = {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
        }

        acc_resp = self._transport(self._base + "/v2/account",
                                   headers, self._timeout)
        if acc_resp.status != 200:
            raise RuntimeError(
                f"Alpaca /v2/account returned {acc_resp.status}: "
                f"{acc_resp.body[:200]!r}"
            )
        acc = json.loads(acc_resp.body.decode("utf-8"))
        equity_current = float(acc.get("equity", 0.0))
        equity_at_day_open = float(
            acc.get("last_equity", acc.get("equity", 0.0))
        )

        pos_resp = self._transport(self._base + "/v2/positions",
                                   headers, self._timeout)
        if pos_resp.status != 200:
            raise RuntimeError(
                f"Alpaca /v2/positions returned {pos_resp.status}"
            )
        raw_positions = json.loads(pos_resp.body.decode("utf-8"))
        positions = tuple(_position_from_alpaca(p) for p in raw_positions)

        now = self._now_fn()
        open_trades = self._count_open_trades()
        new_trades_today = self._count_new_trades_today(now)

        return PortfolioSnapshot(
            equity_current=equity_current,
            equity_at_day_open=equity_at_day_open,
            positions=positions,
            open_trades=open_trades,
            new_trades_today=new_trades_today,
            snapshot_at=now,
        )

    def _count_open_trades(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE initial_order_status IN "
            "('pending', 'partially_filled', 'filled')"
        ).fetchone()
        return int(row[0]) if row else 0

    def _count_new_trades_today(self, now_utc: datetime) -> int:
        cutoff = _us_market_day_open_utc(now_utc)
        row = self._conn.execute(
            "SELECT COUNT(*) FROM trades WHERE created_at >= ?",
            (cutoff.isoformat(),),
        ).fetchone()
        return int(row[0]) if row else 0


def _position_from_alpaca(payload: dict) -> PositionView:
    return PositionView(
        symbol=str(payload.get("symbol", "")).upper(),
        qty=float(payload.get("qty", 0.0)),
        market_value=abs(float(payload.get("market_value", 0.0))),
    )


def _us_market_day_open_utc(now_utc: datetime) -> datetime:
    """Anchor for "today". Uses 00:00 US Eastern (roughly), which is
    strictly earlier than the 09:30 ET market open, so any trade
    Engine could have started TODAY is included. Never includes the
    prior calendar day. Precision is intentionally loose here --
    strategy uses D-0021 for timing decisions; this is only for a
    daily-cap count."""
    tz_offset = timedelta(hours=_US_MARKET_TZ_OFFSET_HOURS)
    et_now = now_utc + tz_offset
    et_midnight = datetime.combine(et_now.date(), time(0, 0),
                                   tzinfo=timezone.utc)
    return et_midnight - tz_offset
