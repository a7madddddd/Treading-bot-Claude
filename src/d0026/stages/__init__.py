"""Percentage-only D-0026 stage evaluators (D-0048).

Every stage evaluator here operates on percentages, ratios, or ranks
- no absolute price, volume, or dollar threshold is used anywhere.
See `docs/trading/decisions.md` D-0048 for the approved values.
"""

from __future__ import annotations

from typing import Dict

from d0026.config import UniverseSelectionConfig
from d0026.models import PipelineStage
from d0026.pipeline import StageEvaluator
from d0026.stages.concentration import ConcentrationStage
from d0026.stages.data_quality import DataQualityStage
from d0026.stages.execution_quality import ExecutionQualityStage
from d0026.stages.ranking import RankingStage
from d0026.stages.regime_adaptation import RegimeAdaptationStage
from d0026.stages.strategy_fit import StrategyMechanicsFitStage
from d0026.stages.top_n import TopNStage
from d0026.stages.tradability import TradabilityStage


def default_percentage_evaluators(
    config: UniverseSelectionConfig,
) -> Dict[PipelineStage, StageEvaluator]:
    """Factory: builds the eight candidate-stage evaluators wired
    with the given config. `PERSIST_SNAPSHOT` is not a candidate
    stage (it is the pipeline's own persistence step)."""

    return {
        PipelineStage.TRADABILITY: TradabilityStage(config),
        PipelineStage.DATA_QUALITY: DataQualityStage(config),
        PipelineStage.EXECUTION_QUALITY: ExecutionQualityStage(config),
        PipelineStage.STRATEGY_MECHANICS_FIT: StrategyMechanicsFitStage(config),
        PipelineStage.REGIME_ADAPTATION: RegimeAdaptationStage(config),
        PipelineStage.RANKING: RankingStage(config),
        PipelineStage.CONCENTRATION: ConcentrationStage(config),
        PipelineStage.TOP_N: TopNStage(config),
    }


__all__ = [
    "ConcentrationStage",
    "DataQualityStage",
    "ExecutionQualityStage",
    "RankingStage",
    "RegimeAdaptationStage",
    "StrategyMechanicsFitStage",
    "TopNStage",
    "TradabilityStage",
    "default_percentage_evaluators",
]
