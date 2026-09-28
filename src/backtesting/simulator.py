"""Backtest simulator (B25).

Replays the D-0004 Ladder + D-0008 Trailing Floor strategy over
historical daily bars, using the exact `trade.models.Trade` state
transitions from the live engine (no re-implementation of strategy
math).

Design: for each symbol, iterate bars day by day. On day 0 (or
after a cooldown following an exit), start a new Trade at the
day's close and freeze the reference. On each subsequent day:
  1. Test protective-floor hit against the day's low. If hit,
     exit at the floor price.
  2. Test Ladder 1 / Ladder 2 triggers against the day's low.
     If hit, record the ladder fill.
  3. Test trailing activation (close >= entry*1.10). If hit,
     activate.
  4. Test trailing ratchet (close >= next threshold).
  5. On period end, close remaining position at last close.

Fill assumption: an order fills at its own trigger price if the
day's [low, high] range covers the trigger. This is the standard
back-test assumption and is bounded by the actual daily range
(never worse than the day's low).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import List, Optional, Sequence

from backtesting.models import (
    Bar, BacktestConfig, BacktestResult, BacktestTrade, ExitReason,
)
from backtesting.metrics import compute_metrics
from proposals.models import approved_strategy_rule_set
from trade.models import (
    InitialOrderStatus, Trade,
)


# Trailing math constants matched to trade.models.activate_trailing
# / ratchet_trailing (D-0004 / D-0008). Hardcoded there; hardcoded
# here too so the backtest reproduces the same numbers exactly.
_TRAILING_ACTIVATION_PCT = 0.10
_TRAILING_RATCHET_PCT = 0.05


def _dt(d: date) -> datetime:
    return datetime.combine(d, time(14, 30), tzinfo=timezone.utc)


class BacktestSimulator:
    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self._config = config or BacktestConfig()
        self._strategy = approved_strategy_rule_set()

    def run(self, symbol: str, bars: Sequence[Bar]) -> BacktestResult:
        if not bars:
            return BacktestResult(
                symbol=symbol, trades=(),
                metrics=compute_metrics(()),
            )

        completed: List[BacktestTrade] = []
        i = 0
        n = len(bars)
        while i < n:
            trade_result = self._simulate_one_trade(symbol, bars, i)
            if trade_result is None:
                break
            trade, exit_index = trade_result
            completed.append(trade)
            i = exit_index + 1 + self._config.cooldown_days_after_exit

        return BacktestResult(
            symbol=symbol,
            trades=tuple(completed),
            metrics=compute_metrics(completed),
        )

    # ---- inner loop ------------------------------------------------

    def _simulate_one_trade(
        self, symbol: str, bars: Sequence[Bar], start_index: int
    ) -> Optional[tuple]:
        """Simulates one Trade from entry at bars[start_index] to its
        first exit. Returns (BacktestTrade, exit_index) or None if the
        run reaches end-of-data without an entry."""

        if start_index >= len(bars):
            return None

        entry_bar = bars[start_index]
        entry_qty = (
            self._config.override_initial_qty
            if self._config.override_initial_qty is not None
            else self._strategy.initial_qty
        )
        # Slippage applied to entry: paid price is worse than the bar's
        # close by cost_model.slippage_bps_buy. The frozen reference
        # (ladder + floor prices) is derived from the actual paid fill,
        # which matches how the live engine anchors to broker-reported
        # fills, not to any pre-order intent.
        entry_price = self._config.cost_model.buy_fill(entry_bar.close)
        commissions_paid = self._config.cost_model.commission(entry_qty)

        trade = Trade(
            trade_id=f"bt-{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            created_at=_dt(entry_bar.bar_date),
        )
        trade = trade.freeze_initial_reference(
            order_status=InitialOrderStatus.FILLED,
            filled_shares=entry_qty,
            fill_price=entry_price,
            strategy=self._strategy,
            now=_dt(entry_bar.bar_date),
        )

        for j in range(start_index + 1, len(bars)):
            bar = bars[j]

            # 1) Floor hit? Protective exit wins over ladder fills.
            floor = trade.active_floor_price
            if floor is not None and bar.low <= floor <= bar.high:
                exit_price_net = self._config.cost_model.sell_fill(floor)
                commissions_paid += self._config.cost_model.commission(
                    trade.total_shares
                )
                return (self._exit_trade(
                    symbol, trade, entry_bar.bar_date, entry_price,
                    exit_date=bar.bar_date, exit_price=exit_price_net,
                    reason=ExitReason.TRAILING_FLOOR_HIT
                    if trade.trailing_activated
                    else ExitReason.FLOOR_HIT,
                    total_commission=commissions_paid,
                ), j)

            # 2) Ladder 2 (deeper) checked BEFORE Ladder 1 because
            #    on a day whose low pierces both, both get filled.
            #    We fill in one atomic order per day (worst case): the
            #    deeper one, then the shallower one.
            ladder2_qty = (
                self._config.override_ladder2_qty
                if self._config.override_ladder2_qty is not None
                else self._strategy.ladder_2_qty
            )
            ladder1_qty = (
                self._config.override_ladder1_qty
                if self._config.override_ladder1_qty is not None
                else self._strategy.ladder_1_qty
            )
            if (not trade.ladder2_filled
                    and trade.ladder2_price is not None
                    and bar.low <= trade.ladder2_price):
                fill_px = self._config.cost_model.buy_fill(trade.ladder2_price)
                trade = self._fill_ladder(trade, action_2=True,
                                          fill_price=fill_px,
                                          fill_qty=ladder2_qty)
                commissions_paid += self._config.cost_model.commission(
                    ladder2_qty
                )
            if (not trade.ladder1_filled
                    and trade.ladder1_price is not None
                    and bar.low <= trade.ladder1_price):
                fill_px = self._config.cost_model.buy_fill(trade.ladder1_price)
                trade = self._fill_ladder(trade, action_2=False,
                                          fill_price=fill_px,
                                          fill_qty=ladder1_qty)
                commissions_paid += self._config.cost_model.commission(
                    ladder1_qty
                )

            # 3) Trailing activation (compounded per D-0008). Round to
            #    match trade.models.activate_trailing's internal rounding.
            wae = trade.weighted_avg_entry_price
            if not trade.trailing_activated and wae is not None:
                activation = round(wae * (1 + _TRAILING_ACTIVATION_PCT), 4)
                if bar.high >= activation:
                    trade = trade.activate_trailing(
                        current_price=activation, now=_dt(bar.bar_date),
                    )

            # 4) Trailing ratchet: while active, if a new higher
            #    threshold is crossed (threshold*1.05 compounded), advance.
            #    Use the SAME rounding rule as trade.models.ratchet_trailing
            #    so the boundary comparison here and the internal check there
            #    agree bit-for-bit.
            if trade.trailing_activated and trade.trailing_current_threshold is not None:
                next_threshold = round(
                    trade.trailing_current_threshold * (1 + _TRAILING_RATCHET_PCT),
                    4,
                )
                while bar.high >= next_threshold:
                    trade = trade.ratchet_trailing(
                        current_price=next_threshold,
                        now=_dt(bar.bar_date),
                    )
                    next_threshold = round(
                        trade.trailing_current_threshold * (1 + _TRAILING_RATCHET_PCT),
                        4,
                    )

        # End of data reached without exit -- close at last close.
        last_bar = bars[-1]
        exit_price_net = self._config.cost_model.sell_fill(last_bar.close)
        commissions_paid += self._config.cost_model.commission(
            trade.total_shares
        )
        return (self._exit_trade(
            symbol, trade, entry_bar.bar_date, entry_price,
            exit_date=last_bar.bar_date, exit_price=exit_price_net,
            reason=ExitReason.END_OF_PERIOD,
            total_commission=commissions_paid,
        ), len(bars) - 1)

    def _fill_ladder(self, trade: Trade, *, action_2: bool,
                     fill_price: float, fill_qty: int) -> Trade:
        from trade.models import TradeAction

        prev_shares = trade.total_shares
        prev_wae = trade.weighted_avg_entry_price or 0.0
        new_shares = prev_shares + fill_qty
        new_wae = (
            (prev_wae * prev_shares + fill_price * fill_qty) / new_shares
        )
        return trade.record_ladder_fill(
            action=TradeAction.LADDER_2 if action_2 else TradeAction.LADDER_1,
            order_id=f"bt-order-{uuid.uuid4().hex[:8]}",
            fill_price=fill_price, fill_qty=fill_qty,
            new_total_shares=new_shares,
            new_weighted_avg_entry_price=new_wae,
            strategy=self._strategy,
        )

    def _exit_trade(
        self, symbol: str, trade: Trade, entry_date: date, entry_price: float,
        *, exit_date: date, exit_price: float, reason: ExitReason,
        total_commission: float = 0.0,
    ) -> BacktestTrade:
        return BacktestTrade(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            initial_shares=trade.initial_filled_shares or 0,
            ladder1_fill_price=trade.ladder1_fill_price,
            ladder1_fill_qty=trade.ladder1_fill_qty or 0,
            ladder2_fill_price=trade.ladder2_fill_price,
            ladder2_fill_qty=trade.ladder2_fill_qty or 0,
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=reason,
            final_shares=trade.total_shares,
            weighted_avg_entry_price=trade.weighted_avg_entry_price or entry_price,
            trailing_activated=trade.trailing_activated,
            trailing_peak_threshold=trade.trailing_current_threshold,
            total_commission=total_commission,
        )
