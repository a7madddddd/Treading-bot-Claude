# Glossary

- **Controller** — the human decision maker (project owner). Final
  authority on strategy, risk, and execution behavior.
- **Approved Trading Policy** — the current rules in `strategy.md`.
- **Original Initial Entry Fill Price** — weighted-average fill of the
  initial entry order only; frozen after reconciliation. All original
  Ladder and Floor levels are computed from this.
- **Ladder** — a rule that adds to a position at a specified drawdown
  threshold. Ladder 1 = −5%, Ladder 2 = −8% (from initial fill).
- **Floor** — the last-resort protective exit at −10% from the initial
  fill. Sells the entire position.
- **Trailing Floor** — a dynamic protective floor that ratchets up as the
  weighted-average-entry-relative price rises. Never moves down. Only
  becomes the active protective floor when higher than the original Floor.
- **Active Protective Floor** — the single, authoritative protective exit
  currently in force. Max(original Floor, activated Trailing Floor).
- **Weighted-Average Entry** — cumulative-spent / total-shares held.
  Used for risk reporting and for Trailing Floor activation. Does **not**
  move the original Ladder or original Floor.
- **Paper Trading** — simulated trading through Alpaca's paper endpoint.
  No real money.
- **Skill** — a small composable capability (research, trade, journal,
  benchmark, report).
- **Routine** — a scheduled task (pre-market, open, midday, EOD).
- **Deterministic layer** — code that owns risk, sizing, execution.
- **AI layer** — advisory research and analysis. Never overrides
  deterministic guardrails.
