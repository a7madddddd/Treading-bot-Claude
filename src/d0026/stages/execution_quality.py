"""Stage C -- Execution Quality, percentage-only (D-0048).

`features.execution_quality_proxy` is treated as a spread measure
(smaller is better -- e.g. bid-ask spread as a fraction of price).
Two filters:
  1. Hard ratio: proxy > max_spread_fraction rejects.
     Only applied when `execution_quality_proxy_is_true_quote=True`,
     because `max_spread_fraction` (D-0048 default 0.15%) is
     calibrated for real bid/ask spreads. When the proxy is derived
     from another source (e.g., IEX-feed intraday range, which is
     typically 1-3% of vwap for large liquid names), the hard cap
     would reject every real symbol.
  2. REMOVED by D-0088: a relative percentile that kept the tightest
     min_spread_tightness_percentile of the pool. With no true quote it
     ran on the intraday-range proxy, making it a VOLATILITY filter
     pulling against Stage D's volatility band over the same number.
     Measured 2026-10-07: the two left a joint window about six
     hundredths of a point wide, and 26 of 12,597 symbols survived the
     day. Liquidity -- what this rule was reaching for -- is Stage A's
     job, measured directly as dollar volume.

Candidates whose proxy is None (unknown) are rejected as
FAILED_EXECUTION_QUALITY with "unknown spread" -- the fail-closed
posture is intentional; unknown execution quality is not proven
tradable.
"""

from __future__ import annotations

from datetime import date
from typing import Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    PipelineStage, RegimeState, RejectionReasonCategory,
    UniverseCandidate, UniverseSelectionRejection,
)
from d0026.percentile_utils import top_percentile
from d0026.pipeline import StageEvaluator, StageResult


class ExecutionQualityStage(StageEvaluator):
    stage = PipelineStage.EXECUTION_QUALITY

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        rejections: list = []

        with_proxy: list = []
        for c in candidates:
            proxy = (c.features.execution_quality_proxy
                     if c.features is not None else None)
            if proxy is None:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_EXECUTION_QUALITY,
                    detail="execution_quality_proxy missing",
                ))
                continue
            # Only apply the absolute cap when the proxy is a true
            # bid/ask quote. Non-quote proxies (e.g. intraday range)
            # are compared only relatively via the percentile filter
            # below.
            is_true_quote = (
                c.features.execution_quality_proxy_is_true_quote
                if c.features is not None else False
            )
            if is_true_quote and proxy > self._config.max_spread_fraction:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_EXECUTION_QUALITY,
                    detail=(
                        f"spread {proxy*100:.3f}% exceeds cap "
                        f"{self._config.max_spread_fraction*100:.3f}%"
                    ),
                ))
                continue
            with_proxy.append(c)

        # D-0088: the relative "keep the tightest N% by spread" rule is
        # REMOVED. The absolute cap above stays, and starts working the
        # day a true bid/ask quote is available.
        #
        # The rule was meant to filter execution cost. With no true
        # quote it ran on the intraday-range proxy, which is a
        # VOLATILITY measure -- and Stage D already filters volatility,
        # in the opposite direction. The two fought over the same
        # number, and the overlap is where the universe went.
        #
        # Measured on 2026-10-07 against 92 symbols that passed Stage A:
        #
        #   this stage's cut:   range <= 1.72%
        #   Stage D's floor:    ATR  >= 2.00%
        #   and ATR - range ran +0.10 to +0.34 for these names
        #   => the joint window was range in [~1.66%, ~1.72%]
        #
        # Six hundredths of a percentage point wide, which is why 26 of
        # 12,597 symbols survived the day's run. On eight large caps
        # checked by name, not one passed both stages: MSFT, AMAT and
        # NVDA cleared D and failed here; JPM, MA and COST cleared here
        # and failed D.
        #
        # Nothing is left unguarded. Liquidity -- the thing this stage
        # was reaching for -- is Stage A's job and it measures it
        # directly, as dollar volume. Volatility is Stage D's job and it
        # is derived from the approved ladder (D-0065). What is gone is
        # a third, inverted volatility filter nobody decided to add.
        return StageResult(survivors=tuple(with_proxy),
                           rejections=tuple(rejections))
