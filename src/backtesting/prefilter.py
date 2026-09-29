"""D-0026 universe pre-filter for the backtest (B27d).

Runs the real D-0048 percentage-only stage evaluators against
historical bar data so a portfolio backtest can honestly reject
symbols the production pipeline would have rejected -- rather than
blindly assuming every symbol is fair game every day.

Design:
  - Input: bars_by_symbol (the same mapping the simulator uses) plus
    a UniverseSelectionConfig. No network calls, no external state.
  - For each request date `d`, this module:
        1. Builds a UniverseCandidate per symbol with >= min_bars
           trailing bars ending on or before `d`.
        2. Computes DailySecurityFeatures via the same formulas
           AlpacaFeatureEnricher uses (mean dollar volume over 20
           bars, mean TR over 14 bars, 30-bar return, mean range/
           vwap over 20 bars).
        3. Runs the D-0048 stages that are computable from bar data
           alone -- Tradability (A), Data Quality (B), Execution
           Quality (C), Strategy Fit (D), Ranking (F), Top-N (H).
           Regime Adaptation (E) runs but passes through under a
           neutral regime, and Concentration (G) passes through
           because we do not carry sector metadata in the backtest.
  - Output: a frozenset of approved symbols for that date. The
    simulator gates Initial Entry on membership in this set.

Symbols already in an open position are NOT affected by this filter;
they follow their ladder/floor rules to completion (mirrors production).

`AlpacaFeatureEnricher` formulas are re-used verbatim so the backtest's
selection logic matches what live selection would produce given the
same bars. If those formulas change, one place changes; the enricher
imports its helpers from this module. (This module imports theirs
today; when the enricher is refactored, the helpers should move to
a shared feature-math module and both callers should import from
there.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from backtesting.models import Bar
from d0026.config import UniverseSelectionConfig
from d0026.identity import (
    IdentityConfidence, IdentityResolution, ResolutionOutcome,
    SecurityIdentity, TickerAlias,
)
from d0026.models import (
    AdjustmentConvention, DailySecurityFeatures, MarketDataBar,
    ORDERED_CANDIDATE_STAGES, PipelineStage, RawCandidateRef,
    RegimeLabel, RegimeState, UniverseCandidate,
)
from d0026.stages import default_percentage_evaluators


_MIN_BARS = 31            # matches AlpacaFeatureEnricher._DEFAULT_MIN_BARS
_LIQUIDITY_WINDOW = 20
_ATR_WINDOW = 14
_MOMENTUM_LOOKBACK = 30
_EXEC_QUALITY_WINDOW = 20


@dataclass(frozen=True)
class BacktestUniversePrefilter:
    """Approves symbols for entry on a given backtest date.

    Determinism: given the same bars_by_symbol and config, the
    approved set for any date is deterministic. Symbol ordering
    passed into stages is alphabetical.
    """

    bars_by_symbol: Mapping[str, Sequence[Bar]]
    config: UniverseSelectionConfig

    def approved_symbols_on(self, as_of_date: date) -> FrozenSet[str]:
        candidates = self._build_candidates(as_of_date)
        if not candidates:
            return frozenset()
        regime = _neutral_regime(as_of_date)
        evaluators = default_percentage_evaluators(self.config)
        survivors = tuple(candidates)
        for stage in ORDERED_CANDIDATE_STAGES:
            evaluator = evaluators[stage]
            result = evaluator.evaluate(survivors, as_of_date, regime)
            survivors = result.survivors
        return frozenset(c.raw.ticker for c in survivors)

    def _build_candidates(
        self, as_of_date: date,
    ) -> List[UniverseCandidate]:
        out: List[UniverseCandidate] = []
        for sym in sorted(self.bars_by_symbol):
            bars = self.bars_by_symbol[sym]
            history = [b for b in bars if b.bar_date <= as_of_date]
            if len(history) < _MIN_BARS:
                continue
            history = history[-max(_MIN_BARS, _MOMENTUM_LOOKBACK + 1):]
            last = history[-1]
            if last.bar_date != as_of_date:
                # No bar today for this symbol; skip -- production
                # feature enricher would also decline.
                continue
            try:
                market_bar = MarketDataBar(
                    security_id=f"bt-{sym}",
                    ticker_as_of_date=sym, bar_date=as_of_date,
                    open=last.open, high=last.high, low=last.low,
                    close=last.close, volume=last.volume,
                    adjustment=AdjustmentConvention.UNKNOWN,
                    source_reference="backtest",
                )
            except ValueError:
                continue

            liquidity = _mean_dollar_volume(history[-_LIQUIDITY_WINDOW:])
            atr = _mean_true_range(history[-(_ATR_WINDOW + 1):])
            momentum = _return_over(history, _MOMENTUM_LOOKBACK)
            exec_q = _mean_range_over_vwap(history[-_EXEC_QUALITY_WINDOW:])

            features = DailySecurityFeatures(
                security_id=market_bar.security_id,
                feature_date=as_of_date,
                liquidity_measure=liquidity,
                atr_measure=atr,
                momentum_measure=momentum,
                execution_quality_proxy=exec_q,
                execution_quality_proxy_is_true_quote=False,
                warm_up_sufficient=True,
                source_reference="backtest",
            )
            identity_id = f"bt-{sym}"
            resolution = IdentityResolution(
                outcome=ResolutionOutcome.RESOLVED,
                ticker=sym, as_of_date=as_of_date,
                identity=SecurityIdentity(
                    security_id=identity_id, display_name=sym,
                    cik=None,
                    confidence=IdentityConfidence.PROVISIONAL,
                ),
                alias=TickerAlias(
                    security_id=identity_id, ticker=sym,
                    effective_start=None, effective_end=None,
                ),
            )
            out.append(UniverseCandidate(
                raw=RawCandidateRef(ticker=sym, as_of_date=as_of_date),
                identity_resolution=resolution,
                bar=market_bar, features=features,
            ))
        return out


def _neutral_regime(as_of_date: date) -> RegimeState:
    """A regime state that never triggers stage E's risk-off branch.

    `UNCLASSIFIED_PENDING_CALIBRATION` with no reference_series_values
    → stage E treats vix_percentile as None → pass-through."""

    return RegimeState(
        as_of_date=as_of_date,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(),
        classification_method_version="backtest-neutral",
    )


# ---- feature math (mirrors AlpacaFeatureEnricher) -------------------

def _mean_dollar_volume(bars: List[Bar]) -> Optional[float]:
    if not bars:
        return None
    vs = [b.volume * b.close for b in bars if b.volume >= 0]
    return (sum(vs) / len(vs)) if vs else None


def _mean_true_range(bars: List[Bar]) -> Optional[float]:
    if len(bars) < 2:
        return None
    trs: List[float] = []
    prev_close: Optional[float] = None
    for b in bars:
        if prev_close is None:
            tr = b.high - b.low
        else:
            tr = max(b.high - b.low,
                     abs(b.high - prev_close),
                     abs(b.low - prev_close))
        trs.append(tr)
        prev_close = b.close
    return (sum(trs) / len(trs)) if trs else None


def _return_over(bars: List[Bar], lookback: int) -> Optional[float]:
    if len(bars) < lookback + 1:
        return None
    past = bars[-(lookback + 1)].close
    now = bars[-1].close
    if past <= 0:
        return None
    return (now - past) / past


def _mean_range_over_vwap(bars: List[Bar]) -> Optional[float]:
    """Uses close as vwap proxy since Bar has no vwap field. Matches
    what AlpacaFeatureEnricher does when a bar's `vw` is missing."""
    if not bars:
        return None
    ratios: List[float] = []
    for b in bars:
        if b.close <= 0:
            continue
        ratios.append((b.high - b.low) / b.close)
    return (sum(ratios) / len(ratios)) if ratios else None
