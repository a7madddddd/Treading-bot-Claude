# Pending Controller Approvals — Live Tracker

**Rule for Claude:** At the start of every session, surface the items in this
file to the Controller. Do not wait to be asked. Items here BLOCK the
system from reaching its intended production state.

Last audit: 2026-10-03.

---

## 🚨 BLOCKING — Must decide before production

### P-001 — Dynamic Universe (D-0026) parameters
- **Status:** PROPOSED / NOT APPROVED
- **File:** `docs/architecture/universe.md`,
  `docs/trading/universe-selection-analysis.md`
- **What it is:** The 8-stage dynamic Universe selection pipeline
  (A tradability → B data quality → C execution → D strategy
  mechanics → E regime → F ranking → G concentration → H top-N).
- **Why it matters:** Without approved parameters the pipeline cannot run.
  Current paper trading uses legacy TSLA/Capitol Trades Routines, NOT this
  pipeline.
- **Decision needed:** Approve the pipeline shape + numeric thresholds,
  or ask Claude to propose simpler defaults.

### P-002 — Ranker scorer choice
- **Status:** TESTED, awaiting Controller decision
- **Findings (2026-10-03 backtests):**
  - Momentum scorer: **-0.99% to -1.66%** edge → fails
  - Mean reversion: **-0.27% to +1.19%** → fragile
  - Pullback-in-uptrend: **+0.40%** combined → weak
  - **Breakout: +0.99% combined** → best candidate
- **Decision needed:** Which scorer wires into Stage F of the Universe
  pipeline? Current production code still uses Momentum.

### P-003 — Legacy Routines vs new pipeline
- **Status:** CONFLICT not surfaced earlier
- **What it is:** Four live Routines (TSLA Monitor, TSLA Wheel Hourly,
  TSLA Wheel Daily, Capitol Trades Ro Khanna) run the OLD system; the
  new D-0026 + ranker + ladder pipeline is NOT yet connected to any
  Routine, so it does not execute on its own.
- **Decision needed:** Disable legacy Routines once the new pipeline is
  running? Or keep both running in parallel?

### P-004 — Backtest universe limitation
- **Status:** FACT the Controller should know
- **What it is:** All 2026-10-03 backtests used hardcoded 12-22 symbol
  universes because Polygon rate-limits prevent fetching 100+ symbols.
  Real production will use D-0026 dynamic universe (50-200 symbols).
- **Implication:** Backtest edge numbers (+0.99% etc) apply to a
  LIMITED universe. True production edge is unknown until live
  measurement.
- **Decision needed:** None (information). Factor this into any
  deployment confidence judgment.

---

## 🟡 OPEN — Can decide later

### P-005 — Position sizer wiring
- Built (B.15) but NOT wired to `TradeProposalService.start_trade`.
- Decision needed: Wire now or after ranker is validated.

### P-006 — Historical data gap
- Can't backtest fundamentals/news/political signals (58% of production
  ranker weight) because free historical sources don't exist.
- Options: pay for data, accept the gap, or build evidence from live
  trading only.

### P-007 — API key rotation
- `.env` keys were pasted in chat earlier.
- Per CLAUDE.md §8, this means they're compromised and need rotation.

---

## Communication protocol reminder

Per CLAUDE.md §11, "Before implementing, inspect the current code first,
re-check whether a previously approved design still matches it, flag any
drift, present the recommended design in Arabic, name any genuinely new
Controller decision explicitly, and wait for approval before writing code."

Claude failed to surface P-001 through P-003 proactively across multiple
Oct-02/Oct-03 sessions. This file now exists so that failure does not
repeat.
