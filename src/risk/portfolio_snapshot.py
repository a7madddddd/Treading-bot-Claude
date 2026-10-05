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
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Callable, List, Mapping, Optional, Tuple

from risk.models import PortfolioSnapshot, PositionView


_ET = ZoneInfo("America/New_York")
"""P-039 (2026-10-05). This was a hardcoded -4.0 hour offset with a
docstring claiming the approximation "over-counts, never under-counts"
for the daily-cap check. That claim held only during EDT. Under EST
(from 1 November 2026) the computed Eastern time runs an hour ahead of
the real one, so between 04:00 and 05:00 UTC the anchor lands on the
WRONG day -- and it lands LATER, not earlier.

Measured, not estimated. At 04:30 UTC on 2026-11-10 it is 23:30 ET on
the 9th, so the day being counted is the 9th and its true anchor is
2026-11-09 05:00 UTC. The old code anchored at 2026-11-10 04:00 UTC:
23 hours later, excluding essentially every trade actually opened that
Eastern day. The daily new-trade count then reads low and the D-0047
cap admits trades it should refuse -- the opposite of what the old
docstring promised.

The window is 23:00-00:00 ET, outside the session, so this was never an
active failure. It is fixed rather than re-documented because a risk
limit must not depend on the season.

ZoneInfo reads the system IANA database, with no network call on a risk
path."""


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
        retry_policy=None,  # Optional common.http_retry.RetryPolicy
    ) -> None:
        if not broker_base_url or not broker_key_id or not broker_secret_key:
            raise ValueError("broker credentials required")
        self._base = broker_base_url.rstrip("/")
        self._key = broker_key_id
        self._secret = broker_secret_key
        self._conn = sqlite_conn
        # Wrap with retry when supplied. B26 already provides the same
        # opt-in wrapping on the Alpaca broker/market-data clients;
        # this closes the "risk snapshot fails on transient 429/5xx"
        # gap the 2026-09-30 audit flagged as Bug #1.
        if retry_policy is not None:
            from common.http_retry import with_retry
            transport = with_retry(transport, policy=retry_policy)
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
        # A Trade row is created at proposal-generation time before
        # Controller approval, with initial_order_status='pending' by
        # default -- so filtering on that status alone (as the
        # pre-2026-09-30 code did) incorrectly counts proposal-shells
        # that have no broker order and no capital committed. Trade
        # itself has no field that reliably signals "submitted"
        # (initial_order_id is defined on the model but never written
        # anywhere in code -- see 2026-09-30 code review). The
        # authoritative signal for "this trade actually reached the
        # broker" lives in the order_executions table.
        #
        # "Open" here specifically means "currently ties up capital":
        #   - HAS an initial-entry buy execution that reached the
        #     broker (broker_order_id IS NOT NULL), AND
        #   - is not fully closed. A trade is fully closed when its
        #     initial-entry order has resolved (initial_filled_shares
        #     is NOT NULL) AND every share subsequently sold out
        #     (total_shares = 0). initial_order_status stays 'filled'
        #     after a Floor exit -- filtering only on status would
        #     wrongly count closed trades against the cap.
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT t.trade_id) "
            "FROM trades t "
            "JOIN order_executions e ON e.trade_id = t.trade_id "
            "WHERE e.side = 'buy' "
            "  AND e.broker_order_id IS NOT NULL "
            "  AND t.initial_order_status IN "
            "      ('pending', 'partially_filled', 'filled') "
            "  AND NOT ( "
            "    t.initial_filled_shares IS NOT NULL "
            "    AND t.total_shares = 0 "
            "  )"
        ).fetchone()
        return int(row[0]) if row else 0

    def _count_new_trades_today(self, now_utc: datetime) -> int:
        # Same fix rationale as _count_open_trades: only trades whose
        # initial-entry buy order reached the broker count as "new
        # trades opened today". A trade whose proposal never got past
        # Controller approval is not one that was opened.
        cutoff = _us_market_day_open_utc(now_utc)
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT t.trade_id) "
            "FROM trades t "
            "JOIN order_executions e ON e.trade_id = t.trade_id "
            "WHERE t.created_at >= ? "
            "  AND e.side = 'buy' "
            "  AND e.broker_order_id IS NOT NULL",
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
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    et_now = now_utc.astimezone(_ET)
    et_midnight = datetime.combine(et_now.date(), time(0, 0), tzinfo=_ET)
    return et_midnight.astimezone(timezone.utc)
