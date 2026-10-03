"""Percentage-based position sizing policy (D-0051).

Replaces the previously-approved fixed share counts (D-0004 §1:
initial_qty=10 / ladder_1_qty=10 / ladder_2_qty=20) with a policy
that sizes each layer as a fraction of account equity. This keeps
dollar exposure roughly constant across any price range, so a
dynamic Universe (D-0048) can propose a $12 symbol and a $750 symbol
without the second producing 60× the risk of the first.

The policy is pure math: it takes (equity, price) and returns the
three integer share counts plus the summed maximum_position. It
performs no broker call, no persistence, no notification -- callers
inject equity and price from their own authoritative source (the
broker at proposal creation time per D-0007).

Default parameters (Controller-approved 2026-10-03, D-0051):
  * trade_budget_pct   = 0.05  — 5% of equity per full trade
  * initial_size_pct   = 0.25  — 25% of the trade budget at Initial Entry
  * ladder_1_size_pct  = 0.25  — 25% at Ladder 1 (-5%)
  * ladder_2_size_pct  = 0.50  — 50% at Ladder 2 (-8%)

Split preserves the original 1:1:2 ratio from D-0004 (10/10/20),
only re-expressing it as a share of a dollar budget rather than a
share count. Floor still fires at -10% and sells the full position,
as before.

Legacy trades (initial_quantity = NULL in persistence) continue to
run on the pre-D-0051 fixed quantities for their remaining Ladder 1 /
Ladder 2 attempts -- this policy applies ONLY to proposals created
after it is active, so prior Controller approvals are not altered
retroactively (CLAUDE.md §9).
"""

from __future__ import annotations

from dataclasses import dataclass


class PositionSizingPolicyError(ValueError):
    """Raised when a PositionSizingPolicy is constructed with
    inconsistent parameters, or when compute_shares() is called with
    non-positive equity or price. There is no fallback: a caller
    unable to supply valid (equity, price) must handle the failure
    explicitly rather than receive a silently-wrong share count."""


@dataclass(frozen=True)
class PositionSizingPolicy:
    """Immutable sizing policy. All percentages are fractions in [0,1]
    (e.g. 0.05 = 5%). The three size percentages must sum to 1.0.

    `min_shares` is a lower bound applied per layer AFTER the dollar
    math -- a $500 trade budget on a $600 stock would compute 0 shares
    before this floor and 1 share after. min_shares=0 disables the
    floor (callers that want to skip entirely-un-affordable symbols
    use `is_tradable()` instead)."""

    trade_budget_pct: float = 0.05
    initial_size_pct: float = 0.25
    ladder_1_size_pct: float = 0.25
    ladder_2_size_pct: float = 0.50
    min_shares: int = 1

    def __post_init__(self) -> None:
        for field_name in ("trade_budget_pct", "initial_size_pct",
                           "ladder_1_size_pct", "ladder_2_size_pct"):
            v = getattr(self, field_name)
            if not isinstance(v, (int, float)) or v <= 0 or v > 1:
                raise PositionSizingPolicyError(
                    f"{field_name} must be a fraction in (0, 1]; got {v!r}"
                )
        total = (self.initial_size_pct + self.ladder_1_size_pct
                 + self.ladder_2_size_pct)
        if abs(total - 1.0) > 1e-6:
            raise PositionSizingPolicyError(
                f"initial_size_pct + ladder_1_size_pct + ladder_2_size_pct "
                f"must sum to 1.0; got {total!r}"
            )
        if not isinstance(self.min_shares, int) or self.min_shares < 0:
            raise PositionSizingPolicyError(
                f"min_shares must be a non-negative int; got {self.min_shares!r}"
            )

    def compute_shares(
        self, *, equity: float, price: float,
    ) -> "SizingResult":
        """Returns the integer share counts for Initial, Ladder 1,
        Ladder 2, plus the summed maximum_position. Raises
        PositionSizingPolicyError on non-positive equity or price so
        a bug upstream (zero cash, missing price feed, negative
        buying power) can never silently produce a zero-share order."""
        if not isinstance(equity, (int, float)) or equity <= 0:
            raise PositionSizingPolicyError(
                f"equity must be a positive number; got {equity!r}"
            )
        if not isinstance(price, (int, float)) or price <= 0:
            raise PositionSizingPolicyError(
                f"price must be a positive number; got {price!r}"
            )

        trade_budget = equity * self.trade_budget_pct

        def _shares_for(size_pct: float) -> int:
            dollars = trade_budget * size_pct
            raw = int(dollars / price)   # floor; never over-buy
            return max(self.min_shares, raw)

        initial_qty = _shares_for(self.initial_size_pct)
        ladder_1_qty = _shares_for(self.ladder_1_size_pct)
        ladder_2_qty = _shares_for(self.ladder_2_size_pct)
        maximum_position = initial_qty + ladder_1_qty + ladder_2_qty
        return SizingResult(
            initial_qty=initial_qty,
            ladder_1_qty=ladder_1_qty,
            ladder_2_qty=ladder_2_qty,
            maximum_position=maximum_position,
            trade_budget_dollars=round(trade_budget, 2),
        )

    def is_tradable(self, *, equity: float, price: float) -> bool:
        """Returns False when the symbol's price exceeds the Initial
        Entry's dollar allocation by more than 2× -- i.e. buying even
        a single share would wildly overshoot the policy's intended
        per-layer exposure. Callers should skip the symbol in that
        case rather than drop to 1 forced share and overspend.

        2× tolerance keeps a $400 initial dollar budget usable for a
        $500 share (would hit the min_shares=1 floor cleanly) but
        rejects a $2,000 share, which would be ~5× the layer budget."""
        if equity <= 0 or price <= 0:
            return False
        initial_dollars = equity * self.trade_budget_pct * self.initial_size_pct
        return price <= initial_dollars * 2


@dataclass(frozen=True)
class SizingResult:
    initial_qty: int
    ladder_1_qty: int
    ladder_2_qty: int
    maximum_position: int
    trade_budget_dollars: float

    def __post_init__(self) -> None:
        for field_name in ("initial_qty", "ladder_1_qty", "ladder_2_qty",
                           "maximum_position"):
            v = getattr(self, field_name)
            if not isinstance(v, int) or v <= 0:
                raise PositionSizingPolicyError(
                    f"{field_name} must be a positive int; got {v!r}"
                )
        s = self.initial_qty + self.ladder_1_qty + self.ladder_2_qty
        if s != self.maximum_position:
            raise PositionSizingPolicyError(
                f"maximum_position ({self.maximum_position}) must equal "
                f"initial+ladder_1+ladder_2 ({s})"
            )


APPROVED_D0051_POLICY = PositionSizingPolicy(
    trade_budget_pct=0.05,
    initial_size_pct=0.25,
    ladder_1_size_pct=0.25,
    ladder_2_size_pct=0.50,
    min_shares=1,
)
"""The Controller-approved D-0051 default policy (2026-10-03).

Any production call site that builds proposals with percentage-based
sizing uses this constant unless a different approved policy exists.
Backtest and tests may construct their own PositionSizingPolicy
instances; this constant is the single source of truth for the live
engine."""
