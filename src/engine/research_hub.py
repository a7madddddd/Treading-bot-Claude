"""Central research hub for symbol evaluation (D-0050 Phase 10).

ONE point where every API's data for a symbol is collected, normalized
into a structured dataclass, and returned ready for the evaluator to
filter and score. Replaces scattered per-source enrichers with a single
collection step that runs all fetches in parallel.

Advisory only (CLAUDE.md §5). This module produces data; the
evaluator (trade_evaluator.py) produces decisions.
"""

from __future__ import annotations

import concurrent.futures as _fut
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Normalized research record
# ---------------------------------------------------------------------------

@dataclass
class SymbolResearch:
    """All API-derived facts for one symbol at one point in time.

    EVERY field is Optional — if an API is down or absent, the
    corresponding field is None, and the evaluator reasons about
    that explicitly rather than crashing."""

    symbol: str
    collected_at: datetime

    # ---- Price / volume (Polygon snapshot) --------------------------
    current_price: Optional[float] = None
    day_change_pct: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    day_volume: Optional[float] = None
    prev_close: Optional[float] = None

    # ---- Fundamentals (Finnhub) -------------------------------------
    pe_ratio: Optional[float] = None
    market_cap_millions: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    next_earnings_date: Optional[date] = None
    next_earnings_days: Optional[int] = None
    eps_estimate: Optional[float] = None
    analyst_buys: Optional[int] = None
    analyst_holds: Optional[int] = None
    analyst_sells: Optional[int] = None
    analyst_buy_ratio: Optional[float] = None  # 0..1
    insider_mspr: Optional[float] = None       # -100..+100

    # ---- Technicals (Alpha Vantage) ---------------------------------
    rsi_14: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_signal: Optional[str] = None  # bullish_crossover / bullish / bearish / bearish_crossover
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_position: Optional[float] = None  # 0=at lower, 1=at upper
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    golden_cross: Optional[bool] = None  # True if SMA50 > SMA200

    # ---- News / catalysts -------------------------------------------
    news_count_48h: int = 0
    news_headlines: List[Tuple[str, str]] = field(default_factory=list)  # (title, source)
    perplexity_catalysts: List[str] = field(default_factory=list)
    perplexity_risks: List[str] = field(default_factory=list)

    # ---- Macro (FRED) -----------------------------------------------
    regime: Optional[str] = None  # risk_on / neutral / risk_off
    vix_percentile: Optional[float] = None  # 0..1
    vix_level: Optional[float] = None
    fed_funds_rate: Optional[float] = None
    yield_curve_spread: Optional[float] = None

    # ---- Company metadata -------------------------------------------
    company_name: Optional[str] = None
    sector: Optional[str] = None

    # ---- Collection health ------------------------------------------
    sources_succeeded: List[str] = field(default_factory=list)
    sources_failed: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------

def _as_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


class SymbolResearchHub:
    """Fetches every API's data for one symbol in parallel and returns
    a fully-populated SymbolResearch. Any single-source failure is
    recorded in sources_failed; the record is still returned."""

    def __init__(
        self,
        *,
        fred=None,
        finnhub=None,
        alpha_vantage=None,
        polygon=None,
        tiingo=None,
        perplexity=None,
        max_workers: int = 6,
        deadline_seconds: float = 60.0,
    ) -> None:
        self._fred = fred
        self._fh = finnhub
        self._av = alpha_vantage
        self._pg = polygon
        self._tn = tiingo
        self._px = perplexity
        self._max_workers = max_workers
        self._deadline = deadline_seconds

    def collect(self, symbol: str) -> SymbolResearch:
        """Returns a SymbolResearch populated by every reachable source."""
        research = SymbolResearch(
            symbol=symbol,
            collected_at=datetime.utcnow(),
        )
        tasks: Dict[str, _fut.Future] = {}
        with _fut.ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            if self._fred is not None:
                tasks["fred"] = ex.submit(self._fetch_fred)
            if self._fh is not None:
                tasks["finnhub"] = ex.submit(self._fetch_finnhub, symbol)
            if self._av is not None:
                tasks["alpha_vantage"] = ex.submit(self._fetch_av, symbol)
            if self._pg is not None:
                tasks["polygon"] = ex.submit(self._fetch_polygon, symbol)
            if self._tn is not None:
                tasks["tiingo"] = ex.submit(self._fetch_tiingo, symbol)
            if self._px is not None:
                tasks["perplexity"] = ex.submit(self._fetch_perplexity, symbol)

            for name, fut in tasks.items():
                try:
                    data = fut.result(timeout=self._deadline)
                except Exception:  # noqa: BLE001
                    research.sources_failed.append(name)
                    continue
                if data is None:
                    research.sources_failed.append(name)
                    continue
                self._apply(research, name, data)
                research.sources_succeeded.append(name)

        return research

    # ---- per-source fetchers ----------------------------------------

    def _fetch_fred(self) -> Optional[dict]:
        from d0026.regime_classifier import classify_regime
        try:
            regime = classify_regime(self._fred, date.today())
            refs = dict(regime.reference_series_values)
            return {
                "regime": regime.label.value,
                "vix_percentile": refs.get("vix_percentile"),
                "vix_level": refs.get("vix_level"),
                "fed_funds_rate": refs.get("fed_funds_rate"),
                "yield_curve_spread": refs.get("yield_curve_spread"),
            }
        except Exception:  # noqa: BLE001
            return None

    def _fetch_finnhub(self, symbol: str) -> Optional[dict]:
        today = date.today()
        out: dict = {}
        try:
            fin = self._fh.basic_financials(symbol)
            if isinstance(fin, dict) and isinstance(fin.get("metric"), dict):
                m = fin["metric"]
                out["pe_ratio"] = _as_float(m.get("peBasicExclExtraTTM"))
                out["market_cap_millions"] = _as_float(m.get("marketCapitalization"))
                out["high_52w"] = _as_float(m.get("52WeekHigh"))
                out["low_52w"] = _as_float(m.get("52WeekLow"))
        except Exception:  # noqa: BLE001
            pass
        try:
            ec = self._fh.earnings_calendar(symbol, today,
                                            today + timedelta(days=90))
            if ec:
                first = ec[0]
                d_raw = first.get("date")
                if isinstance(d_raw, str):
                    try:
                        d = date.fromisoformat(d_raw)
                        out["next_earnings_date"] = d
                        out["next_earnings_days"] = (d - today).days
                        out["eps_estimate"] = _as_float(first.get("epsEstimate"))
                    except ValueError:
                        pass
        except Exception:  # noqa: BLE001
            pass
        try:
            rec = self._fh.recommendation_trends(symbol)
            if rec:
                latest = rec[0]
                buys = int(latest.get("buy", 0) or 0) + int(latest.get("strongBuy", 0) or 0)
                holds = int(latest.get("hold", 0) or 0)
                sells = int(latest.get("sell", 0) or 0) + int(latest.get("strongSell", 0) or 0)
                total = buys + holds + sells
                out["analyst_buys"] = buys
                out["analyst_holds"] = holds
                out["analyst_sells"] = sells
                if total > 0:
                    out["analyst_buy_ratio"] = buys / total
        except Exception:  # noqa: BLE001
            pass
        try:
            ins = self._fh.insider_sentiment(symbol,
                                              today - timedelta(days=90), today)
            if isinstance(ins, dict) and ins.get("data"):
                rows = ins["data"]
                latest = max(rows, key=lambda r: (r.get("year", 0),
                                                   r.get("month", 0)))
                out["insider_mspr"] = _as_float(latest.get("mspr"))
        except Exception:  # noqa: BLE001
            pass
        return out or None

    def _fetch_av(self, symbol: str) -> Optional[dict]:
        out: dict = {}
        try:
            rsi = self._av.rsi(symbol)
            if rsi:
                out["rsi_14"] = rsi[-1][1]
        except Exception:  # noqa: BLE001
            pass
        try:
            macd = self._av.macd(symbol)
            if macd and len(macd) >= 2:
                prev = macd[-2][1] or {}
                curr = macd[-1][1] or {}
                prev_h = prev.get("hist") if isinstance(prev, dict) else None
                curr_h = curr.get("hist") if isinstance(curr, dict) else None
                if isinstance(curr_h, (int, float)):
                    out["macd_hist"] = curr_h
                if isinstance(prev_h, (int, float)) and isinstance(curr_h, (int, float)):
                    if curr_h > 0 and prev_h <= 0:
                        out["macd_signal"] = "bullish_crossover"
                    elif curr_h < 0 and prev_h >= 0:
                        out["macd_signal"] = "bearish_crossover"
                    elif curr_h > 0:
                        out["macd_signal"] = "bullish"
                    else:
                        out["macd_signal"] = "bearish"
        except Exception:  # noqa: BLE001
            pass
        try:
            bb = self._av.bbands(symbol)
            if bb:
                band = bb[-1][1]
                if isinstance(band, dict):
                    out["bb_upper"] = _as_float(band.get("upper"))
                    out["bb_middle"] = _as_float(band.get("middle"))
                    out["bb_lower"] = _as_float(band.get("lower"))
        except Exception:  # noqa: BLE001
            pass
        try:
            s50 = self._av.sma(symbol, time_period=50)
            if s50:
                out["sma_50"] = s50[-1][1]
            s200 = self._av.sma(symbol, time_period=200)
            if s200:
                out["sma_200"] = s200[-1][1]
            if "sma_50" in out and "sma_200" in out:
                out["golden_cross"] = out["sma_50"] > out["sma_200"]
        except Exception:  # noqa: BLE001
            pass
        return out or None

    def _fetch_polygon(self, symbol: str) -> Optional[dict]:
        out: dict = {}
        try:
            snap = self._pg.get_ticker_snapshot(symbol)
            if isinstance(snap, dict):
                day = snap.get("day") if isinstance(snap.get("day"), dict) else {}
                prev = snap.get("prevDay") if isinstance(snap.get("prevDay"), dict) else {}
                out["current_price"] = _as_float(day.get("c"))
                out["day_high"] = _as_float(day.get("h"))
                out["day_low"] = _as_float(day.get("l"))
                out["day_volume"] = _as_float(day.get("v"))
                out["prev_close"] = _as_float(prev.get("c"))
                if out["current_price"] and out["prev_close"] and out["prev_close"] > 0:
                    out["day_change_pct"] = (out["current_price"] - out["prev_close"]) / out["prev_close"] * 100
        except Exception:  # noqa: BLE001
            pass
        try:
            det = self._pg.get_ticker_details(symbol)
            if isinstance(det, dict):
                out["company_name"] = det.get("name")
                out["sector"] = det.get("sic_description")
        except Exception:  # noqa: BLE001
            pass
        return out or None

    def _fetch_tiingo(self, symbol: str) -> Optional[dict]:
        try:
            news = self._tn.get_news([symbol], limit=10)
        except Exception:  # noqa: BLE001
            return None
        if not news:
            return None
        heads: List[Tuple[str, str]] = []
        for item in news[:5]:
            if isinstance(item, dict):
                t = item.get("title")
                s = item.get("source") or ""
                if isinstance(t, str) and t.strip():
                    heads.append((t.strip(), str(s).strip()))
        return {"news_count_48h": len(news), "news_headlines": heads}

    def _fetch_perplexity(self, symbol: str) -> Optional[dict]:
        """Fires the two Perplexity queries (positives + negatives) IN
        PARALLEL rather than sequentially — total latency drops from
        the sum to the slower of the two, which comfortably fits the
        hub's per-source deadline."""
        def _bullets(q: str) -> List[str]:
            try:
                rep = self._px.research_stock(symbol, q)
            except Exception:  # noqa: BLE001
                return []
            findings = getattr(rep, "findings", None) or []
            out = []
            for f in findings[:3]:
                s = getattr(f, "text", None) or getattr(f, "summary", None)
                if isinstance(s, str) and s.strip():
                    out.append(s.strip())
            return out

        cat_q = ("In 3 very short bullets, list the single biggest POSITIVE "
                 "catalyst or tailwind for this stock RIGHT NOW. Each bullet "
                 "under 18 words.")
        risk_q = ("In 3 very short bullets, list the single biggest NEGATIVE "
                  "risk or headwind for this stock RIGHT NOW. Each bullet "
                  "under 18 words.")

        import concurrent.futures as _f
        with _f.ThreadPoolExecutor(max_workers=2) as ex:
            fc = ex.submit(_bullets, cat_q)
            fr = ex.submit(_bullets, risk_q)
            try:
                cat = fc.result(timeout=55.0)
            except Exception:  # noqa: BLE001
                cat = []
            try:
                risk = fr.result(timeout=55.0)
            except Exception:  # noqa: BLE001
                risk = []
        if not cat and not risk:
            return None
        return {"perplexity_catalysts": cat, "perplexity_risks": risk}

    # ---- merge ------------------------------------------------------

    def _apply(self, research: SymbolResearch, source: str, data: dict) -> None:
        # bb_position computed after polygon + alpha_vantage both land
        for key, value in data.items():
            if hasattr(research, key):
                setattr(research, key, value)
        # if both bb_upper/lower and current_price are known, derive position
        if (research.current_price is not None and research.bb_lower is not None
                and research.bb_upper is not None
                and research.bb_upper > research.bb_lower):
            research.bb_position = (research.current_price - research.bb_lower) / (
                research.bb_upper - research.bb_lower)
