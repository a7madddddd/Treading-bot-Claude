"""Stage H -- Top-N, percentage-only (D-0048).

Ranking has already ordered survivors by aggregate score descending.
This stage simply keeps the first `top_n` and rejects the rest as
NOT_IN_TOP_N.

`top_n` is a Controller operational cap (how many positions can be
monitored per day), not a calibration value.
"""

from __future__ import annotations

from datetime import date
from typing import Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    PipelineStage, RegimeState, RejectionReasonCategory,
    UniverseCandidate, UniverseSelectionRejection,
)
from d0026.pipeline import StageEvaluator, StageResult


class TopNStage(StageEvaluator):
    stage = PipelineStage.TOP_N

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        n = self._config.top_n
        survivors = candidates[:n]
        rejections = tuple(
            UniverseSelectionRejection(
                candidate=c, stage=self.stage,
                category=RejectionReasonCategory.NOT_IN_TOP_N,
                detail=f"outside Top-{n} by ranking score",
            )
            for c in candidates[n:]
        )
        return StageResult(survivors=survivors, rejections=rejections)
