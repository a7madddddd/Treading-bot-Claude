# Backtesting Standards

A backtest is evidence, not proof. Every proposed strategy — including
variations of the currently approved one — must be evaluated against these
standards before being considered for approval.

---

## 1. Metrics (report all that apply)

- Historical return (absolute, annualized)
- Win rate / loss rate
- Maximum drawdown (absolute, %)
- Sharpe ratio (annualized, where meaningful)
- Sortino ratio (where meaningful)
- Profit factor
- Expectancy per trade
- Average trade (win / loss)
- Worst trade
- Longest losing streak
- Exposure (time in market)
- Number of trades
- Turnover / transaction cost drag
- Risk of ruin (where meaningful)

## 2. Baseline

Never present a strategy in isolation. Always compare against:

- Buy-and-hold on the same universe over the same period
- A naive momentum or mean-reversion baseline where relevant

Report both absolute and excess metrics.

## 3. Failure modes to check

Before calling a backtest "good", show that we ruled out:

- **Overfitting** — parameters tuned to the sample
- **Look-ahead bias** — using data not available at decision time
- **Survivorship bias** — universe excludes delisted/failed names
- **Data leakage** — feature contamination
- **Unrealistic fills** — filling at prices no real order could get
- **Unrealistic slippage / spread** — model both
- **Zero transaction costs** — include commissions and estimated slippage
- **Regime dependency** — split the sample by regime and re-check
- **Small-sample luck** — report trade count and confidence intervals

## 4. Result labeling

Each backtest result must carry:

- Strategy version / commit hash
- Data source and window
- Universe
- Fee/slippage model
- Random seed (if applicable)
- Author (Controller / Claude / joint)

## 5. Not a green light

A passing backtest is one input to a Controller decision. It is not
approval to change the trading policy. See `decisions.md` for how
approvals are recorded.
