#!/usr/bin/env python3
"""B16 Stage 3 — use-case trials for the eight D-0042 free calibration
data sources.

Runs one small read-only use case per source. Never prints, logs, or
otherwise reveals any API key value. Prints one line per source in the
shape:

    [PASS] <source>  | <concrete evidence, e.g. bar count and price range>
    [FAIL] <source>  | <reason class, never the key>

At the end, prints a compact "N/M PASS" summary and exits 0 iff
everything passed.

Design notes:
- stdlib only (urllib, json, ssl, csv, io); consistent with the rest
  of the project's zero-third-party discipline.
- Uses safe request construction: keys go into headers or query
  parameters via urllib.parse.urlencode, never through shell string
  interpolation. Error bodies are never printed; we extract just the
  HTTP status code and a short class label.
- The script itself is read-only against the network and never writes
  any file under src/, tests/, or docs/. Safe to run repeatedly.
- Polygon Flat Files S3 access is NOT probed here (per Controller
  option (c)); its three env vars' presence is reported as a note.
- Alpha Vantage requires body inspection to catch quota / bad-key
  cases (HTTP 200 is not proof of validity for that provider); we
  parse the JSON top-level keys and treat "Note", "Information", and
  "Error Message" as FAIL classes.
"""

from __future__ import annotations

import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple


HTTP_TIMEOUT = 20
DEFAULT_UA_BROWSER = "Mozilla/5.0 (compatible; TreadingBot/1.0)"
DEFAULT_UA_SEC = "Treading-bot-Claude research contact@example.com"


# --- HTTP helper ----------------------------------------------------


def http_get(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = HTTP_TIMEOUT,
) -> Tuple[int, bytes]:
    """Issues a GET and returns (status_code, body_bytes). Never prints
    anything; never raises on non-2xx (returns the status)."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as http_err:
        try:
            body = http_err.read()
        except Exception:  # noqa: BLE001
            body = b""
        return http_err.code, body
    except Exception as ex:  # noqa: BLE001
        # Network-level failure. Return a synthetic code so the caller
        # can classify it uniformly. Do not include the URL in the
        # error text (it may contain a query-param key).
        return -1, str(type(ex).__name__).encode("utf-8")


# --- Result rendering ----------------------------------------------


def emit_pass(source: str, evidence: str) -> None:
    print(f"[PASS] {source:<20} | {evidence}")


def emit_fail(source: str, reason: str) -> None:
    print(f"[FAIL] {source:<20} | {reason}")


def emit_skip(source: str, reason: str) -> None:
    print(f"[SKIP] {source:<20} | {reason}")


# --- Individual probes ---------------------------------------------


def probe_alpaca_sip() -> bool:
    key_id = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key_id or not secret:
        emit_fail("Alpaca SIP", "env: ALPACA_API_KEY_ID/SECRET missing")
        return False
    url = (
        "https://data.alpaca.markets/v2/stocks/AAPL/bars?"
        + urllib.parse.urlencode(
            {
                "timeframe": "1Day",
                "start": "2025-01-02",
                "end": "2025-01-08",
                "feed": "sip",
            }
        )
    )
    code, body = http_get(
        url,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
        },
    )
    if code != 200:
        emit_fail("Alpaca SIP", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
        bars = obj.get("bars", [])
    except Exception:  # noqa: BLE001
        emit_fail("Alpaca SIP", "JSON parse error")
        return False
    if not bars:
        emit_fail("Alpaca SIP", "no bars returned")
        return False
    closes = [b.get("c") for b in bars if isinstance(b.get("c"), (int, float))]
    if not closes:
        emit_fail("Alpaca SIP", "no numeric closes")
        return False
    emit_pass(
        "Alpaca SIP",
        f"{len(bars)} bars, first {bars[0]['t'][:10]}, close range "
        f"{min(closes):.2f}-{max(closes):.2f}",
    )
    return True


def probe_yahoo_v8() -> bool:
    # 2020-01-02 08:00 UTC → 2020-01-10 08:00 UTC
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?"
        + urllib.parse.urlencode(
            {"period1": "1577952000", "period2": "1578643200", "interval": "1d"}
        )
    )
    code, body = http_get(url, headers={"User-Agent": DEFAULT_UA_BROWSER})
    if code != 200:
        emit_fail("Yahoo v8 chart", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
        result = obj["chart"]["result"][0]
        ts = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception:  # noqa: BLE001
        emit_fail("Yahoo v8 chart", "JSON shape unexpected")
        return False
    closes = [c for c in closes if isinstance(c, (int, float))]
    if not closes:
        emit_fail("Yahoo v8 chart", "no numeric closes")
        return False
    emit_pass(
        "Yahoo v8 chart",
        f"{len(ts)} bars from 2020-01, close range {min(closes):.2f}-{max(closes):.2f}",
    )
    return True


def probe_cboe_vix() -> bool:
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    # CBOE serves via a 307 that curl follows automatically; urllib
    # does the same when we let it, but we need to allow redirects
    # explicitly by not disabling them (default is to follow).
    code, body = http_get(url, headers={"User-Agent": DEFAULT_UA_BROWSER})
    if code != 200 or not body:
        emit_fail("CBOE VIX", f"HTTP {code}")
        return False
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) < 8000:
        emit_fail("CBOE VIX", f"only {len(lines)} lines (<8000)")
        return False
    first_data = lines[1] if len(lines) > 1 else ""
    last_data = lines[-1] if lines else ""
    emit_pass(
        "CBOE VIX",
        f"{len(lines)} lines, first {first_data.split(',')[0]}, last {last_data.split(',')[0]}",
    )
    return True


def probe_sec_edgar() -> bool:
    # Blockbuster CIK 1085734 — delisted 2010, filings still archived.
    url = "https://data.sec.gov/submissions/CIK0001085734.json"
    code, body = http_get(url, headers={"User-Agent": DEFAULT_UA_SEC})
    if code != 200:
        emit_fail("SEC EDGAR", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
        recent = obj["filings"]["recent"]
        forms = recent["form"]
        dates = recent["filingDate"]
    except Exception:  # noqa: BLE001
        emit_fail("SEC EDGAR", "JSON shape unexpected")
        return False
    eight_k_dates = [d for f, d in zip(forms, dates) if f == "8-K"]
    if not eight_k_dates:
        emit_fail("SEC EDGAR", "no 8-K filings found for Blockbuster")
        return False
    emit_pass(
        "SEC EDGAR",
        f"Blockbuster {len(eight_k_dates)} 8-Ks, first {eight_k_dates[-1]}, last {eight_k_dates[0]}",
    )
    return True


def probe_nasdaq_trader() -> bool:
    url = "https://nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
    code, body = http_get(url, headers={"User-Agent": DEFAULT_UA_BROWSER})
    if code != 200 or not body:
        emit_fail("Nasdaq Trader", f"HTTP {code}")
        return False
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) < 5000:
        emit_fail("Nasdaq Trader", f"only {len(lines)} lines (<5000)")
        return False
    # Header line + rows.
    header = lines[0].split("|")[:2]
    emit_pass(
        "Nasdaq Trader",
        f"{len(lines)-1} symbols; header starts {'/'.join(header)}",
    )
    return True


def probe_fja05680_sp500() -> bool:
    # api.github.com is proxied in this environment and returns
    # a canonicalization error for arbitrary repos. Fetching directly
    # via raw.githubusercontent.com works. sp500_ticker_start_end.csv
    # holds the historical membership (ticker, start, end) and is
    # what a point-in-time backtest actually needs.
    url = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
    code, body = http_get(url, headers={"User-Agent": DEFAULT_UA_BROWSER})
    if code != 200 or not body:
        emit_fail("fja05680/sp500", f"HTTP {code}")
        return False
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) < 500:
        emit_fail("fja05680/sp500", f"only {len(lines)} lines")
        return False
    header = lines[0]
    emit_pass(
        "fja05680/sp500",
        f"sp500_ticker_start_end.csv: {len(lines)-1} membership rows; header '{header}'",
    )
    return True


def probe_datasets_finance_vix() -> bool:
    url = "https://raw.githubusercontent.com/datasets/finance-vix/master/data/vix-daily.csv"
    code, body = http_get(url, headers={"User-Agent": DEFAULT_UA_BROWSER})
    if code != 200 or not body:
        emit_fail("finance-vix (GitHub)", f"HTTP {code}")
        return False
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) < 3000:
        emit_fail("finance-vix (GitHub)", f"only {len(lines)} lines")
        return False
    last = lines[-1].split(",")
    emit_pass(
        "finance-vix (GitHub)",
        f"{len(lines)} lines; last row date {last[0]}",
    )
    return True


def probe_polygon_rest_live() -> bool:
    key = os.environ.get("POLYGON_API_KEY")
    if not key:
        emit_fail("Polygon REST", "env: POLYGON_API_KEY missing")
        return False
    url = (
        "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2025-01-02/2025-01-08?"
        + urllib.parse.urlencode({"apiKey": key, "adjusted": "true"})
    )
    code, body = http_get(url)
    if code != 200:
        emit_fail("Polygon REST", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
        results = obj.get("results", []) or []
    except Exception:  # noqa: BLE001
        emit_fail("Polygon REST", "JSON parse error")
        return False
    if not results:
        emit_fail("Polygon REST", "no results")
        return False
    closes = [r.get("c") for r in results if isinstance(r.get("c"), (int, float))]
    emit_pass(
        "Polygon REST",
        f"{len(results)} bars, close range {min(closes):.2f}-{max(closes):.2f}",
    )
    return True


def probe_polygon_rest_delisted() -> bool:
    key = os.environ.get("POLYGON_API_KEY")
    if not key:
        emit_fail("Polygon delisted", "env: POLYGON_API_KEY missing")
        return False
    # Family Dollar FDO — delisted 2015. Probe a mid-2014 window.
    url = (
        "https://api.polygon.io/v2/aggs/ticker/FDO/range/1/day/2014-06-02/2014-06-06?"
        + urllib.parse.urlencode({"apiKey": key, "adjusted": "true"})
    )
    code, body = http_get(url)
    if code == 404:
        emit_fail("Polygon delisted", "FDO 404 (not in free tier or delisted)")
        return False
    if code != 200:
        emit_fail("Polygon delisted", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
        results = obj.get("results", []) or []
    except Exception:  # noqa: BLE001
        emit_fail("Polygon delisted", "JSON parse error")
        return False
    if not results:
        emit_fail(
            "Polygon delisted",
            "FDO empty (delisted-symbol coverage not confirmed on free tier)",
        )
        return False
    closes = [r.get("c") for r in results if isinstance(r.get("c"), (int, float))]
    emit_pass(
        "Polygon delisted",
        f"FDO 2014: {len(results)} bars, close range {min(closes):.2f}-{max(closes):.2f}",
    )
    return True


def probe_fred() -> bool:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        emit_fail("FRED", "env: FRED_API_KEY missing")
        return False
    url = (
        "https://api.stlouisfed.org/fred/series/observations?"
        + urllib.parse.urlencode(
            {
                "series_id": "UNRATE",
                "api_key": key,
                "file_type": "json",
                "limit": "5",
                "sort_order": "desc",
            }
        )
    )
    code, body = http_get(url)
    if code != 200:
        emit_fail("FRED", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
        obs = obj.get("observations", []) or []
    except Exception:  # noqa: BLE001
        emit_fail("FRED", "JSON parse error")
        return False
    if not obs:
        emit_fail("FRED", "no observations returned")
        return False
    first, last = obs[0], obs[-1]
    emit_pass(
        "FRED",
        f"UNRATE {len(obs)} obs, latest {first['date']}={first['value']}, oldest {last['date']}={last['value']}",
    )
    return True


def probe_alpha_vantage() -> bool:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        emit_fail("Alpha Vantage", "env: ALPHA_VANTAGE_API_KEY missing")
        return False
    url = (
        "https://www.alphavantage.co/query?"
        + urllib.parse.urlencode(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": "IBM",
                "outputsize": "compact",
                "apikey": key,
            }
        )
    )
    code, body = http_get(url)
    if code != 200:
        emit_fail("Alpha Vantage", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
    except Exception:  # noqa: BLE001
        emit_fail("Alpha Vantage", "JSON parse error")
        return False
    # Alpha Vantage returns 200 even for bad key / rate limit; the
    # error/quota message lives inside the JSON as one of these keys.
    for bad_key in ("Error Message", "Information", "Note"):
        if bad_key in obj:
            emit_fail("Alpha Vantage", f"body flagged: '{bad_key}'")
            return False
    series = obj.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        emit_fail("Alpha Vantage", "no 'Time Series (Daily)' object")
        return False
    dates = sorted(series.keys(), reverse=True)
    first_date = dates[0]
    last_date = dates[-1]
    emit_pass(
        "Alpha Vantage",
        f"IBM {len(dates)} days, {last_date}..{first_date}",
    )
    return True


def probe_tiingo() -> bool:
    key = os.environ.get("TIINGO_API_KEY")
    if not key:
        emit_fail("Tiingo", "env: TIINGO_API_KEY missing")
        return False
    url = (
        "https://api.tiingo.com/tiingo/daily/aapl/prices?"
        + urllib.parse.urlencode(
            {"startDate": "2025-01-02", "endDate": "2025-01-08"}
        )
    )
    code, body = http_get(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Token {key}",
        },
    )
    if code != 200:
        emit_fail("Tiingo", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
    except Exception:  # noqa: BLE001
        emit_fail("Tiingo", "JSON parse error")
        return False
    if not isinstance(obj, list) or not obj:
        emit_fail("Tiingo", "empty or wrong-shape body")
        return False
    closes = [row.get("close") for row in obj if isinstance(row.get("close"), (int, float))]
    if not closes:
        emit_fail("Tiingo", "no numeric closes")
        return False
    emit_pass(
        "Tiingo",
        f"AAPL {len(obj)} days, close range {min(closes):.2f}-{max(closes):.2f}",
    )
    return True


def probe_finnhub() -> bool:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        emit_fail("Finnhub", "env: FINNHUB_API_KEY missing")
        return False
    url = (
        "https://finnhub.io/api/v1/quote?"
        + urllib.parse.urlencode({"symbol": "AAPL", "token": key})
    )
    code, body = http_get(url)
    if code != 200:
        emit_fail("Finnhub", f"HTTP {code}")
        return False
    try:
        obj = json.loads(body)
    except Exception:  # noqa: BLE001
        emit_fail("Finnhub", "JSON parse error")
        return False
    price = obj.get("c")
    if not isinstance(price, (int, float)) or price <= 0:
        emit_fail("Finnhub", "no positive current price")
        return False
    emit_pass(
        "Finnhub",
        f"AAPL current price {price:.2f}, prev close {obj.get('pc')}, high {obj.get('h')}",
    )
    return True


# --- Notes on skipped items ----------------------------------------


def note_polygon_flat_files() -> None:
    have_id = bool(os.environ.get("POLYGON_S3_ACCESS_KEY_ID"))
    have_secret = bool(os.environ.get("POLYGON_S3_SECRET_KEY"))
    have_endpoint = bool(os.environ.get("POLYGON_S3_ENDPOINT"))
    all_set = have_id and have_secret and have_endpoint
    emit_skip(
        "Polygon Flat Files",
        (
            "S3 creds present, live probe deferred (no boto3/awscli)"
            if all_set
            else "one or more S3 env vars MISSING"
        ),
    )


# --- Driver --------------------------------------------------------


def main() -> int:
    probes = (
        probe_alpaca_sip,
        probe_yahoo_v8,
        probe_cboe_vix,
        probe_sec_edgar,
        probe_nasdaq_trader,
        probe_fja05680_sp500,
        probe_datasets_finance_vix,
        probe_polygon_rest_live,
        probe_polygon_rest_delisted,
        probe_fred,
        probe_alpha_vantage,
        probe_tiingo,
        probe_finnhub,
    )
    print("=" * 78)
    print("B16 Stage 3 use-case trials (D-0042)")
    print("=" * 78)

    passes = 0
    total = len(probes)
    for probe in probes:
        try:
            if probe():
                passes += 1
        except Exception as ex:  # noqa: BLE001
            emit_fail(probe.__name__, f"unexpected {type(ex).__name__}")

    note_polygon_flat_files()

    print("=" * 78)
    print(f"Stage 3 summary: {passes}/{total} PASS")
    print("=" * 78)
    return 0 if passes == total else 1


if __name__ == "__main__":
    sys.exit(main())
