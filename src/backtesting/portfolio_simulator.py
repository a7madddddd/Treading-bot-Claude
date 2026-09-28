"""Portfolio-level backtest simulator (B25b).

Runs the D-0004 Ladder + D-0008 Trailing Floor strategy on a
multi-symbol universe with shared capital and D-0047 portfolio-level
risk limits enforced BEFORE every entry / ladder addition.

Design:
  - Aligns all symbols' bars into one date-ordered timeline.
  - Each day:
      1. Sample equity_at_day_open (before any day-D activity).
      2. Reset the "new trades today" counter.
      3. For each symbol WITH an open trade: check protective floor
         (uses day's low), then ladder fills (uses day's low), then
         trailing activation / ratchet (uses day's high).
      4. For each symbol WITHOUT an open trade (and past its cooldown
         window): consider a new Initial Entry at that day's close,
         subject to D-0047.
      5. Record the equity curve point.
  - End of the aligned timeline: exit any remaining open trades at
     the last bar's close.

D-0047 checks (matches src/risk/enforcer.py's evaluate_new_trade /
evaluate_ladder_addition semantics, replicated here so the backtest
does not depend on the risk enforcer's live-broker snapshot builder):
  new-trade rules: kill_switch, concurrent_trades, new_trades_today,
                   gross_exposure, single_symbol_exposure
  ladder rules:    kill_switch, gross_exposure, single_symbol_exposure
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from backtesting.metrics import _max_drawdown, _sharpe
from backtesting.models import Bar, BacktestTrade, ExitReason
from backtesting.portfolio_models import (
    EquityPoint, PortfolioBacktestConfig, PortfolioBacktestResult,
    PortfolioMetrics, RejectionReason, RejectionRecord,
)
from proposals.models import approved_strategy_rule_set
from trade.models import InitialOrderStatus, Trade, TradeAction


_TRAILING_ACTIVATION_PCT = 0.10
_TRAILING_RATCHET_PCT = 0.05


def _dt(d: date) -> datetime:
    return datetime.combine(d, time(14, 30), tzinfo=timezone.utc)


@dataclass
class _OpenPosition:
    trade: Trade
    entry_date: date
    entry_price: float


@dataclass
class _CompletedRecord:
    trade: BacktestTrade
    exit_date: date


class PortfolioSimulator:
    def __init__(self, config: Optional[PortfolioBacktestConfig] = None) -> None:
        self._config = config or PortfolioBacktestConfig()
        self._strategy = approved_strategy_rule_set()

    # ---- top-level ----------------------------------------------------

    def run(self, bars_by_symbol: Mapping[str, Sequence[Bar]]
            ) -> PortfolioBacktestResult:
        if not bars_by_symbol:
            return _empty_result(self._config.initial_cash)

        dates = self._all_dates(bars_by_symbol)
        bars_index = self._index_bars(bars_by_symbol)

        cash = self._config.initial_cash
        positions: Dict[str, _OpenPosition] = {}
        last_exit_by_symbol: Dict[str, date] = {}
        completed: List[BacktestTrade] = []
        rejections: List[RejectionRecord] = []
        equity_curve: List[EquityPoint] = []
        prev_close_by_symbol: Dict[str, float] = {}

        for d in dates:
            # 1. Snapshot equity at day open (before any activity).
            equity_open = cash + sum(
                pos.trade.total_shares * prev_close_by_symbol.get(sym, pos.entry_price)
                for sym, pos in positions.items()
            )
            new_trades_today = 0

            # Get all symbols that have a bar today.
            symbols_today = [s for s in bars_index if d in bars_index[s]]

            # 2. Update open trades (in a stable symbol-alphabetical
            #    order so the sim is deterministic).
            for sym in sorted(symbols_today):
                if sym not in positions:
                    continue
                bar = bars_index[sym][d]
                outcome = self._advance_open_trade(sym, positions[sym], bar,
                                                   cash, equity_open,
                                                   positions, rejections,
                                                   bars_index)
                cash = outcome["cash"]
                if outcome["exited"]:
                    completed.append(outcome["completed"])
                    del positions[sym]
                    last_exit_by_symbol[sym] = d

            # 3. Consider new entries.
            for sym in sorted(symbols_today):
                if sym in positions:
                    continue
                if not self._past_cooldown(sym, d, last_exit_by_symbol):
                    continue
                bar = bars_index[sym][d]
                entry_qty = (
                    self._config.override_initial_qty
                    if self._config.override_initial_qty is not None
                    else self._strategy.initial_qty
                )
                notional = entry_qty * bar.close

                current_equity = cash + sum(
                    pos.trade.total_shares * bars_index[s][d].close
                    if d in bars_index[s]
                    else pos.trade.total_shares * prev_close_by_symbol.get(s, pos.entry_price)
                    for s, pos in positions.items()
                )
                gross_exposure = sum(
                    pos.trade.total_shares * bars_index[s][d].close
                    if d in bars_index[s]
                    else pos.trade.total_shares * prev_close_by_symbol.get(s, pos.entry_price)
                    for s, pos in positions.items()
                )

                # D-0047: kill switch, concurrent, new-today, gross,
                # single-symbol.
                reason = self._check_new_trade(
                    equity_open=equity_open, current_equity=current_equity,
                    gross_exposure=gross_exposure,
                    concurrent=len(positions),
                    new_today=new_trades_today,
                    symbol_existing=0.0,
                    notional=notional, cash=cash,
                )
                if reason is not None:
                    rejections.append(RejectionRecord(
                        bar_date=d, symbol=sym, reason=reason,
                        detail=f"Initial Entry for {sym} rejected",
                    ))
                    continue

                # Open position.
                cash -= notional
                trade = Trade(
                    trade_id=f"pf-{uuid.uuid4().hex[:12]}",
                    symbol=sym, created_at=_dt(d),
                ).freeze_initial_reference(
                    order_status=InitialOrderStatus.FILLED,
                    filled_shares=entry_qty,
                    fill_price=bar.close,
                    strategy=self._strategy,
                    now=_dt(d),
                )
                positions[sym] = _OpenPosition(
                    trade=trade, entry_date=d, entry_price=bar.close,
                )
                new_trades_today += 1

            # 4. Update prev_close for all symbols with a bar today.
            for sym in symbols_today:
                prev_close_by_symbol[sym] = bars_index[sym][d].close

            # 5. Record equity curve.
            positions_value = sum(
                pos.trade.total_shares * prev_close_by_symbol[s]
                for s, pos in positions.items()
            )
            equity_curve.append(EquityPoint(
                bar_date=d, cash=cash, positions_value=positions_value,
                equity=cash + positions_value,
                open_positions=len(positions),
            ))

        # End-of-timeline: exit remaining positions at last close.
        if positions:
            last_date = dates[-1]
            for sym, pos in list(positions.items()):
                last_bar = bars_index[sym].get(last_date)
                if last_bar is None:
                    # find last available bar for this symbol
                    all_dates_for_sym = sorted(bars_index[sym].keys())
                    if not all_dates_for_sym:
                        continue
                    last_bar = bars_index[sym][all_dates_for_sym[-1]]
                    exit_dt = all_dates_for_sym[-1]
                else:
                    exit_dt = last_date
                exit_price = last_bar.close
                cash += pos.trade.total_shares * exit_price
                completed.append(_build_completed(
                    sym, pos.trade, pos.entry_date, pos.entry_price,
                    exit_dt, exit_price, ExitReason.END_OF_PERIOD,
                ))

        metrics = self._compute_metrics(completed, equity_curve, rejections)
        return PortfolioBacktestResult(
            trades=tuple(completed),
            equity_curve=tuple(equity_curve),
            rejections=tuple(rejections),
            metrics=metrics,
        )

    # ---- inner helpers ------------------------------------------------

    def _advance_open_trade(
        self, sym: str, pos: _OpenPosition, bar: Bar,
        cash: float, equity_open: float,
        positions: Dict[str, _OpenPosition],
        rejections: List[RejectionRecord],
        bars_index: Dict[str, Dict[date, Bar]],
    ) -> dict:
        trade = pos.trade

        # Floor exit first (protective priority).
        floor = trade.active_floor_price
        if floor is not None and bar.low <= floor <= bar.high:
            cash += trade.total_shares * floor
            completed = _build_completed(
                sym, trade, pos.entry_date, pos.entry_price,
                bar.bar_date, floor,
                ExitReason.TRAILING_FLOOR_HIT if trade.trailing_activated
                else ExitReason.FLOOR_HIT,
            )
            return {"cash": cash, "exited": True, "completed": completed}

        # Ladder fills.
        for ladder_2 in (True, False):
            filled_flag = (trade.ladder2_filled if ladder_2
                          else trade.ladder1_filled)
            price_attr = (trade.ladder2_price if ladder_2
                         else trade.ladder1_price)
            if filled_flag or price_attr is None:
                continue
            if bar.low > price_attr:
                continue
            qty = (self._config.override_ladder2_qty
                   if ladder_2 else self._config.override_ladder1_qty)
            if qty is None:
                qty = (self._strategy.ladder_2_qty if ladder_2
                       else self._strategy.ladder_1_qty)
            notional = qty * price_attr

            # D-0047 for ladder: kill-switch + gross + single-symbol only.
            # Mark all positions to the CURRENT day's close where a bar
            # is available. Fixes a real kill-switch blind spot: a
            # multi-symbol crash on the same day would previously value
            # OTHER positions at their entry price, missing the loss.
            def _mark(sym_of_pos: str, p_of_pos: _OpenPosition) -> float:
                if sym_of_pos == sym:
                    return p_of_pos.trade.total_shares * bar.close
                other_bar = bars_index.get(sym_of_pos, {}).get(bar.bar_date)
                px = other_bar.close if other_bar is not None else p_of_pos.entry_price
                return p_of_pos.trade.total_shares * px
            gross_ex = sum(_mark(s, p) for s, p in positions.items())
            current_equity = cash + gross_ex
            symbol_existing = trade.total_shares * bar.close
            reason = self._check_ladder_addition(
                equity_open=equity_open, current_equity=current_equity,
                gross_exposure=gross_ex, symbol_existing=symbol_existing,
                notional=notional, cash=cash,
            )
            if reason is not None:
                rejections.append(RejectionRecord(
                    bar_date=bar.bar_date, symbol=sym, reason=reason,
                    detail=(f"Ladder {'2' if ladder_2 else '1'} "
                           f"for {sym} rejected"),
                ))
                continue
            # Fill it.
            cash -= notional
            prev_shares = trade.total_shares
            prev_wae = trade.weighted_avg_entry_price or 0.0
            new_total = prev_shares + qty
            new_wae = ((prev_wae * prev_shares + price_attr * qty)
                       / new_total)
            trade = trade.record_ladder_fill(
                action=TradeAction.LADDER_2 if ladder_2 else TradeAction.LADDER_1,
                order_id=f"pf-o-{uuid.uuid4().hex[:8]}",
                fill_price=price_attr, fill_qty=qty,
                new_total_shares=new_total,
                new_weighted_avg_entry_price=new_wae,
                strategy=self._strategy,
            )
            pos.trade = trade

        # Trailing activation.
        wae = trade.weighted_avg_entry_price
        if not trade.trailing_activated and wae is not None:
            activation = round(wae * (1 + _TRAILING_ACTIVATION_PCT), 4)
            if bar.high >= activation:
                trade = trade.activate_trailing(
                    current_price=activation, now=_dt(bar.bar_date),
                )
                pos.trade = trade

        # Trailing ratchet.
        if trade.trailing_activated and trade.trailing_current_threshold is not None:
            next_th = round(trade.trailing_current_threshold * (1 + _TRAILING_RATCHET_PCT), 4)
            while bar.high >= next_th:
                trade = trade.ratchet_trailing(
                    current_price=next_th, now=_dt(bar.bar_date),
                )
                pos.trade = trade
                next_th = round(trade.trailing_current_threshold * (1 + _TRAILING_RATCHET_PCT), 4)

        return {"cash": cash, "exited": False, "completed": None}

    def _check_new_trade(
        self, *, equity_open: float, current_equity: float,
        gross_exposure: float, concurrent: int, new_today: int,
        symbol_existing: float, notional: float, cash: float,
    ) -> Optional[RejectionReason]:
        # Kill switch first.
        if equity_open > 0:
            loss_fraction = (equity_open - current_equity) / equity_open
            if loss_fraction >= self._config.daily_loss_kill_switch_fraction:
                return RejectionReason.KILL_SWITCH
        if concurrent >= self._config.max_concurrent_trades:
            return RejectionReason.CONCURRENT_TRADES
        if new_today >= self._config.max_daily_new_trades:
            return RejectionReason.NEW_TRADES_TODAY
        if cash < notional:
            return RejectionReason.INSUFFICIENT_CASH
        if current_equity <= 0:
            return RejectionReason.GROSS_EXPOSURE
        projected_gross = gross_exposure + notional
        if projected_gross / current_equity > self._config.max_gross_exposure_fraction:
            return RejectionReason.GROSS_EXPOSURE
        projected_symbol = symbol_existing + notional
        if projected_symbol / current_equity > self._config.max_single_symbol_fraction:
            return RejectionReason.SINGLE_SYMBOL
        return None

    def _check_ladder_addition(
        self, *, equity_open: float, current_equity: float,
        gross_exposure: float, symbol_existing: float,
        notional: float, cash: float,
    ) -> Optional[RejectionReason]:
        if equity_open > 0:
            loss_fraction = (equity_open - current_equity) / equity_open
            if loss_fraction >= self._config.daily_loss_kill_switch_fraction:
                return RejectionReason.KILL_SWITCH
        if cash < notional:
            return RejectionReason.INSUFFICIENT_CASH
        if current_equity <= 0:
            return RejectionReason.GROSS_EXPOSURE
        if (gross_exposure + notional) / current_equity > self._config.max_gross_exposure_fraction:
            return RejectionReason.GROSS_EXPOSURE
        if (symbol_existing + notional) / current_equity > self._config.max_single_symbol_fraction:
            return RejectionReason.SINGLE_SYMBOL
        return None

    def _past_cooldown(self, sym: str, d: date,
                       last_exit: Dict[str, date]) -> bool:
        prev = last_exit.get(sym)
        if prev is None:
            return True
        return (d - prev).days >= self._config.cooldown_days_after_exit

    # ---- utilities ----------------------------------------------------

    @staticmethod
    def _all_dates(bars_by_symbol: Mapping[str, Sequence[Bar]]) -> List[date]:
        s = set()
        for bars in bars_by_symbol.values():
            for b in bars:
                s.add(b.bar_date)
        return sorted(s)

    @staticmethod
    def _index_bars(bars_by_symbol: Mapping[str, Sequence[Bar]]
                    ) -> Dict[str, Dict[date, Bar]]:
        return {
            sym: {b.bar_date: b for b in bars}
            for sym, bars in bars_by_symbol.items()
        }

    def _compute_metrics(
        self, completed: List[BacktestTrade],
        equity_curve: List[EquityPoint],
        rejections: List[RejectionRecord],
    ) -> PortfolioMetrics:
        initial = self._config.initial_cash
        final_equity = equity_curve[-1].equity if equity_curve else initial
        total_pnl = final_equity - initial
        total_return = total_pnl / initial if initial > 0 else 0.0

        pnls = [t.pnl() for t in completed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_win > 0 else 0.0
        )
        win_rate = len(wins) / len(completed) if completed else 0.0

        equities = [p.equity for p in equity_curve]
        peak = max(equities) if equities else initial
        # Equity-curve based drawdown (dollar).
        max_dd = 0.0
        max_dd_frac = 0.0
        cur_peak = initial
        for e in equities:
            cur_peak = max(cur_peak, e)
            dd = cur_peak - e
            if dd > max_dd:
                max_dd = dd
                max_dd_frac = dd / cur_peak if cur_peak > 0 else 0.0

        returns_per_trade = [t.return_fraction() for t in completed]
        sharpe = _sharpe(returns_per_trade)

        counts_map: Dict[str, int] = {}
        for r in rejections:
            counts_map[r.reason.value] = counts_map.get(r.reason.value, 0) + 1
        return PortfolioMetrics(
            initial_cash=initial, final_equity=final_equity,
            total_return=total_return, total_pnl=total_pnl,
            total_trades=len(completed),
            winning_trades=len(wins), losing_trades=len(losses),
            win_rate=win_rate, profit_factor=profit_factor,
            max_drawdown=max_dd, max_drawdown_fraction=max_dd_frac,
            sharpe_ratio=sharpe, peak_equity=peak,
            total_rejections=len(rejections),
            rejection_counts=tuple(sorted(counts_map.items())),
        )


def _build_completed(
    symbol: str, trade: Trade,
    entry_date: date, entry_price: float,
    exit_date: date, exit_price: float, reason: ExitReason,
) -> BacktestTrade:
    return BacktestTrade(
        symbol=symbol, entry_date=entry_date, entry_price=entry_price,
        initial_shares=trade.initial_filled_shares or 0,
        ladder1_fill_price=trade.ladder1_fill_price,
        ladder1_fill_qty=trade.ladder1_fill_qty or 0,
        ladder2_fill_price=trade.ladder2_fill_price,
        ladder2_fill_qty=trade.ladder2_fill_qty or 0,
        exit_date=exit_date, exit_price=exit_price,
        exit_reason=reason, final_shares=trade.total_shares,
        weighted_avg_entry_price=trade.weighted_avg_entry_price or entry_price,
        trailing_activated=trade.trailing_activated,
        trailing_peak_threshold=trade.trailing_current_threshold,
    )


def _empty_result(initial_cash: float) -> PortfolioBacktestResult:
    return PortfolioBacktestResult(
        trades=(), equity_curve=(), rejections=(),
        metrics=PortfolioMetrics(
            initial_cash=initial_cash, final_equity=initial_cash,
            total_return=0.0, total_pnl=0.0, total_trades=0,
            winning_trades=0, losing_trades=0, win_rate=0.0,
            profit_factor=0.0, max_drawdown=0.0,
            max_drawdown_fraction=0.0, sharpe_ratio=0.0,
            peak_equity=initial_cash, total_rejections=0,
        ),
    )
