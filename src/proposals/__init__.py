"""Trade-proposal MVP (Phase A) — foundation only.

Per the Controller-authorized Phase A scope: fixed-watchlist candidate
source, proposal generation from the existing frozen strategy rules
(`docs/trading/strategy.md`), and a persistent, immutable
proposal/approval state contract matching
`docs/architecture/state-management.md` §1.

Explicitly NOT part of this phase: Routine wiring, Alpaca submission,
Telegram, Universe Search (Stages A-I), any ranking/scoring/regime/
liquidity/strategy-fit logic. This package has zero import-time
dependency on `src/d0026/` or `src/notifications/` and performs no
network I/O.
"""

from .candidate_source import CandidateSource, FixedWatchlistCandidateSource
from .models import (
    ApprovalState,
    FloorContext,
    InvalidFloorContextError,
    StrategyRuleSet,
    StrategyUnavailableError,
    TradeAction,
    TradeProposal,
    approved_strategy_rule_set,
)
from .proposal import build_trade_proposal
from .repository import (
    InMemoryProposalRepository,
    ProposalDecisionConflictError,
    ProposalRepository,
)
from .revalidation import RevalidationResult, validate_for_submission

__all__ = [
    "ApprovalState",
    "TradeAction",
    "TradeProposal",
    "StrategyRuleSet",
    "StrategyUnavailableError",
    "approved_strategy_rule_set",
    "FloorContext",
    "InvalidFloorContextError",
    "CandidateSource",
    "FixedWatchlistCandidateSource",
    "build_trade_proposal",
    "ProposalRepository",
    "InMemoryProposalRepository",
    "ProposalDecisionConflictError",
    "RevalidationResult",
    "validate_for_submission",
]
