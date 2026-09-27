"""Stage B -- Data Quality, percentage-only (D-0048).

Warm-up sufficiency is a boolean already present on features. On
top of that, the completeness fraction lives on features via
`liquidity_measure`/`atr_measure`/`momentum_measure`/
`execution_quality_proxy` -- if all four are non-None, we treat the
security as fully populated for this pass; a security missing three
or more is treated as failing `min_completeness_fraction`.

Note: a richer completeness signal (last-N-days bar coverage) can be
added when the feature enrichment layer supplies it; this stage
already respects `warm_up_sufficient` today.
"""

from __future__ import annotations

from datetime import date
from typing import Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    DailySecurityFeatures, PipelineStage, RegimeState,
    RejectionReasonCategory, UniverseCandidate,
    UniverseSelectionRejection,
)
from d0026.pipeline import StageEvaluator, StageResult


_MEASURE_FIELDS = ("liquidity_measure", "atr_measure",
                   "momentum_measure", "execution_quality_proxy")


def _completeness_fraction(features: DailySecurityFeatures) -> float:
    present = sum(1 for name in _MEASURE_FIELDS
                  if getattr(features, name) is not None)
    return present / len(_MEASURE_FIELDS)


class DataQualityStage(StageEvaluator):
    stage = PipelineStage.DATA_QUALITY

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        survivors: list = []
        rejections: list = []
        for c in candidates:
            if c.features is None:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.MISSING_MARKET_DATA,
                    detail="features missing for stage B",
                ))
                continue
            if not c.features.warm_up_sufficient:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.INSUFFICIENT_WARM_UP,
                    detail="warm_up_sufficient=False",
                ))
                continue
            comp = _completeness_fraction(c.features)
            if comp < self._config.min_completeness_fraction:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_DATA_QUALITY,
                    detail=(
                        f"feature completeness {comp*100:.0f}% below "
                        f"cap {self._config.min_completeness_fraction*100:.0f}%"
                    ),
                ))
                continue
            survivors.append(c)
        return StageResult(survivors=tuple(survivors),
                           rejections=tuple(rejections))
