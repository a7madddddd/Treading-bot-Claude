"""Volatility-adjusted position sizing (D-0050 Phase 15).

Replaces a fixed share count with a dollar-targeted sizing rule that
scales INVERSELY with the stock's realized volatility: high-vol names
get smaller positions so the per-trade dollar risk is roughly
constant across picks.

Formula (dollar-neutralized to target volatility):

    shares  = floor(target_dollars / price *
                    (target_vol / actual_vol_clamped))

Where:
  - target_dollars  = fixed per-trade budget (e.g. $2000)
  - target_vol      = reference annualized vol (default 25% — roughly
                      SPY's long-run vol)
  - actual_vol      = SymbolResearch.volatility_30d_pct (annualized)
                      clamped to [min_vol, max_vol] so a near-zero or
                      enormous vol can't produce absurd quantities.

Fail-open: when actual_vol is None, the sizer returns the un-scaled
share count (target_dollars / price) rather than refusing to size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from engine.research_hub import SymbolResearch


@dataclass(frozen=True)
class SizingConfig:
    target_dollars: float = 2_000.0
    target_vol_pct: float = 25.0
    min_vol_pct: float = 10.0
    max_vol_pct: float = 120.0
    min_shares: int = 1
    max_shares: int = 10_000


DEFAULT_SIZING = SizingConfig()


@dataclass
class SizingResult:
    shares: int
    dollars: float
    vol_scale: float      # the multiplier applied (<1 for high-vol)
    actual_vol_pct: Optional[float]
    reason: str


def size_position(
    symbol: str,
    price: float,
    research: Optional[SymbolResearch] = None,
    config: SizingConfig = DEFAULT_SIZING,
) -> SizingResult:
    """Returns the share count a volatility-neutralized position should
    use. Never raises."""
    if price <= 0 or not math.isfinite(price):
        return SizingResult(shares=0, dollars=0.0, vol_scale=0.0,
                             actual_vol_pct=None,
                             reason="invalid price")

    actual_vol = None
    if research is not None:
        actual_vol = research.volatility_30d_pct

    if actual_vol is None or actual_vol <= 0:
        # Fall back: no vol-neutralization. Use raw dollar budget.
        raw_shares = int(config.target_dollars / price)
        raw_shares = max(config.min_shares,
                         min(config.max_shares, raw_shares))
        return SizingResult(
            shares=raw_shares,
            dollars=raw_shares * price,
            vol_scale=1.0,
            actual_vol_pct=None,
            reason="no-vol-data fallback",
        )

    clamped_vol = max(config.min_vol_pct,
                      min(config.max_vol_pct, actual_vol))
    scale = config.target_vol_pct / clamped_vol
    sized_dollars = config.target_dollars * scale
    shares = int(sized_dollars / price)
    shares = max(config.min_shares, min(config.max_shares, shares))
    return SizingResult(
        shares=shares,
        dollars=shares * price,
        vol_scale=scale,
        actual_vol_pct=actual_vol,
        reason=(f"vol-neutralized (actual {actual_vol:.1f}% / target "
                f"{config.target_vol_pct:.1f}%)"),
    )
