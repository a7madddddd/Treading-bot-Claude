"""Deep research composer for proposal notifications (D-0050 Phase 9).

Produces a multi-section research REPORT (not a one-line bullet) for
every outgoing proposal:

  🌐 Macro          — FRED regime, VIX percentile, Fed rate, yield spread
  🏢 Fundamentals   — Finnhub financials + earnings calendar + analyst
                       trend + insider sentiment
  📈 Technicals     — Alpha Vantage RSI + MACD + BBANDS + SMA50 + SMA200
                       (classified: trend, overbought/oversold, crossover)
  🎯 Live snapshot  — Polygon snapshot + ticker details
  📰 News           — Tiingo top-3 headlines (48h) + Polygon news
  🔍 Catalysts/Risks — Perplexity, two queries (what helps / what hurts)
  ⚖  Verdict        — deterministic synthesis across all signals

Every section is independent and fail-open. A down source leaves its
section blank, but the report is still produced. All API fetches run
in parallel via ThreadPoolExecutor so total latency ≈ slowest source
instead of sum-of-all-sources.

Advisory only (CLAUDE.md §5). The verdict does NOT gate approval; it
is one more piece of context the Controller considers alongside the
strategy's own output.
"""

from __future__ import annotations

import concurrent.futures as _fut
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------

@dataclass
class DeepResearchReport:
    symbol: str
    macro_lines: List[str] = field(default_factory=list)
    fundamentals_lines: List[str] = field(default_factory=list)
    technicals_lines: List[str] = field(default_factory=list)
    snapshot_lines: List[str] = field(default_factory=list)
    news_lines: List[str] = field(default_factory=list)
    catalysts_lines: List[str] = field(default_factory=list)
    risks_lines: List[str] = field(default_factory=list)
    verdict_lines: List[str] = field(default_factory=list)
    # structured signal values used by the verdict synthesizer
    signals: Dict[str, object] = field(default_factory=dict)

    def to_telegram_text(self) -> Optional[str]:
        """Returns the multi-line Telegram block, or None if no section
        produced any data (which means every source was down)."""
        sections: List[Tuple[str, List[str]]] = [
            ("🌐 Macro",            self.macro_lines),
            ("🏢 Fundamentals",     self.fundamentals_lines),
            ("📈 Technicals",       self.technicals_lines),
            ("🎯 Live snapshot",    self.snapshot_lines),
            ("📰 News (48h)",       self.news_lines),
            ("🔍 Catalysts",        self.catalysts_lines),
            ("⚠ Risks",             self.risks_lines),
            ("⚖ Verdict",           self.verdict_lines),
        ]
        emitted = []
        for header, lines in sections:
            if not lines:
                continue
            emitted.append(header + ":")
            for ln in lines:
                emitted.append("  " + ln)
        if not emitted:
            return None
        return "═══════ 📊 Research report ═══════\n" + "\n".join(emitted)


# ---------------------------------------------------------------------------
# Section fetchers (each takes its client-or-None, returns lines + signals)
# ---------------------------------------------------------------------------

def _as_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _fmt_mcap_millions(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}T"
    if v >= 1_000:
        return f"${v/1_000:.1f}B"
    return f"${v:.0f}M"


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


# ---- Macro (FRED) ---------------------------------------------------

def _fetch_macro(fred_client) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    if fred_client is None:
        return lines, signals
    try:
        from d0026.regime_classifier import classify_regime
        regime = classify_regime(fred_client, date.today())
        refs = dict(regime.reference_series_values)
        signals["regime"] = regime.label.value
        signals["vix_percentile"] = refs.get("vix_percentile")
        signals["vix_level"] = refs.get("vix_level")
        signals["fed_funds_rate"] = refs.get("fed_funds_rate")
        signals["yield_curve_spread"] = refs.get("yield_curve_spread")
        line = f"Regime: {regime.label.value.upper()}"
        if "vix_percentile" in refs:
            line += f" (VIX pct {refs['vix_percentile']*100:.0f})"
        if "vix_level" in refs:
            line += f" · VIX {refs['vix_level']:.1f}"
        lines.append(line)
        if "fed_funds_rate" in refs or "yield_curve_spread" in refs:
            parts = []
            if "fed_funds_rate" in refs:
                parts.append(f"Fed {refs['fed_funds_rate']:.2f}%")
            if "yield_curve_spread" in refs:
                parts.append(f"10Y-2Y {refs['yield_curve_spread']:+.2f}")
            lines.append(" · ".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return lines, signals


# ---- Fundamentals (Finnhub) -----------------------------------------

def _fetch_fundamentals(fh_client) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    if fh_client is None:
        return lines, signals

    # Basic financials
    try:
        fin = fh_client.basic_financials("placeholder")  # overridden below
    except Exception:  # noqa: BLE001
        fin = None
    return lines, signals  # placeholder replaced by call_fundamentals below


def _call_fundamentals(fh_client, symbol: str) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    if fh_client is None:
        return lines, signals
    today = date.today()

    try:
        fin = fh_client.basic_financials(symbol)
    except Exception:  # noqa: BLE001
        fin = None
    if isinstance(fin, dict) and isinstance(fin.get("metric"), dict):
        m = fin["metric"]
        pe = _as_float(m.get("peBasicExclExtraTTM"))
        mcap = _as_float(m.get("marketCapitalization"))
        hi = _as_float(m.get("52WeekHigh"))
        lo = _as_float(m.get("52WeekLow"))
        parts = []
        if pe and pe > 0:
            signals["pe"] = pe
            parts.append(f"P/E {pe:.1f}")
        if mcap and mcap > 0:
            signals["market_cap_m"] = mcap
            parts.append(_fmt_mcap_millions(mcap))
        if hi and lo and hi > lo:
            signals["52w_high"] = hi
            signals["52w_low"] = lo
            parts.append(f"52w ${lo:.1f}-${hi:.1f}")
        if parts:
            lines.append(" · ".join(parts))

    try:
        earn = fh_client.earnings_calendar(symbol, today,
                                           today + timedelta(days=60))
    except Exception:  # noqa: BLE001
        earn = []
    if earn:
        first = earn[0]
        d = first.get("date")
        eps_est = _as_float(first.get("epsEstimate"))
        if d:
            try:
                dt = date.fromisoformat(d)
                days = (dt - today).days
                signals["next_earnings_days"] = days
                line = f"Next earnings: {d} ({days}d)"
                if eps_est is not None:
                    line += f" · EPS est ${eps_est:.2f}"
                lines.append(line)
            except ValueError:
                pass

    try:
        rec = fh_client.recommendation_trends(symbol)
    except Exception:  # noqa: BLE001
        rec = []
    if rec:
        latest = rec[0]  # most recent month
        buy = int(latest.get("buy", 0) or 0) + int(latest.get("strongBuy", 0) or 0)
        hold = int(latest.get("hold", 0) or 0)
        sell = int(latest.get("sell", 0) or 0) + int(latest.get("strongSell", 0) or 0)
        total = buy + hold + sell
        if total > 0:
            signals["analyst_buys"] = buy
            signals["analyst_total"] = total
            consensus = "BUY" if buy > (hold + sell) else (
                "SELL" if sell > (buy + hold) else "HOLD")
            lines.append(f"Analyst consensus: {consensus} "
                         f"({buy} buy / {hold} hold / {sell} sell)")

    try:
        ins = fh_client.insider_sentiment(symbol,
                                          today - timedelta(days=90), today)
    except Exception:  # noqa: BLE001
        ins = None
    if isinstance(ins, dict) and isinstance(ins.get("data"), list) and ins["data"]:
        rows = ins["data"]
        latest = max(rows, key=lambda r: (r.get("year", 0), r.get("month", 0)))
        mspr = _as_float(latest.get("mspr"))
        if mspr is not None:
            signals["insider_mspr"] = mspr
            tag = "bullish" if mspr > 20 else ("bearish" if mspr < -20 else "neutral")
            lines.append(f"Insider sentiment: {mspr:+.1f} ({tag})")

    return lines, signals


# ---- Technicals (Alpha Vantage) -------------------------------------

def _call_technicals(av_client, symbol: str) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    if av_client is None:
        return lines, signals

    try:
        rsi_series = av_client.rsi(symbol)
    except Exception:  # noqa: BLE001
        rsi_series = []
    if rsi_series:
        rsi = rsi_series[-1][1]
        signals["rsi"] = rsi
        tag = "overbought ⚠" if rsi >= 70 else (
            "oversold ⚠" if rsi <= 30 else "neutral")
        lines.append(f"RSI(14): {rsi:.0f} ({tag})")

    try:
        macd_series = av_client.macd(symbol)
    except Exception:  # noqa: BLE001
        macd_series = []
    if macd_series and len(macd_series) >= 2:
        prev = macd_series[-2][1] or {}
        curr = macd_series[-1][1] or {}
        prev_h = prev.get("hist") if isinstance(prev, dict) else None
        curr_h = curr.get("hist") if isinstance(curr, dict) else None
        if isinstance(prev_h, (int, float)) and isinstance(curr_h, (int, float)):
            signals["macd_hist"] = curr_h
            if curr_h > 0 and prev_h <= 0:
                lines.append("MACD: bullish crossover (fresh)")
                signals["macd_signal"] = "bullish_crossover"
            elif curr_h < 0 and prev_h >= 0:
                lines.append("MACD: bearish crossover (fresh)")
                signals["macd_signal"] = "bearish_crossover"
            elif curr_h > 0:
                lines.append("MACD: bullish momentum")
                signals["macd_signal"] = "bullish"
            else:
                lines.append("MACD: bearish momentum")
                signals["macd_signal"] = "bearish"

    try:
        bb = av_client.bbands(symbol)
    except Exception:  # noqa: BLE001
        bb = []
    if bb:
        band = bb[-1][1]
        if isinstance(band, dict):
            upper = _as_float(band.get("upper"))
            lower = _as_float(band.get("lower"))
            mid = _as_float(band.get("middle"))
            if upper and lower and mid:
                signals["bb_upper"] = upper
                signals["bb_lower"] = lower
                lines.append(f"BBands(20): ${lower:.1f} / ${mid:.1f} / ${upper:.1f}")

    try:
        sma50 = av_client.sma(symbol, time_period=50)
        sma200 = av_client.sma(symbol, time_period=200)
    except Exception:  # noqa: BLE001
        sma50 = []
        sma200 = []
    if sma50 and sma200:
        v50 = sma50[-1][1]
        v200 = sma200[-1][1]
        signals["sma50"] = v50
        signals["sma200"] = v200
        cross = "golden cross" if v50 > v200 else "death cross"
        lines.append(f"SMA50 ${v50:.1f} · SMA200 ${v200:.1f} ({cross})")

    return lines, signals


# ---- Live snapshot (Polygon REST) -----------------------------------

def _call_snapshot(pg_client, symbol: str) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    if pg_client is None:
        return lines, signals

    try:
        snap = pg_client.get_ticker_snapshot(symbol)
    except Exception:  # noqa: BLE001
        snap = None
    if isinstance(snap, dict):
        day = snap.get("day") if isinstance(snap.get("day"), dict) else {}
        prev = snap.get("prevDay") if isinstance(snap.get("prevDay"), dict) else {}
        price = _as_float(day.get("c"))
        prev_c = _as_float(prev.get("c"))
        hi = _as_float(day.get("h"))
        lo = _as_float(day.get("l"))
        vol = _as_float(day.get("v"))
        if price is not None:
            signals["price"] = price
            if prev_c and prev_c > 0:
                pct = (price - prev_c) / prev_c * 100
                signals["day_pct"] = pct
                lines.append(f"${price:,.2f} ({_fmt_pct(pct)}) prev close ${prev_c:,.2f}")
            else:
                lines.append(f"${price:,.2f}")
        if hi and lo:
            lines.append(f"Day range: ${lo:,.2f} — ${hi:,.2f}")
        if vol:
            signals["volume"] = vol
            if vol >= 1_000_000:
                lines.append(f"Volume: {vol/1_000_000:.1f}M")
            else:
                lines.append(f"Volume: {vol/1_000:.0f}K")

    try:
        det = pg_client.get_ticker_details(symbol)
    except Exception:  # noqa: BLE001
        det = None
    if isinstance(det, dict):
        name = det.get("name")
        sector = det.get("sic_description")
        if name and sector:
            lines.append(f"{name} · {sector}")
        elif name:
            lines.append(str(name))

    return lines, signals


# ---- News (Tiingo + Polygon) ----------------------------------------

def _call_news(tiingo_client, pg_client, symbol: str
               ) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}

    headlines: List[Tuple[str, str]] = []  # (title, source)
    if tiingo_client is not None:
        try:
            news = tiingo_client.get_news([symbol], limit=5)
        except Exception:  # noqa: BLE001
            news = []
        for item in (news or [])[:3]:
            if isinstance(item, dict):
                t = item.get("title")
                s = item.get("source") or ""
                if isinstance(t, str) and t.strip():
                    headlines.append((t.strip(), str(s).strip()))

    if pg_client is not None and len(headlines) < 3:
        try:
            pnews = pg_client.get_news(symbol, limit=5)
        except Exception:  # noqa: BLE001
            pnews = []
        for item in (pnews or []):
            if len(headlines) >= 3:
                break
            if isinstance(item, dict):
                t = item.get("title")
                s_obj = item.get("publisher") or {}
                s = s_obj.get("name") if isinstance(s_obj, dict) else ""
                if isinstance(t, str) and t.strip():
                    headlines.append((t.strip(), str(s or "").strip()))

    for i, (title, src) in enumerate(headlines[:3], start=1):
        if len(title) > 110:
            title = title[:109].rstrip() + "…"
        if src:
            lines.append(f"{i}. \"{title}\" — {src}")
        else:
            lines.append(f"{i}. \"{title}\"")
    signals["news_count"] = len(headlines)
    return lines, signals


# ---- Catalysts + risks (Perplexity, two queries) --------------------

def _call_perplexity(pxclient, symbol: str
                     ) -> Tuple[List[str], List[str]]:
    """Returns (catalysts_lines, risks_lines)."""
    if pxclient is None:
        return [], []

    def _bullets(q: str, max_bullets: int = 3) -> List[str]:
        try:
            rep = pxclient.research_stock(symbol, q)
        except Exception:  # noqa: BLE001
            return []
        findings = getattr(rep, "findings", None) or []
        out = []
        for f in findings[:max_bullets]:
            s = getattr(f, "summary", None)
            if isinstance(s, str) and s.strip():
                out.append("• " + s.strip())
        return out

    cat_q = ("In 3 very short bullets, list the single biggest POSITIVE "
             "catalyst or tailwind for this stock RIGHT NOW. Each bullet "
             "under 18 words. If nothing material, say so.")
    risk_q = ("In 3 very short bullets, list the single biggest NEGATIVE "
              "risk or headwind for this stock RIGHT NOW. Each bullet "
              "under 18 words. If nothing material, say so.")
    return _bullets(cat_q), _bullets(risk_q)


# ---------------------------------------------------------------------------
# Verdict synthesizer (deterministic)
# ---------------------------------------------------------------------------

def _synthesize_verdict(signals: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    concerns: List[str] = []
    positives: List[str] = []

    # Technicals
    rsi = signals.get("rsi")
    if isinstance(rsi, (int, float)):
        if rsi >= 75:
            concerns.append(f"RSI {rsi:.0f} overbought")
        elif rsi <= 25:
            positives.append(f"RSI {rsi:.0f} oversold (reversal setup)")
    macd = signals.get("macd_signal")
    if macd == "bullish_crossover":
        positives.append("MACD just crossed bullish")
    elif macd == "bearish_crossover":
        concerns.append("MACD just crossed bearish")

    # Fundamentals
    pe = signals.get("pe")
    if isinstance(pe, (int, float)):
        if pe > 60:
            concerns.append(f"P/E {pe:.0f} is rich")
    nxt = signals.get("next_earnings_days")
    if isinstance(nxt, int):
        if 0 <= nxt <= 7:
            concerns.append(f"Earnings in {nxt}d (volatility risk)")
    buys = signals.get("analyst_buys")
    tot = signals.get("analyst_total")
    if isinstance(buys, int) and isinstance(tot, int) and tot > 0:
        ratio = buys / tot
        if ratio >= 0.65:
            positives.append(f"Analyst majority BUY ({buys}/{tot})")
        elif ratio <= 0.35:
            concerns.append(f"Analyst majority NOT buying ({buys}/{tot})")

    # Macro
    regime = signals.get("regime")
    if regime == "risk_off":
        concerns.append("Macro regime RISK_OFF")
    elif regime == "risk_on":
        positives.append("Macro regime RISK_ON")

    # Compose verdict text
    if positives:
        lines.append("+ " + " · ".join(positives))
    if concerns:
        lines.append("- " + " · ".join(concerns))
    if not lines:
        lines.append("No strong signals either way.")
    return lines


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------

class DeepResearchComposer:
    """Runs all section fetchers in parallel and assembles a
    DeepResearchReport. Thread pool caps concurrency and gives a hard
    deadline so a slow source does not block the proposal."""

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
        deadline_seconds: float = 30.0,
    ) -> None:
        self._fred = fred
        self._finnhub = finnhub
        self._av = alpha_vantage
        self._polygon = polygon
        self._tiingo = tiingo
        self._perplexity = perplexity
        self._max_workers = max_workers
        self._deadline = deadline_seconds

    def enrich(self, symbol: str) -> Optional[str]:
        """Protocol-compatible with the earlier CompositeEnricher so
        the Engine's self._enrich() wrapper works unchanged."""
        report = self.build_report(symbol)
        return report.to_telegram_text()

    def build_report(self, symbol: str) -> DeepResearchReport:
        report = DeepResearchReport(symbol=symbol)
        all_signals: Dict[str, object] = {}
        any_source = any([
            self._fred, self._finnhub, self._av,
            self._polygon, self._tiingo, self._perplexity,
        ])
        if not any_source:
            return report  # verdict stays empty too

        tasks: Dict[str, _fut.Future] = {}
        with _fut.ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            tasks["macro"] = ex.submit(_fetch_macro, self._fred)
            tasks["fundamentals"] = ex.submit(_call_fundamentals,
                                              self._finnhub, symbol)
            tasks["technicals"] = ex.submit(_call_technicals,
                                             self._av, symbol)
            tasks["snapshot"] = ex.submit(_call_snapshot,
                                           self._polygon, symbol)
            tasks["news"] = ex.submit(_call_news,
                                       self._tiingo, self._polygon, symbol)
            tasks["perplexity"] = ex.submit(_call_perplexity,
                                             self._perplexity, symbol)

            for name, fut in tasks.items():
                try:
                    result = fut.result(timeout=self._deadline)
                except Exception:  # noqa: BLE001
                    continue
                if name == "perplexity":
                    cat, risk = result
                    report.catalysts_lines = cat
                    report.risks_lines = risk
                else:
                    lines, signals = result
                    if name == "macro":
                        report.macro_lines = lines
                    elif name == "fundamentals":
                        report.fundamentals_lines = lines
                    elif name == "technicals":
                        report.technicals_lines = lines
                    elif name == "snapshot":
                        report.snapshot_lines = lines
                    elif name == "news":
                        report.news_lines = lines
                    all_signals.update(signals)

        report.signals = all_signals
        report.verdict_lines = _synthesize_verdict(all_signals)
        return report
