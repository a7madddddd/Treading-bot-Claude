# Pending Controller Approvals — Live Tracker

**Rule for Claude:** At the start of every session, surface the items in
this file to the Controller. Do not wait to be asked. Items here BLOCK
the system from reaching its intended production state.

Per D-0052 (2026-10-05), this file is the authoritative "where did we
stop" board. Every open item has a `P-NNN` row. Closed items stay on the
board, marked RESOLVED with the date and the cause.

**Last full audit: 2026-10-05.** Audit method: live account Routine
listing, `git log` / branch sync check, SQLite state inspection of
`paper_session.sqlite` and `engine_lock.sqlite`, migration dry-run on a
DB copy, and a full test-suite run. Nothing in this file is from memory.

### Measured baseline, 2026-10-05

| Item | Measured value |
|---|---|
| Branch | `claude/youthful-goodall-4cr0ei`, in sync with origin (0 ahead / 0 behind), clean tree |
| Last commit | `4f63fda` — D-0051 percentage-based position sizing |
| Test suite | 1402 passed, 8 subtests passed |
| Engine process | NOT RUNNING |
| `engine_lock.sqlite` | stale row: pid 393, host `vm`, heartbeat 2026-10-02T12:49:33Z |
| Account Routines | 4 engine-heartbeat Routines exist, ALL `enabled: false` since 2026-10-02T14:46Z. No other Routine exists. |
| `paper_session.sqlite` | `PRAGMA user_version = 6`; code expects 7 |
| Trades | 14 rows; 5 filled at 10 shares; 2 (KO, V) never reconciled |
| Universe snapshots | 3 rows, newest `effective_trading_date = 2026-10-01` |

---

## 🚨 BLOCKING — Must decide before the engine runs again

### P-008 — No durable always-on host; the engine is DOWN (B17)
- **Status:** OPEN. This is the single most important open item.
- **FACT:** the engine is not running. It was last started 2026-10-03
  18:07 UTC with `--max-hours 24`, which is a hard self-termination cap,
  and it stopped itself at 2026-10-04 18:07 UTC. It was described to the
  Controller as "24/7"; that description was wrong.
- **FACT:** this project runs inside a cloud container that is reclaimed
  after a period of session inactivity. A background process started
  with `nohup` / `setsid` dies with the container. `--max-hours 168`
  does not change that — it only removes the self-imposed cap.
- **FACT:** `crontab` does not exist in this container, so the
  "daily Universe refresh cron" described in earlier sessions is not
  installed here and is not running.
- **FACT:** the four heartbeat Routines that were built to relaunch the
  engine after a reclaim are all disabled (see P-009).
- **Decision needed:** which host carries the engine.
  1. Re-enable the heartbeat Routines and keep running in this cloud
     container (cheapest, already built; relaunch gap up to ~15 min).
  2. A separate always-on VM with a `systemd` unit (true auto-restart
     across reboot and crash; needs a host the Controller provisions).
  3. Leave the engine down and run it only manually, session by session.

### P-009 — All four engine-heartbeat Routines are disabled
- **Status:** OPEN, unrecorded until now.
- **FACT:** `trig_018TPTdCj4mLWZm9p2ybmpTv` (:25),
  `trig_0195NJrc65vjTARZdL1iMWpx` (:55),
  `trig_019wvmLcrL2zwFjW9NtoYCHR` (:10),
  `trig_01DWE8TW1UM8u6AscZALA2mR` (:40) — schedule
  `CRON_TZ=America/New_York <min> 9-15 * * 1-5`, all `enabled: false`,
  all last updated 2026-10-02T14:46Z. No `ended_reason`, so they were
  paused manually, not auto-disabled.
- **Consequence:** nothing has been checking engine liveness since
  2026-10-02. This is why the 2026-10-04 stop went unnoticed.
- **Second problem in their prompts:** each one relaunches with
  `--symbols TSLA,GOOGL,QQQ`, a hardcoded three-symbol watchlist that
  predates the D-0048 dynamic Universe. Re-enabling them as-is would
  relaunch the engine on the OLD watchlist, not the Universe snapshot.
- **Decision needed:** re-enable them (and fix the hardcoded symbols
  first), or replace them with the host chosen in P-008.

### P-002 — Ranker scorer choice
- **Status:** TESTED, still awaiting Controller decision. Unchanged
  since 2026-10-03.
- **Findings (2026-10-03 backtests):**
  - Momentum scorer: **-0.99% to -1.66%** edge → fails
  - Mean reversion: **-0.27% to +1.19%** → fragile
  - Pullback-in-uptrend: **+0.40%** combined → weak
  - **Breakout: +0.99% combined** → best candidate
- **FACT:** production Stage F still uses Momentum — the scorer that
  tested negative.
- **Decision needed:** which scorer wires into Stage F.

### P-007 — API key rotation
- **Status:** OPEN since 2026-10-02. Not done.
- **FACT:** Alpaca and Telegram keys were pasted into chat. Per
  CLAUDE.md §8 they are compromised.
- **FACT:** there is no `.env` file in this container; the credentials
  arrive as environment variables, so rotation means updating the
  environment's secrets, not a file in the repo.
- **Decision needed:** rotate now, or accept the risk while the account
  is paper-only. Note that the Telegram bot token controls the approval
  channel — a leaked token is an approval-spoofing path, not just a
  data-read risk.

---

## 🟡 OPEN — Operational cleanup, decide soon

### P-010 — Two trades were never reconciled (KO, V)
- **Status:** OPEN, newly found in the 2026-10-05 audit.
- **FACT:** `KO-d7424af8` and `V-6d56fb92`, both created
  2026-10-01T15:29:00Z, have `initial_order_reconciled = 0` and
  `initial_filled_shares = NULL`. Their initial entry was never
  resolved to filled / cancelled / expired.
- **Why it matters:** per D-0009 the Ladder and Floor reference price is
  frozen only after the initial entry is reconciled. These two trades
  have no frozen reference, so no Ladder and no Floor can be computed
  for them. On the next engine start they will be picked up as
  unresolved and polled against Alpaca.
- **Decision needed:** let the engine reconcile them against Alpaca on
  next start (recommended — the broker is the source of truth), or
  mark them abandoned.

### P-011 — DB schema is one version behind the code
- **Status:** OPEN but LOW RISK — verified safe.
- **FACT:** `paper_session.sqlite` is at `user_version = 6`;
  `APPROVED_SCHEMA_VERSION` is 7. Migration
  `0007_proposal_initial_quantity.sql` has not run on the live DB.
- **FACT (verified, not assumed):** the migration was dry-run on a copy
  of the real DB on 2026-10-05. Result: `user_version` → 7,
  `initial_quantity` column present, all 14 existing proposal rows
  `NULL`, which is exactly the D-0051 backward-compat path that keeps
  pre-D-0051 trades on their original 10 / 10 / 20 counts.
- **Decision needed:** none. It migrates automatically on the next
  engine start. Recorded so nobody is surprised by the version jump.

### P-012 — Universe snapshot is stale
- **Status:** OPEN.
- **FACT:** newest snapshot has `effective_trading_date = 2026-10-01`.
  Today is 2026-10-05. In `--universe-mode snapshot` the engine finds no
  snapshot for the current trading date and trades nothing new.
- **FACT:** the snapshots that do exist are thin — the 2026-10-01
  snapshot holds 4 symbols (NVDA, KO, AMZN, V) with
  `score_summary: []`, `sector: null`,
  `confidence_status: not_calibrated`, and a rejection summary showing
  53 symbols dropped at Stage A tradability. The pipeline ran on a small
  candidate pool, which is the documented cause of thin output.
- **Decision needed:** who refreshes the snapshot and when, given that
  no cron exists in this container (ties to P-008).

### P-013 — `routines/README.md` documents Routines that no longer exist
- **Status:** OPEN, newly found. Documentation-only, no trading risk.
- **FACT:** the README lists four live legacy Routines. The live account
  listing on 2026-10-05 returns none of them. They were deleted at some
  point without a record.
- **Consequence for P-003:** the "legacy Routines vs new pipeline"
  conflict is gone — there is nothing legacy left running. See P-003.
- **Decision needed:** none. Claude should correct the README; it is a
  documentation fix, not a trading change.

### P-006 — Historical data gap
- **Status:** OPEN, unchanged.
- Fundamentals / news / political signals carry 58% of the production
  ranker weight and cannot be backtested, because free historical
  sources for them do not exist.
- Options: pay for data, accept the gap, or build evidence from live
  paper trading only.

### P-004 — Backtest universe limitation
- **Status:** FACT the Controller should know. Unchanged.
- All 2026-10-03 backtests used hardcoded 12–22 symbol universes
  because Polygon rate limits prevent fetching 100+ symbols. Production
  uses the D-0026 dynamic universe.
- **Implication:** the edge numbers in P-002 (+0.99% etc.) apply to a
  LIMITED universe. True production edge is unknown until measured live.
- **Decision needed:** none (information). Factor it into any
  deployment-confidence judgment.

---

## ✅ RESOLVED — kept for history

### P-001 — Dynamic Universe (D-0026) parameters — RESOLVED (D-0048, 2026-09-27)
- ✅ APPROVED via D-0048 (percentage-only parameters).
- 8-stage pipeline live with the approved percentile thresholds: top 30%
  by volume, top 40% by cap, ≤ 0.15% spread, ATR 1–5%, Stage F weights
  40 / 30 / 30, Stage G ≤ 30% per sector, Stage H Top-10.
- **Operational note:** the pipeline needs a LARGE candidate pool,
  because every stage is percentile-based. A tiny whitelist of 10–15
  symbols produces zero or near-zero survivors — expected, not a bug.
  See P-012 for the live evidence of this.

### P-003 — Legacy Routines vs new pipeline — RESOLVED (2026-10-05, by audit)
- The four legacy Routines (TSLA Monitor, TSLA Wheel Hourly, TSLA Wheel
  Daily, Capitol Trades Ro Khanna) no longer exist on the account.
- No parallel old-system execution is running. The conflict is closed.
- The remaining work is documentation only — see P-013.

### P-005 — Position sizer wiring — RESOLVED (D-0051, 2026-10-03)
- ✅ APPROVED and wired end-to-end as D-0051.
- The B.15 volatility-adjusted sizer is DELETED (never adopted). D-0051
  uses percentage-of-equity sizing: a 5% trade budget split 25% / 25% /
  50% across the three layers — a dollar-based policy, not a
  volatility-adjusted one.
- Code: `src/proposals/position_sizing.py`, wired through
  `src/engine/engine.py::_sized_strategy` into every production
  INITIAL_ENTRY / LADDER_1 / LADDER_2 / recovery call site.
- Backward compatibility: proposals created before D-0051 keep their
  original approved quantities; only new proposals use percentage
  sizing. Verified on the live DB — see P-011.

---

## Communication protocol reminder

Per CLAUDE.md §11: before implementing, inspect the current code first,
re-check whether a previously approved design still matches it, flag any
drift, present the recommended design in Arabic, name any genuinely new
Controller decision explicitly, and wait for approval before writing
code.

Per CLAUDE.md §12 / D-0052: this file is updated in the same session as
any change. A commit that leaves it stale is an incomplete commit.
