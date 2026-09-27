"""Stage D -- Strategy Mechanics Fit, percentage-only (D-0048).

The Ladder strategy needs meaningful daily movement (too flat = no
Ladder trigger; too wild = Floor whipsaws). Two filters:
  1. ATR / price in [min_atr_fraction, max_atr_fraction].
  2. Momentum rank in the top min_trend_percentile of the pool.
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


class StrategyMechanicsFitStage(StageEvaluator):
    stage = PipelineStage.STRATEGY_MECHANICS_FIT

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        rejections: list = []

        atr_survivors: list = []
        for c in candidates:
            if c.features is None or c.bar is None:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.MISSING_MARKET_DATA,
                    detail="features or bar missing for stage D",
                ))
                continue
            atr = c.features.atr_measure
            price = c.bar.close
            if atr is None or price is None or price <= 0:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_STRATEGY_MECHANICS_FIT,
                    detail="ATR or price missing",
                ))
                continue
            atr_fraction = atr / price
            if atr_fraction < self._config.min_atr_fraction:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_STRATEGY_MECHANICS_FIT,
                    detail=(
                        f"ATR/price {atr_fraction*100:.2f}% below floor "
                        f"{self._config.min_atr_fraction*100:.2f}%"
                    ),
                ))
                continue
            if atr_fraction > self._config.max_atr_fraction:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_STRATEGY_MECHANICS_FIT,
                    detail=(
                        f"ATR/price {atr_fraction*100:.2f}% above cap "
                        f"{self._config.max_atr_fraction*100:.2f}%"
                    ),
                ))
                continue
            atr_survivors.append(c)

        survivors, rejected = top_percentile(
            atr_survivors,
            key=lambda c: (c.features.momentum_measure
                           if c.features is not None else None),
            top_fraction=self._config.min_trend_percentile,
        )
        for c in rejected:
            rejections.append(UniverseSelectionRejection(
                candidate=c, stage=self.stage,
                category=RejectionReasonCategory.FAILED_STRATEGY_MECHANICS_FIT,
                detail=(
                    f"below top {self._config.min_trend_percentile*100:.0f}% "
                    "by momentum"
                ),
            ))

        return StageResult(survivors=survivors, rejections=tuple(rejections))
