"""Composite research enrichers for proposal notifications (D-0050).

Each enricher wraps a data-source client with a strict fail-open
contract and produces a short text block for the proposal message.
The CompositeEnricher runs all configured sub-enrichers and joins
their outputs. Any sub-enricher that fails or returns empty is
silently skipped — a proposal is NEVER blocked on enrichment.

Enrichers (all advisory, per CLAUDE.md §5):
  - PerplexityEnricher    (news/catalysts summary)
  - FinnhubEnricher       (next earnings date + market cap + P/E)
  - AlphaVantageEnricher  (latest RSI + MACD signal)
  - TiingoEnricher        (latest news headline)
  - PolygonEnricher       (today's snapshot: price/change/volume)

All sub-enrichers implement:
    enrich(symbol: str) -> Optional[str]
and MUST NOT raise. Returned string is a single short line (or multi-
line block for Perplexity) that will be joined with newlines.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Protocol


class Enricher(Protocol):
    def enrich(self, symbol: str) -> Optional[str]: ...


# ---------------------------------------------------------------------------
# Perplexity (news/catalysts)
# ---------------------------------------------------------------------------

class PerplexityEnricher:
    _QUESTION = (
        "In 3 short bullets, summarize today's most important news or "
        "catalysts for this stock that a swing trader placing a new "
        "order right now would want to know. Keep each bullet under "
        "20 words. If nothing material is happening, say so plainly."
    )
    _MAX_CHARS = 300

    def __init__(self, client) -> None:
        self._client = client

    def enrich(self, symbol: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            report = self._client.research_stock(symbol, self._QUESTION)
        except Exception:  # noqa: BLE001
            return None
        text = _extract_perplexity_text(report)
        if not text:
            return None
        # strip only trailing/leading newlines, NOT per-line indent --
        # otherwise the first bullet loses its "  • " prefix.
        text = text.strip("\n").rstrip()
        if not text:
            return None
        if len(text) > self._MAX_CHARS:
            text = text[: self._MAX_CHARS - 1].rstrip() + "…"
        return "🔍 Research:\n" + text


def _extract_perplexity_text(report) -> Optional[str]:
    try:
        findings = getattr(report, "findings", None)
        if findings:
            lines = []
            for f in findings:
                s = getattr(f, "text", None) or getattr(f, "summary", None)
                if isinstance(s, str) and s.strip():
                    lines.append("  • " + s.strip())
            if lines:
                return "\n".join(lines)
        summary = getattr(report, "text", None) or getattr(report, "summary", None)
        if isinstance(summary, str) and summary.strip():
            return summary
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Finnhub (next earnings + fundamentals)
# ---------------------------------------------------------------------------

class FinnhubEnricher:
    """Produces a short fundamentals line.

    Fields considered:
      - metric.peBasicExclExtraTTM (P/E ratio)
      - metric.marketCapitalization (in millions USD, Finnhub convention)
      - 52WeekHigh / 52WeekLow (for context)
    """

    def __init__(self, client) -> None:
        self._client = client

    def enrich(self, symbol: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            fin = self._client.basic_financials(symbol)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(fin, dict):
            return None
        m = fin.get("metric")
        if not isinstance(m, dict):
            return None

        parts: List[str] = []
        pe = _as_float(m.get("peBasicExclExtraTTM"))
        if pe is not None and pe > 0:
            parts.append(f"P/E {pe:.1f}")
        mcap_m = _as_float(m.get("marketCapitalization"))
        if mcap_m is not None and mcap_m > 0:
            parts.append(f"MarketCap {_format_market_cap(mcap_m)}")
        high52 = _as_float(m.get("52WeekHigh"))
        low52 = _as_float(m.get("52WeekLow"))
        if high52 and low52 and high52 > low52:
            parts.append(f"52w ${low52:.1f}-${high52:.1f}")

        if not parts:
            return None
        return "💼 Fundamentals: " + " · ".join(parts)


def _as_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _format_market_cap(value_millions_usd: float) -> str:
    """Finnhub returns market cap in millions. 800000 → $800B."""
    v = value_millions_usd
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}T"
    if v >= 1_000:
        return f"${v/1_000:.1f}B"
    return f"${v:.0f}M"


# ---------------------------------------------------------------------------
# Alpha Vantage (RSI + MACD)
# ---------------------------------------------------------------------------

class AlphaVantageEnricher:
    """Produces a short technicals line from the latest daily RSI and
    MACD values."""

    def __init__(self, client) -> None:
        self._client = client

    def enrich(self, symbol: str) -> Optional[str]:
        if self._client is None:
            return None
        parts: List[str] = []
        try:
            rsi_series = self._client.rsi(symbol)
            if rsi_series:
                rsi_val = rsi_series[-1][1]
                tag = "overbought" if rsi_val >= 70 else (
                    "oversold" if rsi_val <= 30 else "neutral")
                parts.append(f"RSI {rsi_val:.0f} ({tag})")
        except Exception:  # noqa: BLE001
            pass
        try:
            macd_series = self._client.macd(symbol)
            if macd_series and len(macd_series) >= 2:
                prev = macd_series[-2][1]
                curr = macd_series[-1][1]
                prev_hist = prev.get("hist") if isinstance(prev, dict) else None
                curr_hist = curr.get("hist") if isinstance(curr, dict) else None
                if isinstance(prev_hist, (int, float)) and isinstance(curr_hist, (int, float)):
                    if curr_hist > 0 and prev_hist <= 0:
                        parts.append("MACD bullish crossover")
                    elif curr_hist < 0 and prev_hist >= 0:
                        parts.append("MACD bearish crossover")
                    elif curr_hist > 0:
                        parts.append("MACD bullish")
                    else:
                        parts.append("MACD bearish")
        except Exception:  # noqa: BLE001
            pass

        if not parts:
            return None
        return "📈 Technicals: " + " · ".join(parts)


# ---------------------------------------------------------------------------
# Tiingo (latest news headline)
# ---------------------------------------------------------------------------

class TiingoEnricher:
    _MAX_HEADLINE_CHARS = 120

    def __init__(self, client) -> None:
        self._client = client

    def enrich(self, symbol: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            news = self._client.get_news([symbol], limit=3)
        except Exception:  # noqa: BLE001
            return None
        if not news:
            return None
        top = news[0]
        if not isinstance(top, dict):
            return None
        title = top.get("title")
        if not isinstance(title, str) or not title.strip():
            return None
        title = title.strip()
        if len(title) > self._MAX_HEADLINE_CHARS:
            title = title[: self._MAX_HEADLINE_CHARS - 1].rstrip() + "…"
        source = top.get("source") or ""
        if isinstance(source, str) and source:
            return f"📰 News: \"{title}\" — {source}"
        return f"📰 News: \"{title}\""


# ---------------------------------------------------------------------------
# Polygon (today's snapshot)
# ---------------------------------------------------------------------------

class PolygonEnricher:
    def __init__(self, client) -> None:
        self._client = client

    def enrich(self, symbol: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            snap = self._client.get_ticker_snapshot(symbol)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(snap, dict):
            return None
        day = snap.get("day") if isinstance(snap.get("day"), dict) else {}
        prev = snap.get("prevDay") if isinstance(snap.get("prevDay"), dict) else {}
        price = _as_float(day.get("c") or snap.get("lastTrade", {}).get("p"))
        prev_close = _as_float(prev.get("c"))
        volume = _as_float(day.get("v"))

        parts: List[str] = []
        if price is not None:
            if prev_close and prev_close > 0:
                pct = (price - prev_close) / prev_close * 100
                sign = "+" if pct >= 0 else ""
                parts.append(f"${price:,.2f} ({sign}{pct:.2f}%)")
            else:
                parts.append(f"${price:,.2f}")
        high = _as_float(day.get("h"))
        low = _as_float(day.get("l"))
        if high and low:
            parts.append(f"H ${high:,.2f} / L ${low:,.2f}")
        if volume is not None and volume > 0:
            parts.append(f"Vol {_format_volume(volume)}")

        if not parts:
            return None
        return "🎯 Snapshot: " + " · ".join(parts)


def _format_volume(v: float) -> str:
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

class CompositeEnricher:
    """Runs every configured sub-enricher and joins non-empty outputs
    with blank-line separators. Order is deterministic (constructor
    order). Each sub-enricher's failure is caught here as a second
    safety net — a buggy sub-enricher never affects the others."""

    _HEADER = "— Research (advisory):"

    def __init__(self, sub_enrichers: List[Enricher]) -> None:
        self._subs = [e for e in sub_enrichers if e is not None]

    def enrich(self, symbol: str) -> Optional[str]:
        if not self._subs:
            return None
        lines: List[str] = []
        for sub in self._subs:
            try:
                out = sub.enrich(symbol)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(out, str) and out.strip():
                # Strip trailing newlines only; keep leading indentation
                # on multi-line blocks (e.g. Perplexity's bullet list).
                lines.append(out.strip("\n").rstrip())
        if not lines:
            return None
        return self._HEADER + "\n" + "\n".join(lines)
