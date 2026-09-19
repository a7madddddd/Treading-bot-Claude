"""build_trade_proposal -- generates a TradeProposal using ONLY the
frozen strategy rule table from `docs/trading/strategy.md` §1-2. No
ranking, score, strategy-fit, regime, or liquidity input is used or
produced, because none legitimately exists for a fixed-watchlist
candidate.

Pure, deterministic, stateless: same inputs -> same proposal content
(save for `proposal_id`, which the caller supplies).

Governance (D1/D2 remediation, Controller-approved):
    - `strategy` (a StrategyRuleSet) and `floor_context` (a FloorContext)
      are REQUIRED, not optional/defaulted. There is no code path in
      this function that can produce a TradeProposal without both
      being present and independently validated by their own
      constructors -- an invalid or missing strategy/floor BLOCKS
      (raises) before any proposal is created. See models.py for the
      validation each performs.
"""

from __future__ import annotations

from datetime import datetime

from .models import FloorContext, StrategyRuleSet, TradeAction, TradeProposal

CANDIDATE_SOURCE_LABEL = "fixed_watchlist"


def build_trade_proposal(
    *,
    proposal_id: str,
    trade_id: str,
    action: TradeAction,
    symbol: str,
    current_price: float,
    as_of: datetime,
    strategy: StrategyRuleSet,
    floor_context: FloorContext,
    weighted_avg_entry_at_proposal: float | None = None,
) -> TradeProposal:
    """`current_price` is used as the proposed initial-entry reference
    price. Per strategy.md §2, the TRUE frozen reference is only set
    once the initial order is actually reconciled -- this proposal's
    `proposed_entry` is a pre-trade reference for Controller review
    only, not a claim that any order has been placed.

    `strategy` must be obtained via `models.approved_strategy_rule_set()`
    (or an equivalent that passes StrategyRuleSet's own validation).
    `floor_context` must be obtained via
    `FloorContext.no_existing_position()` or `FloorContext.known(...)`
    -- both are required keyword arguments with no default, so calling
    this function without them is a TypeError, not a silent fallback.

    `trade_id` is required and caller-supplied -- this function performs
    no derivation of it from symbol, date, or anything else. Callers
    proposing a new attempt for a trade that already has prior attempts
    (e.g. a previously-rejected Ladder 1) pass the SAME `trade_id` as
    those prior attempts; `proposal_id` must still be unique per
    attempt.

    `action` declares which single Controller-gated action this
    specific attempt is FOR (docs/trading/execution.md §2) -- required,
    caller-supplied, never inferred. Per the Controller-approved
    lifecycle rule, ProposalRepository.save() automatically expires any
    previously PENDING or APPROVED attempt sharing the same
    (trade_id, action).
    """

    if current_price <= 0:
        raise ValueError("current_price must be positive")
    if not trade_id:
        raise ValueError("trade_id must be non-empty")
    if not isinstance(action, TradeAction):
        raise TypeError(f"action must be a TradeAction, got {type(action)!r}")

    # strategy and floor_context are re-validated here even though their
    # own __post_init__ already validated at construction -- this
    # protects against a caller holding a reference to an object built
    # before some future validation tightening, and makes this
    # function's own fail-closed guarantee independent of caller
    # discipline elsewhere.
    if not isinstance(strategy, StrategyRuleSet):
        raise TypeError("strategy must be a validated StrategyRuleSet")
    if not isinstance(floor_context, FloorContext):
        raise TypeError("floor_context must be a validated FloorContext")

    proposed_entry = current_price
    ladder_1_trigger = round(proposed_entry * (1 + strategy.ladder_1_pct), 4)
    ladder_2_trigger = round(proposed_entry * (1 + strategy.ladder_2_pct), 4)
    floor_trigger = round(proposed_entry * (1 + strategy.floor_pct), 4)
    active_floor_at_proposal = floor_context.floor_price

    assumptions = (
        "candidate_source is a Controller-specified fixed watchlist -- "
        "no Universe Search filtering, ranking, or scoring was performed",
        "proposed_entry is the current market price at proposal time, "
        "not a confirmed fill -- the true frozen reference per "
        "strategy.md §2 is set only once the initial order is reconciled",
        "no strategy-fit, regime, or liquidity assessment was performed "
        "for this candidate",
    )
    risks = (
        "this candidate was not evaluated by any ranking or quality "
        "filter -- its presence on the watchlist is not evidence of "
        "trade quality",
        "current_price may move between proposal generation and "
        "Controller decision; D-0007 re-validation applies before any "
        "submission",
    )

    return TradeProposal(
        proposal_id=proposal_id,
        trade_id=trade_id,
        proposed_action=action,
        symbol=symbol.strip().upper(),
        candidate_source=CANDIDATE_SOURCE_LABEL,
        current_price_at_proposal=current_price,
        proposed_entry=proposed_entry,
        ladder_1_trigger=ladder_1_trigger,
        ladder_1_quantity=strategy.ladder_1_qty,
        ladder_2_trigger=ladder_2_trigger,
        ladder_2_quantity=strategy.ladder_2_qty,
        floor_trigger=floor_trigger,
        maximum_position=strategy.maximum_position,
        proposal_created_at=as_of,
        weighted_avg_entry_at_proposal=weighted_avg_entry_at_proposal,
        active_floor_at_proposal=active_floor_at_proposal,
        assumptions=assumptions,
        risks=risks,
    )
