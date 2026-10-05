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

### P-014 — In snapshot mode the engine silently falls back to the TSLA test watchlist
- **Status:** OPEN. Highest priority: it can open real paper trades on
  test symbols.
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

### P-015 — The 24/7 gap: nothing refreshes the Universe snapshot
- **Status:** OPEN. This, not the host, is the real 24/7 blocker.
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

### P-016 — Engine startup message always reports "no snapshot", even when one exists
- **Status:** OPEN. A one-line fix; listed high because it destroyed the
  Controller's visibility into P-014 and P-015.
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

### P-017 — `--max-hours` is a self-termination cap, not a 24/7 mechanism
- **Status:** OPEN, pending one fact only the Controller can supply.
- **FACT:** `scripts/run_paper_session.py:356` — `--max-hours` defaults
  to `6.0`. The main loop at line 690 runs
  `while not stop_flag and time.monotonic() < deadline`, where
  `deadline = time.monotonic() + args.max_hours * 3600.0`. When it
  expires the process sends `paper_session_ended` and exits cleanly.
- **FACT:** this is what stopped the engine on 2026-10-04 — it was
  launched on 2026-10-03 at 18:07 UTC with `--max-hours 24`.
- **UNKNOWN:** what the Oracle VM currently launches the engine with,
  and whether a `systemd` unit restarts it. I cannot read the VM from
  here.
- **Decision needed:** confirm the VM's launch command and restart
  policy. For genuine 24/7 the engine needs a large or absent cap plus
  an external supervisor that restarts it, since a clean exit on cap
  expiry looks like success to any supervisor watching exit status.

### P-002 — Ranker scorer choice
- **Status:** TESTED, still awaiting Controller decision. Unchanged
  since 2026-10-03.
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

### P-018 — No US market holiday calendar (D-0006)
- **Status:** OPEN. Matters only because the system is meant to run
  unattended.
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

### P-019 — Recovery path can leave a symbol locked (low severity, self-healing)
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

### P-020 — Saved snapshots are missing score and sector detail
- **Status:** OPEN, data quality.
- **FACT:** in the 2026-10-01 snapshot every symbol entry has
  `score_summary: []`, `sector: null`,
  `identity_confidence: "provisional"`,
  `confidence_status: "not_calibrated"` and
  `risk_status: "not_calibrated"`.
- **FACT:** the rejection summary for that run is
  `A_tradability: 53`, `C_execution_quality: 10`,
  `D_strategy_mechanics_fit: 4` — 67 symbols dropped, 4 survived.
- **Consequence:** a Stage G sector cap cannot be audited after the
  fact when the saved sector is null, and the Stage F ranking that
  chose these four symbols is not reconstructible from the snapshot.
- **Decision needed:** none yet. Tied to P-002 and P-015 — worth
  resolving in the same pass as whichever universe work is approved.

### P-011 — Live DB is one schema version behind the code
- **Status:** OPEN, verified safe, no decision needed.
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
