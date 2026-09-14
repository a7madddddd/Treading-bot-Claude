# Scheduler Design — DST-safe execution of the approved CT schedule

Answers Controller's question: *how will the final runtime execute
these schedules in America/Chicago without seasonal DST drift?*

**No implementation. No language chosen. Evidence-based options with a
recommendation. Nothing on live triggers changes until APPLY.**

---

## 1. Constraint we must satisfy

- Business schedule is defined in **America/Chicago** (D-0005, D-0020, D-0021).
- Wall-clock times must not drift twice a year across DST transitions.
- The runtime must survive restarts (D-0018 — state is deterministic and recoverable), including across a DST weekend.
- Trading must remain paper-only (D-0002) and go through the Controller-approval workflow (D-0003, D-0007).

## 2. Evidence — what the current trigger runtime does

From `list_triggers` inspection (2026-09-14):

- Each trigger stores a single 5-field `cron_expression` string.
- No `timezone` / `tz` field on the trigger record.
- `next_run_at` timestamps align with the cron hour interpreted as **UTC**. That is direct evidence the runtime evaluates in UTC.
- Each trigger carries an `environment_variables: {}` block, empty on all four routines. This is the intended path for env-var injection into the routine's runtime.

Result: the current trigger runtime **cannot natively honor** `TZ=America/Chicago`. Any solution must either replace the schedule host, or work around UTC-only cron.

## 3. Options

### Option A — Migrate scheduling to a persistent TZ-aware host (target design)

A small long-running process, deployed on any host that supports a real timezone (Linux with `tzdata`, a container, a managed VM, a serverless-scheduled job with TZ config), runs the routines. Cron entries live inside that process using a TZ-aware scheduler.

Concrete patterns that satisfy the constraint (language TBD — evidence in the repo doesn't force a choice yet):

- Host cron with `CRON_TZ=America/Chicago` at the top of the crontab. Standard `cron(8)` on modern Linux honors this.
- In-language scheduler with an explicit timezone argument (e.g. `APScheduler(timezone=ZoneInfo("America/Chicago"))`, `node-cron({ timezone: "America/Chicago" })`, `Quartz.NET` with `TimeZoneInfo`, `chrono-tz` in Rust, etc.). The language choice is deferred; all major ecosystems have equivalent primitives.
- Cloud managed scheduler with a native timezone field (GCP Cloud Scheduler, AWS EventBridge Scheduler — both accept an IANA TZ). Introduces cloud dependency; only worth it if you already run one.

Advantages:
- **DST is automatic.** IANA `America/Chicago` handles both CST and CDT for the life of the process.
- **State store colocated** with the scheduler process — no cross-machine coordination needed for D-0018.
- **Runtime env-var injection** for credentials is trivial.
- **Testable** locally under a fake clock.

Disadvantages:
- Requires a host or cloud runtime we don't have yet.
- Requires the language / runtime decision to have been made.

### Option B — Two UTC crons per routine, DST-toggled (interim on existing runtime)

Keep using the existing trigger runtime. For each routine, register **two** triggers with the same prompt:

- Trigger `<name>-cdt`: cron computed for CDT (UTC−5), enabled Mar → Nov.
- Trigger `<name>-cst`: cron computed for CST (UTC−6), enabled Nov → Mar.

Toggle `enabled` on the two DST-transition weekends per year using a very small orchestration job (a separate self-fires trigger, or a manual step).

Example for `tsla-paper-trading-monitor` at 08:30–14:30 CT:

- CDT cron: `30 13-19 * * 1-5`, `enabled: true` during CDT
- CST cron: `30 14-20 * * 1-5`, `enabled: true` during CST

Advantages:
- Uses the existing trigger runtime as-is.
- No new host or language decision required.

Disadvantages:
- Two DST toggles per year, per routine. If the toggle job fails silently, one hour of drift ships to production.
- Doubles the number of triggers under management.
- No natural home for shared state (D-0018) — each firing has to reconcile state from the broker + persistent store on its own.

### Option C — Single UTC cron, manually reissued twice a year

Not acceptable. Silently misses the intended CT time whenever the manual step is forgotten. Included here only to be explicit that we do **not** consider it.

### Option D — "Dispatch" trigger firing more frequently and gating on wall-clock

Register a single trigger that fires every N minutes and, inside its prompt, computes whether the current CT wall-clock matches an intended slot; if not, exit early.

Advantages:
- Handles DST correctly with a single trigger, no manual toggles.

Disadvantages:
- Wastes runs (a routine that should fire 7×/day fires ~24×). Each empty run still costs an LLM invocation on the trigger runtime.
- The gating logic ends up inside every prompt — duplicated and easy to skew.
- Every empty run is another opportunity to hit an API error at the wrong moment.

Not recommended.

## 4. Recommendation — DECIDED

**Approved: Option A** (D-0023). Concretely:
- Language **Python** (D-0022).
- Scheduler primitive `APScheduler` with
  `AsyncIOScheduler(timezone=ZoneInfo("America/Chicago"))` — swappable
  if a better fit is found before implementation, but this is the
  design assumption in every downstream doc.
- State store SQLite (D-0024), colocated with the process, accessed
  through the repository abstraction.
- Approval delivery Telegram bot (D-0025), same process as the engine
  for MVP simplicity.
- Deployment host TBD (see pre-apply-checklist.md B5).

Prior text kept below for historical context.

---

Historic wording (pre-decision):
**Target: Option A** once we decide language + host.

**Acceptable interim (if we must ship before that decision): Option B**, with the DST-toggle job itself codified as an approved routine that:

- reads the current date,
- computes which of the two triggers (`<name>-cdt` / `<name>-cst`) should be enabled,
- calls `update_trigger` twice (enable one, disable the other),
- runs once on the Friday preceding each US DST-transition weekend
  (or, more robustly, on any weekend where the mapping is wrong).

The DST-toggle routine itself is deterministic and safe (it only calls `update_trigger`; it never touches Alpaca).

**Option C is off the table.** **Option D is not recommended.**

## 5. Interaction with the state store (D-0018)

Option A gives the ladder engine a natural home for the persistent state store (SQLite file, JSON journal, or KV store colocated with the process). Recovery on restart is a single `state.load(symbol)` call.

Under Option B, the state store must live on shared infrastructure the trigger runtime can reach: either the broker note (small pieces only), or a hosted key-value store (adds a dependency), or the `environment_variables` on the trigger record (very limited size, not designed for mutable state — **do not use** for state).

That difference is another reason to prefer Option A.

## 6. What this design does NOT decide

- **Language / runtime** — deferred, no repo evidence forces the choice yet.
- **State-store medium** (SQLite vs Postgres vs JSON file vs KV) — deferred to A/B outcome.
- **Debounce policy** — D-0011 remains TBD.
- **Trading universe** — D-0013 remains TBD.
- **Cloud vs self-hosted** — deferred; either is compatible with Option A.

## 7. Blocker status for APPLY

- **BLOCKER-SCHED.** APPLY the corrected `tsla-paper-trading-monitor` prompt on the existing trigger runtime is possible **only under Option B**, and Option B is unfinished — it requires the two-trigger split and a DST-toggle mechanism, neither of which has been designed in detail or approved.
- Under Option A, the corrected prompt would run on a new host; that host does not yet exist.
- Either way, APPLY today would ship the corrected prompt while still leaving a scheduler/DST gap. Controller must explicitly accept that gap (and how long it stands) before APPLY.
