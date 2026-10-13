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
    snapshot_is_previous_session: bool = False
    """P-092. True when the snapshot section holds the PREVIOUS
    session, not today.

    PolygonSource.get_ticker_snapshot falls back from /v2/snapshot (paid
    tiers) to /v2/aggs/ticker/{sym}/prev (free tier) and reshapes the
    previous session into a `day` block. The fallback stamps itself
    `_fallback_source`, but nothing read that stamp, so a closed
    session's bar was printed under the heading "Live snapshot" -- it was
    byte-identical an hour apart on 2026-10-07, and its range
    contradicted the engine's own range for the same day inside one
    message."""

    def to_telegram_text(self) -> Optional[str]:
        """Returns the multi-line Telegram block, or None if no section
        produced any data (which means every source was down)."""
        sections: List[Tuple[str, List[str]]] = [
            ("🌐 Macro",            self.macro_lines),
            ("🏢 Fundamentals",     self.fundamentals_lines),
            ("📈 Technicals",       self.technicals_lines),
            ("📅 Previous session" if self.snapshot_is_previous_session
             else "🎯 Live snapshot", self.snapshot_lines),
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

def _fetch_macro(research) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    if research.regime is None:
        return lines, signals
    signals["regime"] = research.regime
    if research.vix_percentile is not None:
        signals["vix_percentile"] = research.vix_percentile
    if research.vix_level is not None:
        signals["vix_level"] = research.vix_level
    if research.fed_funds_rate is not None:
        signals["fed_funds_rate"] = research.fed_funds_rate
    if research.yield_curve_spread is not None:
        signals["yield_curve_spread"] = research.yield_curve_spread
    line = f"Regime: {research.regime.upper()}"
    if research.vix_percentile is not None:
        line += f" (VIX pct {research.vix_percentile*100:.0f})"
    if research.vix_level is not None:
        line += f" · VIX {research.vix_level:.1f}"
    lines.append(line)
    if research.fed_funds_rate is not None or research.yield_curve_spread is not None:
        parts = []
        if research.fed_funds_rate is not None:
            parts.append(f"Fed {research.fed_funds_rate:.2f}%")
        if research.yield_curve_spread is not None:
            parts.append(f"10Y-2Y {research.yield_curve_spread:+.2f}")
        lines.append(" · ".join(parts))
    return lines, signals


# ---- Fundamentals (Finnhub) -----------------------------------------

def _call_fundamentals(research) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}

    pe = research.pe_ratio
    mcap = research.market_cap_millions
    hi = research.high_52w
    lo = research.low_52w
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

    if research.next_earnings_date is not None:
        signals["next_earnings_days"] = research.next_earnings_days
        line = (f"Next earnings: {research.next_earnings_date.isoformat()} "
                f"({research.next_earnings_days}d)")
        if research.eps_estimate is not None:
            line += f" · EPS est ${research.eps_estimate:.2f}"
        lines.append(line)

    buys = research.analyst_buys
    holds = research.analyst_holds
    sells = research.analyst_sells
    if buys is not None and holds is not None and sells is not None:
        total = buys + holds + sells
        if total > 0:
            signals["analyst_buys"] = buys
            signals["analyst_total"] = total
            consensus = "BUY" if buys > (holds + sells) else (
                "SELL" if sells > (buys + holds) else "HOLD")
            lines.append(f"Analyst consensus: {consensus} "
                         f"({buys} buy / {holds} hold / {sells} sell)")

    mspr = research.insider_mspr
    if mspr is not None:
        signals["insider_mspr"] = mspr
        tag = "bullish" if mspr > 20 else ("bearish" if mspr < -20 else "neutral")
        lines.append(f"Insider sentiment: {mspr:+.1f} ({tag})")

    return lines, signals


# ---- Technicals (Alpha Vantage) -------------------------------------

def _call_technicals(research) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}

    rsi = research.rsi_14
    if rsi is not None:
        signals["rsi"] = rsi
        tag = "overbought ⚠" if rsi >= 70 else (
            "oversold ⚠" if rsi <= 30 else "neutral")
        lines.append(f"RSI(14): {rsi:.0f} ({tag})")

    macd_signal = research.macd_signal
    if macd_signal is not None:
        signals["macd_hist"] = research.macd_hist
        signals["macd_signal"] = macd_signal
        text = {
            "bullish_crossover": "MACD: bullish crossover (fresh)",
            "bearish_crossover": "MACD: bearish crossover (fresh)",
            "bullish": "MACD: bullish momentum",
            "bearish": "MACD: bearish momentum",
        }.get(macd_signal)
        if text:
            lines.append(text)

    if research.bb_upper and research.bb_lower and research.bb_middle:
        signals["bb_upper"] = research.bb_upper
        signals["bb_lower"] = research.bb_lower
        lines.append(f"BBands(20): ${research.bb_lower:.1f} / "
                     f"${research.bb_middle:.1f} / ${research.bb_upper:.1f}")

    if research.sma_50 is not None and research.sma_200 is not None:
        signals["sma50"] = research.sma_50
        signals["sma200"] = research.sma_200
        cross = "golden cross" if research.sma_50 > research.sma_200 else "death cross"
        lines.append(f"SMA50 ${research.sma_50:.1f} · "
                     f"SMA200 ${research.sma_200:.1f} ({cross})")

    return lines, signals


# ---- Live snapshot (Polygon REST) -----------------------------------

def _call_snapshot(research) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}

    if research.snapshot_is_previous_session:
        signals["snapshot_is_previous_session"] = True

    price = research.current_price
    prev_c = research.prev_close
    if price is not None:
        signals["price"] = price
        if prev_c and prev_c > 0:
            pct = research.day_change_pct
            if pct is None:
                pct = (price - prev_c) / prev_c * 100
            signals["day_pct"] = pct
            lines.append(f"${price:,.2f} ({_fmt_pct(pct)}) prev close ${prev_c:,.2f}")
        else:
            lines.append(f"${price:,.2f}")
    if research.day_high and research.day_low:
        lines.append(f"Day range: ${research.day_low:,.2f} — ${research.day_high:,.2f}")
    if research.day_volume:
        signals["volume"] = research.day_volume
        if research.day_volume >= 1_000_000:
            lines.append(f"Volume: {research.day_volume/1_000_000:.1f}M")
        else:
            lines.append(f"Volume: {research.day_volume/1_000:.0f}K")

    if research.company_name and research.sector:
        lines.append(f"{research.company_name} · {research.sector}")
    elif research.company_name:
        lines.append(str(research.company_name))

    return lines, signals


# ---- News (Tiingo + Polygon, already merged by the hub) --------------

def _call_news(research) -> Tuple[List[str], Dict[str, object]]:
    lines: List[str] = []
    signals: Dict[str, object] = {}
    headlines = research.news_headlines[:3]
    for i, (title, src) in enumerate(headlines, start=1):
        if len(title) > 110:
            title = title[:109].rstrip() + "…"
        if src:
            lines.append(f"{i}. \"{title}\" — {src}")
        else:
            lines.append(f"{i}. \"{title}\"")
    signals["news_count"] = len(headlines)
    return lines, signals


# ---- Catalysts + risks (already fetched + filtered by the hub) ------

def _call_perplexity(research) -> Tuple[List[str], List[str]]:
    """Returns (catalysts_lines, risks_lines)."""
    cat = ["• " + line for line in research.perplexity_catalysts]
    risk = ["• " + line for line in research.perplexity_risks]
    return cat, risk


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
        the Engine's self._enrich() wrapper works unchanged.

        D-0092 (2026-10-12): collects research data ONCE via
        SymbolResearchHub.collect(), then derives both the Telegram
        report AND the trade evaluation from that single SymbolResearch
        object. Before this, enrich() ran build_report() (its own
        6-source parallel fetch, 30s deadline) and THEN
        evaluator.evaluate() (a SECOND, separate 6-source parallel
        fetch via its own hub, 60s deadline) sequentially — the same
        six APIs hit twice per proposal, worst case 30s+60s=90s, which
        is exactly engine.ENRICHMENT_TIMEOUT_SECONDS and the reason
        research reports were being dropped (see that constant's
        docstring). Collecting once removes the duplicate fetch
        entirely and halves the worst case to the hub's own 60s
        deadline."""
        any_source = any([self._fred, self._finnhub, self._av,
                          self._polygon, self._tiingo, self._perplexity])
        if not any_source:
            return None

        from engine.research_hub import SymbolResearchHub
        from engine.trade_evaluator import TradeEvaluator, format_evaluation_block

        hub = SymbolResearchHub(
            fred=self._fred, finnhub=self._finnhub,
            alpha_vantage=self._av, polygon=self._polygon,
            tiingo=self._tiingo, perplexity=self._perplexity,
            deadline_seconds=self._deadline,
        )
        research = hub.collect(symbol)

        report = self.build_report(research)
        base = report.to_telegram_text()

        evaluation_block = None
        try:
            evaluator = TradeEvaluator(hub)
            result = evaluator.evaluate_research(research)
            evaluation_block = format_evaluation_block(result)
        except Exception:  # noqa: BLE001
            pass

        if base and evaluation_block:
            return base + "\n" + evaluation_block
        return base or evaluation_block

    def build_report(self, research) -> DeepResearchReport:
        """Builds the Telegram report text from an already-collected
        SymbolResearch (see enrich()). No I/O happens here -- every
        section is a pure formatting pass over `research`'s fields."""
        report = DeepResearchReport(symbol=research.symbol)
        all_signals: Dict[str, object] = {}

        macro_lines, macro_sig = _fetch_macro(research)
        report.macro_lines = macro_lines
        all_signals.update(macro_sig)

        fund_lines, fund_sig = _call_fundamentals(research)
        report.fundamentals_lines = fund_lines
        all_signals.update(fund_sig)

        tech_lines, tech_sig = _call_technicals(research)
        report.technicals_lines = tech_lines
        all_signals.update(tech_sig)

        snap_lines, snap_sig = _call_snapshot(research)
        report.snapshot_lines = snap_lines
        all_signals.update(snap_sig)

        news_lines, news_sig = _call_news(research)
        report.news_lines = news_lines
        all_signals.update(news_sig)

        cat_lines, risk_lines = _call_perplexity(research)
        report.catalysts_lines = cat_lines
        report.risks_lines = risk_lines

        report.snapshot_is_previous_session = bool(
            all_signals.get("snapshot_is_previous_session"))
        report.signals = all_signals
        report.verdict_lines = _synthesize_verdict(all_signals)
        return report
