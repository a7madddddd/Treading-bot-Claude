"""Deterministic portfolio-level risk checks (D-0047).

Pure functions over `PortfolioSnapshot` + `PortfolioRiskLimits`. No
I/O. Callable from anywhere. `PortfolioRiskEnforcer` is a thin
wrapper that pairs a live snapshot-builder with the pure checks so
the caller can express intent (`check_new_trade`,
`check_ladder_addition`) in one line.

Never returns "ALLOWED" without justification. Every rejection
carries a `RiskCheck` naming which rule failed and by how much,
suitable for a Telegram notice.
"""

from __future__ import annotations

from typing import Callable, List

from risk.models import (
    PortfolioRiskLimits, PortfolioSnapshot, RiskCheck, RiskCheckResult,
    RiskVerdict,
)


def evaluate_new_trade(
    *,
    symbol: str,
    proposed_notional: float,
    snapshot: PortfolioSnapshot,
    limits: PortfolioRiskLimits,
) -> RiskCheckResult:
    """Pure evaluation. Returns ALLOWED when ALL rules pass; VIOLATED
    with the failing rule(s) attached otherwise. `proposed_notional`
    is `qty * price` for the new position being opened."""

    symbol = symbol.upper()
    checks: List[RiskCheck] = []

    # Rule 5: daily loss kill switch (first — a hard stop overrides
    # everything else the caller might try to do today).
    dl = _check_daily_loss(snapshot, limits)
    checks.append(dl)

    # Rule 3: concurrent trades cap.
    cc = _check_concurrent_trades(snapshot, limits)
    checks.append(cc)

    # Rule 4: new-trades-today cap.
    nt = _check_new_trades_today(snapshot, limits)
    checks.append(nt)

    # Rule 1: gross exposure after this trade.
    ge = _check_gross_exposure(snapshot, limits,
                               added_notional=proposed_notional)
    checks.append(ge)

    # Rule 2: single-symbol exposure after this trade.
    ss = _check_single_symbol(snapshot, limits, symbol=symbol,
                              added_notional=proposed_notional)
    checks.append(ss)

    return _finalize(checks)


def evaluate_ladder_addition(
    *,
    symbol: str,
    proposed_notional: float,
    snapshot: PortfolioSnapshot,
    limits: PortfolioRiskLimits,
) -> RiskCheckResult:
    """Ladder additions add to an existing symbol position. Concurrent
    trades and new-trades-today do NOT apply here (no new trade is
    opened); gross exposure, single-symbol exposure, and daily loss
    still apply."""

    symbol = symbol.upper()
    checks: List[RiskCheck] = []

    checks.append(_check_daily_loss(snapshot, limits))
    checks.append(_check_gross_exposure(snapshot, limits,
                                        added_notional=proposed_notional))
    checks.append(_check_single_symbol(snapshot, limits, symbol=symbol,
                                       added_notional=proposed_notional))

    return _finalize(checks)


# ---- individual rules --------------------------------------------------

def _check_gross_exposure(snapshot: PortfolioSnapshot,
                          limits: PortfolioRiskLimits,
                          *, added_notional: float) -> RiskCheck:
    equity = snapshot.equity_current
    if equity <= 0:
        return RiskCheck(
            name="gross_exposure",
            passed=False,
            reason=f"equity is non-positive ({equity:.2f}); cannot compute exposure",
        )
    projected = snapshot.gross_exposure() + max(0.0, added_notional)
    fraction = projected / equity
    ok = fraction <= limits.max_gross_exposure_fraction
    return RiskCheck(
        name="gross_exposure",
        passed=ok,
        reason=(
            "" if ok else (
                f"gross exposure would be {fraction*100:.1f}% of equity "
                f"({projected:.2f}/{equity:.2f}), exceeds cap "
                f"{limits.max_gross_exposure_fraction*100:.1f}%"
            )
        ),
    )


def _check_single_symbol(snapshot: PortfolioSnapshot,
                         limits: PortfolioRiskLimits,
                         *, symbol: str, added_notional: float) -> RiskCheck:
    equity = snapshot.equity_current
    if equity <= 0:
        return RiskCheck(
            name="single_symbol_exposure",
            passed=False,
            reason=f"equity is non-positive ({equity:.2f})",
        )
    projected = snapshot.exposure_for_symbol(symbol) + max(0.0, added_notional)
    fraction = projected / equity
    ok = fraction <= limits.max_single_symbol_fraction
    return RiskCheck(
        name="single_symbol_exposure",
        passed=ok,
        reason=(
            "" if ok else (
                f"{symbol} exposure would be {fraction*100:.1f}% of equity "
                f"({projected:.2f}/{equity:.2f}), exceeds cap "
                f"{limits.max_single_symbol_fraction*100:.1f}%"
            )
        ),
    )


def _check_concurrent_trades(snapshot: PortfolioSnapshot,
                             limits: PortfolioRiskLimits) -> RiskCheck:
    ok = snapshot.open_trades < limits.max_concurrent_trades
    return RiskCheck(
        name="concurrent_trades",
        passed=ok,
        reason=(
            "" if ok else (
                f"already {snapshot.open_trades} open trades, cap is "
                f"{limits.max_concurrent_trades}"
            )
        ),
    )


def _check_new_trades_today(snapshot: PortfolioSnapshot,
                            limits: PortfolioRiskLimits) -> RiskCheck:
    ok = snapshot.new_trades_today < limits.max_daily_new_trades
    return RiskCheck(
        name="new_trades_today",
        passed=ok,
        reason=(
            "" if ok else (
                f"already {snapshot.new_trades_today} new trades opened today, "
                f"daily cap is {limits.max_daily_new_trades}"
            )
        ),
    )


def _check_daily_loss(snapshot: PortfolioSnapshot,
                      limits: PortfolioRiskLimits) -> RiskCheck:
    anchor = snapshot.equity_at_day_open
    if anchor <= 0:
        # Do not lock trading based on missing data — but this is a
        # data-quality issue, so report it.
        return RiskCheck(
            name="daily_loss_kill_switch",
            passed=True,
            reason=("prior-day equity anchor is non-positive; "
                    "kill switch cannot evaluate — allowed"),
        )
    loss = anchor - snapshot.equity_current
    loss_fraction = loss / anchor
    triggered = loss_fraction >= limits.daily_loss_kill_switch_fraction
    return RiskCheck(
        name="daily_loss_kill_switch",
        passed=not triggered,
        reason=(
            "" if not triggered else (
                f"daily loss is {loss_fraction*100:.2f}% of prior-day equity "
                f"({loss:.2f}/{anchor:.2f}), kill-switch at "
                f"{limits.daily_loss_kill_switch_fraction*100:.2f}% engaged"
            )
        ),
    )


def _finalize(checks: List[RiskCheck]) -> RiskCheckResult:
    verdict = (RiskVerdict.ALLOWED
               if all(c.passed for c in checks)
               else RiskVerdict.VIOLATED)
    return RiskCheckResult(verdict=verdict, checks=tuple(checks))


# ---- live-wired enforcer ----------------------------------------------

SnapshotBuilder = Callable[[], PortfolioSnapshot]


class PortfolioRiskEnforcer:
    """Convenience wrapper for `ExecutionService`. Rebuilds the
    snapshot before every check (freshness is always the responsibility
    of the injected builder callable). The pure functions above stay
    the authority; this class is just plumbing.

    Fail-closed on builder errors: if the snapshot builder raises
    (Alpaca 5xx, network timeout, malformed body), the check returns
    VIOLATED with a `snapshot_unavailable` RiskCheck, never propagates
    the raw exception. CLAUDE.md §6: a failed safety check must never
    lead to a broker submission -- but must also never crash the
    trigger loop that would otherwise service other trades' Floors."""

    def __init__(self, *, limits: PortfolioRiskLimits,
                 snapshot_builder: SnapshotBuilder,
                 limits_provider=None) -> None:
        """`limits_provider` (D-0077, optional): a zero-argument callable
        returning a `risk.dynamic_limits.LimitDecision`, so the two trade
        COUNT limits can respond to how much of the market the day's
        research actually saw.

        When it is None — the default, and what every existing caller
        does — behaviour is byte-identical to before: the fixed,
        Controller-approved `limits` are enforced.

        It fails OPEN to the approved limits, deliberately. If the
        provider raises, the Controller-approved numbers are enforced
        unchanged. That is the opposite of the fail-CLOSED rule for the
        snapshot builder, and the asymmetry is the point: a missing
        portfolio snapshot means we do not know the current exposure and
        must refuse, while a missing scale only means we cannot tighten
        below limits the Controller already approved as safe.
        """
        self._limits = limits
        self._snapshot_builder = snapshot_builder
        self._limits_provider = limits_provider

    def _effective(self):
        """Returns (limits, blocked_reason). `blocked_reason` is a string
        when the day's data says no new trade may be opened at all."""
        if self._limits_provider is None:
            return self._limits, None
        try:
            decision = self._limits_provider()
        except Exception:  # noqa: BLE001 - fail OPEN to approved limits
            return self._limits, None
        if decision is None:
            return self._limits, None
        if not decision.trading_allowed:
            return self._limits, decision.reason
        return decision.limits, None

    def _blocked(self, reason: str) -> RiskCheckResult:
        return RiskCheckResult(
            verdict=RiskVerdict.VIOLATED,
            checks=(RiskCheck(
                name="degraded_universe_no_new_trades", passed=False,
                reason=reason,
            ),),
        )

    def _safe_snapshot(self):
        try:
            return self._snapshot_builder(), None
        except Exception as ex:  # noqa: BLE001 -- doctrine: fail-closed
            return None, ex

    def _snapshot_unavailable(self, exc: Exception) -> RiskCheckResult:
        return RiskCheckResult(
            verdict=RiskVerdict.VIOLATED,
            checks=(RiskCheck(
                name="snapshot_unavailable", passed=False,
                reason=(f"portfolio snapshot could not be built "
                        f"({type(exc).__name__}: {exc}); fail-closed"),
            ),),
        )

    def check_new_trade(self, *, symbol: str,
                        proposed_notional: float) -> RiskCheckResult:
        snapshot, err = self._safe_snapshot()
        if snapshot is None:
            return self._snapshot_unavailable(err)
        limits, blocked = self._effective()
        if blocked:
            return self._blocked(blocked)
        return evaluate_new_trade(symbol=symbol,
                                  proposed_notional=proposed_notional,
                                  snapshot=snapshot, limits=limits)

    def check_ladder_addition(self, *, symbol: str,
                              proposed_notional: float) -> RiskCheckResult:
        snapshot, err = self._safe_snapshot()
        if snapshot is None:
            return self._snapshot_unavailable(err)
        # A ladder ADDS to a position that already exists and was
        # already approved. The degraded-universe block is about opening
        # NEW exposure, so it deliberately does not apply here -- it must
        # never strand an open trade without its ladder.
        limits, _blocked_reason = self._effective()
        return evaluate_ladder_addition(
            symbol=symbol, proposed_notional=proposed_notional,
            snapshot=snapshot, limits=limits,
        )
