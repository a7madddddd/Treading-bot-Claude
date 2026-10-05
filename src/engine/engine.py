"""Engine -- the persistent-process skeleton that composes the
already-existing, unchanged services into one coherent runtime
(Controller-approved Engine design review, this session).

Owns ONLY sequencing/scheduling/recovery/notification-routing. It
never re-implements a trading rule: every trigger comparison uses
values already frozen on `Trade` (`ladder1_price`/`ladder2_price`/
`active_floor_price`, set once at `freeze_initial_reference()` time);
every approval/D-0007/execution-mechanics/Ladder-2-confirmation/Floor
decision is delegated unchanged to `TradeProposalService`/
`ExecutionService`/`ProposalRepository`.

TWO independent cadences, per the Controller-approved design:
    - Ladder 1/Ladder 2 trigger detection AND the watchlist-driven
      creation of new Trades are BOTH gated by D-0021's existing,
      unchanged hourly schedule (see `schedule.is_d0021_check_time()`)
      -- `run_trigger_check()`. Neither is protective, so neither
      needs faster-than-hourly detection.
    - Reconciliation, decision intake, AND Floor detection are on a
      separate, tighter, independently-approved cadence --
      `run_reconciliation_tick()`. Floor was moved to THIS cadence
      (Controller-approved revision, this session) specifically
      because a protective exit benefits from faster detection than a
      discretionary entry does -- this does not touch or "silently
      change" D-0021 itself, which only ever governed Ladder/entry
      detection.
`run_forever()` composes both against a real clock; every other method
is a plain, synchronous, fully unit-testable function of `now`.

Floor SELL (Controller-approved, this session): reuses
`ExecutionService.submit_protective_exit()` -- proposal-independent,
approval-free, quantity always `trade.total_shares` read fresh, and
auto-applies even a partial fill (no Controller confirmation gate,
unlike Ladder 2 -- see `submit_protective_exit()`'s own docstring for
why).

Floor LIMIT PRICE (Controller-approved, this session): an execution
range of -1% to -0.5% off the current price at the moment Floor fires,
mirroring the SAME "or better" reasoning already approved for Ladder
1/Ladder 2's own execution range (`execution.service.
LADDER_EXECUTION_RANGE_FRACTION`) -- a SELL limit executes at the
specified limit price OR BETTER (i.e. at or above it), so using the
LOWER (more negative, -1%) boundary as the limit gives the order the
widest room to actually fill while still capping the worst acceptable
price at 1% below the trigger-time price -- see
`FLOOR_EXECUTION_RANGE_FRACTION` below and `_check_floor_trigger()`.

Watchlist-driven Trade creation (Controller-approved, this session):
the Engine never selects symbols itself -- `WatchlistSource.
get_active_symbols()` is the sole source, per `docs/architecture/
universe.md` §1. A symbol with no open Trade gets a new Trade +
Initial Entry proposal via `TradeProposalService.start_trade()`,
exactly the same discretionary/approval-gated Initial Entry flow that
already existed -- the Engine only decides WHEN to call it, never
invents any selection logic of its own.
"""

from __future__ import annotations

import time as _time_module
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Optional, Set, Tuple

from execution.broker_client import (
    BrokerClientError,
    BrokerCommunicationError,
    BrokerSubmissionAmbiguousError,
)
from execution.repository import OrderExecutionRepository
from execution.service import (
    ExecutionAlreadyResolvedError,
    ExecutionAlreadySubmittedError,
    ExecutionService,
    Ladder2PartialFillNotPendingError,
    PortfolioRiskViolatedError,
    SubmissionNotAllowedError,
)
from marketdata.source import MarketDataSource, MarketDataUnavailableError
from notifications.service import INotificationService, NotificationEvent, NotificationLevel
from orchestration.trade_proposal_service import LadderAlreadyFilledError, TradeProposalService
from proposals.models import ApprovalState, TradeAction, approved_strategy_rule_set
from proposals.models import FloorContext, StrategyRuleSet
from proposals.position_sizing import (
    APPROVED_D0051_POLICY, PositionSizingPolicyError,
)
from proposals.proposal import build_trade_proposal
from proposals.repository import ProposalDecisionConflictError, ProposalRepository
from trade.models import TradeStateError, describe_status
from trade.repository import TradeRepository

from .decision_source import ControllerDecision, DecisionKind, PendingDecisionSource
from .lock import EngineLock
from .schedule import D0021_TIMEZONE_ET, is_d0021_check_time
from .watchlist import WatchlistSource

_LADDER_ACTIONS: Tuple[TradeAction, ...] = (TradeAction.LADDER_1, TradeAction.LADDER_2)

FLOOR_EXECUTION_RANGE_FRACTION = 0.01
"""Controller-approved (this session): Floor's SELL execution range is
-1% to -0.5% off the current price at the moment Floor fires. Only the
LOWER (-1%, more negative) boundary is actually used as the submitted
limit price -- exactly mirroring how Ladder 1/Ladder 2 only ever use
ONE boundary of their own execution range as the real limit price (see
`execution.service.LADDER_EXECUTION_RANGE_FRACTION` and
`_execution_limit_price_for()`), not both. A SELL limit executes at
the specified price OR BETTER (at or above it) -- using the lower
boundary as the limit therefore gives the order room to fill anywhere
from -1% up through -0.5% and beyond, while never accepting a price
worse than 1% below the trigger-time price."""


def _format_price_context_block(
    buy_price: float, price_context: Optional[dict]
) -> str:
    """Formats the 'Price context' block appended above the Buy price
    line in an Initial Entry notification. Skips any field that is
    absent from `price_context`. Returns an empty string if there is
    nothing worth showing."""

    if not isinstance(price_context, dict) or not price_context:
        return ""

    lines: List[str] = []
    prev = price_context.get("previous_close")
    if isinstance(prev, (int, float)) and prev > 0:
        change = buy_price - prev
        pct = (change / prev) * 100.0
        arrow = "▲" if change > 0 else ("▼" if change < 0 else "•")
        lines.append(f"  Previous close: ${prev:,.2f}")
        lines.append(
            f"  Change now:     {arrow} ${change:+,.2f}  ({pct:+.2f}%)"
        )

    hi = price_context.get("today_high")
    lo = price_context.get("today_low")
    if (
        isinstance(hi, (int, float)) and hi > 0
        and isinstance(lo, (int, float)) and lo > 0
    ):
        lines.append(f"  Today range:    ${lo:,.2f} - ${hi:,.2f}")

    op = price_context.get("today_open")
    if isinstance(op, (int, float)) and op > 0:
        lines.append(f"  Today open:     ${op:,.2f}")

    if not lines:
        return ""
    return (
        "\n"
        "Price context (why this level now):\n"
        + "\n".join(lines)
        + "\n"
    )


def _format_proposal_message(
    proposal, *, recovery: bool = False,
    price_context: Optional[dict] = None,
) -> str:
    """Human-readable notification body for a proposal_awaiting_approval
    event. Deliberately shows prices, quantities, dollar cost, and every
    downside threshold in plain language -- so the Controller can decide
    without opening a database or a trade log. IDs (proposal_id,
    trade_id) are intentionally omitted from the visible text; the
    buttons' callback_data still carries the proposal_id, and the
    text-command fallback (/approve <id>) is unchanged.

    `price_context`, when supplied, is a dict from
    MarketDataSource.get_price_context (previous_close, today_open,
    today_high, today_low, today_volume). Any subset may be present;
    the format helper shows only the fields it received. Enrichment
    only -- never affects a trigger or execution decision.
    """

    symbol = proposal.symbol
    action = proposal.proposed_action
    prefix = "[recovery] " if recovery else ""

    if action is TradeAction.INITIAL_ENTRY:
        buy_price = proposal.proposed_entry
        # D-0051: read the Initial-Entry share count from the Proposal
        # itself. Pre-D-0051 proposals persisted with initial_quantity
        # = None (never set) fall back to the frozen pre-D-0051 fixed
        # 10 so legacy-era display stays faithful to what the Controller
        # actually approved then.
        qty = (proposal.initial_quantity
               if proposal.initial_quantity is not None
               else approved_strategy_rule_set().initial_qty)
        cost = buy_price * qty
        l1 = proposal.ladder_1_trigger
        l2 = proposal.ladder_2_trigger
        fl = proposal.floor_trigger
        context_block = _format_price_context_block(buy_price, price_context)
        return (
            f"{prefix}🎯 {symbol} — Initial Entry\n"
            f"{context_block}"
            f"\n"
            f"Buy price: ${buy_price:,.2f}\n"
            f"Quantity:  {qty} shares\n"
            f"Cost:      ${cost:,.2f}\n"
            f"\n"
            f"Downside safeguards (auto-computed from buy price):\n"
            f"  Ladder 1 buy at: ${l1:,.2f}  (-5%)\n"
            f"  Ladder 2 buy at: ${l2:,.2f}  (-8%)\n"
            f"  Auto-sell floor: ${fl:,.2f}  (-10%, protective)"
        )

    if action in (TradeAction.LADDER_1, TradeAction.LADDER_2):
        if action is TradeAction.LADDER_1:
            trigger = proposal.ladder_1_trigger
            qty = proposal.ladder_1_quantity
            pct = 5
            label = "Ladder 1"
        else:
            trigger = proposal.ladder_2_trigger
            qty = proposal.ladder_2_quantity
            pct = 8
            label = "Ladder 2"
        entry_ref = proposal.proposed_entry
        cost = trigger * qty
        avg_at = proposal.weighted_avg_entry_at_proposal
        floor = proposal.active_floor_at_proposal
        after = ""
        if avg_at is not None:
            after = (
                f"\n"
                f"Current avg entry: ${avg_at:,.2f}"
            )
        if floor is not None:
            after += (
                f"\n"
                f"Active floor: ${floor:,.2f}  (unchanged by this ladder)"
            )
        return (
            f"{prefix}📉 {symbol} — {label} (buy more, price down {pct}%)\n"
            f"\n"
            f"Trigger price:  ${trigger:,.2f}\n"
            f"Reference entry: ${entry_ref:,.2f}\n"
            f"Change from entry: -{pct}%\n"
            f"\n"
            f"Additional buy: {qty} shares\n"
            f"Cost:           ${cost:,.2f}"
            f"{after}"
        )

    # Fallback for any future action -- keep something human-readable
    # rather than silently omitting the action.
    return f"{prefix}{symbol} — {action.value} awaiting Controller approval."


def _format_political_tag(signal) -> str:
    """D-0058: the distinct tag on a politically-backed proposal.

    Carries the politician NAMES and the trade counts, not just a score,
    because the Controller's stated reason for tracking this source is
    judging the specific people -- "the political persons who manage the
    market". A bare number cannot be judged; a name can.

    Returns "" for a missing or unreadable signal, so a malformed
    signal object can never block a proposal. Every field is read
    defensively: this object comes from an external scraper.
    """

    if signal is None:
        return ""
    try:
        buys = int(getattr(signal, "politician_buys_30d", 0) or 0)
        sells = int(getattr(signal, "politician_sells_30d", 0) or 0)
        names = list(getattr(signal, "recent_names", None) or [])
        committee = bool(getattr(signal, "committee_match", False))
        weighted = float(getattr(signal, "weighted_signal", 0.0) or 0.0)
    except Exception:  # noqa: BLE001 - never block a proposal on a tag
        return ""

    if not buys and not sells and not names:
        return ""

    lines = ["", "--- POLITICAL SIGNAL (D-0058 reserved slot) ---"]
    if buys:
        lines.append(f"congress buys (30d): {buys}")
    if sells:
        lines.append(f"congress sells (30d): {sells}")
    if names:
        lines.append(f"who: {', '.join(str(n) for n in names[:5])}")
    if committee:
        lines.append("committee match: YES")
    lines.append(f"weighted signal: {weighted:.1f}")
    lines.append("Advisory only -- it never bypassed any safety stage.")
    return "\n".join(lines)


class Engine:
    def __init__(
        self,
        *,
        trade_repo: TradeRepository,
        proposal_repo: ProposalRepository,
        execution_repo: OrderExecutionRepository,
        trade_proposal_service: TradeProposalService,
        execution_service: ExecutionService,
        market_data: MarketDataSource,
        watchlist: WatchlistSource,
        decision_source: PendingDecisionSource,
        notifier: INotificationService,
        lock: EngineLock,
        db_persister: Optional[Callable[[datetime], None]] = None,
        proposal_enricher=None,
        trade_evaluator=None,
        portfolio_filter=None,
        macro_calendar=None,
        political_universe_source=None,
    ) -> None:
        self._trade_repo = trade_repo
        self._proposal_repo = proposal_repo
        self._execution_repo = execution_repo
        self._trade_proposal_service = trade_proposal_service
        self._execution_service = execution_service
        self._market_data = market_data
        self._watchlist = watchlist
        self._decision_source = decision_source
        self._notifier = notifier
        self._lock = lock
        self._notified: Set[Tuple[str, str]] = set()
        # Controller-approved 2026-10-01: optional callback invoked at
        # the END of each tick to persist the live DB (and ONLY the DB
        # file) to the remote git branch, so a cloud-container reclaim
        # mid-session does not lose state. See
        # scripts/run_paper_session.py for the production wiring.
        # The callback is best-effort: a git failure is logged but
        # never crashes the trading loop (the DB stays on local disk
        # for the next tick to retry).
        self._db_persister = db_persister
        # D-0050 Phase 2: optional Perplexity-driven research enrichment
        # appended to new proposal notifications. Advisory only; a None
        # value disables enrichment entirely, and any failure in the
        # enricher produces no change to the outgoing message.
        self._proposal_enricher = proposal_enricher
        # D-0050 Phase 10: optional TradeEvaluator ranks watchlist
        # candidates per trigger-check. When set, only Top-3 symbols
        # with soft_score >= 60 become proposals; when None, every
        # watchlist symbol without an open trade becomes a proposal
        # (the pre-D-0050 behavior).
        self._evaluator = trade_evaluator
        # D-0050 Phase 14: optional PortfolioFilter runs AFTER the
        # evaluator to enforce sector + correlation caps.
        self._portfolio_filter = portfolio_filter
        # D-0050 Phase 16: optional MacroEventCalendar blocks new
        # proposals in the pre-event window.
        self._macro_calendar = macro_calendar
        # D-0051 (2026-10-03): the Controller-approved percentage-based
        # sizing policy used to compute per-layer share counts for every
        # new proposal created in this engine. Replaces the pre-D-0051
        # fixed 10/10/20 that was hardcoded in approved_strategy_rule_set.
        # Not swappable at the method call site -- this is the single
        # authoritative policy for live proposals in this engine.
        self._sizing_policy = APPROVED_D0051_POLICY
        # D-0050 Phase B.27: political universe source. When set, its
        # Top-N tickers are UNIONed with the watchlist each cycle, and
        # its signal data enriches every candidate's SymbolResearch
        # before the evaluator scores it.
        self._political_universe = political_universe_source

    def _sized_strategy(self, *, price: float) -> Optional[StrategyRuleSet]:
        """D-0051: queries the broker for the account's current equity,
        then runs the Controller-approved sizing policy to compute the
        three per-layer share counts for a NEW proposal at this price.

        Returns None (and raises nothing) on any failure the engine must
        not propagate as a crash: broker unreachable, account blocked,
        policy rejects the inputs (equity or price non-positive, symbol
        too expensive to size at even one share). The CALLER checks the
        return value and simply skips creating the proposal when it is
        None -- fail-closed, no silent fallback to the pre-D-0051 fixed
        10/10/20 that would secretly size differently from the Controller's
        approved percentage policy.

        Pre-D-0051 proposals already persisted on disk are untouched by
        this method: when they advance to Ladder 1 or Ladder 2 their
        quantities come from TradeProposal.ladder_1_quantity /
        ladder_2_quantity exactly as before. Only brand-new INITIAL_ENTRY
        proposals (and their Ladder successors) use the new policy."""
        try:
            equity = self._execution_service._broker.get_account_equity()
        except Exception as exc:  # noqa: BLE001 -- fail-closed
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="position_sizing_broker_unavailable",
                message=(f"D-0051 position sizing could not query broker "
                         f"equity: {type(exc).__name__}: {exc}. "
                         "No proposal created this cycle."),
                symbol=None,
            )
            return None
        try:
            sizing = self._sizing_policy.compute_shares(
                equity=equity, price=price,
            )
        except PositionSizingPolicyError as exc:
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="position_sizing_rejected",
                message=(f"D-0051 position sizing rejected inputs "
                         f"(equity=${equity:,.2f}, price=${price:,.2f}): "
                         f"{exc}. No proposal created."),
                symbol=None,
            )
            return None
        return approved_strategy_rule_set(
            initial_qty=sizing.initial_qty,
            ladder_1_qty=sizing.ladder_1_qty,
            ladder_2_qty=sizing.ladder_2_qty,
            maximum_position=sizing.maximum_position,
        )

    def _enrich(self, symbol: str, base_message: str) -> str:
        """Fail-open wrapper that appends an enrichment blurb to a
        proposal message, or returns the message unchanged on any
        failure or when no enricher is configured."""
        if self._proposal_enricher is None:
            return base_message
        try:
            from engine.proposal_enricher import append_enrichment
            blurb = self._proposal_enricher.enrich(symbol)
            return append_enrichment(base_message, blurb)
        except Exception:  # noqa: BLE001 -- never block a proposal
            return base_message

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, *, now: datetime) -> None:
        """Acquires the lock, then runs the full recovery pass. Raises
        EngineLockHeldError (propagated from EngineLock.acquire()) if
        another live Engine already holds it -- this method never
        silently proceeds unprotected."""

        self._lock.acquire(now=now)
        self._heartbeat(now=now)
        self.recover(now=now)

    def shutdown(self) -> None:
        """Clean shutdown: releases the lock. Safe to call even if
        start() was never called or the lock was already lost."""
        self._lock.release()

    def _heartbeat(self, *, now: datetime) -> None:
        self._lock.heartbeat(now=now)

    # ------------------------------------------------------------------
    # Recovery (startup, before entering the normal loop)
    # ------------------------------------------------------------------

    def recover(self, *, now: datetime) -> None:
        """Startup recovery pass. Per the Controller-approved design:
        no new persistence, no new broad query -- walks
        TradeRepository.list_active() (already exists, already
        documented for restart recovery) -> ProposalRepository.
        list_for_trade() -> existing execution lookups, re-deriving
        everything from state that is already there. Never marks
        anything resolved on its own; only calls existing, idempotent
        service methods and re-fires notifications for anything still
        genuinely pending."""

        self._heartbeat(now=now)
        self._execution_service.reconcile_unresolved(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            self._recover_trade(trade_record.trade.trade_id, now=now)
        self._heartbeat(now=now)

    def _recover_trade(self, trade_id: str, *, now: datetime) -> None:
        record = self._trade_repo.get(trade_id)
        if record is None:
            return

        proposals = self._proposal_repo.list_for_trade(trade_id)
        if not proposals and describe_status(record.trade) == "AWAITING_INITIAL_FILL":
            # Crash window documented in TradeProposalService.start_trade()'s
            # own docstring: the Trade was persisted but the process died
            # before its Initial Entry proposal was. Re-create the missing
            # proposal -- never creates a second Trade, never touches an
            # existing proposal.
            self._recreate_missing_initial_entry(trade_id, record.trade.symbol, now=now)

        for proposal in proposals:
            if proposal.approval_state is ApprovalState.PENDING:
                self._notify_once(
                    kind="pending_approval",
                    key=proposal.proposal_id,
                    level=NotificationLevel.IMPORTANT,
                    event="proposal_awaiting_approval",
                    message=self._enrich(proposal.symbol, _format_proposal_message(
                        proposal,
                        recovery=True,
                        price_context=(
                            self._safe_price_context(proposal.symbol)
                            if proposal.proposed_action is TradeAction.INITIAL_ENTRY
                            else None
                        ),
                    )),
                    symbol=proposal.symbol,
                    interactive_actions=(
                        ("✅ Approve", f"approve:{proposal.proposal_id}"),
                        ("❌ Reject", f"reject:{proposal.proposal_id}"),
                    ),
                )
                continue

            if proposal.approval_state is ApprovalState.APPROVED:
                # Same live-observed gap as in _apply_decision: an
                # Initial Entry approved before a process crash never
                # got a broker order (its per-cycle submission path is
                # gated behind ACTIVE status, which it does not yet
                # have). On startup, retry the submission if no
                # execution record exists yet. Ladders are submitted
                # by _process_trade at the next tick as before.
                if (
                    proposal.proposed_action is TradeAction.INITIAL_ENTRY
                    and self._execution_repo.get_by_proposal_id(proposal.proposal_id) is None
                ):
                    try:
                        price = self._market_data.get_last_trade(proposal.symbol)
                    except MarketDataUnavailableError:
                        # Leave APPROVED; next reconciliation tick with
                        # working market data will retry via the same
                        # gap-fix in _apply_decision (queue drain) OR
                        # future recovery ticks. Not fatal.
                        continue
                    # P-019 fix (2026-10-05): initial_entry_trade_id was
                    # MISSING here while the two equivalent call sites
                    # (_recover_approved_without_execution and
                    # _apply_decision) both pass it. Per
                    # _submit_approved's own docstring that argument is
                    # what abandons the Trade on a terminal pre-fill
                    # refusal -- without it the Trade stays in
                    # AWAITING_INITIAL_FILL and _check_watchlist's
                    # has_open_trade guard locks the symbol out, which
                    # is exactly the KO/V failure.
                    #
                    # Severity was low because
                    # _recover_approved_without_execution runs on EVERY
                    # reconciliation tick, covers the same condition and
                    # DOES pass the argument, so the gap self-healed in
                    # about 30 seconds. Fixed anyway: an inconsistency
                    # between three call sites doing the same thing is a
                    # trap for the next reader, and relying on another
                    # method to clean up after this one is not a
                    # contract anybody wrote down.
                    self._submit_approved(
                        proposal.proposal_id,
                        current_price=price,
                        active_floor_price=proposal.floor_trigger,
                        now=now,
                        initial_entry_trade_id=proposal.trade_id,
                    )
                    continue
                self._execution_service.recover_if_terminal(proposal.proposal_id, now=now)
                self._maybe_notify_ladder2_pending_confirmation(proposal.proposal_id, now=now)

        # SELL-side (Floor) recovery -- has no proposal to anchor a
        # lookup to, so it needs its own call regardless of the
        # proposal walk above. Safe/idempotent: a no-op if no
        # protective-exit execution exists, or if it already applied.
        self._execution_service.recover_protective_exit_if_terminal(trade_id, now=now)

    def _recreate_missing_initial_entry(self, trade_id: str, symbol: str, *, now: datetime) -> None:
        try:
            price = self._market_data.get_last_trade(symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=(
                    f"[recovery] Could not re-create the missing Initial Entry proposal for "
                    f"trade {trade_id!r} ({symbol}): {exc}"
                ),
                symbol=symbol,
            )
            return

        # D-0051: recovery of a missing Initial Entry still goes through
        # the Controller-approved sizing policy -- a recovered proposal
        # must size identically to how a fresh one would right now.
        strategy = self._sized_strategy(price=price)
        if strategy is None:
            return
        proposal_id = f"{trade_id}-initial_entry-{uuid.uuid4().hex[:8]}"
        proposal = build_trade_proposal(
            proposal_id=proposal_id,
            trade_id=trade_id,
            action=TradeAction.INITIAL_ENTRY,
            symbol=symbol,
            current_price=price,
            as_of=now,
            strategy=strategy,
            floor_context=FloorContext.no_existing_position(),
        )
        self._proposal_repo.save(proposal)
        self._notify_once(
            kind="pending_approval",
            key=proposal.proposal_id,
            level=NotificationLevel.IMPORTANT,
            event="proposal_awaiting_approval",
            message=self._enrich(symbol, _format_proposal_message(
                proposal, recovery=True,
                price_context=self._safe_price_context(symbol),
            )),
            symbol=symbol,
            interactive_actions=(
                ("✅ Approve", f"approve:{proposal.proposal_id}"),
                ("❌ Reject", f"reject:{proposal.proposal_id}"),
            ),
        )

    # ------------------------------------------------------------------
    # Reconciliation + decision intake (tighter, independent cadence)
    # ------------------------------------------------------------------

    def run_reconciliation_tick(self, *, now: datetime) -> None:
        self._heartbeat(now=now)
        self._execution_service.reconcile_unresolved(now=now)
        self._heartbeat(now=now)
        self._apply_decisions(now=now)
        self._heartbeat(now=now)
        # Recovery pass for APPROVED proposals whose submission never
        # happened (crash between record_decision and _submit_approved,
        # or any other reason the execution row is absent). Without
        # this, an INITIAL_ENTRY approved just before a container
        # restart stays APPROVED-but-unsubmitted forever (2026-10-01
        # Finding #4).
        self._recover_approved_without_execution(now=now)
        self._heartbeat(now=now)
        # TTL sweep runs AFTER decisions are applied, so a decision that
        # arrived in this same tick always wins over the timer.
        self._expire_stale_proposals(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            trade_id = trade_record.trade.trade_id
            self._check_floor_trigger(trade_id, now=now)
            for proposal in self._proposal_repo.list_for_trade(trade_id):
                if proposal.proposed_action is TradeAction.LADDER_2 and proposal.approval_state is ApprovalState.APPROVED:
                    self._maybe_notify_ladder2_pending_confirmation(proposal.proposal_id, now=now)
        self._heartbeat(now=now)
        self._persist_db_best_effort(now=now)

    def _recover_approved_without_execution(self, *, now: datetime) -> None:
        """Finds proposals in state APPROVED that have no execution
        row (never submitted) and retries submission.

        Scope: INITIAL_ENTRY only. Ladder APPROVED-but-unsubmitted is
        already retried by _process_trade on every D-0021 :30 tick --
        this method deliberately does not duplicate that, since D-0007
        requires ladder revalidation to use a fresh market price
        anyway and _process_trade already has that structure.
        """

        for trade_record in self._trade_repo.list_active():
            trade = trade_record.trade
            for proposal in self._proposal_repo.list_for_trade(trade.trade_id):
                if proposal.proposed_action is not TradeAction.INITIAL_ENTRY:
                    continue
                if proposal.approval_state is not ApprovalState.APPROVED:
                    continue
                if self._execution_repo.get_by_proposal_id(proposal.proposal_id) is not None:
                    continue
                # APPROVED, INITIAL_ENTRY, no execution row -- retry.
                try:
                    price = self._market_data.get_last_trade(proposal.symbol)
                except MarketDataUnavailableError as exc:
                    self._notify(
                        level=NotificationLevel.CRITICAL,
                        event="market_data_unavailable",
                        message=(
                            f"Recovery: could not fetch current price for "
                            f"{proposal.symbol} to retry approved Initial Entry "
                            f"{proposal.proposal_id}: {exc}."
                        ),
                        symbol=proposal.symbol,
                    )
                    continue
                self._notify(
                    level=NotificationLevel.IMPORTANT,
                    event="initial_entry_recovery_submit",
                    message=(
                        f"Recovery: retrying submission of approved Initial Entry "
                        f"{proposal.proposal_id} for {proposal.symbol} (no execution "
                        f"row found)."
                    ),
                    symbol=proposal.symbol,
                )
                self._submit_approved(
                    proposal.proposal_id,
                    current_price=price,
                    active_floor_price=proposal.floor_trigger,
                    now=now,
                    initial_entry_trade_id=proposal.trade_id,
                )

    def _expire_stale_proposals(self, *, now: datetime) -> None:
        """Expires PENDING proposals older than PROPOSAL_TTL_SECONDS.

        Two deliberate restrictions, both of which would be real bugs if
        relaxed:

        1. ONLY PENDING is touched. An APPROVED proposal is a Controller
           decision and is never discarded by a timer -- D-0007
           revalidation already refuses a stale approved submission on
           price drift. (Enforced again inside plan_expiry.)

        2. ONLY an expired INITIAL_ENTRY abandons its trade. A LADDER
           proposal belongs to a trade that already HOLDS SHARES, and
           abandoning that trade would drop a live position's Ladder and
           Floor tracking -- losing the protective floor on a real
           position. An expired ladder simply lapses; the next D-0021
           tick re-proposes it from the unchanged frozen reference.
        """

        for trade_record in self._trade_repo.list_active():
            trade = trade_record.trade
            for proposal in self._proposal_repo.list_for_trade(trade.trade_id):
                if proposal.approval_state is not ApprovalState.PENDING:
                    continue
                age_seconds = (now - proposal.proposal_created_at).total_seconds()
                if age_seconds < self.PROPOSAL_TTL_SECONDS:
                    continue
                self._expire_one_proposal(proposal, age_seconds=age_seconds, now=now)

    def _expire_one_proposal(self, proposal, *, age_seconds: float, now: datetime) -> None:
        try:
            self._proposal_repo.expire_pending(
                proposal.proposal_id, expired_at=now,
            )
        except ProposalDecisionConflictError:
            # A decision landed between the list and the write -- the
            # Controller won the race, which is the correct outcome.
            # Nothing to report: the decision path notifies on its own.
            return
        except Exception as exc:  # noqa: BLE001 - main-loop shield
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="proposal_expiry_failed",
                message=(
                    f"Could not expire stale proposal "
                    f"{proposal.proposal_id} ({proposal.symbol}): {exc}. "
                    f"It stays PENDING and will be retried next tick."
                ),
                symbol=proposal.symbol,
            )
            return

        minutes = age_seconds / 60.0
        self._notify(
            level=NotificationLevel.IMPORTANT,
            event="proposal_expired",
            message=(
                f"Proposal {proposal.proposal_id} ({proposal.symbol}, "
                f"{proposal.proposed_action.value}) EXPIRED after "
                f"{minutes:.0f} minutes with no decision. Its Approve / "
                f"Reject buttons no longer do anything. Nothing was "
                f"bought or sold."
            ),
            symbol=proposal.symbol,
        )

        if proposal.proposed_action is TradeAction.INITIAL_ENTRY:
            # Release the symbol. Without this the trade stays in
            # AWAITING_INITIAL_FILL and _check_watchlist locks the
            # symbol out forever -- exactly what happened to KO and V.
            self._abandon_initial_entry_after_refusal(
                proposal.trade_id, proposal.proposal_id,
                reason=(f"no Controller decision within "
                        f"{self.PROPOSAL_TTL_SECONDS / 60.0:.0f} minutes"),
                now=now,
            )

    def _check_floor_trigger(self, trade_id: str, *, now: datetime) -> None:
        """Floor detection -- Controller-approved to run on THIS
        (faster, independent) cadence rather than D-0021, specifically
        because a protective exit benefits from faster detection than
        a discretionary entry. No Controller approval gate at any
        point in this method, by design (execution.md §1/§2; TradeAction
        deliberately excludes FLOOR)."""

        record = self._trade_repo.get(trade_id)
        if record is None:
            return
        trade = record.trade
        if describe_status(trade) != "ACTIVE":
            return

        try:
            price = self._market_data.get_last_trade(trade.symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=f"Could not get current price for {trade.symbol} (trade {trade_id}) for Floor check: {exc}",
                symbol=trade.symbol,
            )
            return

        # Trailing floor activation + ratchet (D-0004/D-0008). Must run
        # BEFORE reading active_floor so the latest trailing_floor_price
        # participates in the floor comparison this same tick.
        trade, record = self._maybe_update_trailing(record, price, now=now)
        if record is None:
            return

        active_floor = trade.active_floor_price
        if active_floor is None:
            return

        if price > active_floor:
            return

        existing = self._execution_repo.get_by_trade_id_and_side(trade_id, "sell")
        if existing is not None and not existing.execution.is_broker_terminal:
            return  # already has a live protective exit in flight -- never a duplicate submission

        # Controller-approved execution range: -1% to -0.5% off the
        # current price. Only the lower (-1%) boundary is submitted as
        # the actual limit price -- see FLOOR_EXECUTION_RANGE_FRACTION's
        # own docstring for why (mirrors the Ladder 1/Ladder 2 execution
        # range's "one boundary only" pattern exactly). Rounding is left
        # to the broker-client formatter (SEC Rule 612, side-aware
        # ROUND_UP for sell) so precision is quantized in exactly one
        # place.
        limit_price = price * (1 - FLOOR_EXECUTION_RANGE_FRACTION)

        self._notify(
            level=NotificationLevel.CRITICAL,
            event="floor_triggered",
            message=(
                f"Floor triggered for trade {trade_id} ({trade.symbol}): price {price} at or below "
                f"active floor {active_floor}. Submitting protective SELL for {trade.total_shares} "
                f"shares at limit {limit_price:.4f} (or better; broker quantizes to SEC-compliant precision)."
            ),
            symbol=trade.symbol,
        )

        try:
            exit_record = self._execution_service.submit_protective_exit(
                trade_id, quantity=trade.total_shares, limit_price=limit_price, now=now
            )
        except ExecutionAlreadySubmittedError:
            return  # benign: another cycle already has one in flight
        except BrokerSubmissionAmbiguousError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_submission_ambiguous",
                message=(
                    f"Floor SELL submission for trade {trade_id} had an ambiguous outcome: {exc}. "
                    "Will resolve via reconciliation."
                ),
                symbol=trade.symbol,
            )
            return
        except Exception as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_submission_failed",
                message=f"Floor SELL submission failed for trade {trade_id}: {exc}",
                symbol=trade.symbol,
            )
            return

        self._notify_floor_outcome(trade_id, exit_record.execution, symbol=trade.symbol)

    def _persist_db_best_effort(self, *, now: datetime) -> None:
        """Invoke the Controller-approved DB persister (git commit +
        push of paper_session.sqlite only) at the end of every tick.
        Never crashes the main loop on failure -- any git error
        surfaces as a CRITICAL notification and the next tick retries.
        """
        if self._db_persister is None:
            return
        try:
            self._db_persister(now)
        except Exception as exc:  # noqa: BLE001 -- engine shield
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="db_persist_failed",
                message=(
                    f"DB persistence to git failed ({type(exc).__name__}): "
                    f"{exc}. State is still on local disk; next tick retries. "
                    "If this repeats, the cloud container may lose state "
                    "on reclaim."
                ),
            )

    def _maybe_update_trailing(self, record, price, *, now):
        """Trailing floor activation + ratchet (strategy.md Sec.5 /
        D-0004 / D-0008). Threshold-based, not price-based: activates
        the first time price reaches weighted_avg_entry_price * 1.10
        and the trailing floor becomes activation_threshold * 0.95;
        each subsequent threshold is previous * 1.05 and the new floor
        is new_threshold * 0.95. The floor only ever moves UP (defended
        in trade.models.ratchet_trailing). Called from
        _check_floor_trigger on the faster reconciliation cadence,
        BEFORE active_floor_price is read, so the latest trailing
        state participates in the floor comparison this same tick.
        Idempotent: a tick where no activation/ratchet condition holds
        is a plain no-op; a tick exactly on the activation threshold
        activates once and returns.

        Returns the (possibly-updated) (trade, record) tuple. If a
        write failed (concurrent revision, repo error) returns the
        original record so the floor check still runs against the
        pre-update state and the next tick retries."""

        trade = record.trade
        wae = trade.weighted_avg_entry_price
        if wae is None:
            return trade, record

        # Activation.
        if not trade.trailing_activated:
            activation_threshold = round(wae * 1.10, 4)
            if price >= activation_threshold:
                try:
                    updated = trade.activate_trailing(
                        current_price=activation_threshold, now=now,
                    )
                    new_record = self._trade_repo.update(
                        updated,
                        expected_revision=record.revision,
                        transition="trailing_activated",
                        now=now,
                    )
                    self._notify(
                        level=NotificationLevel.IMPORTANT,
                        event="trailing_activated",
                        message=(
                            f"Trailing floor ACTIVATED for {trade.symbol} (trade "
                            f"{trade.trade_id}): price {price:.4f} reached activation "
                            f"threshold {activation_threshold:.4f} (WAE {wae:.4f} x 1.10). "
                            f"Trailing floor now at {updated.trailing_floor_price:.4f} "
                            f"(vs original floor {trade.original_floor_price:.4f})."
                        ),
                        symbol=trade.symbol,
                    )
                    record = new_record
                    trade = record.trade
                except TradeStateError:
                    # price slipped below threshold between read and
                    # activate -- benign, retry next tick.
                    return trade, record
                except Exception as exc:  # noqa: BLE001
                    self._notify(
                        level=NotificationLevel.CRITICAL,
                        event="trailing_activation_failed",
                        message=(
                            f"Trailing activation write failed for {trade.symbol} "
                            f"(trade {trade.trade_id}): {exc}. Will retry on the next tick."
                        ),
                        symbol=trade.symbol,
                    )
                    return trade, record

        # Ratchet (loop while the current price keeps clearing the next
        # +5% threshold -- matches the backtest simulator's `while` so
        # live and simulated trades behave identically for the same
        # price path).
        while (
            trade.trailing_activated
            and trade.trailing_current_threshold is not None
        ):
            next_threshold = round(trade.trailing_current_threshold * 1.05, 4)
            if price < next_threshold:
                break
            try:
                updated = trade.ratchet_trailing(current_price=next_threshold, now=now)
            except TradeStateError:
                break
            try:
                new_record = self._trade_repo.update(
                    updated,
                    expected_revision=record.revision,
                    transition="trailing_ratcheted",
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="trailing_ratchet_failed",
                    message=(
                        f"Trailing ratchet write failed for {trade.symbol} (trade "
                        f"{trade.trade_id}): {exc}. Will retry on the next tick."
                    ),
                    symbol=trade.symbol,
                )
                return trade, record
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="trailing_ratcheted",
                message=(
                    f"Trailing floor RATCHETED for {trade.symbol} (trade "
                    f"{trade.trade_id}): new threshold {next_threshold:.4f}, "
                    f"new trailing floor {updated.trailing_floor_price:.4f}."
                ),
                symbol=trade.symbol,
            )
            record = new_record
            trade = record.trade

        return trade, record

    def _notify_floor_outcome(self, trade_id: str, execution, *, symbol: str) -> None:
        if not execution.is_broker_terminal:
            return
        if execution.filled_qty == execution.requested_qty:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_executed_full",
                message=(
                    f"Floor executed for trade {trade_id}: sold {execution.filled_qty}/"
                    f"{execution.requested_qty} shares. Trade closed."
                ),
                symbol=symbol,
            )
        elif execution.filled_qty > 0:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_executed_partial",
                message=(
                    f"Floor partially filled for trade {trade_id}: sold {execution.filled_qty}/"
                    f"{execution.requested_qty} shares (auto-applied, no approval needed). The "
                    "remaining shares will be re-offered for exit on a later cycle."
                ),
                symbol=symbol,
            )
        else:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_execution_rejected",
                message=f"Floor SELL for trade {trade_id} was rejected/expired with zero fill.",
                symbol=symbol,
            )

    def _apply_decisions(self, *, now: datetime) -> None:
        for decision in self._decision_source.poll():
            self._apply_decision(decision, now=now)

    def _apply_decision(self, decision: ControllerDecision, *, now: datetime) -> None:
        proposal = self._proposal_repo.get(decision.proposal_id)
        if proposal is None:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="decision_unknown_proposal",
                message=f"Controller decision received for unknown proposal_id {decision.proposal_id!r} -- ignored.",
            )
            return

        if decision.kind is DecisionKind.CONFIRM_LADDER2_PARTIAL_FILL:
            try:
                self._execution_service.confirm_ladder2_partial_fill(
                    decision.proposal_id, decided_by=decision.decided_by, now=now
                )
            except Ladder2PartialFillNotPendingError as exc:
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="ladder2_confirmation_refused",
                    message=f"Ladder 2 partial-fill confirmation for {decision.proposal_id} refused: {exc}",
                    symbol=proposal.symbol,
                )
            return

        approved = decision.kind is DecisionKind.APPROVE
        try:
            self._proposal_repo.record_decision(
                decision.proposal_id,
                approved=approved,
                decided_by=decision.decided_by,
                decided_at=now,
                action=proposal.proposed_action,
            )
        except ProposalDecisionConflictError as exc:
            # A normal, expected consequence of the 60-minute TTL: the
            # Controller comes back after an hour and taps a button on a
            # proposal that has since expired. That is routine, not a
            # fault, so it must NOT be reported as CRITICAL -- an alarm
            # that fires on ordinary behavior trains the Controller to
            # ignore alarms. Every OTHER conflict (already decided,
            # unknown id) stays CRITICAL, because those are real.
            # Re-read rather than trusting the copy fetched before the
            # call: that copy is a snapshot from earlier in this method,
            # and the whole point here is to classify a state that may
            # have changed since. One extra lookup, on the error path
            # only.
            current = self._proposal_repo.get(decision.proposal_id)
            if (current is not None
                    and current.approval_state is ApprovalState.EXPIRED):
                self._notify(
                    level=NotificationLevel.IMPORTANT,
                    event="decision_on_expired_proposal",
                    message=(
                        f"Your decision for {decision.proposal_id} "
                        f"({proposal.symbol}) arrived after the proposal "
                        f"had already expired, so it was not applied. "
                        f"Nothing was bought or sold. The symbol is free "
                        f"again and may be re-proposed on a later cycle "
                        f"at a fresh price."
                    ),
                    symbol=proposal.symbol,
                )
                return
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="decision_conflict",
                message=f"Controller decision for {decision.proposal_id} could not be recorded: {exc}",
                symbol=proposal.symbol,
            )
            return

        # For a REJECTED Initial Entry, mark the Trade as ABANDONED so
        # the symbol is available on the next watchlist tick. A Trade
        # row is created at proposal-generation time before Controller
        # approval; if it stays in AWAITING_INITIAL_FILL after the
        # rejection, _check_watchlist's "already has an open trade for
        # this symbol" guard permanently locks the symbol out (a
        # live-observed 2026-09-30 audit finding).
        if (
            not approved
            and proposal.proposed_action is TradeAction.INITIAL_ENTRY
        ):
            trade_record = self._trade_repo.get(proposal.trade_id)
            if trade_record is not None:
                trade = trade_record.trade
                # Only abandon if the trade is still fresh (never
                # reconciled). A rejection arriving for a
                # somehow-already-reconciled trade is odd but not
                # dangerous -- ignore it and leave the trade alone.
                if not trade.initial_order_reconciled:
                    try:
                        self._trade_repo.update(
                            trade.abandon_before_fill(now=now),
                            expected_revision=trade_record.revision,
                            transition="rejected_before_fill",
                            now=now,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Shielded like the submission path -- a repo
                        # write failure here must never crash the
                        # engine.
                        self._notify(
                            level=NotificationLevel.CRITICAL,
                            event="abandon_failed",
                            message=(
                                f"Could not mark {proposal.trade_id} as ABANDONED after "
                                f"rejection of {proposal.proposal_id}: {exc}. "
                                f"Symbol may be blocked from new proposals until DB is cleaned."
                            ),
                            symbol=proposal.symbol,
                        )
            return

        # For an Initial Entry approval, submit to the broker immediately.
        # The per-cycle _process_trade loop skips AWAITING_INITIAL_FILL
        # trades (see the guard there), so nothing else in this file
        # would ever fire submit_approved_proposal for an Initial Entry
        # -- the proposal would sit APPROVED-but-unsubmitted forever
        # (a real bug observed live in the 2026-09-29 paper session).
        # Ladder approvals continue to be submitted by _process_trade at
        # the next tick, which also revalidates the trigger against the
        # current price and D-0007's ±0.5% band.
        if approved and proposal.proposed_action is TradeAction.INITIAL_ENTRY:
            try:
                price = self._market_data.get_last_trade(proposal.symbol)
            except MarketDataUnavailableError as exc:
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="market_data_unavailable",
                    message=(
                        f"Could not fetch current price for {proposal.symbol} to submit "
                        f"approved Initial Entry proposal {proposal.proposal_id}: {exc}. "
                        f"Proposal remains APPROVED; submission will be retried on the next "
                        f"reconciliation tick that has market data."
                    ),
                    symbol=proposal.symbol,
                )
                return
            # An INITIAL_ENTRY has no prior position and therefore no
            # PRIOR active floor -- but Controller-approved D-0007
            # revalidation still requires a non-None floor for every
            # submission (see tests/proposals/test_revalidation.py's
            # TestD2UnknownFloorBlocks). The proposal itself already
            # carries the floor level this trade WILL have once it
            # opens (proposal.floor_trigger = entry * (1 + floor_pct)),
            # which is the semantically correct value to revalidate
            # the entry price against.
            self._submit_approved(
                proposal.proposal_id,
                current_price=price,
                active_floor_price=proposal.floor_trigger,
                now=now,
                initial_entry_trade_id=proposal.trade_id,
            )
            return

        # For a LADDER approval, submit immediately too -- the per-cycle
        # _process_trade loop only submits on D-0021 :30 ticks, so a
        # Controller approval at (say) 10:45 would sit unsubmitted until
        # 11:30. That 45-minute wait guarantees D-0007's 5-minute /
        # +/-0.5% window expires before submission ever runs, and every
        # legitimate Ladder approval gets refused as EXPIRED / PRICE_DRIFT.
        # Ladder pricing is anchored on the trade's frozen original entry
        # and its already-computed trigger, so we still need the live
        # price to satisfy D-0007's band check plus the active floor
        # from the trade itself (an existing position always has a
        # concrete original floor).
        if approved and proposal.proposed_action in _LADDER_ACTIONS:
            trade_record = self._trade_repo.get(proposal.trade_id)
            if trade_record is None:
                # Genuinely broken state; the shield above already
                # handles unknown-proposal, but a missing trade is
                # different and worth its own CRITICAL notification.
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="decision_missing_trade",
                    message=(
                        f"Approved LADDER {proposal.proposal_id} references trade "
                        f"{proposal.trade_id} which is missing from the repository. "
                        f"Submission skipped; investigate the state store."
                    ),
                    symbol=proposal.symbol,
                )
                return
            trade = trade_record.trade
            try:
                price = self._market_data.get_last_trade(proposal.symbol)
            except MarketDataUnavailableError as exc:
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="market_data_unavailable",
                    message=(
                        f"Could not fetch current price for {proposal.symbol} to submit "
                        f"approved LADDER proposal {proposal.proposal_id}: {exc}. "
                        f"Proposal remains APPROVED; the per-cycle _process_trade path "
                        f"will retry on the next :30 tick with a fresh price."
                    ),
                    symbol=proposal.symbol,
                )
                return
            self._submit_approved(
                proposal.proposal_id,
                current_price=price,
                active_floor_price=trade.active_floor_price,
                now=now,
            )

    # ------------------------------------------------------------------
    # Trigger detection (D-0021-gated cadence)
    # ------------------------------------------------------------------

    def run_trigger_check(self, *, now: datetime) -> None:
        self._heartbeat(now=now)
        self._check_watchlist(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            self._process_trade(trade_record.trade.trade_id, now=now)
            self._heartbeat(now=now)
        self._persist_db_best_effort(now=now)

    # D-0050 Phase 10 — Controller-approved policy for the evaluator:
    #   TOP_N_PER_CYCLE  — at most N new proposals per trigger-check
    #   MIN_SCORE        — a candidate must score >= this to become a proposal
    _TOP_N_PER_CYCLE = 3
    _MIN_SCORE = 60.0

    PROPOSAL_TTL_SECONDS = 3600.0
    """Controller-approved 2026-10-05: a PENDING proposal that gets no
    decision within 60 minutes expires, and an expired INITIAL_ENTRY
    releases its symbol.

    Why exactly 60 minutes, rather than a round guess: it is one
    D-0021 cycle. The trigger schedule fires hourly on the half hour
    (engine/schedule.py), so a proposal dies precisely before the next
    cycle could produce its replacement -- no overlap, no gap, and no
    second timing concept introduced into the system.

    Why a TTL is needed at all: before this, NOTHING expired a PENDING
    proposal. It was superseded only when a newer proposal for the same
    (trade_id, action) was saved -- and for an INITIAL_ENTRY that can
    never happen, because the trade sits in AWAITING_INITIAL_FILL and
    _check_watchlist excludes the symbol while it does. Live evidence:
    KO and V sat PENDING from 2026-10-01 and locked both symbols out
    indefinitely.

    Why staleness matters beyond the lock: D-0007 revalidation refuses
    a submission once price has drifted more than 0.5% from the
    proposal's trigger. An hours-old proposal is therefore very likely
    to be refused on approval anyway -- so keeping it alive offers the
    Controller a choice that no longer really exists."""

    def _notify_nothing_to_trade(self, reason: str, *, now: datetime) -> None:
        """Controller-approved 2026-10-05: on a day where the Engine has
        nothing it can legitimately propose, say so ONCE, instead of
        staying silent.

        Silence is ambiguous -- the Controller cannot tell "the universe
        was empty today" from "the engine is dead" or "the pipeline
        crashed". One positive message per trading day removes that
        ambiguity. `reason` names WHICH of the no-trade cases fired, so
        a genuine pipeline failure (no snapshot at all) is
        distinguishable from a normal quiet day (candidates evaluated,
        none scored high enough).

        Deduplicated per US-Eastern trading date via the same
        `_notified` set `_notify_once` uses. That set is in-memory, so
        an Engine restart mid-day re-sends it once -- deliberately kept,
        because a restart is itself something the Controller should see.
        """

        trading_date = now.astimezone(D0021_TIMEZONE_ET).date().isoformat()
        self._notify_once(
            kind="nothing_to_trade",
            key=trading_date,
            level=NotificationLevel.IMPORTANT,
            event="nothing_to_trade_today",
            message=(
                f"No new trade proposals for {trading_date} (ET).\n"
                f"Reason: {reason}\n"
                "The engine is running normally; existing positions "
                "continue to be monitored (Ladder / Floor / Trailing "
                "are unaffected). This is a status message, not an error."
            ),
            symbol=None,
        )

    def _market_open_for_new_proposals(self, *, now: datetime) -> bool:
        """Controller-approved 2026-10-05: ask the broker whether the
        market is open before creating ANY new proposal.

        SCOPE -- this gates ONE thing and must never gate more.
        It is called only from _check_watchlist, so it can stop new
        proposals and nothing else. Reconciliation, the protective
        Floor, and the Trailing Floor all run regardless of this
        answer, and that is not an accident:

            An order approved at 15:59 can fill at 16:00:01, after the
            close. If the engine stopped reconciling while the market
            was shut, it would never learn that the fill happened, so
            it would never compute that position's Floor -- leaving a
            REAL position unprotected overnight while the engine
            believed nothing was held.

        Blocking an exit to "be safe" is never safe. Only entries wait.

        FAIL CLOSED. Any error means "unknown", and unknown is treated
        as closed. The asymmetry is deliberate: a wrong 'open' creates
        a real order priced off a stale quote, while a wrong 'closed'
        costs one cycle, and the next D-0021 trigger is an hour later.
        The Controller stated the preference directly -- "I didn't mind
        if I lose any chance to buy any item, otherwise I didn't want
        to make mistakes".

        The broker already retries transient failures three times
        (common/http_retry.RetryPolicy, wired in run_paper_session), so
        an exception reaching here is not a single blip.
        """

        try:
            is_open = self._execution_service._broker.is_market_open()
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash
            self._notify_nothing_to_trade(
                f"could not reach the broker to confirm the market is "
                f"open ({type(exc).__name__}: {exc}). Failing closed: no "
                f"new proposal this cycle. Existing positions are still "
                f"monitored.",
                now=now,
            )
            return False

        if not is_open:
            self._notify_nothing_to_trade(
                "the broker reports the market is CLOSED right now "
                "(holiday, half-day early close, or an unscheduled "
                "closure). No new proposals; existing positions are "
                "still monitored.",
                now=now,
            )
            return False
        return True

    def _check_watchlist(self, *, now: datetime) -> None:
        """Watchlist-driven Trade creation.

        Pre-D-0050: every watchlist symbol without an open trade became
        a proposal.

        D-0050 Phase 10 (Controller-approved): when a TradeEvaluator
        is attached, candidates run through the hub+evaluator first.
        Only the Top-3 with soft_score >= 60 become proposals; the
        rest are silently dropped. A symbol with an open trade
        (AWAITING_INITIAL_FILL or ACTIVE) is still excluded up-front,
        same as before — the Engine never proposes a duplicate.
        """

        if not self._market_open_for_new_proposals(now=now):
            return

        candidates: list = []
        seen_syms: set = set()
        for symbol in self._watchlist.get_active_symbols():
            if symbol in seen_syms:
                continue
            seen_syms.add(symbol)
            records = self._trade_repo.list_for_symbol(symbol)
            has_open_trade = any(describe_status(r.trade) in ("AWAITING_INITIAL_FILL", "ACTIVE") for r in records)
            if has_open_trade:
                continue
            candidates.append(symbol)

        # D-0058 (Controller-approved 2026-10-05): the political source
        # supplies SIGNALS ONLY. It no longer adds symbols.
        #
        # It used to UNION its Top-N tickers into `candidates` here,
        # AFTER the D-0026 pipeline had already run. That was a second
        # door into the watchlist which skipped every safety stage:
        # tradability, liquidity, the 0.15% spread cap, the ATR band and
        # the sector cap. A congressman's illiquid small-cap with a 2%
        # spread reached a proposal with no spread check at all, and the
        # ladder buys three times -- roughly 6% lost to spread alone
        # against a -10% floor, before the strategy even starts.
        #
        # It would also have bypassed D-0056's leveraged/inverse filter,
        # which lives in the broker provider and only sees symbols that
        # provider fetched. A politician buying a 2x inverse fund would
        # have walked straight past the DXD protection.
        #
        # The symbols are not lost by removing this: production runs the
        # pipeline over every tradable US equity, so a political pick
        # that is a real equity is ALREADY in the candidate pool and is
        # ranked like any other -- with its political score, and with
        # the reserved slot below guaranteeing it a place when it
        # qualifies. What it no longer gets is a way around the safety
        # stages.
        political_signals: dict = {}
        if self._political_universe is not None:
            try:
                political_signals = self._political_universe.get_signals()
            except Exception as exc:  # noqa: BLE001
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="political_universe_failed",
                    message=f"Political universe source failed: {exc}",
                    symbol=None,
                )
            else:
                self._report_political_signals(political_signals, now=now)

        if not candidates:
            # Nothing proposable this tick. Distinguish the cases so the
            # Controller can tell a pipeline failure from a quiet day.
            if not seen_syms:
                reason = (
                    "no universe snapshot for today -- the D-0026 "
                    "selection run produced no symbols (or has not run "
                    "yet). Per D-0026 no-universe = no-trade."
                )
            else:
                reason = (
                    f"all {len(seen_syms)} universe symbol(s) already "
                    "have an open trade, so there is nothing new to "
                    "propose."
                )
            self._notify_nothing_to_trade(reason, now=now)
            return

        if self._evaluator is None:
            # Pre-D-0050 flow: every candidate becomes a proposal.
            for symbol in candidates:
                self._start_new_trade(symbol, now=now)
            return

        # Rank, keep passing + score >= MIN_SCORE, cap at TOP_N.
        # D-0050 Phase B.28: enrich each candidate's SymbolResearch
        # with the political signal map so the evaluator sees it.
        try:
            ranked = self._evaluator.rank(candidates)
            if political_signals:
                for r in ranked:
                    sig = political_signals.get(r.symbol)
                    if sig is None or r.research is None:
                        continue
                    r.research.political_buys_30d = sig.politician_buys_30d
                    r.research.political_sells_30d = sig.politician_sells_30d
                    r.research.political_recent_names = list(sig.recent_names)
                    r.research.political_cluster_score = sig.cluster_score
                    r.research.political_committee_match = sig.committee_match
                    r.research.political_weighted_signal = sig.weighted_signal
                    r.research.political_sell_wave = sig.sell_wave
                    # Re-evaluate: the initial rank() scored WITHOUT the
                    # political signal because the research was fresh
                    # from the hub. Now that we've injected the signal,
                    # we must re-score so hard-filter (sell_wave) and
                    # soft-score (_score_political) actually see it.
                    new_result = self._evaluator.evaluate_research(r.research)
                    r.soft_score = new_result.soft_score
                    r.score_breakdown = new_result.score_breakdown
                    r.passes_hard_filter = new_result.passes_hard_filter
                    r.hard_filter_reasons = new_result.hard_filter_reasons
                # Re-sort: a political boost or sell-wave rejection may
                # have changed the order.
                passed = sorted([r for r in ranked if r.passes_hard_filter],
                                key=lambda r: -r.soft_score)
                failed = [r for r in ranked if not r.passes_hard_filter]
                ranked = passed + failed
        except Exception as exc:  # noqa: BLE001
            # Evaluator must never block the trigger loop; fall through
            # to the un-ranked flow so no opportunity is missed.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="evaluator_failed",
                message=f"TradeEvaluator failed; falling back to un-ranked candidates: {exc}",
                symbol=None,
            )
            for symbol in candidates:
                self._start_new_trade(symbol, now=now)
            return

        # D-0050 Phase 16: macro-event blackout window.
        if self._macro_calendar is not None:
            try:
                block, reason = self._macro_calendar.should_block_new_proposals(now)
            except Exception:  # noqa: BLE001
                block, reason = False, ""
            if block:
                self._notify(
                    level=NotificationLevel.IMPORTANT,
                    event="macro_event_blackout",
                    message=f"Skipping new proposals this cycle: {reason}",
                    symbol=None,
                )
                return

        accepted_results = [r for r in ranked
                            if r.passes_hard_filter and r.soft_score >= self._MIN_SCORE]

        # D-0050 Phase 14: portfolio-level filter (sector + correlation).
        if self._portfolio_filter is not None and accepted_results:
            try:
                triplets = [(r.symbol, r.soft_score, r.research)
                            for r in accepted_results]
                filtered = self._portfolio_filter.apply(triplets)
                accepted_symbols = {fp.symbol for fp in filtered if fp.accepted}
                accepted_results = [r for r in accepted_results
                                    if r.symbol in accepted_symbols]
            except Exception as exc:  # noqa: BLE001
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="portfolio_filter_failed",
                    message=f"Portfolio filter failed; proceeding without it: {exc}",
                    symbol=None,
                )

        accepted = self._apply_political_reserved_slot(
            accepted_results, political_signals=political_signals,
        )

        if not accepted:
            # Candidates existed but none survived research. Report it
            # once per trading day, with the counts, so the Controller
            # sees that the pipeline ran and simply found nothing good
            # enough -- not that the engine is stuck.
            n_cand = len(candidates)
            n_hard = sum(1 for r in ranked if not r.passes_hard_filter)
            best = max((r.soft_score for r in ranked
                        if r.passes_hard_filter), default=None)
            best_txt = ("none passed the hard filter" if best is None
                        else f"best score {best:.1f} < required "
                             f"{self._MIN_SCORE:.0f}")
            self._notify_nothing_to_trade(
                f"{n_cand} candidate(s) evaluated, {n_hard} rejected by "
                f"the hard filter, and {best_txt}.",
                now=now,
            )
            return

        for r in accepted:
            self._start_new_trade(
                r.symbol, now=now,
                political_signal=political_signals.get(r.symbol),
            )

    def _report_political_signals(self, signals: dict, *, now: datetime) -> None:
        """D-0058: one report per ET trading day listing what the
        tracked politicians bought.

        Deliberately includes symbols that did NOT become proposals.
        Without that, the Controller only ever sees the political picks
        that happened to survive every filter, which systematically
        hides the signal's real breadth and makes it impossible to judge
        whether the filters are discarding good political ideas.

        Information only. It creates no proposal, changes no score, and
        carries no buttons -- so it cannot affect trading in any way.
        Sent at OPTIONAL level because it is a daily digest, not an
        event that needs action.
        """

        if not signals:
            return
        trading_date = now.astimezone(D0021_TIMEZONE_ET).date().isoformat()

        rows = []
        for symbol, signal in signals.items():
            try:
                buys = int(getattr(signal, "politician_buys_30d", 0) or 0)
                sells = int(getattr(signal, "politician_sells_30d", 0) or 0)
                weighted = float(getattr(signal, "weighted_signal", 0.0) or 0.0)
                names = list(getattr(signal, "recent_names", None) or [])
                committee = bool(getattr(signal, "committee_match", False))
            except Exception:  # noqa: BLE001 - external scraper data
                continue
            rows.append((weighted, symbol, buys, sells, names, committee))
        if not rows:
            return
        rows.sort(key=lambda r: -r[0])

        lines = [f"[POLITICAL REPORT] {trading_date} (ET)",
                 f"{len(rows)} symbol(s) with congressional activity:"]
        for weighted, symbol, buys, sells, names, committee in rows:
            bits = [f"{symbol}: signal {weighted:.1f}"]
            if buys:
                bits.append(f"{buys} buy(s)")
            if sells:
                bits.append(f"{sells} sell(s)")
            if committee:
                bits.append("committee match")
            if names:
                bits.append(", ".join(str(n) for n in names[:3]))
            lines.append("  " + " | ".join(bits))
        lines.append("")
        lines.append("Includes symbols that did NOT become proposals. "
                     "Information only -- nothing here was traded.")

        self._notify_once(
            kind="political_report",
            key=trading_date,
            level=NotificationLevel.OPTIONAL,
            event="political_daily_report",
            message="\n".join(lines),
            symbol=None,
        )

    def _apply_political_reserved_slot(
        self, accepted_results: list, *, political_signals: dict,
    ) -> list:
        """D-0058 (Controller-approved 2026-10-05): reserve ONE of the
        `_TOP_N_PER_CYCLE` proposals for the best politically-backed
        candidate.

        Why a reserved slot instead of doubling `weight_political`
        (which was the Controller's first instinct, and was rejected
        with reasons recorded in D-0058):

        - It GUARANTEES the political idea reaches the Controller
          whenever a valid one exists. Raising a weight only makes that
          more likely.
        - It leaves every other candidate's score untouched, so a score
          keeps meaning exactly what it meant before.
        - It stays measurable. The political component is computed for
          EVERY candidate, so doubling its weight would blend the signal
          into one number and no proposal could ever be attributed to
          it -- whether politicians actually help could never be
          established. With a labelled slot, one proposal per cycle is
          political and two are not, and after a month the two groups
          are directly comparable.

        The candidate must already have passed EVERYTHING: the D-0026
        pipeline, the hard filter, the `_MIN_SCORE` threshold and the
        portfolio filter. This method only reorders what already
        qualified -- it never admits a candidate that failed a check,
        and it never grants a political pick an exemption.

        If no political candidate qualifies, the slot reverts to normal
        ranking and nothing is wasted.
        """

        top_n = self._TOP_N_PER_CYCLE
        if not political_signals or len(accepted_results) <= top_n:
            # Nothing to arbitrate: every qualifier already fits.
            return accepted_results[:top_n]

        natural = accepted_results[:top_n]
        if any(r.symbol in political_signals for r in natural):
            # A political pick already made it on merit. Reserving a
            # slot now would only displace another qualifier for no
            # gain.
            return natural

        promoted = next(
            (r for r in accepted_results[top_n:]
             if r.symbol in political_signals),
            None,
        )
        if promoted is None:
            return natural

        # Drop the LOWEST-scoring natural qualifier, never the best.
        kept = natural[: top_n - 1]
        displaced = natural[top_n - 1]
        self._notify(
            level=NotificationLevel.OPTIONAL,
            event="political_slot_reserved",
            message=(
                f"Reserved the political slot for {promoted.symbol} "
                f"(score {promoted.soft_score:.1f}); {displaced.symbol} "
                f"(score {displaced.soft_score:.1f}) was not proposed "
                f"this cycle. Per D-0058 one of the {top_n} proposals "
                f"per cycle is reserved for the best politically-backed "
                f"candidate that passed every check."
            ),
            symbol=promoted.symbol,
        )
        return kept + [promoted]

    def _start_new_trade(
        self, symbol: str, *, now: datetime, political_signal=None,
    ) -> None:
        try:
            price = self._market_data.get_last_trade(symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=f"Could not get current price for {symbol} to start a new watchlist-driven trade: {exc}",
                symbol=symbol,
            )
            return

        strategy = self._sized_strategy(price=price)
        if strategy is None:
            # Fail-closed: the sizing policy declined this symbol/price
            # or the broker was unreachable. _sized_strategy already
            # emitted the right-level notification. Do not fall back to
            # the pre-D-0051 fixed 10/10/20 -- that would silently size
            # a position the Controller never approved.
            return
        trade_id = f"{symbol}-{uuid.uuid4().hex[:8]}"
        proposal_id = f"{trade_id}-initial_entry-{uuid.uuid4().hex[:8]}"
        _trade_record, proposal = self._trade_proposal_service.start_trade(
            trade_id=trade_id,
            symbol=symbol,
            proposal_id=proposal_id,
            current_price=price,
            strategy=strategy,
            floor_context=FloorContext.no_existing_position(),
            now=now,
        )
        message = self._enrich(symbol, _format_proposal_message(
            proposal,
            price_context=self._safe_price_context(symbol),
        ))
        message += _format_political_tag(political_signal)
        self._notify(
            level=NotificationLevel.IMPORTANT,
            event=("political_proposal_awaiting_approval"
                   if political_signal is not None
                   else "proposal_awaiting_approval"),
            message=message,
            symbol=symbol,
            interactive_actions=(
                ("✅ Approve", f"approve:{proposal.proposal_id}"),
                ("❌ Reject", f"reject:{proposal.proposal_id}"),
            ),
        )
        self._notified.add(("pending_approval", proposal.proposal_id))

    def _safe_price_context(self, symbol: str) -> Optional[dict]:
        """Best-effort wrapper around MarketDataSource.get_price_context.
        Never raises: a failure just means the price-context block is
        omitted from the notification (per contract)."""
        try:
            return self._market_data.get_price_context(symbol)
        except Exception:  # noqa: BLE001 - never block a proposal on this
            return None

    def _process_trade(self, trade_id: str, *, now: datetime) -> None:
        record = self._trade_repo.get(trade_id)
        if record is None:
            return
        trade = record.trade
        if describe_status(trade) != "ACTIVE":
            return  # AWAITING_INITIAL_FILL: the Initial Entry proposal/approval/
            # submission/reconciliation flow is handled by _check_watchlist()/
            # start_trade() and the normal Proposal/Execution machinery, not
            # by this per-cycle Ladder/Floor loop.

        try:
            price = self._market_data.get_last_trade(trade.symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=f"Could not get current price for {trade.symbol} (trade {trade_id}): {exc}",
                symbol=trade.symbol,
            )
            return

        active_floor = trade.active_floor_price

        for action, filled, trigger_price in (
            (TradeAction.LADDER_1, trade.ladder1_filled, trade.ladder1_price),
            (TradeAction.LADDER_2, trade.ladder2_filled, trade.ladder2_price),
        ):
            if filled or trigger_price is None:
                continue

            # D-0011's proposal-creation gate / strategy.md §4: never
            # propose a Ladder at or below the active floor. D-0007's
            # own floor-priority check (validate_for_submission(),
            # unchanged) remains the authoritative enforcement at
            # submission time regardless of this pre-check.
            if active_floor is not None and trigger_price <= active_floor:
                continue

            latest = self._latest_proposal_for(trade_id, action)
            has_live_attempt = latest is not None and latest.approval_state in (
                ApprovalState.PENDING,
                ApprovalState.APPROVED,
            )

            if price <= trigger_price and not has_live_attempt:
                self._create_ladder_proposal(
                    trade_id,
                    action,
                    original_entry_price=trade.original_initial_entry_fill_price,
                    active_floor=active_floor,
                    now=now,
                )
            elif latest is not None and latest.approval_state is ApprovalState.APPROVED:
                self._submit_approved(latest.proposal_id, current_price=price, active_floor_price=active_floor, now=now)

    def _latest_proposal_for(self, trade_id: str, action: TradeAction):
        relevant = [p for p in self._proposal_repo.list_for_trade(trade_id) if p.proposed_action is action]
        return relevant[-1] if relevant else None

    def _create_ladder_proposal(
        self,
        trade_id: str,
        action: TradeAction,
        *,
        original_entry_price: Optional[float],
        active_floor: Optional[float],
        now: datetime,
    ) -> None:
        """`build_trade_proposal()` (unchanged, called unmodified via
        TradeProposalService.propose_next_action()) always recomputes
        `ladder_1_trigger`/`ladder_2_trigger` from its `current_price`
        argument, for BOTH Initial Entry and Ladder proposals -- its
        own docstring only documents the Initial Entry meaning of that
        parameter. For a Ladder proposal this argument must be the
        Trade's frozen ORIGINAL initial-entry fill price (D-0001: every
        percentage is relative to the original fill, never a floating
        reference) so that the resulting ladder_1_trigger/ladder_2_trigger
        exactly reproduce Trade.ladder1_price/ladder2_price (already
        frozen at freeze_initial_reference() time) -- NEVER the live
        market price at proposal-creation time, which would silently
        recompute a drifting, strategy-incorrect trigger."""

        proposal_id = f"{trade_id}-{action.value}-{uuid.uuid4().hex[:8]}"
        floor_context = FloorContext.known(active_floor) if active_floor is not None else FloorContext.no_existing_position()
        # D-0051: Ladder-1 and Ladder-2 proposals reuse the SAME per-layer
        # sizing policy, but their effective share counts come from the
        # TradeProposal fields on the ORIGINAL Initial Entry proposal
        # (ladder_1_quantity / ladder_2_quantity, already stored in SQLite).
        # The strategy we hand to propose_next_action here still needs
        # positive integers for post-validation, so we re-run the policy
        # with the current equity + the FROZEN original entry price. This
        # keeps the StrategyRuleSet internally consistent without
        # affecting the broker-facing quantity (that path reads
        # proposal.ladder_1_quantity / ladder_2_quantity directly).
        strategy = self._sized_strategy(price=original_entry_price)
        if strategy is None:
            return
        try:
            proposal = self._trade_proposal_service.propose_next_action(
                trade_id=trade_id,
                action=action,
                proposal_id=proposal_id,
                current_price=original_entry_price,
                strategy=strategy,
                floor_context=floor_context,
                now=now,
            )
        except LadderAlreadyFilledError:
            return

        self._notify(
            level=NotificationLevel.IMPORTANT,
            event="proposal_awaiting_approval",
            message=self._enrich(proposal.symbol, _format_proposal_message(proposal)),
            symbol=proposal.symbol,
            interactive_actions=(
                ("✅ Approve", f"approve:{proposal.proposal_id}"),
                ("❌ Reject", f"reject:{proposal.proposal_id}"),
            ),
        )
        self._notified.add(("pending_approval", proposal.proposal_id))

    def _abandon_initial_entry_after_refusal(
        self, trade_id: str, proposal_id: str, *, reason: str, now: datetime
    ) -> None:
        """Mark an INITIAL_ENTRY trade ABANDONED when its approved
        submission is refused BEFORE any fill (D-0007 revalidation or
        D-0047 portfolio risk). Mirrors the REJECT-branch abandonment
        in _apply_decision so the symbol is freed on the next
        watchlist tick instead of staying permanently locked out in
        AWAITING_INITIAL_FILL."""

        trade_record = self._trade_repo.get(trade_id)
        if trade_record is None:
            return
        trade = trade_record.trade
        if trade.initial_order_reconciled:
            # Any fill already happened -- never abandon a trade that
            # has reached a reconciled state.
            return
        try:
            self._trade_repo.update(
                trade.abandon_before_fill(now=now),
                expected_revision=trade_record.revision,
                transition="abandoned_after_refusal",
                now=now,
            )
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="initial_entry_abandoned",
                message=(
                    f"Initial Entry {proposal_id} ({trade.symbol}, trade "
                    f"{trade_id}) marked ABANDONED after submission refusal "
                    f"({reason}). Symbol released for the next watchlist tick."
                ),
                symbol=trade.symbol,
            )
        except Exception as exc:  # noqa: BLE001
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="abandon_failed",
                message=(
                    f"Could not mark {trade_id} as ABANDONED after submission "
                    f"refusal for {proposal_id}: {exc}. Symbol may remain blocked "
                    f"until the DB is cleaned."
                ),
                symbol=trade.symbol,
            )

    def _submit_approved(
        self,
        proposal_id: str,
        *,
        current_price: float,
        active_floor_price: Optional[float],
        now: datetime,
        initial_entry_trade_id: Optional[str] = None,
    ) -> None:
        """Submission dispatch wrapper.

        `initial_entry_trade_id` -- when set, this is an INITIAL_ENTRY
        submission attempt (caller holds the trade_id) and a terminal
        pre-fill refusal (D-0007 revalidation EXPIRED/PRICE_DRIFT, or
        D-0047 portfolio risk) must abandon the Trade so the symbol is
        released on the next watchlist tick. Without this, the Trade
        stays in AWAITING_INITIAL_FILL forever and _check_watchlist's
        has_open_trade guard permanently locks the symbol out (same
        shape as the 2026-09-30 Bug #3 fix for the REJECT branch, now
        extended to the approve-then-refused branch -- 2026-10-01
        Finding #2)."""

        try:
            self._execution_service.submit_approved_proposal(
                proposal_id, current_price=current_price, active_floor_price=active_floor_price, now=now
            )
        except (ExecutionAlreadyResolvedError, ExecutionAlreadySubmittedError):
            return  # benign: already handled by a prior cycle
        except SubmissionNotAllowedError as exc:
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="submission_not_allowed",
                message=f"Submission for {proposal_id} refused by D-0007 revalidation: {exc}",
            )
            if initial_entry_trade_id is not None:
                self._abandon_initial_entry_after_refusal(
                    initial_entry_trade_id, proposal_id,
                    reason=f"D-0007 revalidation refused: {exc}", now=now,
                )
        except PortfolioRiskViolatedError as exc:
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="submission_risk_violated",
                message=f"Submission for {proposal_id} refused by D-0047 portfolio risk enforcer: {exc}",
            )
            if initial_entry_trade_id is not None:
                self._abandon_initial_entry_after_refusal(
                    initial_entry_trade_id, proposal_id,
                    reason=f"D-0047 portfolio risk refused: {exc}", now=now,
                )
        except BrokerSubmissionAmbiguousError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="submission_ambiguous",
                message=f"Submission for {proposal_id} had an ambiguous outcome: {exc}. Will resolve via reconciliation.",
            )
        except BrokerCommunicationError as exc:
            # Network / transport failure talking to the broker: surface
            # the failure, keep the execution row in SUBMITTED_UNKNOWN so
            # reconciliation can resolve it, and never crash the main
            # loop.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="broker_communication_error",
                message=f"Broker communication failure for {proposal_id}: {exc}. Reconciliation will resolve.",
            )
        except BrokerClientError as exc:
            # Definite broker-side rejection (HTTP 4xx/5xx that the
            # broker processed enough to reject) -- e.g. an invalid
            # limit price, an unknown symbol, or an account block.
            # Surface it, do NOT retry automatically, and keep the main
            # loop alive so other proposals can still be handled.
            # A real live example (2026-09-30): Alpaca rejected an
            # Initial Entry limit price with sub-penny precision --
            # AlpacaBrokerClient now formats the price to Alpaca's
            # SEC-compliant precision so this specific case no longer
            # happens, but other 4xx rejections remain possible and
            # must never crash the engine.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="broker_rejected",
                message=f"Broker rejected submission for {proposal_id}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - engine main-loop shield
            # Defensive final catch: any other error from the execution
            # path (e.g. an ExecutionServiceError subclass we did not
            # anticipate, or a bug during a repo write) must surface as
            # a CRITICAL notification, never as a main-loop crash.
            # Without this, a single misbehaving proposal takes down
            # the whole session and every other symbol along with it
            # -- a real live 2026-09-30 failure mode.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="submission_unexpected_error",
                message=(
                    f"Unexpected error submitting {proposal_id} "
                    f"({type(exc).__name__}): {exc}. Engine keeps running; "
                    f"reconciliation will retry as appropriate."
                ),
            )

    # ------------------------------------------------------------------
    # Ladder 2 partial-fill confirmation notification (shared by
    # recovery and the reconciliation tick -- same derivable condition)
    # ------------------------------------------------------------------

    def _maybe_notify_ladder2_pending_confirmation(self, proposal_id: str, *, now: datetime) -> None:
        proposal = self._proposal_repo.get(proposal_id)
        if proposal is None or proposal.proposed_action is not TradeAction.LADDER_2:
            return
        execution_record = self._execution_repo.get_by_proposal_id(proposal_id)
        if execution_record is None:
            return
        execution = execution_record.execution
        if not execution.is_broker_terminal:
            return
        if execution.filled_qty == 0 or execution.filled_qty == execution.requested_qty:
            return

        trade_record = self._trade_repo.get(proposal.trade_id)
        if trade_record is None or trade_record.trade.ladder2_filled:
            return

        self._notify_once(
            kind="ladder2_partial_fill",
            key=proposal_id,
            level=NotificationLevel.IMPORTANT,
            event="ladder2_partial_fill_pending_confirmation",
            message=(
                f"Ladder 2 for proposal {proposal_id} intended {execution.requested_qty} shares; "
                f"broker's final terminal fill was {execution.filled_qty}. Awaiting explicit "
                "Controller approval before recording this as Ladder 2 completion. No automatic "
                "top-up will be submitted for the remainder."
            ),
            symbol=proposal.symbol,
        )

    # ------------------------------------------------------------------
    # Notification dedup (in-memory, Controller-approved)
    # ------------------------------------------------------------------

    def _notify_once(
        self,
        *,
        kind: str,
        key: str,
        level: NotificationLevel,
        event: str,
        message: str,
        symbol: Optional[str] = None,
        interactive_actions: tuple = (),
    ) -> None:
        dedup_key = (kind, key)
        if dedup_key in self._notified:
            return
        self._notified.add(dedup_key)
        self._notify(
            level=level, event=event, message=message, symbol=symbol,
            interactive_actions=interactive_actions,
        )

    def _notify(
        self, *, level: NotificationLevel, event: str, message: str,
        symbol: Optional[str] = None, interactive_actions: tuple = (),
    ) -> None:
        self._notifier.send(NotificationEvent(
            level=level, event=event, message=message, symbol=symbol,
            interactive_actions=interactive_actions,
        ))

    # ------------------------------------------------------------------
    # Real-clock composition. Every method above is a plain function of
    # `now` and is exercised directly by tests; this is the only method
    # that actually sleeps, and is intentionally thin (composition only,
    # no new logic) so nothing load-bearing lives here untested.
    # ------------------------------------------------------------------

    def run_forever(
        self,
        *,
        reconciliation_interval_seconds: float,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep_fn: Callable[[float], None] = _time_module.sleep,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Composes run_reconciliation_tick() (every
        reconciliation_interval_seconds) with run_trigger_check() (only
        when is_d0021_check_time(now) is True) against a real clock.
        `max_iterations` is test-only -- bounds the loop instead of
        running forever, so this method itself is exercisable without
        mocking an infinite loop. Does NOT call start()/shutdown() --
        the caller acquires the lock and runs recovery before this, and
        releases the lock after, exactly like any other Engine method."""

        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            now = now_fn()
            self.run_reconciliation_tick(now=now)
            if is_d0021_check_time(now):
                self.run_trigger_check(now=now)
            self._heartbeat(now=now_fn())
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                sleep_fn(reconciliation_interval_seconds)
