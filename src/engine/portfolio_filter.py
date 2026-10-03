"""Portfolio-level diversification filter (D-0050 Phase 14).

Applied AFTER TradeEvaluator ranks candidates, BEFORE they become
proposals. Prevents the Top-N from being concentrated in a single
sector or in highly-correlated names.

Two independent gates:

  1. Sector cap: no more than max_per_sector picks share one sector
     (Polygon's sic_description, bucketed to a short name).
  2. Correlation cap: no two picks have 30-day return correlation
     above correlation_cap. The second (lower-score) pick is dropped.

Fail-open: missing sector OR missing return history is NOT a reason
to drop — the gate only fires when BOTH candidates have the data to
compare. This keeps the filter from accidentally rejecting the whole
ranker when APIs are degraded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from engine.research_hub import SymbolResearch


@dataclass(frozen=True)
class PortfolioFilterConfig:
    max_per_sector: int = 1
    correlation_cap: float = 0.70
    sector_lookup_bucket_map: Tuple[Tuple[str, str], ...] = (
        # Lowercased substrings of Polygon's sic_description → bucket.
        ("semiconductor", "Semiconductors"),
        ("computer", "Software/Hardware"),
        ("software", "Software/Hardware"),
        ("services-prepackaged", "Software/Hardware"),
        ("telecom", "Communications"),
        ("broadcast", "Communications"),
        ("pharmaceutical", "Healthcare"),
        ("medical", "Healthcare"),
        ("hospital", "Healthcare"),
        ("bank", "Financials"),
        ("insurance", "Financials"),
        ("investment", "Financials"),
        ("retail", "Consumer-Discretionary"),
        ("restaurant", "Consumer-Discretionary"),
        ("motor vehicle", "Consumer-Discretionary"),
        ("beverage", "Consumer-Staples"),
        ("food", "Consumer-Staples"),
        ("tobacco", "Consumer-Staples"),
        ("petroleum", "Energy"),
        ("oil", "Energy"),
        ("gas distribution", "Energy"),
        ("aircraft", "Industrials"),
        ("industrial", "Industrials"),
        ("transport", "Industrials"),
        ("utilit", "Utilities"),
        ("real estate", "Real-Estate"),
        ("reit", "Real-Estate"),
    )


DEFAULT_CONFIG = PortfolioFilterConfig()


def _sector_bucket(sic_description: Optional[str],
                   cfg: PortfolioFilterConfig) -> Optional[str]:
    if not isinstance(sic_description, str) or not sic_description.strip():
        return None
    hay = sic_description.lower()
    for needle, bucket in cfg.sector_lookup_bucket_map:
        if needle in hay:
            return bucket
    # Fall back to the raw description as its own bucket.
    return sic_description.strip()


@dataclass
class FilteredPick:
    symbol: str
    score: float
    research: SymbolResearch
    accepted: bool
    rejection_reason: Optional[str] = None


class PortfolioFilter:
    """Applies the two gates in order (sector first, correlation
    second). Returns a parallel list of FilteredPicks so the caller
    can both (a) see what was dropped and (b) consume only the
    accepted ones."""

    def __init__(
        self,
        *,
        config: PortfolioFilterConfig = DEFAULT_CONFIG,
        closes_provider: Optional[
            Callable[[str], List[Tuple[object, float]]]] = None,
    ) -> None:
        self._cfg = config
        # Pluggable closes provider for correlation. In production the
        # engine will pass a PolygonSource.get_aggregates adapter; in
        # tests we pass synthetic data.
        self._closes = closes_provider

    def apply(
        self,
        ranked: Sequence[Tuple[str, float, SymbolResearch]],
    ) -> List[FilteredPick]:
        """``ranked`` is best-first. Keep each pick that passes BOTH
        gates against already-accepted picks."""
        out: List[FilteredPick] = []
        accepted_sectors: Dict[str, int] = {}
        accepted_symbols: List[str] = []
        accepted_returns_cache: Dict[str, List[float]] = {}

        for symbol, score, research in ranked:
            # ---- Gate 1: sector cap -----------------------------
            bucket = _sector_bucket(research.sector, self._cfg)
            if bucket is not None:
                if accepted_sectors.get(bucket, 0) >= self._cfg.max_per_sector:
                    out.append(FilteredPick(
                        symbol=symbol, score=score, research=research,
                        accepted=False,
                        rejection_reason=f"sector-cap ({bucket})"))
                    continue

            # ---- Gate 2: correlation cap ------------------------
            drop_reason: Optional[str] = None
            if self._closes is not None and accepted_symbols:
                try:
                    me = self._returns_series(symbol, accepted_returns_cache)
                except Exception:  # noqa: BLE001
                    me = None
                if me:
                    for other in accepted_symbols:
                        other_rets = accepted_returns_cache.get(other)
                        if not other_rets:
                            continue
                        rho = _pearson(me, other_rets)
                        if rho is not None and rho > self._cfg.correlation_cap:
                            drop_reason = (f"correlation {rho:.2f} > "
                                            f"{self._cfg.correlation_cap:.2f} "
                                            f"with {other}")
                            break
            if drop_reason:
                out.append(FilteredPick(
                    symbol=symbol, score=score, research=research,
                    accepted=False, rejection_reason=drop_reason))
                continue

            # ---- Accepted ----------------------------------------
            out.append(FilteredPick(
                symbol=symbol, score=score, research=research,
                accepted=True))
            accepted_symbols.append(symbol)
            if bucket is not None:
                accepted_sectors[bucket] = accepted_sectors.get(bucket, 0) + 1
            # Prime this symbol's returns in the cache so the NEXT
            # candidate can correlation-check against it.
            if self._closes is not None:
                try:
                    self._returns_series(symbol, accepted_returns_cache)
                except Exception:  # noqa: BLE001
                    pass

        return out

    def _returns_series(self, symbol: str,
                        cache: Dict[str, List[float]]) -> Optional[List[float]]:
        """Daily log returns for the last ~30 days, cached per symbol."""
        if symbol in cache:
            return cache[symbol]
        if self._closes is None:
            return None
        try:
            bars = self._closes(symbol)
        except Exception:  # noqa: BLE001
            cache[symbol] = []
            return []
        closes = [float(c) for _, c in bars if isinstance(c, (int, float))]
        if len(closes) < 10:
            cache[symbol] = []
            return []
        import math
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))
                if closes[i - 1] > 0]
        rets = rets[-30:]  # last 30 daily returns
        cache[symbol] = rets
        return rets


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    """Returns Pearson correlation, or None if series are too short
    or degenerate (zero stddev)."""
    import math
    n = min(len(a), len(b))
    if n < 5:
        return None
    xs = a[-n:]
    ys = b[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
    if sx == 0 or sy == 0:
        return None
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / n
    return cov / (sx * sy)
