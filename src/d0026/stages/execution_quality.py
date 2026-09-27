"""Stage C -- Execution Quality, percentage-only (D-0048).

`features.execution_quality_proxy` is treated as a spread measure
(smaller is better -- e.g. bid-ask spread as a fraction of price).
Two filters:
  1. Hard ratio: proxy > max_spread_fraction rejects.
  2. Percentile: after (1), keep top min_spread_tightness_percentile
     ranked by 1 / max(proxy, 1e-9) so tighter spreads win.

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
            if proxy > self._config.max_spread_fraction:
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

        survivors, rejected = top_percentile(
            with_proxy,
            key=lambda c: -c.features.execution_quality_proxy,  # smaller spread => larger key
            top_fraction=self._config.min_spread_tightness_percentile,
        )
        for c in rejected:
            rejections.append(UniverseSelectionRejection(
                candidate=c, stage=self.stage,
                category=RejectionReasonCategory.FAILED_EXECUTION_QUALITY,
                detail=(
                    f"below top {self._config.min_spread_tightness_percentile*100:.0f}% "
                    "by spread tightness"
                ),
            ))
        return StageResult(survivors=survivors, rejections=tuple(rejections))
