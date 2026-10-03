"""Political-signal universe source (D-0050 Phase B.27).

Each trading day, this source fetches the latest Congressional trades
from every configured provider, aggregates them, and emits the Top-N
tickers (by weighted political signal) as additional candidates for
the normal ranker + evaluator pipeline. No approval step; the
whitelist + weighting + hard-filter protections upstream are
sufficient. Final proposal still requires Controller approval
through the standard flow.

Cache: results are memoized for one trading day so the trigger-check
loop can call `.get_active_symbols()` every 60 minutes without
re-hitting APIs. The cache is implicit — a daily call to
`refresh_if_stale(now)` is enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from research.political_aggregator import PoliticalAggregator, PoliticalTrade
from research.political_cluster import TickerPoliticalSignal, build_signals


@dataclass
class PoliticalUniverseConfig:
    max_symbols_per_day: int = 5
    min_signal_strength: float = 5.0   # weighted_signal threshold
    include_committee_matched_first: bool = True


DEFAULT_CONFIG = PoliticalUniverseConfig()


class PoliticalUniverseSource:
    """Returns the Top-N tickers the whitelist has been buying,
    refreshed once per day. Also exposes signal data so the engine
    can enrich SymbolResearch for every candidate it ranks (not just
    the ones this source added)."""

    def __init__(
        self,
        aggregator: PoliticalAggregator,
        config: PoliticalUniverseConfig = DEFAULT_CONFIG,
    ) -> None:
        self._agg = aggregator
        self._cfg = config
        self._cached_date: Optional[date] = None
        self._cached_tickers: Tuple[str, ...] = ()
        self._cached_signals: Dict[str, TickerPoliticalSignal] = {}
        self._cached_trades: Tuple[PoliticalTrade, ...] = ()

    # ---- universe interface -----------------------------------------

    def get_active_symbols(self) -> Tuple[str, ...]:
        today = date.today()
        self._refresh_if_stale(today)
        return self._cached_tickers

    # ---- signal map (used by the engine to enrich SymbolResearch) ---

    def get_signals(self) -> Dict[str, TickerPoliticalSignal]:
        today = date.today()
        self._refresh_if_stale(today)
        return dict(self._cached_signals)

    # ---- internals --------------------------------------------------

    def _refresh_if_stale(self, today: date) -> None:
        if self._cached_date == today:
            return
        try:
            trades = self._agg.fetch_all()
        except Exception:  # noqa: BLE001
            trades = []
        self._cached_trades = tuple(trades)
        try:
            signals = build_signals(list(trades), now=today)
        except Exception:  # noqa: BLE001
            signals = {}
        self._cached_signals = signals
        self._cached_tickers = self._top_tickers(signals)
        self._cached_date = today

    def _top_tickers(
        self, signals: Dict[str, TickerPoliticalSignal],
    ) -> Tuple[str, ...]:
        cfg = self._cfg
        viable = [
            s for s in signals.values()
            if s.weighted_signal >= cfg.min_signal_strength
            and s.politician_buys_30d >= 1
            and not s.sell_wave
        ]
        if cfg.include_committee_matched_first:
            viable.sort(
                key=lambda s: (-int(s.committee_match),
                                -s.weighted_signal,
                                -s.politician_buys_30d))
        else:
            viable.sort(key=lambda s: (-s.weighted_signal,
                                        -s.politician_buys_30d))
        return tuple(s.ticker for s in viable[: cfg.max_symbols_per_day])
