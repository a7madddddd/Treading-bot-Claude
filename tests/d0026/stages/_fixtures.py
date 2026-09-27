"""Shared fixtures for stage tests. Build a UniverseCandidate with
minimal boilerplate."""

from __future__ import annotations

from datetime import date
from typing import Optional

from d0026.identity import (
    IdentityConfidence, IdentityResolution, ResolutionOutcome,
    SecurityIdentity, TickerAlias,
)
from d0026.models import (
    AdjustmentConvention, DailySecurityFeatures, MarketDataBar,
    RawCandidateRef, RegimeLabel, RegimeState, UniverseCandidate,
)


DATE = date(2026, 1, 5)


def regime(vix_percentile: Optional[float] = None) -> RegimeState:
    refs = ()
    if vix_percentile is not None:
        refs = (("vix_percentile", vix_percentile),)
    label = (RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION
             if vix_percentile is not None else RegimeLabel.UNKNOWN)
    return RegimeState(
        as_of_date=DATE, label=label, reference_series_values=refs,
        classification_method_version="fixture-v1",
    )


def candidate(
    ticker: str, *,
    close: float = 100.0,
    liquidity: Optional[float] = 5_000_000.0,
    atr: Optional[float] = 2.5,
    momentum: Optional[float] = 0.05,
    spread: Optional[float] = 0.001,
    warm_up_sufficient: bool = True,
    source_reference: str = "test",
    completeness_measures_present: bool = True,
) -> UniverseCandidate:
    identity = SecurityIdentity(
        security_id=f"sec-{ticker}", display_name=f"{ticker} Corp",
        cik="0000000001", confidence=IdentityConfidence.RESOLVED_CIK,
    )
    alias = TickerAlias(
        security_id=identity.security_id, ticker=ticker,
        effective_start=None, effective_end=None,
    )
    resolution = IdentityResolution(
        outcome=ResolutionOutcome.RESOLVED, ticker=ticker,
        as_of_date=DATE, identity=identity, alias=alias,
    )
    bar = MarketDataBar(
        security_id=identity.security_id, ticker_as_of_date=ticker,
        bar_date=DATE, open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=1_000_000.0,
        adjustment=AdjustmentConvention.SPLIT_ADJUSTED,
        source_reference=source_reference,
    )
    if not completeness_measures_present:
        liquidity = None
        atr = None
        momentum = None
        spread = None
    features = DailySecurityFeatures(
        security_id=identity.security_id, feature_date=DATE,
        liquidity_measure=liquidity, atr_measure=atr,
        momentum_measure=momentum, execution_quality_proxy=spread,
        execution_quality_proxy_is_true_quote=True,
        warm_up_sufficient=warm_up_sufficient,
        source_reference=source_reference,
    )
    return UniverseCandidate(
        raw=RawCandidateRef(ticker=ticker, as_of_date=DATE),
        identity_resolution=resolution, bar=bar, features=features,
    )
