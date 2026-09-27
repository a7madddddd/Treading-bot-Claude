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
    the authority; this class is just plumbing."""

    def __init__(self, *, limits: PortfolioRiskLimits,
                 snapshot_builder: SnapshotBuilder) -> None:
        self._limits = limits
        self._snapshot_builder = snapshot_builder

    def check_new_trade(self, *, symbol: str,
                        proposed_notional: float) -> RiskCheckResult:
        snapshot = self._snapshot_builder()
        return evaluate_new_trade(symbol=symbol,
                                  proposed_notional=proposed_notional,
                                  snapshot=snapshot, limits=self._limits)

    def check_ladder_addition(self, *, symbol: str,
                              proposed_notional: float) -> RiskCheckResult:
        snapshot = self._snapshot_builder()
        return evaluate_ladder_addition(
            symbol=symbol, proposed_notional=proposed_notional,
            snapshot=snapshot, limits=self._limits,
        )
