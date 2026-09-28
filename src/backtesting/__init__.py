"""Backtesting infrastructure (B25).

Simulates the D-0004 / D-0007 / D-0008 / D-0009 / D-0010 Ladder +
Floor + Trailing strategy against historical daily bars. Uses the
same `trade.models.Trade` state transitions as the live engine, so
the P&L numbers reflect the actual strategy math -- no re-implementation.

Simplifications vs the live engine (documented, not hidden):
  - Auto-approves every Controller step (no Telegram round-trip).
  - Bars are daily (no intra-day timing); D-0011 debounce is not
    applied.
  - Simulated broker fills orders when the day's low/high crosses
    the limit; slippage is set to zero (worst-case is bounded by
    the daily low/high range).
  - D-0007 5-minute re-check is not applied to auto-approved
    fills.

Advisory only: no real broker is ever contacted. Never places or
modifies a paper or live order.
"""
