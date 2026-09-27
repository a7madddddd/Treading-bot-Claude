"""Stage G -- Concentration, percentage-only (D-0048).

Enforces two limits over ranked candidates:
  1. No sector holds more than `max_sector_fraction` of the surviving
     pool (after this stage runs -- greedy: iterate candidates in
     ranked order, drop any that would push a sector over the cap).
  2. Pairwise correlation cap: drop any candidate whose price series
     correlation with any earlier-selected candidate exceeds
     `max_pairwise_correlation`. Correlation source is out of scope
     for the domain model as it stands; when unavailable, this rule
     is skipped without effect.

Sector information is read from `candidate.features.source_reference`
by convention when it carries a `sector=` fragment; missing sector
data means the candidate is treated as sector="unknown" and only
the correlation rule applies. This is documented behavior, not a
silent failure.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional, Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    PipelineStage, RegimeState, RejectionReasonCategory,
    UniverseCandidate, UniverseSelectionRejection,
)
from d0026.pipeline import StageEvaluator, StageResult


def _ceil_int(x: float) -> int:
    n = int(x)
    return n + 1 if x - n > 1e-12 else n


_SECTOR_FRAG = "sector="


def _sector_of(candidate: UniverseCandidate) -> str:
    ref = (candidate.features.source_reference
           if candidate.features is not None else "")
    idx = ref.find(_SECTOR_FRAG)
    if idx < 0:
        return "unknown"
    rest = ref[idx + len(_SECTOR_FRAG):]
    # Sector fragment ends at the next space or ";" or end-of-string.
    end = len(rest)
    for sep in (" ", ";", ","):
        pos = rest.find(sep)
        if 0 <= pos < end:
            end = pos
    return rest[:end].lower() or "unknown"


class ConcentrationStage(StageEvaluator):
    stage = PipelineStage.CONCENTRATION

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        rejections: list = []
        survivors: list = []
        # Fixed cap per sector: ceil(cap * pool_size) allowed per sector,
        # against the CURRENT pool size (len(candidates)) so the cap
        # gains meaning as the pool grows. Small pools (< 1/cap) never
        # over-restrict; a 4-candidate pool with 4 different sectors
        # all pass under a 30% cap because ceil(0.3 * 4) = 2 per sector
        # and each sector has 1.
        n = len(candidates)
        max_per_sector = max(1, _ceil_int(self._config.max_sector_fraction * n))
        counts: Dict[str, int] = {}
        for c in candidates:
            sector = _sector_of(c)
            projected = counts.get(sector, 0) + 1
            if projected > max_per_sector:
                rejections.append(UniverseSelectionRejection(
                    candidate=c, stage=self.stage,
                    category=RejectionReasonCategory.FAILED_CONCENTRATION,
                    detail=(
                        f"sector {sector!r} already at cap "
                        f"({max_per_sector} of pool={n}); this candidate "
                        f"would push it to {projected}"
                    ),
                ))
                continue
            counts[sector] = projected
            survivors.append(c)

        # Pairwise correlation rule: not applied unless a correlation
        # source is available. Kept as a documented no-op today; the
        # snapshot builder is where a correlation matrix would enter
        # the pipeline in a future extension.
        return StageResult(survivors=tuple(survivors),
                           rejections=tuple(rejections))
