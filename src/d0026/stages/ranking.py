"""Stage F -- Ranking, percentage-only (D-0048).

Combines three per-candidate rank-normalized scores into an
aggregate:
  score = momentum_weight * momentum_rank
        + quality_weight  * quality_rank
        + liquidity_weight * liquidity_rank

Every constituent is a rank in [0, 1]. Quality is derived from
inverse ATR (lower ATR => higher quality within surviving pool);
that keeps stage F self-contained even before a richer quality
factor is wired in.

Ranking is a scoring stage, not a reject stage; every candidate
survives with its computed aggregate exposed via
UniverseCandidate.features.momentum_measure carried forward and
the ranking recorded on the pipeline output (SelectedCandidateEntry
in the surrounding orchestrator).
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Tuple

from d0026.config import UniverseSelectionConfig
from d0026.models import (
    PipelineStage, RegimeState, UniverseCandidate,
)
from d0026.percentile_utils import rank_percentile
from d0026.pipeline import StageEvaluator, StageResult


class RankingStage(StageEvaluator):
    stage = PipelineStage.RANKING

    def __init__(self, config: UniverseSelectionConfig) -> None:
        self._config = config

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        # Rank each feature dimension separately. rank_percentile
        # drops None values, so each dict may not cover every candidate.
        mom_ranks = dict(rank_percentile(
            candidates,
            key=lambda c: (c.features.momentum_measure
                           if c.features is not None else None),
        ))
        liq_ranks = dict(rank_percentile(
            candidates,
            key=lambda c: (c.features.liquidity_measure
                           if c.features is not None else None),
        ))
        # Quality = 1 - rank(ATR); lower ATR => higher quality rank.
        atr_ranks = dict(rank_percentile(
            candidates,
            key=lambda c: (c.features.atr_measure
                           if c.features is not None else None),
        ))
        w = self._config
        scored: list = []
        for c in candidates:
            m = mom_ranks.get(c, 0.0)
            q = 1.0 - atr_ranks.get(c, 0.5)
            l = liq_ranks.get(c, 0.0)
            score = (w.momentum_weight * m
                     + w.quality_weight * q
                     + w.liquidity_weight * l)
            scored.append((score, c))
        # Order by score descending. `SelectedCandidateEntry` is
        # constructed by the pipeline orchestrator; here we just
        # return survivors in ranked order so the next stages
        # (concentration, top-N) see them ranked.
        scored.sort(key=lambda sc: -sc[0])
        # Stash aggregate score on the candidate's feature record's
        # audit trail? The domain model is frozen -- we cannot mutate
        # a candidate. Downstream stages therefore rely on the ORDER
        # of survivors as the ranked-descending signal, plus a
        # side-channel `ranked_scores` we ship as metadata is out of
        # scope here. Order-preserving ranking is sufficient for
        # concentration + Top-N (the only two downstream consumers).
        return StageResult(
            survivors=tuple(c for _, c in scored),
            rejections=(),
        )
