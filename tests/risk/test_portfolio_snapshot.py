import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from typing import List, Mapping

from persistence.db import bootstrap_schema, connect
from risk.portfolio_snapshot import (
    HttpResponse, LivePortfolioSnapshotBuilder,
    _us_market_day_open_utc,
)


class _StubTransport:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "timeout": timeout})
        return self.mapping[url]


def _account_body(equity=50000.0, last_equity=50000.0):
    return HttpResponse(200, json.dumps({
        "status": "ACTIVE",
        "cash": str(equity),
        "equity": str(equity),
        "last_equity": str(last_equity),
        "buying_power": str(equity * 4),
        "portfolio_value": str(equity),
    }).encode())


def _positions_body(positions: List[dict]) -> HttpResponse:
    return HttpResponse(200, json.dumps(positions).encode())


class TestSnapshotBuilder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "s.sqlite")
        self.conn = connect(self.db)
        bootstrap_schema(self.conn)
        self.addCleanup(self.conn.close)

    def _insert_trade(self, trade_id, symbol, created_at_iso, status="filled"):
        self.conn.execute(
            "INSERT INTO trades (trade_id, symbol, created_at, "
            "initial_order_status) VALUES (?, ?, ?, ?)",
            (trade_id, symbol, created_at_iso, status),
        )

    def test_reads_equity_and_positions(self):
        base = "https://paper-api.alpaca.markets"
        transport = _StubTransport({
            base + "/v2/account": _account_body(equity=50000.0,
                                                last_equity=48000.0),
            base + "/v2/positions": _positions_body([
                {"symbol": "TSLA", "qty": "10", "market_value": "3700"},
                {"symbol": "AAPL", "qty": "8", "market_value": "1960"},
            ]),
        })
        builder = LivePortfolioSnapshotBuilder(
            broker_base_url=base, broker_key_id="k", broker_secret_key="s",
            sqlite_conn=self.conn, transport=transport,
        )
        snap = builder()
        self.assertAlmostEqual(snap.equity_current, 50000.0)
        self.assertAlmostEqual(snap.equity_at_day_open, 48000.0)
        self.assertEqual(len(snap.positions), 2)
        self.assertAlmostEqual(snap.gross_exposure(), 3700.0 + 1960.0)
        self.assertAlmostEqual(snap.exposure_for_symbol("TSLA"), 3700.0)

    def test_counts_open_trades(self):
        now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        self._insert_trade("t1", "TSLA", now.isoformat(), status="pending")
        self._insert_trade("t2", "AAPL", now.isoformat(),
                           status="partially_filled")
        self._insert_trade("t3", "SPY", now.isoformat(), status="filled")
        self._insert_trade("t4", "F", now.isoformat(), status="cancelled")
        base = "https://paper-api.alpaca.markets"
        transport = _StubTransport({
            base + "/v2/account": _account_body(),
            base + "/v2/positions": _positions_body([]),
        })
        builder = LivePortfolioSnapshotBuilder(
            broker_base_url=base, broker_key_id="k", broker_secret_key="s",
            sqlite_conn=self.conn, transport=transport, now_fn=lambda: now,
        )
        snap = builder()
        # pending + partially_filled + filled = 3; cancelled excluded
        self.assertEqual(snap.open_trades, 3)

    def test_counts_new_trades_today(self):
        now = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)  # 16:00 ET
        # 5 UTC on 2026-09-27 is 01:00 ET -> today's midnight ET
        yesterday = (now - timedelta(days=1)).isoformat()
        today_early = datetime(2026, 9, 27, 5, 0,
                               tzinfo=timezone.utc).isoformat()
        today_now = now.isoformat()
        self._insert_trade("t_y", "AAPL", yesterday, status="filled")
        self._insert_trade("t_a", "TSLA", today_early, status="filled")
        self._insert_trade("t_b", "SPY", today_now, status="filled")
        base = "https://paper-api.alpaca.markets"
        transport = _StubTransport({
            base + "/v2/account": _account_body(),
            base + "/v2/positions": _positions_body([]),
        })
        builder = LivePortfolioSnapshotBuilder(
            broker_base_url=base, broker_key_id="k", broker_secret_key="s",
            sqlite_conn=self.conn, transport=transport, now_fn=lambda: now,
        )
        snap = builder()
        self.assertEqual(snap.new_trades_today, 2)

    def test_alpaca_error_raises(self):
        base = "https://paper-api.alpaca.markets"
        transport = _StubTransport({
            base + "/v2/account": HttpResponse(401, b'{"error":"bad key"}'),
            base + "/v2/positions": _positions_body([]),
        })
        builder = LivePortfolioSnapshotBuilder(
            broker_base_url=base, broker_key_id="k", broker_secret_key="s",
            sqlite_conn=self.conn, transport=transport,
        )
        with self.assertRaises(RuntimeError):
            builder()


class TestMarketDayOpen(unittest.TestCase):
    def test_utc_night_before_still_maps_to_next_day(self):
        # 2026-09-27 03:00 UTC = 2026-09-26 23:00 ET -> anchor is
        # 2026-09-26 midnight ET (= 04:00 UTC on 26th).
        now_utc = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        anchor = _us_market_day_open_utc(now_utc)
        self.assertEqual(anchor.date(), datetime(2026, 9, 26).date())


if __name__ == "__main__":
    unittest.main()
