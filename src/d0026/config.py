"""Percentage-only D-0026 pipeline parameters (D-0048).

Every threshold in this file is either:
  - A ratio (already a percentage of some base value)
  - A percentile rank cutoff (top X% / bottom X% of the current pool)
  - A percentile of a historical distribution
  - A category label (SEC size definitions, sector taxonomy)
  - A domain-approved count (Top-N; Controller decision, not a
    calibration value)

No absolute price, volume, or dollar threshold appears anywhere.
Every value here was approved by the Controller as D-0048 and can
be changed only by a new Controller decision.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseSelectionConfig:
    """The Controller-approved D-0048 percentage parameters.

    Defaults match D-0048, EXCEPT the Stage D ATR band, which D-0065
    (2026-10-05) narrowed from 1%-5% to 2%-4% on measured evidence. See
    that decision for the measurement and the reasoning; every other
    value below is D-0048's unchanged.

    Defaults:
      Stage A (Tradability):
        min_volume_percentile         = 0.30
        drop_bottom_price_percentile  = 0.20
        min_market_cap_percentile     = 0.40

      Stage B (Data Quality):
        min_completeness_fraction     = 0.90
        max_gap_fraction              = 0.05

      Stage C (Execution Quality):
        max_spread_fraction           = 0.0015   # 0.15% of price
        min_spread_tightness_percentile = 0.40

      Stage D (Strategy Fit):
        min_atr_fraction              = 0.02     # 2% of price (D-0065)
        max_atr_fraction              = 0.04     # 4% of price (D-0065)
        min_trend_percentile          = 0.50

      Stage E (Regime):
        vix_history_days              = 126      # ~6 months of TDs
        vix_topmost_percentile        = 0.80     # top 20% => risk-off

      Stage F (Ranking):
        momentum_weight               = 0.40
        quality_weight                = 0.30
        liquidity_weight              = 0.30

      Stage G (Concentration):
        max_sector_fraction           = 0.30
        max_pairwise_correlation      = 0.70

      Stage H (Top-N):
        top_n                         = 10       # Controller operational cap
    """

    # Stage A
    min_volume_percentile: float = 0.30
    drop_bottom_price_percentile: float = 0.20
    min_market_cap_percentile: float = 0.40

    # Stage B
    min_completeness_fraction: float = 0.90
    max_gap_fraction: float = 0.05

    # Stage C
    max_spread_fraction: float = 0.0015
    min_spread_tightness_percentile: float = 0.40
    """D-0088: NO LONGER READ. The relative tightness rule it
    configured was removed because, with no true bid/ask quote, it
    ran on the intraday-range proxy and became a second volatility
    filter fighting Stage D over the same number. The field is kept
    so existing stored configs still load and so the number that was
    in force is not erased from the record; nothing reads it."""

    # Stage D
    min_atr_fraction: float = 0.02
    """D-0065 (2026-10-05), narrowed from D-0048's 0.01 on measured
    evidence. Below 2% a symbol moves less than $2 a day per $100 of
    price, so the approved -5% Ladder 1 trigger is ~5 average adverse
    days away. Most dips recover within two or three, so the ladder
    would rarely fire at all: the strategy degrades into a single buy
    with three quarters of the trade budget left idle. Measured on the
    live universe, 22.1% of symbols sat in the 1-2% range."""
    max_atr_fraction: float = 0.04
    """D-0065 (2026-10-05), narrowed from D-0048's 0.05 on measured
    evidence. Above 4% a symbol covers the whole -5%/-8%/-10% ladder in
    about two days, so the Floor becomes the normal ending rather than
    the last-resort exit strategy.md section 1 calls it -- the position
    is never given time to recover. Measured on the live universe:
    PTHS 4.85%, DYN 4.92% and MSOS 4.96% all reach the Floor in 2.0-2.1
    average adverse days, and 10.7% of symbols sat in the 4-5% range."""
    min_trend_percentile: float = 0.50

    # Stage E
    vix_history_days: int = 126
    vix_topmost_percentile: float = 0.80

    # Stage F
    momentum_weight: float = 0.40
    quality_weight: float = 0.30
    liquidity_weight: float = 0.30

    # Stage G
    max_sector_fraction: float = 0.30
    max_pairwise_correlation: float = 0.70

    # Stage H
    top_n: int = 10

    def __post_init__(self) -> None:
        # Every percentage must be a proper ratio in [0, 1].
        for name in (
            "min_volume_percentile", "drop_bottom_price_percentile",
            "min_market_cap_percentile", "min_completeness_fraction",
            "max_gap_fraction", "max_spread_fraction",
            "min_spread_tightness_percentile", "min_atr_fraction",
            "max_atr_fraction", "min_trend_percentile",
            "vix_topmost_percentile", "momentum_weight",
            "quality_weight", "liquidity_weight",
            "max_sector_fraction", "max_pairwise_correlation",
        ):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {v!r}")
        # Weights sum to 1.
        total = (self.momentum_weight + self.quality_weight
                 + self.liquidity_weight)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"ranking weights must sum to 1.0, got "
                f"{self.momentum_weight}+{self.quality_weight}+"
                f"{self.liquidity_weight}={total}"
            )
        # ATR band coherent.
        if not (self.min_atr_fraction < self.max_atr_fraction):
            raise ValueError("min_atr_fraction must be < max_atr_fraction")
        # Positive integer.
        if self.top_n < 1:
            raise ValueError("top_n must be >= 1")
        if self.vix_history_days < 20:
            raise ValueError("vix_history_days must be >= 20")
