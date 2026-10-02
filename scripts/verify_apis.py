"""Diagnostic: calls every API endpoint the deep-research pipeline
uses with the REAL env-var keys, prints what each actually returned,
and asserts the fields my parsers read are present in the response.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/verify_apis.py AAPL

Any line prefixed ❌ flags a shape mismatch the parser cannot handle
today. Any line prefixed ⚠ is a soft issue (rate limit / empty).
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))


SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

PASS_COUNT = 0
FAIL_COUNT = 0
WARN_COUNT = 0


def section(name: str) -> None:
    print(f"\n{'=' * 68}\n  {name}\n{'=' * 68}")


def ok(msg: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  ✅ {msg}")


def fail(msg: str) -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    print(f"  ❌ {msg}")


def warn(msg: str) -> None:
    global WARN_COUNT
    WARN_COUNT += 1
    print(f"  ⚠  {msg}")


def info(msg: str) -> None:
    print(f"     {msg}")


def shorten(obj, n: int = 300) -> str:
    s = repr(obj)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# 1. ALPACA (live price endpoint used by MarketDataSource)
# ---------------------------------------------------------------------------

section(f"1. ALPACA — latest trade for {SYMBOL}")
try:
    kid = os.environ.get("ALPACA_API_KEY_ID", "")
    sec = os.environ.get("ALPACA_API_SECRET_KEY", "")
    if not (kid and sec):
        fail("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    else:
        url = f"https://data.alpaca.markets/v2/stocks/{SYMBOL}/trades/latest"
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec,
        })
        with urllib.request.urlopen(req, timeout=10,
                                     context=ssl.create_default_context()) as r:
            data = json.loads(r.read())
            info(f"keys: {list(data.keys())}")
            trade = data.get("trade")
            if isinstance(trade, dict) and "p" in trade:
                ok(f"trade.p = ${trade['p']}")
            else:
                fail(f"missing trade.p; got {shorten(data)}")
except Exception as exc:  # noqa: BLE001
    fail(f"Alpaca error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 2. FRED
# ---------------------------------------------------------------------------

section("2. FRED — VIXCLS + DFF + T10Y2Y")
try:
    from marketdata.fred_source import FredSource
    fred = FredSource.from_env()
    if fred is None:
        fail("FRED_API_KEY unset")
    else:
        for series in ("VIXCLS", "DFF", "T10Y2Y"):
            obs = fred.get_series(series,
                                   date.today() - timedelta(days=30),
                                   date.today())
            if obs:
                ok(f"{series}: {len(obs)} obs, latest = ({obs[-1][0]}, {obs[-1][1]})")
            else:
                fail(f"{series}: EMPTY (bad key? rate limited?)")
except Exception as exc:  # noqa: BLE001
    fail(f"FRED error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 3. FINNHUB (4 endpoints)
# ---------------------------------------------------------------------------

section(f"3. FINNHUB — financials + earnings + analysts + insider for {SYMBOL}")
try:
    from marketdata.finnhub_source import FinnhubSource
    fh = FinnhubSource.from_env()
    if fh is None:
        fail("FINNHUB_API_KEY unset")
    else:
        # basic_financials
        fin = fh.basic_financials(SYMBOL)
        if isinstance(fin, dict) and isinstance(fin.get("metric"), dict):
            m = fin["metric"]
            info(f"metric has {len(m)} fields")
            for key in ("peBasicExclExtraTTM", "marketCapitalization",
                        "52WeekHigh", "52WeekLow"):
                if key in m and m[key] is not None:
                    ok(f"metric.{key} = {m[key]}")
                else:
                    fail(f"metric.{key} MISSING (deep_research reads this)")
        else:
            fail(f"basic_financials shape unexpected: {shorten(fin)}")

        # earnings_calendar
        ec = fh.earnings_calendar(SYMBOL, date.today(),
                                   date.today() + timedelta(days=90))
        if ec:
            first = ec[0]
            info(f"first entry keys: {list(first.keys())}")
            if "date" in first and "epsEstimate" in first:
                ok(f"earnings_calendar: {len(ec)} entries, next = {first.get('date')} "
                   f"EPS est = {first.get('epsEstimate')}")
            else:
                fail(f"earnings_calendar missing date/epsEstimate: {shorten(first)}")
        else:
            warn("earnings_calendar: empty (ok if no earnings in next 90d)")

        # recommendation_trends
        rec = fh.recommendation_trends(SYMBOL)
        if rec:
            latest = rec[0]
            info(f"latest keys: {list(latest.keys())}")
            needed = ("buy", "strongBuy", "hold", "sell", "strongSell")
            missing = [k for k in needed if k not in latest]
            if not missing:
                ok(f"recommendation_trends: buy={latest.get('buy')} "
                   f"strongBuy={latest.get('strongBuy')} hold={latest.get('hold')} "
                   f"sell={latest.get('sell')} strongSell={latest.get('strongSell')}")
            else:
                fail(f"recommendation_trends missing: {missing}")
        else:
            fail("recommendation_trends: empty")

        # insider_sentiment
        ins = fh.insider_sentiment(SYMBOL,
                                    date.today() - timedelta(days=180),
                                    date.today())
        if isinstance(ins, dict) and ins.get("data"):
            rows = ins["data"]
            info(f"{len(rows)} rows, first keys: {list(rows[0].keys())}")
            if "mspr" in rows[-1]:
                ok(f"insider_sentiment latest mspr = {rows[-1]['mspr']}")
            else:
                fail("insider_sentiment missing 'mspr' field")
        else:
            warn(f"insider_sentiment: no data (common on free tier): {shorten(ins)}")
except Exception as exc:  # noqa: BLE001
    fail(f"Finnhub error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 4. ALPHA VANTAGE (5 endpoints)
# ---------------------------------------------------------------------------

section(f"4. ALPHA VANTAGE — RSI + MACD + BBANDS + SMA50 + SMA200 for {SYMBOL}")
try:
    from marketdata.alpha_vantage_source import AlphaVantageSource
    av = AlphaVantageSource.from_env()
    if av is None:
        fail("ALPHA_VANTAGE_API_KEY unset")
    else:
        for label, result in (
            ("rsi(14)", av.rsi(SYMBOL)),
            ("macd", av.macd(SYMBOL)),
            ("bbands(20)", av.bbands(SYMBOL)),
            ("sma(50)", av.sma(SYMBOL, time_period=50)),
            ("sma(200)", av.sma(SYMBOL, time_period=200)),
        ):
            if result:
                ok(f"{label}: {len(result)} points, latest = {result[-1]}")
            else:
                warn(f"{label}: EMPTY (free tier is 5 req/min / 500/day; "
                     "may be rate-limited)")
except Exception as exc:  # noqa: BLE001
    fail(f"Alpha Vantage error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 5. TIINGO (news + EOD)
# ---------------------------------------------------------------------------

section(f"5. TIINGO — news + EOD for {SYMBOL}")
try:
    from marketdata.tiingo_source import TiingoSource
    tn = TiingoSource.from_env()
    if tn is None:
        fail("TIINGO_API_KEY unset")
    else:
        news = tn.get_news([SYMBOL], limit=5)
        if news:
            first = news[0]
            info(f"news[0] keys: {list(first.keys())}")
            if "title" in first:
                ok(f"news: {len(news)} items, first title = {shorten(first['title'], 80)}")
            else:
                fail(f"news item missing 'title': {shorten(first)}")
            if "source" in first:
                ok(f"news[0].source = {first['source']!r}")
            else:
                warn("news[0] has no 'source' — composer will omit it")
        else:
            warn("get_news: empty (ok if no recent news)")

        eod = tn.get_eod_prices(SYMBOL,
                                 date.today() - timedelta(days=10),
                                 date.today())
        if eod:
            info(f"eod[-1] keys: {list(eod[-1][1].keys())}")
            ok(f"eod: {len(eod)} days, latest = {eod[-1][0]} "
               f"close={eod[-1][1].get('close')}")
        else:
            fail("get_eod_prices: EMPTY")
except Exception as exc:  # noqa: BLE001
    fail(f"Tiingo error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 6. POLYGON REST (snapshot + details + news)
# ---------------------------------------------------------------------------

section(f"6. POLYGON REST — snapshot + details + news for {SYMBOL}")
try:
    from marketdata.polygon_source import PolygonSource
    pg = PolygonSource.from_env()
    if pg is None:
        fail("POLYGON_API_KEY unset")
    else:
        snap = pg.get_ticker_snapshot(SYMBOL)
        if isinstance(snap, dict):
            info(f"snapshot keys: {list(snap.keys())}")
            day = snap.get("day") if isinstance(snap.get("day"), dict) else None
            prev = snap.get("prevDay") if isinstance(snap.get("prevDay"), dict) else None
            if day:
                missing = [k for k in ("c", "h", "l", "v") if k not in day]
                if not missing:
                    ok(f"day: c=${day['c']} h=${day['h']} l=${day['l']} v={day['v']}")
                else:
                    fail(f"snapshot.day missing: {missing}")
            else:
                fail("snapshot.day missing")
            if prev and "c" in prev:
                ok(f"prevDay.c = ${prev['c']}")
            else:
                warn("prevDay.c missing — day %% change will be omitted")
        else:
            fail(f"get_ticker_snapshot: {shorten(snap)}")

        det = pg.get_ticker_details(SYMBOL)
        if isinstance(det, dict):
            info(f"details keys: {list(det.keys())[:12]}")
            if det.get("name"):
                ok(f"details.name = {det['name']!r}")
            if det.get("sic_description"):
                ok(f"details.sic_description = {det['sic_description']!r}")
        else:
            fail("get_ticker_details: None")

        news = pg.get_news(SYMBOL, limit=3)
        if news:
            info(f"news[0] keys: {list(news[0].keys())[:10]}")
            pub = news[0].get("publisher")
            if isinstance(pub, dict) and pub.get("name"):
                ok(f"news[0].publisher.name = {pub['name']!r}")
            else:
                warn(f"publisher shape: {shorten(pub)}")
        else:
            warn("news empty")
except Exception as exc:  # noqa: BLE001
    fail(f"Polygon REST error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 7. PERPLEXITY
# ---------------------------------------------------------------------------

section(f"7. PERPLEXITY — research_stock for {SYMBOL}")
try:
    from research.perplexity_agent import PerplexityAgentClient
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not key:
        fail("PERPLEXITY_API_KEY unset")
    else:
        px = PerplexityAgentClient(api_key=key)
        rep = px.research_stock(SYMBOL,
            "In 2 very short bullets, name the most important news for this "
            "stock today. If nothing, say so.")
        info(f"report type: {type(rep).__name__}")
        if rep is not None:
            findings = getattr(rep, "findings", None)
            if findings is None:
                fail(f"report has no .findings; attrs = {dir(rep)[:20]}")
            else:
                ok(f"findings count: {len(findings)}")
                for i, f in enumerate(findings[:3]):
                    text = getattr(f, "text", None) or getattr(f, "summary", None)
                    if text:
                        ok(f"findings[{i}].text = {shorten(text, 120)}")
                    else:
                        fail(f"findings[{i}] has no text/summary; "
                             f"attrs = {[a for a in dir(f) if not a.startswith('_')][:10]}")
        else:
            fail("report is None")
except Exception as exc:  # noqa: BLE001
    fail(f"Perplexity error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 8. POLYGON S3 (SigV4 + list_objects)
# ---------------------------------------------------------------------------

section("8. POLYGON S3 — list one month's day-aggregate files")
try:
    from marketdata.polygon_source import PolygonS3Config
    cfg = PolygonS3Config.from_env()
    if cfg is None:
        fail("POLYGON_S3_* not all set")
    else:
        client = cfg.build_client()
        today = date.today()
        prefix = f"us_stocks_sip/day_aggs_v1/{today.year}/{today.month:02d}/"
        objs = client.list_objects(prefix, max_pages=1)
        if objs:
            ok(f"list_objects({prefix!r}): {len(objs)} objects")
            info(f"first: key={objs[0]['key']} size={objs[0]['size']}")
        else:
            # Try last month
            lm = today.replace(day=1) - timedelta(days=1)
            prefix2 = f"us_stocks_sip/day_aggs_v1/{lm.year}/{lm.month:02d}/"
            objs2 = client.list_objects(prefix2, max_pages=1)
            if objs2:
                ok(f"last month fallback: {len(objs2)} objs, first key = {objs2[0]['key']}")
            else:
                fail("S3 list EMPTY on both current and last month — check "
                     "SigV4 signing or key permissions")
except Exception as exc:  # noqa: BLE001
    fail(f"Polygon S3 error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n" + "=" * 68)
print(f"  Diagnostic complete: {PASS_COUNT} ✅  {WARN_COUNT} ⚠  {FAIL_COUNT} ❌")
print("=" * 68)
sys.exit(0 if FAIL_COUNT == 0 else 1)
