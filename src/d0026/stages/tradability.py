"""Stage A -- Tradability, percentage-only (D-0048).

Applies two percentile-based filters:
  1. Keep top `min_volume_percentile` of the pool ranked by
     `features.liquidity_measure` (dollar volume proxy).
  2. Drop bottom `drop_bottom_price_percentile` by `bar.close`
     (excludes penny stocks without a fixed dollar threshold).

Candidates missing features or bar are rejected as MISSING_MARKET_DATA
(not FAILED_TRADABILITY): the data problem is distinct from a real
tradability decision.
"""

from __future__ import annotations

from datetime import date
from typing import Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    PipelineStage, RegimeState, RejectionReasonCategory,
    UniverseCandidate, UniverseSelectionRejection,
)
from d0026.percentile_utils import drop_bottom_percentile, top_percentile
from d0026.pipeline import StageEvaluator, StageResult


class TradabilityStage(StageEvaluator):
    stage = PipelineStage.TRADABILITY

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        rejections: list = []

        with_data: list = []
        for c in candidates:
            if c.features is None or c.bar is None:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.MISSING_MARKET_DATA,
                    detail="features or bar missing for stage A",
                ))
                continue
            with_data.append(c)

        survivors_vol, rejected_vol = top_percentile(
            with_data,
            key=lambda c: (c.features.liquidity_measure
                           if c.features is not None else None),
            top_fraction=self._config.min_volume_percentile,
        )
        for c in rejected_vol:
            rejections.append(UniverseSelectionRejection(
                candidate=c, stage=self.stage,
                category=RejectionReasonCategory.FAILED_TRADABILITY,
                detail=(
                    f"below top {self._config.min_volume_percentile*100:.0f}% "
                    "by liquidity"
                ),
            ))

        survivors_price, rejected_price = drop_bottom_percentile(
            survivors_vol,
            key=lambda c: c.bar.close if c.bar is not None else None,
            bottom_fraction=self._config.drop_bottom_price_percentile,
        )
        for c in rejected_price:
            rejections.append(UniverseSelectionRejection(
                candidate=c, stage=self.stage,
                category=RejectionReasonCategory.FAILED_TRADABILITY,
                detail=(
                    f"in bottom "
                    f"{self._config.drop_bottom_price_percentile*100:.0f}% "
                    "by price (penny-stock filter)"
                ),
            ))

        return StageResult(survivors=survivors_price,
                           rejections=tuple(rejections))
