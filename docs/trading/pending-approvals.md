# Pending Controller Approvals — Live Tracker

**Rule for Claude:** At the start of every session, surface the items in
this file to the Controller. Do not wait to be asked. Items here BLOCK
the system from reaching its intended production state.

Per D-0052 (2026-10-05) this file is the authoritative "where did we
stop" board. Every open item has a `P-NNN` row. Closed items stay on the
board, marked RESOLVED with the date and the cause.

**Last full audit: 2026-10-05 (second pass, code-level).**

The first pass on 2026-10-05 was wrong on four points. The Controller
corrected them and they are recorded as RESOLVED below, not deleted:
the Oracle VM host exists and the engine runs on it; the Routines were
disabled deliberately because they were TSLA test-only; the API keys
were already rotated; and KO/V were never actually decided. This second
pass was done by reading the code and querying the live DB, with every
claim proven by a direct call — not by inspecting the cloud container I
happen to run in.

### Standing project rule the Controller restated on 2026-10-05

> Anything containing TSLA, or shaped like the TSLA Routines, is
> TEST-ONLY. Production works from the D-0026 / D-0048 dynamic Universe.
> The system of record is the latest code on the GitHub branch.

This rule is what makes P-014 below a defect rather than a design
choice: it was approved as a transition-period fallback under B22, and
the transition period is over.

---

## 🚨 BLOCKING — in priority order

### P-014 — Snapshot-mode TSLA fallback — RESOLVED (D-0054, 2026-10-05)
- **Status:** APPROVED by the Controller and IMPLEMENTED. Snapshot mode
  now passes `fallback_watchlist=None`, and an empty universe is
  REPORTED once per ET trading date instead of being silent. 10 new
  tests; suite 1402 -> 1412 PASS. See D-0054.
- The original finding is kept below for history.
- **FACT, proven by code read:** `scripts/run_paper_session.py:497`
  constructs the universe source as
  `SnapshotUniverseSource(snapshot_repo, fallback_watchlist=symbols)`
  where `symbols` comes from `--symbols`, whose default is
  `TSLA,AAPL,SPY`.
- **FACT:** `src/engine/snapshot_watchlist.py:48-53` — when no snapshot
  exists for today's ET date, or the snapshot is empty, the source
  returns the fallback if one was passed, and `()` only when
  `fallback_watchlist is None`.
- **FACT:** the repo's own checklist states the strict rule and that the
  runner deliberately breaks it:
  `docs/trading/pre-apply-checklist.md` row B22 — "Strict D-0026 §6
  (no-universe = no-trade) is honored when `fallback_watchlist=None`;
  the runner passes the CLI `--symbols` as fallback so a session started
  before any snapshot exists still behaves like before."
- **FACT:** the newest snapshot in the DB is for `2026-10-01`. Any run
  after that date in snapshot mode therefore uses the fallback.
- **Consequence:** the engine has been eligible to propose INITIAL_ENTRY
  on TSLA, AAPL and SPY on every trading day since 2026-10-02, under a
  flag named `--universe-mode snapshot`.
- **Decision needed:** make the strict rule real. Recommended: pass
  `fallback_watchlist=None` in snapshot mode, so no snapshot means no
  new trade, per D-0026 §6. This changes trading behavior, so it needs
  Controller approval and a decision entry.

### P-015 — Nothing refreshed the Universe — RESOLVED (D-0062, 2026-10-05)
- **Status:** IMPLEMENTED. `deploy/universe-refresh.timer` runs the
  D-0026 selection every weekday at 08:45 America/New_York, validated
  with `systemd-analyze calendar`. Installation on the VM is the
  Controller's step; see `deploy/README.md`.
- The original finding is kept below for history.
- **FACT:** the Engine only ever READS snapshots. Nothing in `src/engine`
  writes one. The only writer is `scripts/run_universe_selection.py`,
  which is a one-shot script that must be invoked.
- **FACT:** `src/engine/snapshot_watchlist.py:47` looks up the snapshot
  for **today's** ET date (`_current_effective_date_et`). A snapshot
  from a previous day is never reused. So a day without a refresh run is
  a day with no universe.
- **FACT:** the scheduler that was built to solve exactly this is not
  wired. `src/scheduler/daemon.py` (`SchedulerDaemon`, B29 / D-0023) is
  complete and tested, but its only production entry point,
  `scripts/run_scheduler.py`, registers ONE job and that job is a
  placeholder: `_real_slot()` prints
  `"D-0021 slot ... -- live routine not yet wired"` and does nothing.
  No universe-refresh job is registered anywhere.
- **Measured consequence:** newest snapshot `2026-10-01`; today is
  `2026-10-05`. Four trading days with no universe.
- **Decision needed:** how the daily refresh runs. Options:
  1. Register a universe-refresh `ScheduledJob` in
     `scripts/run_scheduler.py` and run that daemon under `systemd` on
     the Oracle VM (keeps the schedule inside the project, DST-safe,
     already tested).
  2. A `systemd` timer, or a cron entry, on the Oracle VM that invokes
     `scripts/run_universe_selection.py` once per trading morning.
  3. Have the engine itself refresh the snapshot at its first tick of a
     new trading date.
- **Note for whichever is chosen:** the refresh must land BEFORE the
  09:30 ET trigger, or the first tick of the day sees no universe.

### P-016 — Startup message always reported "no snapshot" — RESOLVED (D-0055, 2026-10-05)
- **Status:** FIXED. Now calls `get_latest_for_date` with the same ET
  effective-date helper the Engine uses, and a real lookup failure is
  printed instead of silently becoming "no snapshot". Verified against
  the live DB. See D-0055.
- The original finding is kept below for history.
- **FACT, proven by direct call:** `scripts/run_paper_session.py:648`
  calls `snapshot_repo.get_snapshot_for(now.date())`. That method does
  not exist on `SqliteSnapshotRepository`. Verified:
  `hasattr(SqliteSnapshotRepository, "get_snapshot_for")` is `False`;
  `hasattr(..., "get_latest_for_date")` is `True`; calling the wrong
  name raises
  `AttributeError: 'SqliteSnapshotRepository' object has no attribute 'get_snapshot_for'`.
- **FACT:** that call sits inside a bare `except Exception` which sets
  `today_snap = None`, so the branch ALWAYS takes the else path and
  always sends: "No universe snapshot for today yet — engine will pick
  it up on the next cycle once it is written."
- **Proof it is a lie, not a report:** querying the same DB with the
  correct method, `get_latest_for_date(date(2026,10,1))`, returns a
  snapshot. The message said there was none.
- **Second defect in the same block:** the message uses `now.date()`,
  which is a UTC date, while the watchlist source uses the ET date.
  Between 00:00 and 04:00 UTC the two disagree by one day, so even with
  the method name fixed the message could contradict the engine.
- **Origin:** introduced by Claude in commit `feb422a`. The surrounding
  comment claims the static fallback is "(unused)", which P-014 proves
  false.
- **Decision needed:** none. Pure bug fix, no trading-behavior change.

### P-017 — Engine had no supervisor — RESOLVED (D-0062, 2026-10-05)
- **Status:** IMPLEMENTED. `deploy/trading-engine.service` with
  `Restart=always`, `RestartSec=310`, `StartLimitIntervalSec=0`.
  Installation on the VM is the Controller's step.
- The original finding is kept below for history.
- **Host (confirmed):** Oracle Cloud VM, user `opc`, host
  `trading-bot-vnic`, project at `/home/opc/Treading-bot-Claude`,
  interpreter `python3.11`, credentials in a local `.env` file on the
  VM. The project is NOT on the Controller's Windows machine; Windows
  is only the SSH client.
- **FACT, from the VM log:** `[shutdown] max-hours cap (24.0h) reached`
  — the 2026-10-04 stop was the `--max-hours` cap, exactly as
  predicted, not a crash.
- **FACT:** the engine is currently launched by hand with
  `nohup ... & disown`, PID 159529, with
  `--universe-mode snapshot --max-hours 168 --enable-research --no-db-push --skip-confirm`.
- **Three ways that run ends with nothing restarting it:**
  1. the 168-hour cap expires (around 2026-10-12),
  2. the VM reboots — `nohup` does not survive a reboot,
  3. the process crashes.
- **FACT making the cap worse than a crash:** on cap expiry the process
  exits with status 0. A supervisor configured with
  `Restart=on-failure` would treat that as success and never restart
  it. Only `Restart=always` covers it.
- **FACT, relevant to any auto-restart:** `src/engine/lock.py` judges
  liveness ONLY by `heartbeat_at`, with
  `STALE_THRESHOLD_SECONDS = 300.0`. On a clean stop the lock IS
  released (`Engine.shutdown()` runs in the runner's `finally`), but
  after a hard kill a restart within 5 minutes hits
  `EngineLockHeldError` and exits. A supervisor therefore needs
  `RestartSec` greater than 300, or systemd's start-rate limit
  disabled, or it will give up and park the unit in a failed state.
- **FACT:** `--max-hours` has no "unlimited" value — it is
  `type=float, default=6.0` and the loop always builds a deadline from
  it.
- **Decision needed:** install a supervisor. Recommended: a `systemd`
  service with `Restart=always`, `RestartSec=310`,
  `StartLimitIntervalSec=0`, a very large `--max-hours`, and a wrapper
  script that sources `.env` before exec'ing python. Pairs with P-015,
  whose refresh job wants a `systemd` timer on the same VM.

### P-014 note — RESOLVED (2026-10-05): the fallback is gone from the VM, confirmed by the engine's own startup message
- The command above passes `--universe-mode snapshot` and NO
  `--symbols`, so `symbols` resolves to the default `TSLA,AAPL,SPY`
  and becomes the fallback watchlist per P-014.
- **UNKNOWN:** whether the VM's own `paper_session.sqlite` holds a
  snapshot for the current trading date. Since 2026-10-03 the VM runs
  with `--no-db-push`, so its DB is not the one in this repo and cannot
  be inspected from here. This must be checked on the VM before the
  next session open.

### P-002 — Ranker scorer choice — DEFERRED (D-0057, 2026-10-05)
- **Status:** Controller decided to DEFER, not to pick. Production
  keeps D-0048's Stage F unchanged. Re-opened for decision as P-026
  once live measurement exists. Claude's ranked recommendation for
  that revisit is recorded in D-0057 and must not be re-derived from
  scratch.
- Two audit findings drove the deferral: momentum is a GATE in Stage D
  (`min_trend_percentile = 0.50`) before it is a weight in Stage F, so
  swapping the scorer alone cannot change which symbols survive; and
  every measured number came from 12–22 symbol universes that do not
  represent the real snapshot.
- The original finding is kept below for history.
- **Findings (2026-10-03 backtests):**
  - Momentum: **-0.99% to -1.66%** → fails
  - Mean reversion: **-0.27% to +1.19%** → fragile
  - Pullback-in-uptrend: **+0.40%** → weak
  - **Breakout: +0.99%** → best candidate
- **FACT:** production Stage F still uses Momentum, the scorer that
  tested negative. Read P-004 before weighting these numbers.
- **Decision needed:** which scorer wires into Stage F.

---

## 🟡 OPEN — smaller, still real

### P-018 — No US market holiday calendar — RESOLVED (D-0060, 2026-10-05)
- **Status:** SOLVED, and not with a calendar. The engine now asks the
  BROKER per tick (`is_market_open()` reading `/v2/clock`) before
  creating any new proposal, failing closed on error. That handles
  holidays, half-days and unscheduled closures alike and needs no
  yearly maintenance. 8 tests. See D-0060.
- The original finding is kept below for history.
- **FACT:** `src/engine/schedule.py:60` states it in its own docstring —
  `is_d0021_check_time` "does NOT account for US market holidays
  (D-0006) -- a known, explicitly-flagged gap". `src/scheduler/daemon.py:21`
  flags the same gap. A repo-wide search for "holiday" finds no
  calendar implementation.
- **FACT:** the engine's main loop does not gate ticks on market-open
  state. It calls `/v2/clock` once during preflight only.
- **Consequence:** on a weekday holiday the 09:30 ET tick fires, and a
  scheduled universe refresh would run against stale bars.
- **Decision needed:** add a holiday calendar, or gate the loop on
  Alpaca `/v2/clock` `is_open` per tick. The second is cheaper and uses
  the broker as the source of truth.

### P-010 — KO and V were never decided — CORRECTED
- **Status:** OPEN, awaiting one Controller click each.
- **FACT, proven by DB query:** both proposals are
  `approval_state = 'pending'`, with `approval_received_at`,
  `decided_by`, `approved_action` and `expired_at` all NULL.
  - `KO-d7424af8-initial_entry-42839e85`
  - `V-6d56fb92-initial_entry-ca9af192`
- So they were **not** rejected. The Controller's recollection was
  right to be uncertain. Account-wide state: 5 approved, 7 rejected,
  2 pending.
- **FACT:** both trades read `AWAITING_INITIAL_FILL` via
  `describe_status`, so `src/engine/engine.py:1121-1123` excludes those
  two symbols from any new proposal while they stay in that state.
- **FACT:** they are not permanently stuck. `Engine.recover()` →
  `_recover_trade` (`engine.py:447-467`) re-sends a PENDING proposal
  with fresh Approve / Reject buttons on every engine start.
- **RISK if approved now:** the proposals carry 2026-10-01 prices.
  D-0007 revalidation (`src/proposals/revalidation.py`) blocks a
  submission when `|current - trigger| / trigger > 0.005`, so a
  four-day-old proposal will almost certainly be refused, then
  abandoned, releasing the symbol.
- **RECOMMENDATION:** reject both. It reaches the same end state
  deterministically instead of relying on a drift refusal.
- **Related structural gap:** there is **no** time-based expiry for a
  PENDING proposal anywhere in the code. A pending proposal is only
  ever superseded when a new proposal for the same trade and action is
  saved (`src/proposals/repository.py:243`). A pending proposal can
  therefore sit for an unbounded time and keep its symbol locked.
  Worth a decision on a PENDING TTL.

### P-019 — Recovery path symbol lockout — RESOLVED (2026-10-05)
- **Status:** FIXED. `_recover_trade` now passes
  `initial_entry_trade_id`, matching the other two INITIAL_ENTRY call
  sites. 4 tests, including a structural guard asserting that exactly
  three call sites pass it, so a new one cannot silently reintroduce
  the lockout.
- The original finding is kept below for history.
- **Status:** OPEN, low.
- **FACT:** `src/engine/engine.py:490` submits an INITIAL_ENTRY from the
  startup recovery path WITHOUT passing `initial_entry_trade_id`. The
  equivalent call sites at `engine.py:632` and `engine.py:1026` both
  pass it.
- **Why it matters:** `_submit_approved`'s own docstring
  (`engine.py:1478-1487`) says that argument is what abandons the Trade
  on a terminal pre-fill refusal, "without this, the Trade stays in
  AWAITING_INITIAL_FILL forever and _check_watchlist's has_open_trade
  guard permanently locks the symbol out".
- **Why it is low:** `_recover_approved_without_execution`
  (`engine.py:582`) runs on EVERY reconciliation tick, covers the same
  condition, and does pass the argument — so the gap self-heals within
  one tick, about 30 seconds at the default `--reconcile-seconds`.
- **Decision needed:** none. Consistency fix, no behavior change.

### P-020 — Snapshot audit trail is thin — CORRECTED, not a defect
- **Status:** OPEN as a known limitation, NOT a bug. Claude's first
  wording called it a defect; that was wrong.
- **FACT:** `score_summary: []` is BY DESIGN.
  `src/d0026/ranking.py` keeps `RANKING_METRIC_DEFINITIONS = ()`
  deliberately empty, and `compute_ranking_score_summary` returns `()`
  unconditionally with that registry, raising `NotImplementedError` if
  a non-empty one is ever supplied — "D-0026 CALIBRATION = BLOCKED".
  Declaring any metric there requires its own Controller decision.
- **FACT:** `sector: null` is also BY DESIGN and hardcoded at
  `src/d0026/snapshot.py:275`, whose docstring states: "``sector`` is
  not sourced anywhere in the current Phase A models and is left
  ``None`` — not guessed."
- **FACT:** a sector provider IS wired into the enricher
  (`scripts/run_universe_selection.py:209`), so Stage G can enforce its
  ≤ 30% sector cap from the candidate's `source_reference`. Only the
  snapshot's own audit field is left None.
- **Real remaining consequence:** a published snapshot cannot be
  audited after the fact — the Stage F ordering that chose its symbols
  is not reconstructible from the stored row, and the sector cap that
  Stage G applied is not visible in it. The ordering itself is carried
  only as the ORDER of survivors, per the comment in
  `src/d0026/stages/ranking.py`.
- **Decision needed:** none now. It becomes relevant when P-002 is
  decided, because a calibrated Stage F is exactly what unlocks a
  populated `score_summary`.

### P-011 — DB schema version — RESOLVED (2026-10-05, by VM evidence)
- **Status:** CLOSED. The VM reported `schema version: 7`, so migration
  0007 had already applied there. The repo copy was the stale one.
- The original finding is kept below for history.
- **FACT:** the repo's `paper_session.sqlite` is at
  `PRAGMA user_version = 6`; `APPROVED_SCHEMA_VERSION` is 7.
- **FACT, proven by dry run on a copy of the real DB:** after
  `bootstrap_schema`, `user_version` → 7, the `initial_quantity` column
  exists, and all 14 existing proposal rows are NULL — exactly the
  D-0051 backward-compat path that keeps pre-D-0051 trades on their
  original 10 / 10 / 20 counts.
- **CAVEAT:** this was verified on the DB in the repo. If the Oracle VM
  carries its own DB file, that one has not been checked.

### P-006 — Historical data gap
- Fundamentals / news / political signals carry 58% of the production
  ranker weight and cannot be backtested, because free historical
  sources for them do not exist.
- Options: pay for data, accept the gap, or build evidence from live
  paper trading only.

### P-004 — Backtest universe limitation
- All 2026-10-03 backtests used hardcoded 12–22 symbol universes
  because Polygon rate limits prevent fetching 100+ symbols.
- **Implication:** the P-002 edge numbers apply to a LIMITED universe.
  True production edge is unknown until measured live.
- **Decision needed:** none (information).

---

## ✅ RESOLVED — kept for history

### P-001 — Dynamic Universe (D-0026) parameters — RESOLVED (D-0048, 2026-09-27)
- APPROVED via D-0048 (percentage-only parameters). 8-stage pipeline
  live: top 30% by volume, top 40% by cap, ≤ 0.15% spread, ATR 1–5%,
  Stage F weights 40 / 30 / 30, Stage G ≤ 30% per sector, Stage H Top-10.
- **Operational note:** the pipeline needs a LARGE candidate pool,
  because every stage is percentile-based. P-020 holds the live
  evidence of a small pool's output.

### P-003 / P-013 — Legacy Routines — RESOLVED (Controller, 2026-10-05)
- The four legacy Routines (TSLA Monitor, TSLA Wheel Hourly, TSLA Wheel
  Daily, Capitol Trades Ro Khanna) were TEST-ONLY and the Controller
  disabled them deliberately because they are not used any more.
- The live account listing confirms none of them exists.
- There is no conflict and nothing to re-enable. The four heartbeat
  Routines are also intentionally off.
- **Remaining:** `routines/README.md` still describes them as live.
  Documentation correction only, no trading change.

### P-005 — Position sizer wiring — RESOLVED (D-0051, 2026-10-03)
- APPROVED and wired end-to-end. 5% trade budget split 25% / 25% / 50%
  across the three layers, via
  `src/proposals/position_sizing.py` and
  `src/engine/engine.py::_sized_strategy`.
- Backward compatibility verified on the live DB — see P-011.

### P-007 — API key rotation — RESOLVED (Controller, 2026-10-05)
- The Controller rotated / replaced the keys before this session.

### P-008 — Durable always-on host (B17) — RESOLVED (Controller, 2026-10-05)
- The Oracle Cloud VM is provisioned, the system is hosted on it, and
  the engine runs there.
- Claude's 2026-10-05 first-pass claim that "there is no host" was
  wrong: it described the ephemeral cloud container Claude itself runs
  in, not the Controller's VM. The container is a development
  workspace, never the trading host.
- The genuinely open parts were split out into P-015 (nothing refreshes
  the Universe) and P-017 (confirm the VM's launch command and restart
  policy).

### P-009 — Heartbeat Routines disabled — RESOLVED (Controller, 2026-10-05)
- Disabled deliberately. They were built to keep the ephemeral cloud
  container warm, and they relaunched on a hardcoded `TSLA,GOOGL,QQQ`
  watchlist. Both reasons are obsolete now that the VM is the host and
  the Universe is the watchlist.
- Nothing to re-enable. Any liveness supervision belongs on the VM —
  see P-017.

### P-012 — Stale Universe snapshot — SUPERSEDED by P-015 (2026-10-05)
- The stale snapshot is a symptom. The cause is that no scheduled job
  writes one. Tracked as P-015.

---

## Communication protocol reminder

Per CLAUDE.md §11: before implementing, inspect the current code first,
re-check whether a previously approved design still matches it, flag any
drift, present the recommended design in Arabic, name any genuinely new
Controller decision explicitly, and wait for approval before writing
code.

Per CLAUDE.md §12 / D-0052: this file is updated in the same session as
any change. A commit that leaves it stale is an incomplete commit.

---

## 🚨 ADDED 2026-10-05 (second VM audit) — highest priority

### P-021 — Leveraged and inverse products in the Universe — RESOLVED (D-0056, 2026-10-05)
- **Status:** APPROVED by the Controller and IMPLEMENTED. Excluded at
  the provider, before any pipeline stage. Filter defaults to ON.
  29 new tests; suite 1412 -> 1440 PASS. See D-0056.
- The ordinary-fund question stays open as P-024.
- The original finding is kept below for history.
- **FACT, from the Controller's own VM, snapshot `2026-10-03T16:18:56`,
  the newest snapshot that exists:**
  `WBD, MUFG, DXD, VOD, MAGS, QQQI, PFE, ILF, CGGR, BCI`
- Of those ten, only three are ordinary operating companies
  (`WBD`, `MUFG`, `VOD`, plus `PFE`). The rest are funds:
  - `DXD` — ProShares UltraShort QQQ: a **−2× LEVERAGED INVERSE** ETF
  - `MAGS` — Magnificent-7 ETF
  - `QQQI` — Nasdaq covered-call income ETF
  - `ILF` — Latin America 40 ETF
  - `CGGR` — Capital Group Growth ETF
  - `BCI` — broad commodity ETF
- **FACT, root cause:** `src/d0026/alpaca_provider.py:94` requests
  `/v2/assets?status=active&asset_class=us_equity`. Alpaca classifies
  ETFs, leveraged ETFs and inverse ETFs as `us_equity` — there is no
  separate asset class for them. A repo-wide search for an ETF,
  leverage, inverse, or asset-type filter in `src/d0026/` finds
  **nothing**. No stage of the 8-stage pipeline excludes them.

#### Why `DXD` specifically is a loss generator under the approved ladder

`DXD` rises when the market falls and falls when the market rises. The
approved ladder (D-0004) BUYS MORE as price falls. So during a normal
market rally the engine would average down into a leveraged bet against
that rally — the worst possible direction for this strategy.

Worked example, the ordinary case rather than a tail case. Suppose
`QQQ` gains 5% over one week:

| Step | `DXD` move | Ladder action |
|---|---|---|
| index +2.5% | ≈ −5% | Ladder 1 fires, position doubles |
| index +4% | ≈ −8% | Ladder 2 fires, position at maximum |
| index +5% | ≈ −10% | Floor fires, SELL ALL |

The entire three-layer ladder — designed to be worked over a multi-day
pullback — compresses into days, on a **rising** market, which is the
market's normal state. Under D-0051 that is the full 5%-of-equity trade
budget taken to its floor: on $100,000 equity, about **$500 lost**
per such position, with the ladder having tripled the exposure on the
way down.

Two further structural problems, independent of direction:
1. **Volatility decay.** A −2× fund rebalances daily. Over weeks it
   loses value even if the index ends flat. The ladder holds positions
   for days to weeks, so time works against the position on top of
   direction.
2. **2× amplification breaks the trigger spacing.** The −5% / −8% /
   −10% levels were chosen for a normal equity's daily range. A −2×
   fund covers all three on a 5% index move, so the levels no longer
   represent three distinct decision points.

- **RECOMMENDATION (changes trading behavior — needs Controller
  approval):** add an instrument-eligibility filter ahead of Stage A
  that rejects, at minimum, leveraged and inverse products. The
  cheapest reliable signal available for free is the asset's own name
  text from `/v2/assets` (`2X`, `3X`, `ULTRA`, `ULTRASHORT`,
  `INVERSE`, `BEAR`, `SHORT`), combined with an explicit decision on
  whether ordinary ETFs are eligible at all.
- **CONTROLLER DECISION, 2026-10-05 — PARTIALLY DECIDED:** the
  Controller approved excluding leveraged and inverse products NOW
  ("we need to exclude the DXD from our universe search because this
  will kill the strategy"), and deferred the broader
  "should we trade ordinary ETFs at all" question to a separate study
  ("we didn't want to kill the strategy before we study that thing").
  So the interim rule is option 3 below — everything except leveraged
  and inverse — and option 1 versus option 2 becomes P-024.
- **Original framing of the deferred question:** should the Universe
  trade ETFs at all? The approved strategy's research layer scores
  fundamentals (P/E, earnings) — fields that do not exist for a fund.
  An ETF therefore scores on a research model built for companies.
  Three options:
  1. Equities only. Simplest, and matches what the research layer can
     actually evaluate.
  2. Equities plus plain index ETFs, with leveraged and inverse
     excluded.
  3. Everything except leveraged and inverse.
- Claude's recommendation is **option 1** for now, moving to option 2
  later with a measured reason, because every component of the research
  layer except price and volume is undefined for a fund.

### P-022 — VM behind the branch head — RESOLVED (2026-10-05)
- **Status:** CLOSED. The Controller pulled and restarted; the VM ran
  the full suite itself (1473 passed, 45 subtests) and now runs under
  systemd user units.
- The original finding is kept below for history.
- **FACT:** the VM reports `327145b`. The branch head is `07e8e23`.
  The VM therefore does NOT have D-0054 (fallback removal) or D-0055
  (startup-message fix).
- **FACT:** the VM's running command is
  `--universe-mode snapshot` with no `--symbols`, so under the OLD code
  the fallback is `TSLA,AAPL,SPY`.
- **FACT:** the VM has no snapshot for 2026-10-05, and its newest is
  2026-10-03.
- **FACT:** on the VM, `AAPL` and `SPY` both have
  `initial_order_reconciled=1, initial_filled_shares=0`, i.e. status
  ABANDONED, and `_check_watchlist` excludes only AWAITING_INITIAL_FILL
  and ACTIVE. ABANDONED symbols are therefore eligible candidates
  again.
- **Consequence:** at the 09:30 ET tick on Monday 2026-10-06, the
  running engine can propose INITIAL_ENTRY on `AAPL` and `SPY` —
  TEST-ONLY symbols — because it has not got the fix.
- **Action required (Controller):** pull and restart on the VM before
  Monday's open.

### P-023 — SQLite write contention — RESOLVED (D-0062, 2026-10-05)
- **Status:** ADDRESSED. `BUSY_TIMEOUT_MS = 30000` in
  `src/persistence/db.py`, re-verified with the same two-process
  measurement: the case that failed at 5.01s now waits 10.05s and
  succeeds. Insurance, not a fix for an observed failure.
- The original finding is kept below for history.
- **FACT:** `src/persistence/db.py::connect` sets no journal mode and
  no busy timeout, so the effective settings are
  `journal_mode = delete` and `busy_timeout = 5000` ms (both read back
  directly from a live connection).
- **FACT, measured with two real processes, not assumed:**

| journal mode | busy timeout | engine READ during a held write | engine WRITE during a held write |
|---|---|---|---|
| delete | 5000 ms | ok (0.00 s) | FAILS after 5.01 s |
| WAL | 5000 ms | ok (0.00 s) | FAILS after 5.01 s |
| WAL | 30000 ms | ok (0.00 s) | ok (waited 10.05 s) |

- **Important correction to the obvious answer:** WAL does NOT fix
  this. WAL separates readers from writers, and reads were never
  blocked. The failing case is writer-versus-writer, and only a larger
  `busy_timeout` fixes it.
- **Why the real risk is nonetheless LOW:**
  `SqliteSnapshotRepository.save` holds its write transaction for a
  SINGLE one-row `INSERT`. All the slow work — Alpaca fetches,
  enrichment, the eight stages — happens outside any transaction. Both
  sides therefore hold write locks for milliseconds, well inside the
  existing 5-second timeout.
- **RECOMMENDATION:** raise `busy_timeout` to 30000 ms as cheap
  insurance when the daily run is wired. Labelled insurance, not a fix
  for an observed failure — no `database is locked` error has actually
  been seen in this project.

### P-010 — KO and V — CLOSED (2026-10-05, by VM evidence)
- No action is needed, and the earlier plan to reject them is moot.
- **FACT, from the VM:** `pending proposals:` is EMPTY, and both `KO`
  and `V` show `initial_order_reconciled=1, initial_filled_shares=0`,
  i.e. status ABANDONED. They resolved themselves to zero fills.
- The two PENDING rows Claude reported earlier exist only in the
  repo's own stale copy of the DB. Since 2026-10-03 the VM runs with
  `--no-db-push`, so the repo DB and the VM DB have diverged and the
  repo copy is no longer evidence about production.
- **Lesson recorded:** the repo's `paper_session.sqlite` must not be
  treated as production state while the VM runs with `--no-db-push`.


### P-024 — Should the Universe trade ordinary ETFs at all? (deferred study)
- **Status:** OPEN, deliberately deferred by the Controller on
  2026-10-05 pending study. P-021's leveraged/inverse exclusion is NOT
  waiting on this — it proceeds now.
- **The question:** `MAGS`, `QQQI`, `ILF`, `CGGR`, `BCI` are ordinary
  (non-leveraged, non-inverse) funds that the live pipeline selected.
  They are not dangerous the way `DXD` is. But the research layer
  scores `fundamentals` out of 16 points from P/E and earnings, fields
  that do not exist for a fund, so every fund is scored by a model
  built for operating companies and silently loses those points.
- **What must be studied before deciding:**
  1. How many points does a fund structurally forfeit in the current
     scoring, and does that already exclude them in practice?
  2. Do funds mean-revert on the 5–10% scale the ladder needs, or do
     they trend more smoothly than single stocks (which would mean the
     ladder rarely adds and the strategy degrades to a plain buy)?
  3. Is a fund's lower volatility an advantage (fewer floor hits) or a
     disadvantage (ladder never triggers)?
- **Decision needed later:** equities only, or equities plus plain
  index funds with a fund-aware scoring path.

#### P-024 — measurement result (2026-10-05, run on the VM)
- **FACT, how it was measured.** `scripts/measure_atr_distribution.py`
  (read-only: no snapshot, no DB write, no Telegram) classifies each
  symbol as `stock` / `fund` / `unknown` from the broker's asset NAME,
  because `/v2/assets` returns `class="us_equity"` for an operating
  company and an ETF alike — there is no structural field separating
  them. `unknown` is reported, never silently folded into either side.
- **FACT, two independent samples agree:**

| sample | stock mean ATR | stock in band | fund mean ATR | fund in band |
|---|---|---|---|---|
| 200 symbols (Claude container) | 3.48% | 54.3% | 1.01% | 5.6% |
| 300 symbols (VM, `opc`) | 3.75% | 50.0% | 0.83% | 6.2% |

  VM classification counts: stock 92 (35.0%), fund 64 (24.3%),
  unknown 107 (40.7%).
- **FACT, what this answers.** Study question 1 of P-024 ("does the
  scoring already exclude funds in practice?") is answered by a
  different and stronger mechanism than scoring: **D-0065's 2%–4% ATR
  band excludes 93.8% of funds at Stage D**, before the research layer
  scores anything. A fund's mean ATR of 0.83% is less than half the
  2% floor. The 16-point `fundamentals` forfeit (ceiling 84 against a
  60 threshold) is therefore a second line of defence, not the first.
- **FACT, what this does NOT answer.** Study questions 2 and 3 — ladder
  behavior on funds — remain unmeasured, and cannot be measured from a
  distribution table. They need filled trades.
- **RECOMMENDATION: do NOT add a separate ETF rule.** Reason: a new
  filter would duplicate, with a hand-picked constant, an exclusion
  that an existing measured parameter already performs on 93.8% of
  cases. The ~6% of funds that do pass are precisely the volatile ones
  the ladder is built for, so excluding them by instrument type rather
  than by behavior would be a category rule standing in for a risk
  rule.
- **What would change the recommendation:** a real 06:00 ET snapshot in
  which a fund reaches the Top-10. That is the only evidence that moves
  this from theory to practice, and it costs nothing to wait for.
- **Status:** OPEN — awaiting Controller decision (close with "no
  separate ETF rule", or keep open until a fund appears in a real
  Top-10).

### P-025 — ATR band — RESOLVED (D-0065, 2026-10-05): narrowed to 2%–4% on measured evidence
- **Status:** OPEN, newly surfaced 2026-10-05 under CLAUDE.md §0.d
  (re-challenge an approved decision when evidence demands). Raised
  by Claude, not asked for.
- **FACT, approved parameters (D-0048):**
  `min_atr_fraction = 0.01`, `max_atr_fraction = 0.05` — a symbol
  qualifies if its average daily true range is between 1% and 5% of
  price.
- **FACT, approved ladder (D-0004):** add at −5%, add at −8%, exit at
  −10%, measured from the frozen initial fill price.
- **The problem:** those two approved decisions interact, and nothing
  reconciles them. How fast the ladder walks depends entirely on where
  in the band a symbol sits:

| ATR | days of average adverse move to −5% | to −8% | to −10% |
|---|---|---|---|
| 1% | ≈ 5 | ≈ 8 | ≈ 10 |
| 5% | ≈ 1 | ≈ 1.6 | ≈ 2 |

- **What it means in practice.** At the bottom of the band the ladder
  almost never fires and the Floor is effectively unreachable, so the
  strategy degrades into a plain single buy with no laddering at all.
  At the top of the band all three levels are routinely crossed inside
  one week, so the full three-layer position is built and then stopped
  out — the Floor becomes the normal outcome rather than the
  last-resort exit `strategy.md` §1 calls it.
- **Why this is the same class of error as D-0051.** D-0051 fixed a
  60× spread in DOLLAR exposure caused by a fixed share count meeting
  a wide price range. This is a 5× spread in TIME-to-trigger caused by
  fixed percentage levels meeting a wide volatility range. Same shape:
  one approved constant meeting another approved range, with no rule
  connecting them.
- **Options (none implemented; all change trading behavior):**
  1. Narrow the ATR band, e.g. 2%–4%, so the ladder's pace is
     comparable across symbols. Cheapest, fully reversible, shrinks
     the candidate pool.
  2. Scale the ladder levels by the symbol's own ATR, e.g. trigger at
     −1.5 × ATR and −2.5 × ATR instead of fixed −5% / −8%. Most
     correct in principle, but it changes the approved strategy's core
     numbers and needs its own decision and backtest.
  3. Leave it and MEASURE first — record each filled trade's ATR at
     entry alongside its outcome, then decide with real data.
- **Claude's RECOMMENDATION: option 3 now, then option 1.** Reason:
  this is a hypothesis about pace, not an observed loss. The project
  has no live measurement yet, and changing two approved numbers on
  reasoning alone is exactly what D-0052 and §0.d were written to
  prevent in the other direction. Recording ATR-at-entry costs one
  column and no behavior change, and it makes options 1 and 2
  decidable with evidence within a few weeks of real trading.
- **What would change the recommendation:** if the first live
  measurements show Floor exits clustering on high-ATR names, option 1
  becomes urgent rather than optional.


### P-026 — Re-decide the Stage F scorer, with real measurements
- **Status:** OPEN, scheduled not blocked. Deferred by D-0057.
- **Entry conditions — all three must hold before this is decidable:**
  1. the daily Universe run is live, so a real snapshot exists each
     trading day;
  2. 2–4 weeks of live proposals and their outcomes are recorded;
  3. each filled trade carries its selection context (its rank, its
     score breakdown, and — per P-025 — its ATR at entry).
- **Claude's recommendation, already argued in D-0057, ranked:**
  1. Trend Filter + Dip Ranking, 2. Breakout, 3. Relax the Stage D
  momentum gate, 4. Pullback-in-Uptrend, 5. Quality + Liquidity only,
  6. Mean Reversion, 7. Momentum (current production, ranked last).
- **The real lever, easy to miss:** Stage D's momentum gate, not Stage
  F's weight. Stage D discards the bottom half by 30-day return before
  any scorer runs, and a genuine Breakout candidate has a weak 30-day
  return by construction — so it is rejected before it can ever be
  scored. Any revisit that touches only Stage F will appear to change
  nothing.
- **What would overturn the recommendation:** live results showing the
  current Momentum scorer positive on the real universe.

### P-027 — Political picks — RESOLVED (D-0061, 2026-10-05)
- **Status:** IMPLEMENTED, with one correction to the approved plan.
  D-0058 said to MERGE political symbols into the pool before the
  pipeline; reading the code showed that was both unnecessary (they are
  already in the whole-market pool) and unsafe (injected symbols would
  bypass D-0056's leveraged/inverse filter). The real fix was to DELETE
  the post-pipeline UNION. Controller approved the correction before
  any code was written. 14 tests. See D-0061.
- Three measures: one of the 3 proposals per cycle reserved for the
  best qualifying political pick (reverting to normal ranking if none
  qualifies); a visually distinct Telegram tag carrying politician
  names and trade dates; and a daily report of what the tracked
  politicians bought, including symbols that did not become proposals.
- `weight_political = 15.0` stays UNCHANGED. Raising it to 30 was
  rejected because it would make attribution impossible — the
  political component is computed for every candidate, so after
  blending, no proposal could be traced to the political signal.
- Political symbols also move INSIDE the pipeline rather than being
  UNIONed in after it, so they face every safety stage. This closes
  the bypass where a congressman's illiquid small-cap with a 2% spread
  reached a proposal with no spread check at all — roughly 6% lost to
  spread across the ladder's three buys, against a −10% floor.
- **Deliberately sequential:** the weight question is revisited after a
  month of comparing the reserved slot's outcomes against the other
  two slots, with the Controller's own numbers.


---

## 2026-10-05, end of session — DEPLOYMENT VERIFIED LIVE

First time the system is genuinely supervised. Verified on the
Controller's VM, not inferred:

| check | result |
|---|---|
| test suite ON THE VM | 1473 passed, 45 subtests |
| `systemctl --user status trading-engine` | `active (running)`, PID 202308 |
| heartbeat age | 28.1s (reconcile interval is 30s) |
| `Linger` | `yes` — survives logout and reboot |
| timer next elapse | `Tue 2026-10-06 12:45 GMT` = 08:45 EDT |

### P-028 — RESOLVED (2026-10-05): the band was decided (D-0065) before the first autonomous day
- **Status:** OPEN. A decision is needed BEFORE 08:45 ET tomorrow.
- **FACT:** the timer fires at 08:45 America/New_York on 2026-10-06 and
  writes the first automatic snapshot. The engine then has a universe
  for the first time since 2026-10-03, so the 09:30 ET trigger can
  produce real proposals.
- **FACT:** P-025 (the ATR band) is still undecided. The approved band
  is 1%–5%, so tomorrow's first real proposals can come from either
  extreme of the tree — a symbol whose ladder never fires, or one that
  walks Ladder 1, Ladder 2 and the Floor inside two days.
- **FACT:** P-021 is fixed, so no leveraged or inverse product can be
  selected. That danger is closed.
- **Controller's stated intent:** study the ATR band, the scorer and
  the DXD question before trading resumes.
- **Options:**
  1. Disable the timer until the ATR band is decided
     (`systemctl --user disable --now universe-refresh.timer`). Nothing
     trades; the engine keeps monitoring and keeps reporting.
  2. Let it run tomorrow with the current 1%–5% band and treat the
     first days as the measurement P-025 asks for, recording ATR at
     entry.
  3. Decide the band tonight from the measurement tool, then let the
     timer run.
- **Claude's recommendation: option 3, falling back to option 1.** The
  measurement tool is read-only and quick; deciding on real numbers
  before the first autonomous day is strictly better than deciding
  after. If there is no time for it, option 1 costs one trading day and
  keeps the decision clean — the engine is currently trading nothing
  anyway, so nothing is lost that is not already lost.

### P-029 — RESOLVED (D-0066, 2026-10-05): timer moved to 06:00 ET, ~3.5h of headroom. Measuring the real throughput remains a nice-to-have, not a blocker.
- **Status:** OPEN and TIME-CRITICAL. The timer fires tomorrow 08:45 ET.
- **FACT, measured on the VM 2026-10-05:** the broker returns **12,591**
  tradable symbols. D-0056 excludes **908** as leveraged or inverse
  (7.2% of the market), leaving **11,683** to enrich.
- **FACT:** enrichment is ONE bars request per symbol
  (`AlpacaFeatureEnricher._fetch_bars`). There is no batch path.
- **Arithmetic:**

| requests/min | full-run duration |
|---|---|
| 200 | ~58 min |
| 300 | ~39 min |
| 500 | ~23 min |

  Alpaca's free tier is commonly 200/min. Starting at 08:45 ET that run
  finishes around **09:43 — after the 09:30 open**, so the first D-0021
  trigger of the day would still find no snapshot.
- **Why this was not visible before:** the 2026-10-03 snapshot processed
  only 77 candidates (rejection summary A:53, C:10, D:4, plus 10
  survivors), because that run was whitelisted or capped. The timer runs
  **un-whitelisted** on purpose — every pipeline stage is
  percentile-based and needs the full pool — so it is the first run to
  face the real symbol count.
- **An architectural note, not a bug:** Stage A's volume percentile
  cannot pre-filter before enrichment, because the volume it ranks on is
  itself a feature that enrichment produces. So the cost cannot be
  avoided by reordering stages.
- **Options:**
  1. Move the timer earlier — 06:00 ET gives 3.5 hours of headroom and
     costs nothing. Smallest change, fixes the stated risk.
  2. Measure the real throughput first (time one capped run), then pick
     the time from the measured rate instead of an assumed limit.
  3. Cap the run with `--max-symbols`. Rejected on its own: an
     arbitrary alphabetical slice of the market is not a universe, and
     it silently reintroduces the small-pool problem P-001 warns about.
- **Claude's RECOMMENDATION: 1 and 2 together.** Move the timer to
  06:00 ET now, because it is free and removes the deadline risk
  entirely; and time one run so the figure is measured rather than
  assumed.

### P-025 measurement — CLOSED (D-0065): the data below is what the decision was made on
- **Status:** OPEN, now with data. 150-symbol sample, VM, 2026-10-05.

| band | count | share | branch |
|---|---|---|---|
| 0–1% | 18 | 12.1% | rejected by Stage D |
| 1–2% | 33 | 22.1% | slow — ladder rarely fires |
| 2–3% | 43 | 28.9% | middle |
| 3–4% | 24 | 16.1% | middle |
| 4–5% | 16 | 10.7% | fast — floor in ~2 days |
| >5% | 15 | 10.1% | rejected by Stage D |

- **The fast branch is REAL, not theoretical.** `PTHS` 4.85%, `DYN`
  4.92%, `MSOS` 4.96% all reach the −10% Floor in **2.0–2.1 average
  adverse days**. Claude's previous note — written from a hand-picked
  sample of liquid large caps where nothing exceeded 4% — was wrong to
  call that branch theoretical. The hand-picked sample was the
  unrepresentative one.
- **The slow branch is also real:** 22.1% sit at 1–2%, where −5% is four
  to five average adverse days away.
- **Starvation is NOT a risk**, which was the whole reason for
  measuring. Projected to the 11,683 symbols entering the pipeline:
  1%–5% ≈ 9,100 survivors; 2%–4% ≈ 5,260. Thousands either way, against
  a Top-10 output.
- **METHODOLOGICAL CAVEAT, stated because it bounds the conclusion:**
  this measures the RAW pool. In the pipeline, Stage D sees only what
  survived Stages A–C, which keep the most liquid names — and liquid
  names skew slower. So the post-A/B/C distribution is probably shifted
  toward the slow end, making the fast tail smaller and the slow
  problem larger than the table above shows.
- **Decision needed:** keep 1%–5%, or narrow. Claude's recommendation is
  **2%–4%**: it removes both tails, is justified by the measured pace
  table rather than by reasoning alone, and leaves ~5,260 candidates —
  no starvation. The caveat above argues for confirming on the
  post-A/B/C pool first if the Controller wants certainty rather than a
  well-supported choice.

### P-030 — RESOLVED (2026-10-05): TimeoutStartSec=4h
- **Status:** RESOLVED same day (2026-10-05), recorded because it is the
  most instructive failure of the deployment.
- **FACT:** for `Type=oneshot`, `TimeoutStartSec` bounds the entire run
  and defaults to `DefaultTimeoutStartSec` = **90 seconds**, confirmed
  in `/etc/systemd/user.conf`.
- **FACT:** the full D-0026 run enriches 11,683 symbols at one bars
  request each — roughly **58 minutes** (P-029).
- **What happened:** enabling the timer fired it immediately
  (`Persistent=true` catching up the missed 08:45 slot) at 14:19.
  systemd killed the run 90 seconds later. The only visible trace was
  `Active: inactive (dead)` — no error, no snapshot, and no journal to
  read because of the storage problem recorded alongside it.
- **How it was found:** running the same script by hand succeeded
  immediately and wrote a snapshot. That isolated the fault to the unit
  rather than the code, which the journal could not have told us.
- **Fix:** `TimeoutStartSec=4h`. Bounded rather than `infinity`, so a
  genuinely hung run is still killed instead of blocking the next day's
  timer.
- **Why it matters beyond itself:** this would have failed EVERY
  morning, silently, while every other check looked healthy — the
  engine running, the timer scheduled, the units enabled. It was caught
  only because the first run was forced immediately instead of waiting
  for tomorrow.

### P-031 — SELF-RESOLVING: tomorrow's 06:00 run supersedes it (get_latest_for_date returns the newest row for the date). No action needed.
- **Status:** OPEN, needs one action.
- **FACT:** the manual 40-symbol diagnostic run wrote a real snapshot
  for `2026-10-05` containing exactly ONE symbol: `LOW`.
- **FACT:** that is now what `SnapshotUniverseSource` returns, so the
  engine can propose an INITIAL_ENTRY on `LOW` at the next D-0021
  trigger — from a capped diagnostic run, not a real selection.
- **Not dangerous:** it is paper trading, every proposal still requires
  Controller approval, and `LOW` passed the full pipeline including the
  new 2%–4% ATR band and the leveraged/inverse filter. But a test
  artifact should not be production state.
- **FACT:** `get_latest_for_date` returns the NEWEST snapshot for the
  date, so a proper full run today simply supersedes it. No deletion is
  needed.
- **Action:** run the real job once today, which both replaces the
  one-symbol snapshot and measures the true runtime for P-029.

### P-032 — Universe run starved the engine's Floor checks — RESOLVED (D-0067, 2026-10-05)
- **Status:** FIXED. `run_universe_selection.py` now refuses to start
  while the market is open unless `--force` is passed, failing closed
  when market state cannot be determined. All three paths tested
  against the real broker; the fail-closed test found and fixed a bug
  where the guard produced a traceback instead of its refusal message.
  See D-0067.
- Verified recovered: all five open positions returned live prices
  immediately after the run was stopped.
- The original finding is kept below for history.
- **What happened:** a MANUAL universe run was started at 14:54 UTC
  (10:54 ET) — mid-session, with the market open. It issues ~11,683
  bars requests and exhausts Alpaca's rate limit. The engine, polling
  every 30 seconds for its open positions, then received HTTP 429:

```
[CRITICAL] market_data_unavailable
Symbol: GOOGL
Could not get current price for GOOGL (trade GOOGL-07c7589f) for Floor
check: data provider returned HTTP 429 for 'GOOGL': too many requests.
```

- **Why it is a SAFETY issue, not noise.** Read
  `Engine._check_floor_trigger`: on `MarketDataUnavailableError` it
  notifies and **returns without evaluating the floor**. So for the
  duration of the universe run, the protective Floor and the Trailing
  Floor were not being evaluated on five real open positions — `TSLA`,
  `GOOGL`, `QQQ`, `NVDA`, `AMZN`. The engine never crashed and retried
  every 30s, and the observed failures were intermittent rather than
  total, but the protection was degraded for the whole window.
- **This is Claude's error, not a design fault.** The approved 06:00 ET
  schedule avoids the collision entirely: the market is closed, the run
  finishes around 07:00, and there are no positions to poll. Claude
  asked the Controller to start a full run by hand at 10:54 ET without
  accounting for the collision with the live engine.
- **Resolution:** the Controller stopped the run. The one-symbol test
  snapshot remains today's universe, which is acceptable.
- **CONTROLLER DECISION needed — a guard so this cannot recur:**
  1. **Refuse by default during market hours.**
     `run_universe_selection.py` asks the broker whether the market is
     open and exits unless `--force` is passed. Cheapest, and it uses
     the `is_market_open()` method D-0060 already added.
  2. **Throttle the universe run** to leave headroom for the engine.
     More correct in principle, but it lengthens a run that is already
     ~58 minutes and the right headroom figure is unknown.
  3. **Give the engine a separate, prioritised data path.** Most
     robust, most work, and probably unnecessary once (1) exists.
- **Claude's RECOMMENDATION: option 1.** The 06:00 schedule already
  means the production path never collides, so the guard exists purely
  to stop a human — including Claude — from doing by hand what the
  timer would never do. `--force` keeps it possible when genuinely
  needed, with the consequence stated out loud.
- **What would change the recommendation:** if the Controller ever
  wants an intraday refresh as normal practice, option 2 becomes
  necessary rather than optional.

---

## 📋 BACKLOG — Controller-requested, deliberately NOT now (2026-10-05)

Recorded with their real questions attached. The point is that a future
session starts from substance instead of re-deriving it.

### P-033 — Futures contracts
- **Status:** BACKLOG. Requested by the Controller 2026-10-05, to be
  designed later.
- **What it is:** extend the system beyond US equities to futures.
- **VERIFIED 2026-10-05: Alpaca does NOT support futures.** This is now
  a FACT, not an unknown, and it blocks the whole item on the broker.
  - Alpaca's own documentation: "Alpaca currently supports stocks, ETFs
    listed in the US public exchanges (NMS stocks), Options trading,
    and cryptocurrencies. Support for other asset classes, such as
    futures, FX, private equities, and international equities are on
    our roadmap."
  - Corroborated against the Controller's LIVE account. `/v2/account`
    reports capabilities explicitly per asset class —
    `crypto_status = ACTIVE`, `crypto_tier = 1`,
    `options_approved_level = 3`, `options_trading_level = 3`,
    `options_buying_power = 90601.85`, `shorting_enabled = True` — and
    carries **no futures field of any kind**.
  - **A probe that proved nothing, recorded so it is not repeated.**
    Querying `/v2/assets?asset_class=futures` returns HTTP 200 with
    `[]`, which looks like evidence until you run the control: a
    deliberately nonsense `asset_class=banana_futures` ALSO returns
    HTTP 200 `[]`. The endpoint silently ignores unknown values, so an
    empty result there says nothing either way. The account object and
    the documentation are the real evidence.
- **Consequence:** P-033 cannot proceed until Alpaca ships futures, or
  the Controller adds a second broker. It stays in the backlog, and the
  architectural objections below remain valid for whenever that
  changes.
- **Worth knowing meanwhile — the account ALREADY has two capabilities
  this project does not use:** options at level 3 with $90,601 of
  options buying power, and crypto at tier 1. Neither is a free lunch:
  **options carry the same three objections as futures** (expiry
  breaking the frozen reference, intrinsic leverage breaking D-0051
  sizing, and a contract multiplier), so adopting them would need the
  same parallel-instrument-class design rather than a flag. Crypto has
  no expiry and no multiplier, but trades 24/7, which breaks D-0021's
  seven fixed ET times and D-0060's open/closed gate. Recorded as
  context for a future decision, NOT as a recommendation.
- **Why this is architecture, not a feature flag.** Four approved
  contracts assume an instrument that behaves like a share, and a
  futures contract breaks each one:

  1. **Expiry breaks the frozen reference (D-0009).** A trade is
     anchored to its original initial-entry fill price for the life of
     the position. A futures contract expires, and continuing the
     position means rolling into a different contract at a different
     price. "The trade is the symbol" stops being true, and the Ladder
     and Floor levels have nothing stable to hang from.
  2. **Leverage is intrinsic, so D-0051 sizing does not translate.**
     Sizing computes `floor(dollars / price)` from 5% of equity. A
     contract controls a notional far larger than its margin, so the
     same formula would buy a position many times the intended risk —
     the same class of error D-0051 was created to fix, in a new form.
  3. **The ATR band (D-0065) is a percentage of price.** For a
     margined contract the meaningful denominator is margin or
     notional, not price, so 2%–4% means something different and the
     measured evidence behind it does not carry over.
  4. **Near-24-hour sessions break D-0021 and D-0060.** The trigger
     schedule is seven fixed ET times inside a 09:30–16:00 session, and
     the market-open gate asks a binary open/closed question. Neither
     survives a contract that trades almost continuously.

- **Also unaddressed:** contract multipliers, tick sizes, margin calls
  (an equity position cannot be liquidated by the broker for margin;
  a futures position can, which is an exit path the engine does not
  model at all).
- **Recommended shape when it is taken up:** treat it as a parallel
  instrument class with its own sizing, its own schedule and its own
  reference-price contract, rather than widening the equity path.
  Mixing them is how the 60× exposure bug (D-0051) and the leveraged-
  ETF bug (D-0056) both happened — one path, two instrument behaviors.

### P-034 — A "remind me later" action on Telegram proposals
- **Status:** BACKLOG. Requested by the Controller 2026-10-05.
- **What it is:** a third inline button beside Approve and Reject.
- **It interacts directly with something built TODAY, which is why the
  obvious implementation is probably wrong.** D-0059 gives a PENDING
  proposal a 60-minute TTL. A naive "remind me later" would extend that
  TTL and re-send the same proposal.
- **Why that would be close to useless.** D-0007 revalidation refuses a
  submission once price has drifted more than **0.5%** from the
  proposal's trigger. A proposal held for an hour so it can be
  re-offered will, in most sessions, simply be refused on approval. The
  Controller would get a button that looks like a choice and mostly
  is not.
- **The better framing, recorded so it is not re-derived:** what is
  actually wanted is almost certainly *"I am interested in this SYMBOL,
  ask me again with a FRESH price"* — a snooze on the symbol, not a
  stay of execution on a stale proposal. That suggests:
  - expire the current proposal normally (no change to D-0059);
  - mark the symbol as Controller-flagged;
  - have `_check_watchlist` give a flagged symbol priority on the next
    D-0021 cycle, if it still passes every stage, with a fresh price
    and a fresh proposal.
  That is a different and smaller change than extending a TTL, and it
  produces a proposal that can actually be approved.
- **Open questions for the design session:**
  1. How long does a flag last — one cycle, the trading day, or until
     cleared?
  2. Does a flagged symbol bypass `_MIN_SCORE`, or only reorder within
     the qualifiers (as D-0058's political slot does)? Bypassing a
     threshold on request is a different risk from reordering.
  3. Does it occupy one of the three per-cycle proposal slots, or sit
     outside the cap?
  4. What happens if the symbol stops passing the pipeline — silence,
     or a message saying why it will not return?
- **Touches:** `src/notifications/telegram.py` (the button),
  `src/engine/decision_source.py` (a third `DecisionKind`),
  `Engine._apply_decision`, and `_check_watchlist`.

### P-035 — Why 2026-10-05 produced zero proposals (root cause, fully traced)
- **Status:** DIAGNOSED 2026-10-05, no code change made. Three design
  gaps it exposed are P-036, P-037, P-038 below — all OPEN and all
  needing Controller approval because they change selection behavior.
- **The symptom:** the Controller received no proposal to approve or
  reject for the whole trading day, plus repeated "market data not
  available" notifications.
- **FACT, the causal chain, each step verified on the VM:**
  1. `universe-refresh.timer` was enabled mid-morning. With
     `Persistent=true` systemd immediately caught up the missed 06:00 ET
     slot, so a FULL run (`whitelist=ALL`) started at **14:19:35 UTC =
     10:19 ET — with the market open**.
  2. The code running at that moment **predated the P-032 guard**.
     Proof: `head logs/universe.log` begins at `[exclude] ...` with no
     `[guard]` line, and `_market_closed_or_forced` prints one on every
     path. Nothing refused the run.
  3. That run issued one bars request per symbol and exhausted the
     broker's rate limit. The engine then got HTTP 429 while polling
     prices, and `Engine._check_floor_trigger` returns WITHOUT
     evaluating the floor when market data is unavailable — so the
     protective Floor and Trailing Floor were degraded on five open
     positions (AMZN, GOOGL, NVDA, QQQ, TSLA) while everything looked
     healthy.
  4. The run was stopped deliberately at **15:31:27 UTC** with SIGTERM.
     Confirmed, not inferred: `Result=signal`, `ExecMainCode=2`
     (CLD_KILLED), `ExecMainStatus=15` (SIGTERM). Not an OOM — the VM
     has 22.9 GB with 19.2 GB free and `dmesg` shows no OOM kill.
     So it never completed and never wrote a snapshot.
  5. Separately, a small capped test run wrote today's snapshot at
     14:52:36 with **one symbol** (`LOW`). Arithmetic proof that it was
     capped, not rate-starved: the rejection summary totals
     30 + 6 + 3 = 39 dropped, 1 survivor — a 40-candidate pool, versus
     the 11,683 a real run starts from.
  6. The engine therefore spent the entire trading day on a one-symbol
     universe written by a test.
- **FACT, a measurement that came out of the failed run.** It reached
  7,850 of 11,683 symbols in 71 min 48 s = **~109 symbols/min**, so a
  full run takes **~107 minutes**, not the 58 minutes estimated in
  `deploy/universe-refresh.timer`. The 06:00 ET start finishes ~07:47
  ET, 1 h 43 m before the open. The superseded 08:45 start would have
  finished ~10:32 ET — **after** the open. The move to 06:00 is now
  justified by measurement rather than by estimate.
- **Not a defect:** the five open positions are auto-excluded from the
  pool by design (Controller-approved 2026-10-01), so their absence
  from the universe is correct.
- **FACT, the engine itself was healthy throughout.** Verified at
  17:25 UTC: `systemctl --user is-active trading-engine.service` =
  `active`, `NRestarts=0`, heartbeat age 30.4 s against a 30 s
  reconcile interval, account ACTIVE with equity 100,426.18 USD. It
  restarted cleanly at 15:40:39 UTC, right after the morning's runs
  were stopped.
- **FACT, the engine IS in snapshot mode.** Its live command line reads
  `--universe-mode snapshot`, so the `symbols=('TSLA','AAPL','SPY')`
  line in the log is the unused argparse default, not a watchlist. A
  note for future diagnosis: the preflight block that would say this
  explicitly (`universe: D-0026 snapshot ...`) goes to Telegram via
  `notifier.send`, NOT to stdout, so its absence from `logs/engine.log`
  proves nothing either way. The process command line is the evidence.
- **Still open:** why the one survivor `LOW` produced no proposal.
  Candidates: the research evaluator's 60/100 minimum, D-0047 portfolio
  limits with five positions already open, or the macro blackout
  calendar. **It cannot be answered from the current logs** —
  `logs/engine.log` has held 1,179 bytes since startup and records
  nothing about trigger checks, candidate evaluation or rejection
  reasons. That blindness is the finding; P-038's observability work is
  the fix, and until then every such question needs a live reproduction
  rather than a log read.

#### D-0068 — live verification on the VM (2026-10-05, 17:50 UTC)
Run by the Controller on the production host, not in a Claude container.

1. **Full suite on the VM:** `1515 passed, 54 subtests passed`.
2. **The decision, computed from the VM's own copy of the runner:**

```
TODAY'S DISASTER COMMAND (--max-symbols 40)
   persist = False | guard = 0
   capped run (--max-symbols 40): printed, NOT saved

TOMORROW 06:00 PRODUCTION RUN (no flags)
   persist = True | guard = 500
   full run: refusing below 500 raw candidates
```

3. **Live invocation** of the exact incident command exited 75 at the
   P-032 market-open guard, having issued one `/v2/clock` call and
   nothing else.
4. **Nothing was destroyed:** the latest snapshot for 2026-10-05 is
   still the 14:52:36 row.

**Honest limit of this verification.** The capped run stopped at the
market-open guard, so P-037's *refusal to persist* was proved by its 13
unit tests and by the decision above, not yet by a completed live run.
The first end-to-end proof is tomorrow's 06:00 ET run, whose snapshot
will carry a populated `data_quality_summary` (P-038) — that printed
block is itself the evidence that all three changes are live.

**Deliberately NOT done:** the one-symbol snapshot for 2026-10-05 was
left in place. At 13:50 ET roughly two hours of trading remained and a
full run takes ~107 minutes, so a replacement run would finish after the
close while costing the rate limit the engine needs for its Floor checks
— the exact trade that caused P-032. Tomorrow's 06:00 run replaces it
for free.

### P-036 — No minimum size on an approved snapshot — RESOLVED (D-0068, 2026-10-05)
- **Status:** RESOLVED by D-0068, implemented and tested 2026-10-05 (1515 passed). Original entry below.
- **Status when raised:** OPEN, raised by Claude 2026-10-05 under CLAUDE.md §0.d.
  Changes selection behavior, so NOT implemented.
- **FACT:** today's snapshot carried `is_empty = 0` with exactly one
  symbol. The pipeline guards the zero case only; one, two or three
  survivors are accepted as a healthy trading universe.
- **Failure mode:** any run that is capped, truncated, rate-starved or
  interrupted writes a plausible-looking snapshot that silently becomes
  the day's trading policy. That is exactly what happened today, and
  nothing anywhere reported it.
- **RECOMMENDATION:** a minimum-survivor threshold (e.g. 5). Below it
  the snapshot is marked empty with a reason and an IMPORTANT
  notification is sent, instead of being written as normal. Cheap,
  fully reversible, and it converts a silent bad day into a message.

### P-037 — A test run and the production run write the same table — RESOLVED (D-0068, 2026-10-05)
- **Status:** RESOLVED by D-0068, implemented and tested 2026-10-05 (1515 passed). Original entry below.
- **Status when raised:** OPEN, raised by Claude 2026-10-05. Changes selection
  behavior, so NOT implemented.
- **FACT:** `scripts/run_universe_selection.py --max-symbols 40` and the
  scheduled full run both write a row to `universe_snapshots` keyed only
  by `effective_trading_date`, and `SnapshotUniverseSource` takes the
  latest row for today. Nothing marks a row as experimental.
- **Failure mode:** an interactive experiment becomes the live trading
  universe, as it did today. The Controller has no way to tell the two
  apart by looking at the data.
- **RECOMMENDATION:** `--max-symbols > 0` implies a non-production run.
  Either refuse to persist it unless `--allow-test-snapshot` is passed,
  or record the cap in the snapshot and have `SnapshotUniverseSource`
  skip capped rows. Option A is simpler and fails safe.

### P-038 — The data-quality section of a snapshot is empty — RESOLVED (D-0068, 2026-10-05)
- **Status:** RESOLVED by D-0068, implemented and tested 2026-10-05 (1515 passed). Original entry below.
- **Status when raised:** OPEN, raised by Claude 2026-10-05.
- **FACT:** `data_quality_json` on today's snapshot decoded to nothing —
  zero entries. The section that exists to report how sound the inputs
  were reported nothing at all on the single worst universe run to date.
- **Failure mode:** the one field designed to catch a degraded run is
  silent exactly when it matters.
- **RECOMMENDATION:** populate it with at least: symbols fetched,
  symbols enriched, enrichment failures, and whether a cap was applied.
  Observability only — it changes no trading decision — but it is the
  evidence base every future diagnosis will rest on.

### P-039 — Fixed −4h US Eastern offset breaks when EST returns (1 Nov 2026) — RESOLVED (D-0069, 2026-10-05)
- **Status:** RESOLVED by D-0069, Controller-approved and pushed 2026-10-05. The risk-side impact turned out larger than first reported: a 23-hour under-count on the D-0047 daily cap, not just one quiet hour. Original entry below.
- **Status when raised:** OPEN, found 2026-10-05 during the pre-run bug hunt the
  Controller asked for. NOT fixed: it touches a risk module, so it waits
  for approval per CLAUDE.md §12.a.
- **FACT:** two "what is today's trading date" helpers use a hardcoded
  offset rather than a timezone:
  `src/engine/snapshot_watchlist.py:25` and
  `src/risk/portfolio_snapshot.py:28`, both
  `_US_MARKET_TZ_OFFSET_HOURS = -4.0`. US Eastern is UTC−4 only during
  EDT; from 1 November 2026 it is UTC−5.
- **FACT, measured not estimated.** Replaying a full winter day hour by
  hour against `zoneinfo`, exactly **one** UTC hour disagrees:

| UTC | true ET | true date | computed date |
|---|---|---|---|
| 04:00 | 23:00 ET | 2026-11-09 | 2026-11-10 |

  Every hour that matters is correct, verified directly:

| moment (EST) | true | computed |
|---|---|---|
| universe run 06:00 | 2026-11-10 | 2026-11-10 |
| market open 09:30 | 2026-11-10 | 2026-11-10 |
| market close 16:00 | 2026-11-10 | 2026-11-10 |

- **So the practical risk is LOW and must not be overstated:** the one
  bad hour is 23:00–00:00 ET, outside the session, outside the 06:00
  run, and outside the D-0021 trigger window. Trading TIMING already
  uses real `ZoneInfo` (`src/engine/schedule.py`,
  `src/scheduler/next_fire.py`), so nothing that fires an order is
  affected.
- **Why raise it anyway:** it is a landmine with a known detonation
  date. During that hour the engine asks for tomorrow's snapshot, finds
  none, and returns an empty watchlist — the same silent shape as the
  2026-10-05 failure, which took a full day to notice.
- **RECOMMENDATION:** replace both constants with
  `ZoneInfo("America/New_York")`, matching what the scheduling modules
  already do. Behavior inside trading hours is unchanged — that is what
  the table above establishes — so the change is verifiable as a no-op
  where it counts and a correction where it does not.

### P-040 — The P-036 pool guard catches truncation, not partial fetches
- **Status:** OPEN, stated as a known limit of D-0068 rather than a
  defect in it.
- **FACT:** `--min-candidates` defaults to 500 against a real pool of
  ~11,683. It therefore catches a catastrophically short fetch (40, as
  on 2026-10-05) but would accept a partial one — say 5,000 symbols from
  a provider that paginated badly — as a normal run.
- **Why the threshold is not simply raised:** a high threshold turns any
  legitimate shrinkage of the tradable universe into a refused day, and
  a refused day costs new entries. 500 is deliberately more than 20×
  below normal so it only fires on an unmistakable break.
- **What makes this acceptable today:** D-0068's P-038 counters now put
  `raw_candidates_fetched` in every snapshot, so a partial fetch is
  visible after the fact even though it is not refused.
- **RECOMMENDATION:** revisit once several real runs have recorded their
  `raw_candidates_fetched`, and set a relative guard (e.g. refuse below
  60% of the trailing median) from measured values rather than from a
  number chosen today. Deliberately deferred: inventing that constant
  now would repeat the mistake D-0065 corrected.

---

## Bug hunt, 2026-10-05 (Controller-requested: "find the list of bugs")

A deliberate audit of the safety-critical paths, after the day's
incident. Findings are reported with what was VERIFIED in code, not what
was suspected. Where a suspicion did not survive reading the code, it is
recorded as cleared rather than quietly dropped — a list padded with
non-bugs is worse than a short one.

### P-041 — A market-data outage floods the Controller with CRITICAL alerts — RESOLVED (D-0070, 2026-10-05)
- **Status:** RESOLVED by D-0070, Controller-approved and pushed 2026-10-05.
- **Status when raised:** OPEN. Confirmed by code reading AND by what the Controller
  experienced on 2026-10-05 ("I have so many messages told me the data
  not available for the item").
- **FACT:** `Engine._notify` (`src/engine/engine.py:2113`) sends every
  call straight to the notifier. There is **no deduplication and no rate
  limit** — `_notify_once` and its `_notified` set exist, but the
  market-data failures do not use them.
- **FACT:** `_check_floor_trigger` runs for every ACTIVE trade on every
  reconciliation tick (`run_reconciliation_tick`, line 639), and the
  tick interval is 30 s.
- **Arithmetic, not estimate:** 5 open positions ÷ 30 s = 10 CRITICAL
  messages per minute, **600 per hour**, for as long as the outage
  lasts. There are 7 separate `market_data_unavailable` notify sites.
- **Why this is a safety bug, not noise:** a Controller buried under 600
  identical alerts mutes the channel or stops reading it. The next
  message after that is the one that matters — a Floor execution, a
  partial fill, a submission failure. The alert channel is the only
  channel, so degrading it degrades every protection that depends on it.
- **RECOMMENDATION:** collapse repeated `market_data_unavailable` into
  one alert per symbol per outage, with a single follow-up when it
  clears. The existing `_notify_once` mechanism already has the right
  shape; the outage key would be `(symbol, "market_data_outage")`,
  cleared on the first successful price read.

### P-042 — A long proposal message can be silently dropped by Telegram — RESOLVED (D-0070, 2026-10-05)
- **Status:** RESOLVED by D-0070 (both halves: the 4096-char cap and the delivery report), Controller-approved and pushed 2026-10-05.
- **Status when raised:** OPEN. Structural; whether it fires on a given day depends
  on how much the research APIs return.
- **FACT:** Telegram's `sendMessage` rejects any `text` longer than 4096
  characters with HTTP 400. `TelegramNotificationService._format_text`
  (`src/notifications/telegram.py:112`) applies **no cap**.
- **FACT:** proposal messages are enriched by `CompositeEnricher`
  (`src/engine/enrichers.py:291`), which joins the output of every
  configured sub-enricher with **no overall cap**. The VM's startup log
  shows five enabled:
  `Perplexity, Finnhub, AlphaVantage, Tiingo, Polygon`.
  Only `PerplexityEnricher` caps itself (300 chars) and `PolygonEnricher`
  caps a headline; the rest are uncapped.
- **FACT, why the failure is silent:** `Engine._notify` discards the
  returned `NotificationResult` entirely. A 400 is retried by the
  transport, fails identically every time, and the engine never learns.
- **Consequence chain:** the dropped message is the one carrying the
  inline ✅/❌ buttons. `_start_new_trade` then adds the proposal to
  `_notified` regardless of outcome, so it is never re-advertised in
  that session — and D-0068's 60-minute TTL expires it quietly. The
  Controller sees nothing at any point.
- **Partly mitigated, honestly:** `recover()` re-notifies PENDING
  proposals on startup (line 487), so a restart would surface it. But
  the TTL is 60 minutes and the engine is designed to run for months.
- **RECOMMENDATION:** cap the text in the Telegram client, where the
  limit actually lives — truncate at ~3900 characters with a visible
  marker, so the approval buttons always arrive even when the research
  blurb is long. Separately, have `_notify` log a non-delivered result.
  The cap belongs in the client, not in each enricher, because the
  limit is the transport's and new enrichers must not have to know it.

### P-043 — "Floor not evaluated" has no escalation if it persists — RESOLVED (D-0071, 2026-10-05)
- **Status:** RESOLVED by D-0071, Controller-approved and pushed 2026-10-05.
- **Status when raised:** OPEN, lower priority than P-041/P-042 and recorded as
  such.
- **FACT:** on `MarketDataUnavailableError`, `_check_floor_trigger`
  notifies and returns without evaluating the Floor
  (`src/engine/engine.py:793-801`). That is the correct immediate
  behavior — guessing a price to evaluate a protective exit would be
  worse.
- **The gap:** there is no state that says "this position's protective
  exit has not been evaluated for N minutes". One failed read and a
  two-hour outage produce the same message at the same level, and the
  only difference is how many copies arrive — which P-041 is about.
- **RECOMMENDATION:** track consecutive failures per symbol and escalate
  once past a threshold (e.g. 10 consecutive misses ≈ 5 minutes) with a
  distinct event name, so a sustained outage is a different alert from a
  blip. Deliberately NOT proposing an automatic action: selling on
  missing data is the one thing that must never happen.

### Checked and NOT a bug (recorded so the audit is auditable)
- **`_notified` grows forever.** True — it is never pruned, and the
  engine is meant to run for years. But the growth is a few short tuples
  per day: 10 proposals/day for 10 years is ~36,500 entries, roughly
  5 MB. Not a leak worth code. Recorded rather than listed, because
  padding a bug list is its own failure.
- **Broad `except Exception: pass` in `d0026/regime_classifier.py` and
  `engine/research_hub.py`.** Both are advisory layers that are
  documented to fail open, and neither can influence a risk limit or a
  protective exit. Correct as written.
- **Telegram transport failures.** `send` retries and returns a result
  rather than raising, so a failed notification never retries a trade —
  which is exactly what CLAUDE.md §6 requires.
- **Pending proposals after a restart.** `recover()` re-fires the
  notification for every PENDING proposal, so a restart does not strand
  an approval request.
- **Today's three new guards are actually wired**, verified by call
  site: `_expire_stale_proposals` (line 637),
  `_market_open_for_new_proposals` (line 1401),
  `_notify_nothing_to_trade` (lines 1367, 1377, 1469, 1575).

---

## Execution-path audit, 2026-10-05 (Controller-requested)

Read end to end: `src/execution/service.py` (858 lines),
`Engine._process_trade`'s ladder loop, `Trade.freeze_initial_reference`,
and the protective-exit path. Two confirmed defects, one deferred idea,
and the suspicions that did not survive the code.

### P-044 — A partial LADDER-1 fill strands real shares with no floor — RESOLVED (D-0072, 2026-10-05)
- **Status:** RESOLVED by D-0072 for Ladder 1. Ladder 2 stays as designed; the residual risk there is P-049.
- **Status when raised:** OPEN. **The most serious finding of 2026-10-05.** Real
  money, silent, and reachable from the live system today.
- **FACT, three independent gaps that compound:**
  1. `ExecutionService._apply_to_trade_if_terminal` returns without
     recording a ladder fill when
     `execution.filled_qty != execution.requested_qty`
     (`src/execution/service.py:572`). The code says so in its own
     comment: for LADDER_1 "no confirmation path exists (not approved)
     -- the fill simply stays unrepresented in Trade".
  2. `ExecutionService.submit_protective_exit` sells the caller's
     `trade.total_shares` and explicitly refuses to sell more than the
     Trade believes it holds (`service.py:700`).
  3. There is **no notification at all** for a partial LADDER-1 fill.
     `_maybe_notify_ladder2_pending_confirmation` exists for Ladder 2;
     Ladder 1 has no equivalent anywhere in `engine.py`.
- **Worked example, with numbers:**

| step | real position at Alpaca | `trade.total_shares` |
|---|---|---|
| Initial entry fills 20 @ $250 | 20 | 20 |
| Ladder 1 approved for 5 @ $237.50 | 20 | 20 |
| **only 3 fill, order terminal** | **23** | **20** |
| price reaches the floor, $225 | 23 | 20 |
| floor sells `total_shares` | **3 left** | 0 |

  Those 3 shares sit at the broker with **no trade, no floor, no
  monitoring and no message**. At $225 that is $675 that can fall to
  zero with nothing watching it. The weighted average entry is also
  stale, so every later percentage is computed from a wrong base.
- **Why it does not self-heal:** `ladder1_filled` stays False, so the
  ladder loop keeps evaluating it; but the proposal stays APPROVED, so
  `has_live_attempt` is True and no new proposal is created, and the
  `elif` branch re-submits into `ExecutionAlreadySubmittedError`, which
  the engine catches. The trade is stuck in that state indefinitely.
- **REPRODUCED, not inferred (2026-10-05, Controller asked for
  re-verification).** A script drove the REAL `ExecutionService`, the
  REAL `Trade` model and the REAL SQLite repositories, stubbing only the
  broker and the price feed: initial entry filled 20 @ $250, Ladder 1
  proposed and approved for 4 shares, broker returned a TERMINAL fill of
  2. Output:

```
after initial entry : total_shares=20  ladder1_price=237.5  floor=225.0
ladder 1 proposal   : qty=4 trigger=$237.5
submitted           : requested_qty=4

broker actually filled      : 2 extra shares
REAL position at the broker : 22 shares
trade.total_shares          : 20 shares
ladder1_filled flag         : False
weighted_avg_entry_price    : 250.0
notifications sent          : NONE

>>> SHARES THE FLOOR WOULD LEAVE BEHIND : 2
>>> value at the floor price $225.0: $450.00
```

  Both halves of the finding are confirmed by execution: the shares are
  unrecorded, and **not one notification is sent**. The weighted average
  also stays at the pre-ladder 250.0.

- **CORRECTION to the line above, after checking what the weighted
  average actually drives.** "Every later percentage is computed from a
  stale base" was too broad and is withdrawn. Verified in code:
  `Trade.record_ladder_fill` states and enforces that it "never touches
  `original_floor_price`, `ladder1_price`, or `ladder2_price`" — those
  come from the FROZEN original entry (D-0001/D-0009), so the ladder
  triggers and the original −10% floor are **unaffected** by the stale
  average.

  Exactly one thing is affected, and it is on the protective side:
  `Trade.activate_trailing` sets
  `activation_threshold = weighted_avg_entry_price * 1.10`, and the
  trailing floor is `activation_threshold * 0.95` (D-0008).

| | true | stale (what the system holds) |
|---|---|---|
| weighted average | $248.8636 | $250.0000 |
| trailing activates at | $273.75 | $275.00 |
| trailing floor set at | $260.0625 | $261.2500 |

  **The dangerous case is a rally that peaks between the two
  thresholds.** A peak at $274.00 activates the trailing floor under the
  true average and does **not** activate it under the stale one. The
  position then falls back protected only by the original floor at
  $225.00 instead of $260.06 — **$35.06 per share, $701.25 on the 20
  shares the trade believes it holds**, on top of the 2 stranded shares.

- **Why the trailing floor does not rescue the stranded shares either.**
  Both the original and the trailing floor exit through the same call,
  `submit_protective_exit(trade_id, quantity=trade.total_shares, ...)`
  (`src/engine/engine.py:837`). The trailing floor sells the same
  understated count, so it inherits the stranding rather than fixing
  it — and its activation point is computed from the stale average as
  shown above.

- **The "no message" half, verified exhaustively.** Every notification
  event name the engine can emit was listed (41 of them). The list
  contains `ladder2_partial_fill_pending_confirmation` and **no
  `ladder1_*` event of any kind**. The asymmetry is total.

- **RECOMMENDATION (needs Controller approval — it changes execution
  behavior):** mirror the Ladder-2 design, which the Controller already
  approved for exactly this situation. A partial Ladder-1 fill should
  (a) send an IMPORTANT notification naming the filled and requested
  quantities, and (b) offer the same explicit confirmation that
  `confirm_ladder2_partial_fill` provides, so the shares are recorded
  on the Trade and therefore covered by the floor. Until then the risk
  is live.
- **Smaller, strictly-safe alternative if the full path is too big for
  now:** send the notification only. That alone converts a silent
  stranding into something the Controller can act on by hand, and
  changes no execution logic.

### P-045 — The engine never checks its share count against the broker — RESOLVED (D-0073, 2026-10-05)
- **Status:** RESOLVED by D-0073 with option A (detect and report, never correct).
- **Status when raised:** OPEN. This is the control that would have caught P-044
  automatically.
- **FACT:** `/v2/positions` is read in exactly one place,
  `src/risk/portfolio_snapshot.py:125`, and only for dollar exposure.
  Nothing anywhere compares Alpaca's actual `qty` for a symbol with
  `trade.total_shares`.
- **Consequence:** any divergence — a partial fill (P-044), a manual
  trade placed by the Controller in the Alpaca UI, a corporate action,
  a broker-side cancellation — is invisible forever. The engine
  protects the position it *believes* it has.
- **RECOMMENDATION:** a read-only drift check on each reconciliation
  tick: for every ACTIVE trade, compare the broker's `qty` with
  `trade.total_shares` and send ONE notification per divergence (using
  the D-0070 dedup pattern, so it cannot flood). **No automatic
  correction** — the engine must not silently rewrite its own state
  from the broker; the Controller decides. Observability only, which
  makes it the cheapest and least risky of the open items.

### P-046 — Protective floor as a resting broker-side order (Controller's idea, deferred)
- **Status:** DEFERRED by the Controller, 2026-10-05: "about the floor
  point, note it down, we will return for it and discuss it."
- **The Controller's observation, which is correct:** the floor level is
  computed from the initial entry price, which is frozen (D-0009) and
  persisted, and the position itself exists at Alpaca independently of
  our process. So the floor LEVEL is known from the moment the entry
  fills and never changes.
- **The idea that follows:** place a resting stop/stop-limit SELL at the
  broker at that level, once, at entry. Protection would then survive a
  market-data outage, an engine crash, a VM reboot, and a container
  reclaim — today it survives none of those, because the floor is only
  evaluated by our own polling loop.
- **Known trade-offs to work through before deciding:** a resting order
  fires on an intraday spike our 30-second polling might never see
  (safer or worse depending on the Controller's intent); the trailing
  floor (D-0008) moves, so the resting order must be replaced on every
  ratchet; a partial fill at the broker splits the position; and the
  resting order must be cancelled on any ladder that changes the share
  count. None are blockers, all need deciding.
- **CODE SEARCH, 2026-10-05 (before any design work, per CLAUDE.md
  §0.b). The architecture already specifies this and the state layer is
  already built — it was never connected.**

  Already present:
  * `Trade` carries `protective_order_id`,
    `protective_order_stop_price`, `protective_order_status` and
    `protective_order_lineage` (`src/trade/models.py:110-113`).
  * `Trade.update_protective_order()` exists, validates the stop price,
    and appends the replaced id to the lineage so a
    cancel-and-replace chain is auditable (`models.py:443`).
  * `SqliteTradeRepository` persists all four fields and enforces
    uniqueness of the active protective order id, reading the previous
    id inside the same transaction (`sqlite_repository.py:69-138`).
  * `docs/architecture/state-management.md` §"Protective order state"
    describes it as "the current authoritative sell **stop**" — a
    broker-side stop was the intent from the start (D-0018).

  Never used: `update_protective_order` is called by **no production
  code path**. The floor is evaluated only by the engine's own polling
  loop.

  The one genuine gap: `BrokerClient.submit_order()` takes only
  `limit_price` (`src/execution/broker_client.py:71-78`) — there is no
  stop price, no order type and no time-in-force, so no stop order can
  be placed today.

- **The decision that matters most, and it is a safety one.** With a
  resting stop at the broker AND the engine's own floor check, BOTH can
  fire on the same position. Selling 20 shares twice does not sell 40 —
  it sells 20 and **opens a 20-share SHORT**, which is outside the
  approved strategy entirely. Any design here must make one of the two
  authoritative and the other stand down, and that rule has to be
  decided before a line is written.

- **Second decision: a plain stop is forbidden by the approved
  strategy.** A stop order becomes a MARKET order when triggered, and
  the project's standing constraint is "no Market Orders". So it must be
  a stop-LIMIT — which can fail to fill in a gap-down, meaning the
  protection that was supposed to be more reliable can simply not
  execute. That trade-off is the heart of the choice, not a detail.

- **Third: the trailing floor moves (D-0008).** Every ratchet requires
  cancelling and replacing the resting order, and every ladder fill
  changes the share count and requires the same. The lineage field
  already exists for exactly this.

- **Not implemented, not designed in detail. Parked for discussion.**

### Checked and NOT a bug
- **A partial INITIAL ENTRY fill.** Suspected to strand shares the same
  way, because `_apply_to_trade_if_terminal` labels a partial initial
  fill `InitialOrderStatus.CANCELLED`. It does not:
  `Trade.freeze_initial_reference` branches on `filled_shares == 0`, not
  on the status label, so a partial initial fill still computes
  `ladder1_price`, `ladder2_price` and `original_floor_price` and sets
  `total_shares = filled_shares` (`src/trade/models.py:246-273`). The
  position is protected. The suspicion was wrong and is recorded as
  wrong.
- **`reconcile_unresolved` aborting a sweep on one bad row.** It catches
  per row, logs with `logger.exception`, and leaves the row unresolved
  so it retries next pass. Correct.
- **Re-submitting an already-submitted ladder every tick.** The `elif`
  branch does call `_submit_approved` again, but
  `ExecutionAlreadySubmittedError` is raised and caught. A no-op, not a
  duplicate order.
- **The protective exit double-applying.** `_apply_protective_exit`
  derives the target `total_shares` from the execution's own immutable
  fields rather than subtracting from current state, so re-applying is
  idempotent by construction.

### P-047 — Option 3 for P-044 has an idempotency trap (found BEFORE coding) — RESOLVED (D-0072, 2026-10-05)
- **Status:** RESOLVED by D-0072's absolute `position_from_ledger`, with four idempotency tests.
- **Status when raised:** OPEN — design constraint for the Controller-approved
  option 3, recorded before any code was written.
- **FACT:** `ExecutionService.recover_if_terminal` re-calls
  `_apply_to_trade_if_terminal` on every engine startup recovery, and
  its docstring states exactly why that is safe today:

  > "Trade's own fields already are that flag … `ladder1_filled` /
  > `ladder2_filled` checks, plus `Trade.record_ladder_fill()`'s own
  > refusal to fill twice"

- **Why option 3 breaks it:** option 3 deliberately records the shares
  WITHOUT setting `ladder1_filled`, so that the trading decision stays
  with the Controller. That removes the only thing preventing a second
  application. A naive `total_shares + filled_qty` would **add the same
  partial fill again on every restart** — 20 → 22 → 24 → 26. That is a
  worse bug than the one being fixed, and it would be silent.
- **The pattern the codebase already uses for this.**
  `_apply_protective_exit` solves the identical problem without any new
  flag, and says so: it computes the TARGET `total_shares` from the
  execution's own immutable fields "never by subtracting
  `execution.filled_qty` from whatever `total_shares` happens to be
  right now".
- **RECOMMENDATION:** option 3 must compute an ABSOLUTE target, never an
  increment: derive `total_shares` and the weighted average from the
  full set of TERMINAL BUY executions for the trade (the ledger, all
  immutable) minus what the protective exit sold. Re-running then
  converges on the same number however many times it runs, with no new
  persistence — the same guarantee the sell side already has.
- **Not implemented yet.** Writing the increment version would have been
  quick and wrong; this is the one place where being fast is the bug.

### P-048 — Option C of P-045 (freeze on divergence) has no decision channel
- **Status:** OPEN — answers the Controller's question of 2026-10-05:
  "if I choose option C … how I can decide and what's the channel I will
  decide using it".
- **FACT:** every Controller decision in this system is
  `(kind, proposal_id)`. `DecisionKind` has exactly three members —
  `APPROVE`, `REJECT`, `CONFIRM_LADDER2_PARTIAL_FILL`
  (`src/engine/decision_source.py:35`) — and
  `_parse_callback_data` (`src/notifications/telegram_decision.py:424`)
  returns `Optional[Tuple[DecisionKind, str]]`, where the string is a
  proposal id. A callback without a proposal id is dropped.
- **Consequence:** a freeze is not about any proposal, so **today there
  is no way to lift one**. Choosing option C as it stands would mean
  trading stops and the only exits are restarting the engine or editing
  the database by hand.
- **What option C actually requires, therefore:** a new decision kind
  that is trade- or account-scoped rather than proposal-scoped, a
  callback payload that carries no proposal id, a persisted freeze flag
  that survives a restart (an in-memory one would silently unfreeze on
  the next restart — the worst possible behavior), and a notification
  carrying the unfreeze button.
- **RECOMMENDATION:** take option A (detect and report) now, and treat
  option C as a separate, properly-scoped change afterwards. A freeze
  whose release mechanism does not exist is more dangerous than the
  divergence it guards against.


### P-049 — A partial LADDER-2 fill still strands shares until confirmed — RESOLVED (D-0074, 2026-10-05)
- **Status:** RESOLVED. D-0074 dissolved this rather than patching it: both ladders now record a partial fill and close, so there is no window between the fill and a confirmation.
- **Status when raised:** OPEN, created by D-0072's deliberate scoping.
- **FACT:** D-0072 records a partial Ladder 1 automatically. Ladder 2
  keeps its Controller-approved flow — notify, then require
  `confirm_ladder2_partial_fill` — so between the fill and the
  Controller pressing confirm, the shares are real at the broker and
  absent from `trade.total_shares`. The protective exit would leave them
  behind, exactly as P-044 described.
- **Why it was not changed anyway:** recording them first made the
  confirmation add them a SECOND time (10 + 10 → 30, caught by the
  existing tests), and the Ladder 2 confirmation flow is approved as it
  stands. Changing it needs its own decision, not a side effect of
  another one.
- **The difference that makes this tolerable:** the Controller is TOLD.
  Ladder 2 has always had its message; Ladder 1 had none. A loss he can
  see and choose is not the same failure as one he cannot.
- **RECOMMENDATION:** extend D-0072 to Ladder 2 — record the shares
  automatically, and keep the confirmation button for the LADDER
  COMPLETION decision only, with `confirm_ladder2_partial_fill` setting
  the flag without re-adding the quantity. That makes both ladders
  consistent and keeps every decision the Controller already has.
  Needs approval.

### P-050 — A ladder order that fills ZERO shares is stuck forever, silently — RESOLVED (D-0074, 2026-10-05)
- **Status:** RESOLVED by D-0074 with option (b): the ladder becomes genuinely retryable and the Controller is told. Controller's reasoning: never stall on a ladder, because the falling price does not wait.
- **Status when raised:** OPEN, found 2026-10-05 while implementing the Controller's
  ladder decision. Needs a decision of its own.
- **REPRODUCED, not inferred.** Same harness as P-044, with the broker
  returning a terminal fill of 0:

```
broker actually filled      : 0 extra shares
trade.total_shares          : 20 shares
ladder1_filled flag         : False
notifications sent          : NONE

>>> ladder still open?  True
>>> ladder-1 proposals: 1  states=['APPROVED']
>>> after 5 more trigger checks at the trigger price:
>>>   ladder-1 proposals: 1  states=['APPROVED']
>>>   a NEW chance to buy? NO -- the ladder is stuck
```

- **What this means.** The ladder level was reached, the order went to
  the broker, nothing filled, and the ladder is left flagged as NOT
  filled — so it looks available. It is not: the proposal stays
  APPROVED, so `has_live_attempt` blocks any new proposal, and the
  resubmit branch is refused as already-submitted. Five further trigger
  checks at the trigger price produced no new chance to buy.
- **And the Controller is never told.** Zero notifications. The ladder
  simply never happens, and nothing says so.
- **The decision needed:** when a ladder order fills nothing at all,
  should the ladder
  (a) CLOSE, like a partial fill does under the Controller's 2026-10-05
      decision — the level was reached and the market offered nothing;
  (b) genuinely REOPEN, so a later trigger can try again — which means
      retiring the stale APPROVED proposal so a new one can be created;
      or
  (c) stay as it is, but with a notification so at least it is visible.
- **Claude's RECOMMENDATION: (b), with a notification.** A partial fill
  and a zero fill are different events. In a partial fill the market
  answered and the answer was "this much" — closing it is honest. In a
  zero fill the market did not answer at all, and the Controller's own
  reasoning ("buy what exists") implies nothing existed at that instant,
  not that nothing exists today. Forfeiting a ladder because of one
  empty moment is a strategy change nobody chose. (c) is the cheap
  stopgap if (b) is too large for now; (a) is defensible but silently
  loses a ladder the strategy counts on.
