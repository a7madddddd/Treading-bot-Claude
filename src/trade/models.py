"""Trade state entity -- the generic, symbol-agnostic per-trade record
specified by `docs/architecture/state-management.md` Sec.1-2, refined by
the Controller-approved design review (this session): no stored
lifecycle `status` field -- every status distinction is fully derivable
from the fields below via `describe_status()`, never cached as an
independent, driftable source of truth.

Pure data + pure transition methods only. No persistence, no Alpaca, no
execution, no scheduler, no D-0011, no Telegram -- all explicitly
deferred. Mirrors the immutable-dataclass-plus-named-transition-methods
pattern already established by `src/proposals/models.py` (TradeProposal,
StrategyRuleSet): every method returns a NEW Trade via
`dataclasses.replace`, never mutates `self`.

Governance boundary (unchanged from src/proposals/): this module has no
knowledge of real fills/executions beyond what a caller (the future
Engine, reconciling against the broker) explicitly supplies. It never
computes a weighted-average or fill quantity itself -- those numbers are
supplied by the caller as already-reconciled broker truth, exactly as
`state-management.md` Sec.3 requires ("prefer broker as source of
truth").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from proposals.models import StrategyRuleSet, TradeAction


class TradeStateError(RuntimeError):
    """Raised when a Trade transition would violate one of the
    approved invariants (docs/trading/strategy.md,
    docs/architecture/state-management.md, D-0009, D-0010). Per
    Controller governance: an invalid transition must BLOCK, never
    silently no-op or invent a value."""


class InitialOrderStatus(Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_INITIAL_ORDER_STATUSES = frozenset(
    {InitialOrderStatus.FILLED, InitialOrderStatus.CANCELLED, InitialOrderStatus.EXPIRED}
)
"""D-0009: the initial entry reference may be frozen only once the
initial order is 'fully filled or fully reconciled (all of: filled,
cancelled, or expired)'. PENDING and PARTIALLY_FILLED (while still
open) are never terminal -- freezing during either is refused."""


@dataclass(frozen=True)
class Trade:
    """Immutable per-trade record, keyed by (symbol, trade_id). Every
    field's writer, mutability, and source of truth matches the
    Controller-approved design review; see that review for the full
    per-field rationale. No field here is TSLA-specific or hardcodes
    any strategy value -- strategy numbers are always supplied by the
    caller as a StrategyRuleSet, never re-typed here."""

    trade_id: str
    symbol: str
    created_at: datetime

    # -- Frozen original reference (write-once, set only via freeze_initial_reference) --
    initial_order_id: Optional[str] = None
    initial_order_status: InitialOrderStatus = InitialOrderStatus.PENDING
    initial_order_reconciled: bool = False
    original_initial_entry_fill_price: Optional[float] = None
    initial_filled_shares: Optional[int] = None
    freeze_timestamp: Optional[datetime] = None

    # -- Derived-then-frozen levels (write-once, set only via freeze_initial_reference) --
    ladder1_price: Optional[float] = None
    ladder2_price: Optional[float] = None
    original_floor_price: Optional[float] = None

    # -- Ladder fill state (independent per ladder) --
    ladder1_proposal_id: Optional[str] = None
    ladder1_filled: bool = False
    ladder1_fill_order_id: Optional[str] = None
    ladder1_fill_price: Optional[float] = None
    ladder1_fill_qty: Optional[int] = None

    ladder2_proposal_id: Optional[str] = None
    ladder2_filled: bool = False
    ladder2_fill_order_id: Optional[str] = None
    ladder2_fill_price: Optional[float] = None
    ladder2_fill_qty: Optional[int] = None

    # -- Live risk metrics (broker-reconciled cache, never independently authoritative) --
    total_shares: int = 0
    weighted_avg_entry_price: Optional[float] = None

    # -- Trailing floor state --
    trailing_activated: bool = False
    trailing_current_threshold: Optional[float] = None
    trailing_floor_price: Optional[float] = None
    trailing_last_updated_at: Optional[datetime] = None

    # -- Protective order state --
    protective_order_id: Optional[str] = None
    protective_order_stop_price: Optional[float] = None
    protective_order_status: Optional[str] = None
    protective_order_lineage: Tuple[str, ...] = ()

    # -- Reconciliation state --
    last_broker_poll_at: Optional[datetime] = None
    last_reconciled_at: Optional[datetime] = None
    last_error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise TradeStateError("trade_id must be non-empty")
        if not self.symbol:
            raise TradeStateError("symbol must be non-empty")

        if not self.initial_order_reconciled:
            if self.freeze_timestamp is not None:
                raise TradeStateError("freeze_timestamp must be None before reconciliation")
            if self.original_initial_entry_fill_price is not None:
                raise TradeStateError("original_initial_entry_fill_price must be None before reconciliation")
            if self.initial_filled_shares is not None:
                raise TradeStateError("initial_filled_shares must be None before reconciliation")
            if self.ladder1_price is not None or self.ladder2_price is not None or self.original_floor_price is not None:
                raise TradeStateError("derived levels must be None before reconciliation")
        else:
            if self.freeze_timestamp is None:
                raise TradeStateError("a reconciled trade must carry freeze_timestamp")
            if self.initial_filled_shares is None:
                raise TradeStateError("a reconciled trade must carry initial_filled_shares")
            if self.initial_filled_shares < 0:
                raise TradeStateError("initial_filled_shares must not be negative")
            if self.initial_filled_shares > 0:
                # Real position -- reference and derived levels must exist.
                if self.original_initial_entry_fill_price is None or not math.isfinite(
                    self.original_initial_entry_fill_price
                ) or self.original_initial_entry_fill_price <= 0:
                    raise TradeStateError(
                        "original_initial_entry_fill_price must be a valid positive finite price "
                        "when initial_filled_shares > 0"
                    )
                if self.ladder1_price is None or self.ladder2_price is None or self.original_floor_price is None:
                    raise TradeStateError("derived levels must be set when initial_filled_shares > 0")
            else:
                # D-0009/D-0010 zero-fill case ("abandoned") -- no reference, no levels.
                if self.original_initial_entry_fill_price is not None:
                    raise TradeStateError("no reference may exist when initial_filled_shares == 0")
                if self.ladder1_price is not None or self.ladder2_price is not None or self.original_floor_price is not None:
                    raise TradeStateError("no derived levels may exist when initial_filled_shares == 0")

        for label, filled, order_id, price, qty in (
            ("ladder1", self.ladder1_filled, self.ladder1_fill_order_id, self.ladder1_fill_price, self.ladder1_fill_qty),
            ("ladder2", self.ladder2_filled, self.ladder2_fill_order_id, self.ladder2_fill_price, self.ladder2_fill_qty),
        ):
            if filled:
                if order_id is None or price is None or qty is None:
                    raise TradeStateError(f"{label}_filled=True requires order_id/price/qty all set")
            else:
                if order_id is not None or price is not None or qty is not None:
                    raise TradeStateError(f"{label} fill fields must be None while {label}_filled=False")

        if (self.ladder1_filled or self.ladder2_filled) and not (
            self.initial_order_reconciled and (self.initial_filled_shares or 0) > 0
        ):
            raise TradeStateError("a ladder cannot be filled on a trade with no reconciled initial position")

        if self.total_shares < 0:
            raise TradeStateError("total_shares must not be negative")

        if self.trailing_activated:
            if self.trailing_current_threshold is None or self.trailing_floor_price is None or self.trailing_last_updated_at is None:
                raise TradeStateError("trailing_activated=True requires threshold/floor/timestamp all set")
        else:
            if self.trailing_current_threshold is not None or self.trailing_floor_price is not None or self.trailing_last_updated_at is not None:
                raise TradeStateError("trailing fields must be None while trailing_activated=False")

    # ------------------------------------------------------------------
    # Derived (never independently writable)
    # ------------------------------------------------------------------

    @property
    def active_floor_price(self) -> Optional[float]:
        """max(original_floor_price, trailing_floor_price if active) --
        strategy.md Sec.4/Sec.5. None until the initial reference is
        frozen with a real position (no floor exists before then)."""
        if self.original_floor_price is None:
            return None
        if self.trailing_activated and self.trailing_floor_price is not None:
            return max(self.original_floor_price, self.trailing_floor_price)
        return self.original_floor_price

    @property
    def active_floor_source(self) -> Optional[str]:
        if self.original_floor_price is None:
            return None
        if self.trailing_activated and self.trailing_floor_price is not None and self.trailing_floor_price > self.original_floor_price:
            return "trailing"
        return "original"

    # ------------------------------------------------------------------
    # Transitions -- each returns a NEW Trade, never mutates self
    # ------------------------------------------------------------------

    def freeze_initial_reference(
        self,
        *,
        order_status: InitialOrderStatus,
        filled_shares: int,
        fill_price: Optional[float],
        strategy: StrategyRuleSet,
        now: datetime,
    ) -> "Trade":
        """D-0009/D-0010: freezes the original reference exactly once,
        the first time the initial order reaches a TERMINAL status
        (filled/cancelled/expired). Refuses while pending or
        partially_filled-and-open. `fill_price`/`filled_shares` must
        already be the broker-reconciled weighted-average/quantity of
        the initial order's actual fills -- this method performs no
        fill-aggregation math of its own.

        If `filled_shares == 0` (D-0010 zero-fill terminal case, the
        prior "abandoned" concept), no reference or derived levels are
        computed -- there is nothing to protect."""

        if self.initial_order_reconciled:
            raise TradeStateError(f"trade {self.trade_id!r} is already reconciled -- freeze may only happen once")
        if order_status not in TERMINAL_INITIAL_ORDER_STATUSES:
            raise TradeStateError(
                f"cannot freeze: initial_order_status {order_status.value!r} is not terminal "
                "(must be filled, cancelled, or expired)"
            )
        if filled_shares < 0:
            raise TradeStateError("filled_shares must not be negative")
        if not isinstance(strategy, StrategyRuleSet):
            raise TypeError("strategy must be a validated StrategyRuleSet")

        if filled_shares == 0:
            return replace(
                self,
                initial_order_status=order_status,
                initial_order_reconciled=True,
                initial_filled_shares=0,
                freeze_timestamp=now,
            )

        if fill_price is None or not math.isfinite(fill_price) or fill_price <= 0:
            raise TradeStateError(f"fill_price {fill_price!r} is not a valid positive finite price")

        ladder1_price = round(fill_price * (1 + strategy.ladder_1_pct), 4)
        ladder2_price = round(fill_price * (1 + strategy.ladder_2_pct), 4)
        original_floor_price = round(fill_price * (1 + strategy.floor_pct), 4)

        return replace(
            self,
            initial_order_status=order_status,
            initial_order_reconciled=True,
            original_initial_entry_fill_price=fill_price,
            initial_filled_shares=filled_shares,
            freeze_timestamp=now,
            ladder1_price=ladder1_price,
            ladder2_price=ladder2_price,
            original_floor_price=original_floor_price,
            total_shares=filled_shares,
            weighted_avg_entry_price=fill_price,
        )

    def record_ladder_fill(
        self,
        action: TradeAction,
        *,
        order_id: str,
        fill_price: float,
        fill_qty: int,
        new_total_shares: int,
        new_weighted_avg_entry_price: float,
        strategy: StrategyRuleSet,
    ) -> "Trade":
        """Records a Ladder 1 or Ladder 2 fill. `new_total_shares` and
        `new_weighted_avg_entry_price` must already be the
        broker-reconciled post-fill values -- this method performs no
        averaging math itself. Never touches original_floor_price,
        ladder1_price, or ladder2_price -- the frozen reference is
        write-once and this method has no path to it."""

        if action not in (TradeAction.LADDER_1, TradeAction.LADDER_2):
            raise TradeStateError(f"record_ladder_fill only accepts LADDER_1/LADDER_2, got {action!r}")
        if not (self.initial_order_reconciled and (self.initial_filled_shares or 0) > 0):
            raise TradeStateError("cannot record a ladder fill on a trade with no reconciled initial position")
        if not isinstance(strategy, StrategyRuleSet):
            raise TypeError("strategy must be a validated StrategyRuleSet")
        if new_total_shares > strategy.maximum_position:
            raise TradeStateError(
                f"new_total_shares {new_total_shares} exceeds maximum_position {strategy.maximum_position}"
            )
        if fill_price <= 0 or not math.isfinite(fill_price):
            raise TradeStateError(f"fill_price {fill_price!r} is not a valid positive finite price")
        if fill_qty <= 0:
            raise TradeStateError("fill_qty must be positive")

        if action is TradeAction.LADDER_1:
            if self.ladder1_filled:
                raise TradeStateError(f"trade {self.trade_id!r} Ladder 1 is already filled -- cannot fill twice")
            return replace(
                self,
                ladder1_filled=True,
                ladder1_fill_order_id=order_id,
                ladder1_fill_price=fill_price,
                ladder1_fill_qty=fill_qty,
                total_shares=new_total_shares,
                weighted_avg_entry_price=new_weighted_avg_entry_price,
            )

        if self.ladder2_filled:
            raise TradeStateError(f"trade {self.trade_id!r} Ladder 2 is already filled -- cannot fill twice")
        return replace(
            self,
            ladder2_filled=True,
            ladder2_fill_order_id=order_id,
            ladder2_fill_price=fill_price,
            ladder2_fill_qty=fill_qty,
            total_shares=new_total_shares,
            weighted_avg_entry_price=new_weighted_avg_entry_price,
        )

    def activate_trailing(self, *, current_price: float, now: datetime) -> "Trade":
        """strategy.md Sec.5: activates the first time price reaches
        weighted_avg_entry_price * 1.10. Threshold-based
        (D-0008) -- the floor is activation_threshold * 0.95, NOT the
        current tick price."""

        if self.trailing_activated:
            raise TradeStateError(f"trade {self.trade_id!r} trailing floor is already activated")
        if self.weighted_avg_entry_price is None:
            raise TradeStateError("cannot activate trailing floor -- no weighted_avg_entry_price set")
        activation_threshold = round(self.weighted_avg_entry_price * 1.10, 4)
        if current_price < activation_threshold:
            raise TradeStateError(
                f"current_price {current_price!r} has not reached the activation threshold {activation_threshold!r}"
            )
        trailing_floor_price = round(activation_threshold * 0.95, 4)
        return replace(
            self,
            trailing_activated=True,
            trailing_current_threshold=activation_threshold,
            trailing_floor_price=trailing_floor_price,
            trailing_last_updated_at=now,
        )

    def ratchet_trailing(self, *, current_price: float, now: datetime) -> "Trade":
        """strategy.md Sec.5: each next threshold = previous * 1.05,
        floor = new threshold * 0.95 (D-0008, threshold-based). The
        trailing floor only ever moves up -- enforced defensively even
        though the compounding formula should never produce a lower
        value."""

        if not self.trailing_activated:
            raise TradeStateError("cannot ratchet -- trailing floor is not yet activated")
        assert self.trailing_current_threshold is not None and self.trailing_floor_price is not None

        next_threshold = round(self.trailing_current_threshold * 1.05, 4)
        if current_price < next_threshold:
            raise TradeStateError(
                f"current_price {current_price!r} has not reached the next threshold {next_threshold!r}"
            )
        new_floor = round(next_threshold * 0.95, 4)
        if new_floor < self.trailing_floor_price:
            raise TradeStateError("computed ratchet floor would decrease -- refusing (trailing floor never moves down)")

        return replace(
            self,
            trailing_current_threshold=next_threshold,
            trailing_floor_price=new_floor,
            trailing_last_updated_at=now,
        )

    def reconcile_position(
        self, *, total_shares: int, weighted_avg_entry_price: Optional[float], strategy: StrategyRuleSet, now: datetime
    ) -> "Trade":
        """Generic broker-reconciliation writer for the live risk
        metrics, for updates that are not a specific ladder fill (e.g.
        a protective exit reducing total_shares to 0). Broker is
        always the source of truth for these two fields
        (state-management.md Sec.3)."""

        if not (self.initial_order_reconciled and (self.initial_filled_shares or 0) > 0):
            raise TradeStateError("cannot reconcile position on a trade with no reconciled initial position")
        if not isinstance(strategy, StrategyRuleSet):
            raise TypeError("strategy must be a validated StrategyRuleSet")
        if total_shares < 0:
            raise TradeStateError("total_shares must not be negative")
        if total_shares > strategy.maximum_position:
            raise TradeStateError(f"total_shares {total_shares} exceeds maximum_position {strategy.maximum_position}")
        if weighted_avg_entry_price is not None and (
            not math.isfinite(weighted_avg_entry_price) or weighted_avg_entry_price <= 0
        ):
            raise TradeStateError(f"weighted_avg_entry_price {weighted_avg_entry_price!r} is not a valid positive finite price")

        return replace(
            self,
            total_shares=total_shares,
            weighted_avg_entry_price=weighted_avg_entry_price,
            last_reconciled_at=now,
        )

    def update_protective_order(self, *, order_id: str, stop_price: float, status: str, now: datetime) -> "Trade":
        """Records the current authoritative protective stop.
        Cancellation-replacement lineage is preserved (append-only,
        never shrinks) -- state-management.md Sec.1 'Protective order
        state'."""

        if stop_price <= 0 or not math.isfinite(stop_price):
            raise TradeStateError(f"stop_price {stop_price!r} is not a valid positive finite price")
        lineage = self.protective_order_lineage
        if self.protective_order_id is not None and self.protective_order_id != order_id:
            lineage = lineage + (self.protective_order_id,)
        return replace(
            self,
            protective_order_id=order_id,
            protective_order_stop_price=stop_price,
            protective_order_status=status,
            protective_order_lineage=lineage,
            last_reconciled_at=now,
        )

    def record_reconciliation(self, *, now: datetime, error: Optional[str] = None) -> "Trade":
        """Reconciliation bookkeeping -- state-management.md Sec.1
        'Reconciliation'. `error=None` clears any previously recorded
        error."""
        return replace(self, last_broker_poll_at=now, last_reconciled_at=now, last_error=error)


def describe_status(trade: Trade) -> str:
    """Pure, derived, NEVER-persisted presentational label. Computed
    fresh from `trade`'s fields on every call -- there is no stored
    `status` field anywhere on Trade, by explicit Controller-approved
    design decision: every distinction below is fully derivable from
    fields the schema needs for other reasons, and a separately stored
    status would be a second, driftable source of truth for the same
    facts.

    One of: "AWAITING_INITIAL_FILL", "ABANDONED", "ACTIVE", "CLOSED".
    """
    if not trade.initial_order_reconciled:
        return "AWAITING_INITIAL_FILL"
    if (trade.initial_filled_shares or 0) == 0:
        return "ABANDONED"
    if trade.total_shares > 0:
        return "ACTIVE"
    return "CLOSED"
