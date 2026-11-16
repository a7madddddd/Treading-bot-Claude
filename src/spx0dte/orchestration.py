"""Thin orchestration for the SPX 0DTE module (D-0095 execution wiring).

Owns NO business rule of its own -- sequences calls into existing,
unmodified authorities exactly like `orchestration.trade_proposal_service`
does for the equity-ladder flow: `OptionTradeProposal` governs its own
lifecycle (`spx0dte.proposal`), `SpxProposalRepository` is the only
persistence (`spx0dte.sqlite_repository`), `submit_entry_order` is the
only path to the broker (`spx0dte.execution`), and Telegram send/receive
stays inside `spx0dte.telegram_approval`.

Scope of this module: ENTRY only. Takes a candidate (from
`spx0dte.signal`/`entry_filters`, already run through those), creates a
proposal, notifies the Controller, and on an APPROVE decision still
inside the 5-minute window, submits a single buy order for the
approved quantity. Exit/close submission is explicitly out of scope
(see `execution.py`'s own docstring).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from execution.broker_client import BrokerClient

from spx0dte.execution import SubmissionResult, submit_entry_order
from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState
from spx0dte.sqlite_repository import SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind, send_proposal_message


def new_proposal_id() -> str:
    return f"SPX-{uuid.uuid4().hex[:12]}"


def build_proposal(
    *, date: str, direction: str, strike: int, occ_symbol: str,
    entry_time: datetime, hold_cap_minutes: float, estimated_premium: float,
    vix: Optional[float], filters_passed: str, now: datetime,
) -> OptionTradeProposal:
    return OptionTradeProposal(
        proposal_id=new_proposal_id(), date=date, direction=direction, strike=strike,
        occ_symbol=occ_symbol, entry_time=entry_time, hold_cap_minutes=hold_cap_minutes,
        estimated_premium=estimated_premium, vix=vix, filters_passed=filters_passed,
        created_at=now,
    )


def _format_message(p: OptionTradeProposal) -> str:
    vix_str = f"{p.vix:.1f}" if p.vix is not None else "n/a"
    return (
        f"SPX 0DTE entry proposal\n"
        f"Date:       {p.date}\n"
        f"Direction:  {p.direction.upper()}\n"
        f"Strike:     {p.strike}\n"
        f"Symbol:     {p.occ_symbol}\n"
        f"Entry time: {p.entry_time.isoformat()}\n"
        f"Hold cap:   {p.hold_cap_minutes:.0f} min\n"
        f"Est. premium: ${p.estimated_premium:.2f} (informational -- re-priced at submission)\n"
        f"VIX:        {vix_str}\n"
        f"Filters:    {p.filters_passed}\n"
        f"Proposal id: {p.proposal_id}\n"
        f"Approval valid <= 5 min after you decide."
    )


def create_and_notify(
    repo: SpxProposalRepository, proposal: OptionTradeProposal,
    *, bot_token: str, chat_id: str,
) -> bool:
    """Persists the proposal, then attempts to notify. The proposal
    row is saved either way -- a failed Telegram send is a logged,
    non-fatal event, not a reason to lose the candidate (matches the
    equity-ladder flow's own failure-mode table in
    docs/architecture/telegram-approval.md §5)."""
    repo.save(proposal)
    return send_proposal_message(
        bot_token=bot_token, chat_id=chat_id,
        text=_format_message(proposal), proposal_id=proposal.proposal_id,
    )


def process_decisions(
    repo: SpxProposalRepository, decisions: List[ControllerDecision],
    broker: BrokerClient, *, quantity: int, now: Optional[datetime] = None,
) -> List[SubmissionResult]:
    """Applies each decision to its proposal and, for an APPROVE still
    inside the 5-minute window, submits a buy order. Returns one
    SubmissionResult per decision that reached submission (a REJECT,
    an unknown proposal_id, or an already-decided proposal produces no
    SubmissionResult -- nothing was submitted)."""
    now = now or datetime.now(timezone.utc)
    results: List[SubmissionResult] = []

    for decision in decisions:
        proposal = repo.get(decision.proposal_id)
        if proposal is None:
            continue  # unknown proposal_id -- nothing to apply a decision to
        if proposal.approval_state != ProposalApprovalState.PENDING:
            continue  # already decided once; a decision may only be recorded once

        approved = decision.kind == DecisionKind.APPROVE
        decided = proposal.with_decision(approved=approved, decided_by=decision.decided_by, decided_at=now)
        repo.save(decided)

        if not approved:
            continue

        if not decided.is_approval_still_valid(now=now):
            continue  # expired between decision and this processing pass

        result = submit_entry_order(
            broker, client_order_id=decided.proposal_id, occ_symbol=decided.occ_symbol,
            quantity=quantity, limit_price=decided.estimated_premium,
        )
        results.append(result)

    return results


def expire_stale_pending(repo: SpxProposalRepository, *, older_than: datetime) -> int:
    """Expires every PENDING proposal created before `older_than`.
    Returns the count expired. A proposal the Controller never acted
    on must not sit PENDING forever -- mirrors the equity-ladder
    flow's own supersession/expiry discipline."""
    count = 0
    for proposal in repo.list_pending():
        if proposal.created_at < older_than:
            repo.save(proposal.expire(expired_at=older_than))
            count += 1
    return count
