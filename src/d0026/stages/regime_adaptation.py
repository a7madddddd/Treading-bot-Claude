"""Stage E -- Regime Adaptation, percentage-only (D-0048).

Regime state is supplied to the pipeline as `RegimeState`. This
stage reads its `reference_series_values` for a "vix_percentile"
entry (0..1). If VIX is above the historical percentile cap
(`vix_topmost_percentile`), the regime is deemed risk-off and this
stage filters to top-half by momentum. Otherwise it is a pass-through.

The RegimeClassifier that populates `vix_percentile` is a separate
component (a snapshot builder, not a strategy engine); this stage
reads only what it is given, never fetches data itself.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    PipelineStage, RegimeState, RejectionReasonCategory,
    UniverseCandidate, UniverseSelectionRejection,
)
from d0026.percentile_utils import top_percentile
from d0026.pipeline import StageEvaluator, StageResult


def _vix_percentile(regime_state: RegimeState) -> Optional[float]:
    for name, value in regime_state.reference_series_values:
        if name == "vix_percentile":
            return value
    return None


class RegimeAdaptationStage(StageEvaluator):
    stage = PipelineStage.REGIME_ADAPTATION

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        vix_pct = _vix_percentile(regime_state)
        if vix_pct is None or vix_pct < self._config.vix_topmost_percentile:
            # Regime is normal or unknown -> pass-through (fail-open for
            # this stage: uncertain regime is not a rejection reason).
            return StageResult(survivors=candidates, rejections=())

        # Risk-off: keep top half by momentum only. This is a defensive
        # tightening, not a strategy change.
        rejections: list = []
        survivors, rejected = top_percentile(
            candidates,
            key=lambda c: (c.features.momentum_measure
                           if c.features is not None else None),
            top_fraction=0.5,
        )
        for c in rejected:
            rejections.append(UniverseSelectionRejection(
                candidate=c, stage=self.stage,
                category=RejectionReasonCategory.FAILED_REGIME_ADAPTATION,
                detail=(
                    f"risk-off regime (VIX pct {vix_pct*100:.0f}% > "
                    f"{self._config.vix_topmost_percentile*100:.0f}%); "
                    "not in top 50% momentum"
                ),
            ))
        return StageResult(survivors=survivors, rejections=tuple(rejections))
