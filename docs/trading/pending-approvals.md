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

### P-017 — The engine has no supervisor; it is restarted by hand
- **Status:** OPEN. Facts now CONFIRMED by the Controller's own VM
  session on 2026-10-05, so this is no longer UNKNOWN.
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

### P-014 note — the fallback is LIVE on the VM right now
- The command above passes `--universe-mode snapshot` and NO
  `--symbols`, so `symbols` resolves to the default `TSLA,AAPL,SPY`
  and becomes the fallback watchlist per P-014.
- **UNKNOWN:** whether the VM's own `paper_session.sqlite` holds a
  snapshot for the current trading date. Since 2026-10-03 the VM runs
  with `--no-db-push`, so its DB is not the one in this repo and cannot
  be inspected from here. This must be checked on the VM before the
  next session open.

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

---

## 🚨 ADDED 2026-10-05 (second VM audit) — highest priority

### P-021 — The live Universe selects leveraged and inverse ETFs. One is a −2× inverse fund.
- **Status:** OPEN. This is the most dangerous finding to date. It is a
  real money-losing path, not a theoretical one.
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

### P-022 — The VM is running code older than the branch head
- **Status:** OPEN. Time-critical: it matters before Monday's open.
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

### P-023 — SQLite write contention between the engine and a daily universe run
- **Status:** OPEN, measured, LOW risk — recorded so the 24/7 wiring
  does not introduce it by accident.
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
