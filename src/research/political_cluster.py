"""Political-trade clustering + ticker signal scoring
(D-0050 Phase B.24).

Given a list of PoliticalTrade records (from the aggregator), builds
a per-ticker signal composed of:

  politician_buys_30d        — count of whitelisted BUYs in 30 days
  politician_sells_30d       — count of whitelisted SELLs in 30 days
  politician_recent_names    — unique names (preserving order)
  politician_cluster_score   — 0..20, scaled by distinct buyer count
  politician_committee_match — bool: 1+ buyer sits on relevant committee
  politician_weighted_signal — composite 0..25
                                = clustering + committee bonus +
                                  alpha-weight bonus
  sell_wave                  — 3+ whitelisted SELLs in 30 days → True
                                (triggers hard-filter reject upstream)

All advisory. The pipeline downstream (SymbolResearch + evaluator)
treats weighted_signal as a soft score contribution, not as a trade
instruction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from research.political_aggregator import PoliticalTrade
from research.politicians import lookup
from research.committee_mapper import committee_matches_sector


_LOOKBACK_DAYS = 30
_CLUSTER_WINDOW_DAYS = 14


@dataclass
class TickerPoliticalSignal:
    ticker: str
    politician_buys_30d: int = 0
    politician_sells_30d: int = 0
    recent_names: List[str] = field(default_factory=list)
    cluster_score: float = 0.0
    committee_match: bool = False
    weighted_signal: float = 0.0
    sell_wave: bool = False

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "buys_30d": self.politician_buys_30d,
            "sells_30d": self.politician_sells_30d,
            "names": list(self.recent_names),
            "cluster_score": round(self.cluster_score, 2),
            "committee_match": self.committee_match,
            "weighted_signal": round(self.weighted_signal, 2),
            "sell_wave": self.sell_wave,
        }


def build_signals(
    trades: List[PoliticalTrade],
    *,
    now: Optional[date] = None,
    sector_lookup: Optional[Dict[str, str]] = None,
) -> Dict[str, TickerPoliticalSignal]:
    """Returns {ticker: TickerPoliticalSignal} aggregated across all
    whitelisted politicians' trades.

    ``sector_lookup`` is an optional {ticker: sic_description} map used
    for the committee-relevance test. When missing, committee_match
    silently stays False (fail-open).
    """
    if now is None:
        now = date.today()
    sector_lookup = sector_lookup or {}

    cutoff_30d = now - timedelta(days=_LOOKBACK_DAYS)
    cutoff_cluster = now - timedelta(days=_CLUSTER_WINDOW_DAYS)

    # Group trades by ticker.
    by_ticker: Dict[str, List[PoliticalTrade]] = defaultdict(list)
    for t in trades:
        if t.ticker == "PENDING":
            # eDisclosure stubs: we count the signal that an insider
            # filed, but without a ticker there's nothing to attribute.
            continue
        if t.trade_date < cutoff_30d:
            continue
        by_ticker[t.ticker].append(t)

    signals: Dict[str, TickerPoliticalSignal] = {}
    for ticker, bucket in by_ticker.items():
        sig = _signal_for_ticker(ticker, bucket, now,
                                  cutoff_cluster, sector_lookup)
        if (sig.politician_buys_30d == 0
                and sig.politician_sells_30d == 0):
            continue
        signals[ticker] = sig
    return signals


def _signal_for_ticker(
    ticker: str,
    trades: List[PoliticalTrade],
    now: date,
    cutoff_cluster: date,
    sector_lookup: Dict[str, str],
) -> TickerPoliticalSignal:
    sig = TickerPoliticalSignal(ticker=ticker)

    buyers_30d: Set[str] = set()
    sellers_30d: Set[str] = set()
    recent_cluster_buyers: Set[str] = set()
    alpha_sum = 0.0
    committee_match_any = False

    sic = sector_lookup.get(ticker)

    for t in trades:
        if t.action == "BUY":
            buyers_30d.add(t.politician_name)
            if t.trade_date >= cutoff_cluster:
                recent_cluster_buyers.add(t.politician_name)
            prof = lookup(t.politician_name)
            if prof is not None:
                alpha_sum += (prof.alpha_weight - 1.0)
            if committee_matches_sector(t.politician_name, sic):
                committee_match_any = True
        elif t.action == "SELL":
            sellers_30d.add(t.politician_name)

    sig.politician_buys_30d = len(buyers_30d)
    sig.politician_sells_30d = len(sellers_30d)
    sig.recent_names = sorted(buyers_30d | sellers_30d)
    sig.committee_match = committee_match_any
    sig.sell_wave = len(sellers_30d) >= 3

    # Clustering: 1 unique buyer in last 14d → 0, 3 → 10, 5+ → 20.
    n_cluster = len(recent_cluster_buyers)
    if n_cluster <= 1:
        sig.cluster_score = 0.0
    else:
        # Linear ramp capped at 20.
        sig.cluster_score = min(20.0, (n_cluster - 1) * 5.0)

    # Composite weighted signal (0..25):
    #   cluster_score (0..20)
    # + committee bonus (3 if any match)
    # + alpha bonus (up to 2 if top-alpha politicians involved)
    composite = sig.cluster_score
    if committee_match_any:
        composite += 3.0
    composite += min(2.0, max(0.0, alpha_sum))
    sig.weighted_signal = min(25.0, composite)

    return sig
