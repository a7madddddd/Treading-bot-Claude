# Decision Log

Append-only. Never rewrite an approved decision — supersede it with a new
entry that references the old one.

Each entry:

- Date
- Decision ID
- Title
- Status: PROPOSED / APPROVED / SUPERSEDED / REJECTED
- Context
- Decision
- Rationale
- Supersedes (if any)
- Approved by

---

## D-0001 — Initial approved trading strategy

- **Date:** 2026-09-13
- **Status:** APPROVED
- **Approved by:** Controller (project owner)
- **Context:** First codification of the trading policy from the project
  specification.
- **Decision:** Adopt the laddered entry with fixed Floor as documented in
  `strategy.md` (Buy 10 @ 0%, Ladder 1 +10 @ −5%, Ladder 2 +20 @ −8%,
  Floor SELL ALL @ −10%, all relative to the ORIGINAL INITIAL ENTRY FILL
  PRICE). Trailing Floor as documented in `strategy.md` §5.
- **Rationale:** Baseline policy for the paper-trading system. All future
  variations start as experiments and require a new APPROVED decision to
  become policy.
- **Supersedes:** none.

## D-0002 — Paper trading only

- **Date:** 2026-09-13
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** The system is paper trading only. Live trading is not
  enabled and requires explicit Controller change to this decision.
- **Rationale:** Safety while the system is being built and evaluated.

## D-0005 — Timezone: routines run on US Central Time

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** All trading routines are scheduled in **US Central Time
  (America/Chicago)**. Controller's local timezone (UTC+3) is NOT used
  for routine scheduling. The scheduler must be **timezone-aware** so
  DST transitions (CST ↔ CDT) are handled automatically.
- **Rationale:** The strategy targets the US equity session; aligning
  routines to CT keeps pre-market / open / midday / EOD anchored to the
  market clock regardless of the Controller's local time.
- **Note:** A Middle-East workweek layer may be added later as a
  separate scheduling consideration; not now.

## D-0006 — Calendar: US market workdays only

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** Routines run on the **US market trading calendar** —
  weekdays only, with US market holidays and early-close days
  respected. Weekends are skipped.
- **Rationale:** No point running strategy routines when the US market
  is closed.

## D-0007 — Ladder-approval expiration: 5 minutes AND within 0.5%

- **Date:** 2026-09-14
- **Status:** APPROVED (supersedes the earlier deferral of D-0007)
- **Approved by:** Controller
- **Decision:** A Ladder approval is valid for **at most 5 minutes**
  AND the market price must remain **within ±0.5% of the trigger
  price** at the moment of order submission. If either condition is
  violated, the approval expires and a new Controller approval is
  required.
- **Rationale:** Prevents an approval at one price from executing at a
  materially different price during fast-moving conditions.
- **Enforcement:** the execution engine must re-check both conditions
  immediately before submitting the paper order, not just at the
  moment approval is received.

## D-0008 — Trailing-floor calculation is threshold-based

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** When a trailing threshold is crossed, the new trailing
  floor is computed as **threshold_price × 0.95**, NOT as
  actual_tick × 0.95. This makes the ratchet deterministic and
  reproducible regardless of tick stream jitter or gaps.
- **Rationale:** Determinism during MVP; safer under gap-up conditions
  than tick-based (never claims a locked-in higher stop than the
  threshold rule guarantees).
- **Supersedes:** clarifies D-0001 and D-0004 for the "current market
  price" wording.

## D-0009 — Partial fills: reference-price basis

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:**
  - The **original** Ladder / Floor reference (frozen initial entry
    fill price) is frozen **only after the initial entry order is fully
    filled or fully reconciled** (all of: filled, cancelled, or
    expired).
  - The **trailing-floor basis** (weighted-average filled entry price)
    updates continuously as fills reconcile.
- **Rationale:** Matches the source spec's "frozen after initial entry
  is fully reconciled" and preserves a live risk view for the trailing
  layer.

## D-0010 — Partial fill + cancel/expire of the initial entry

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** If the initial entry order is partially filled and then
  cancelled or expired, treat the **actual filled quantity** as the
  initial position and use the **weighted-average filled price** as the
  original reference. Unfilled shares are NOT part of the position and
  are NOT retried.
- **Rationale:** Keeps behavior explicit and safe; no invention of
  additional retry/roll policy.

## D-0011 — Ladder trigger debounce — PROPOSED (refined)

- **Date:** 2026-09-14 (proposal; trigger-source part superseded by
  D-0012; wording refined this session after Controller review of the
  BLOCKED_EXPIRED treatment)
- **Status:** **PROPOSED** — awaiting Controller final approval
- **Proposed by:** Claude (analysis in `docs/trading/debounce-analysis.md`)
- **Proposal (refined):** Ladder trigger debounce is a **per-level
  state machine** with **asymmetric re-arm** — hysteresis is applied
  only to market-driven blocks, not to Controller-availability
  expirations. Full wording is in `debounce-analysis.md §9`.
  Summary:
  - States per Ladder level per trade: `IDLE`, `PROPOSAL_PENDING`,
    `APPROVED`, `EXECUTED`, `REJECTED`, `BLOCKED_EXPIRED`,
    `BLOCKED_PRICE`, `BLOCKED_FLOOR_PRIORITY`.
  - Terminal (no further activity this trade): `EXECUTED`, `REJECTED`,
    `BLOCKED_FLOOR_PRIORITY`.
  - `BLOCKED_EXPIRED` → `IDLE` with `armed = True`. Next scheduled
    check may re-ask if trigger still holds. **Justification:**
    expiration is a human-availability event, not a market signal;
    under the hourly cadence (D-0021) this is bounded to ≤ 7 asks per
    Ladder per day and preserves responsiveness to a live
    opportunity. If cadence ever tightens to sub-minute intervals,
    revisit as a follow-up D-0011 revision.
  - `BLOCKED_PRICE` → `IDLE` with `armed = False`. Re-arm requires
    `p_last ≥ trigger × 1.005` on a subsequent check (D-0007's ±0.5%
    band, reused; no invented constant).
  - Proposal creation gate: `state == IDLE AND armed == True AND
    p_last ≤ trigger AND trigger > active_floor_price`.
  - Alpaca `client_order_id = proposal.id` provides broker-side
    idempotency (D-0025).
  - All debounce state persisted in SQLite (D-0024); survives
    restart and reconciliation. On restart, any pending proposal
    older than 5 minutes is transitioned to `BLOCKED_EXPIRED`
    (and thus back to IDLE with armed=True).
- **Rationale:** state handles duplicate proposals/orders;
  asymmetric re-arm handles market-driven oscillation
  (BLOCKED_PRICE) while preserving responsiveness after a temporary
  Controller absence (BLOCKED_EXPIRED); 0.5% reuses an
  already-approved constant.
- **Note:** the trigger-source half of the original D-0011 (trade
  prints vs bars vs quote midpoint) is settled by D-0012 (Alpaca
  Last Trade).
- **Decision required:** Controller review of the refined
  `debounce-analysis.md` and explicit APPROVED / REJECTED / MODIFIED
  response.

## D-0012 — Market data source: Alpaca Last Trade for ladder triggers

- **Date:** 2026-09-14
- **Status:** APPROVED (supersedes the earlier deferral of D-0012)
- **Approved by:** Controller
- **Decision:** Ladder trigger evaluation uses the **latest valid trade
  price** from Alpaca's Last Trade endpoint
  (`https://data.alpaca.markets/v2/stocks/{SYMBOL}/trades/latest`).
  A Ladder trigger fires when this price is **at or below** the
  configured ladder trigger price.
- **Rationale:** Uses the same source the venue reports for the
  symbol; keeps the ladder condition simple and consistent with what
  is already wired into the live routine.
- **Open item:** Tier (IEX free vs SIP paid) and debounce policy are
  still open — see D-0011 for debounce, and the choice between feeds
  should be revisited if IEX gaps affect trigger reliability on the
  chosen universe.

## D-0014 — Four live Routines discovered and snapshot into the repo

- **Date:** 2026-09-14
- **Status:** APPROVED (as a record of state; not a change to policy)
- **Approved by:** Controller
- **Decision:** The four live account-level Routines discovered on
  2026-09-14 have been snapshotted into `routines/` (prompts, with
  Alpaca keys redacted, plus metadata). See
  `docs/trading/routine-policy-alignment.md` for how each aligns with
  the approved policy. The live Routines have NOT been altered.
- **Open follow-ups:**
  - Rotate the leaked Alpaca paper API keys and update each live
    trigger via `update_trigger` to reference env vars.
  - Reconcile the divergences in `tsla-paper-trading-monitor` (either
    fix the routine to match approved policy, or supersede the affected
    decisions to match the routine, or disable the routine).
  - Decide whether the Wheel strategy and Capitol Trades mirror
    strategy are (a) new approved policies, (b) experiments, or
    (c) disabled.

## D-0013 — Trading universe remains TBD

- **Date:** 2026-09-14
- **Status:** DEFERRED (explicit TBD)
- **Approved by:** Controller (as a deferral)
- **Decision:** The trading universe (single symbol / fixed list /
  dynamic watchlist) remains TBD until the architecture inspection is
  complete and the Controller decides.

## D-0015 — Routine 1 (`tsla-paper-trading-monitor`) will be fixed to match approved policy

- **Date:** 2026-09-14
- **Status:** APPROVED (directive, pending APPLY)
- **Approved by:** Controller
- **Decision:** The live TSLA Paper Trading Monitor will be rewritten so
  it conforms to the approved Ladder Strategy. The approved policy
  remains the source of truth; the routine is corrected, not the
  policy. Specifically: original references frozen from initial fill
  (D-0001, D-0009); original Floor is not cancelled/re-placed on
  ladder fill; trailing math is compounded thresholds × 0.95
  (D-0004, D-0008); Ladder 1 and Ladder 2 require Controller
  approval per trigger with 5-min / ±0.5% expiration (D-0003, D-0007).
- **Enforcement rule:** approval age ≤ 5 minutes AND `abs(current − trigger)/trigger ≤ 0.005` must both re-pass immediately before order submission.
- **Application:** proposed prompt lives at
  `routines/tsla-paper-trading-monitor/prompt-proposed.md`. Requires
  Controller "APPLY THE CHANGES" before `update_trigger` is called.

## D-0016 — Routine 2 (`tsla-wheel-hourly-monitor`) will be disabled

- **Date:** 2026-09-14
- **Status:** APPROVED (directive, pending APPLY)
- **Approved by:** Controller
- **Decision:** The TSLA Wheel Hourly Monitor will be disabled on the
  live trigger. Its prompt and metadata remain in the repo for future
  research reference. `tsla-wheel-daily-summary` is untouched (read-only).
- **Rationale:** Options wheel is not the approved strategy and
  auto-executes without approval. May be revisited later as a separate
  experiment.

## D-0017 — Routine 3 (`capitol-trades-copy-ro-khanna`) becomes research-only

- **Date:** 2026-09-14
- **Status:** APPROVED (directive, pending APPLY)
- **Approved by:** Controller
- **Decision:** Refactor from an auto-executing trade-copier to an
  **independent research/signal-collection source**. It must not place
  Alpaca orders. It must not buy or sell. It emits structured findings
  (politician, security, ticker, transaction type, disclosed date,
  publication date, size range, source URL, extraction timestamp,
  confidence/validation status) into the research pipeline alongside
  Perplexity, for Controller review.
- **Rationale:** Preserves the research value of congressional
  disclosures without violating the approved-approval and
  approved-strategy rules. The Controller remains the decision maker.
- **Application:** proposed prompt lives at
  `routines/capitol-trades-copy-ro-khanna/prompt-proposed.md`.

## D-0018 — State management: deterministic and recoverable

- **Date:** 2026-09-14
- **Status:** APPROVED (directive)
- **Approved by:** Controller
- **Decision:** The trading engine must maintain deterministic,
  recoverable state for at least the fields listed in
  `docs/architecture/state-management.md`. State must survive a
  routine restart. No exclusive reliance on in-memory variables.
  The concrete store (file, DB, broker-note, etc.) is TBD pending
  language/runtime choice; the design principle is fixed here.

## D-0019 — Research architecture: Perplexity and Capitol Trades are independent sources

- **Date:** 2026-09-14
- **Status:** APPROVED (directive)
- **Approved by:** Controller
- **Decision:** Perplexity research and Capitol Trades research
  operate side-by-side as independent evidence sources. The research
  synthesis layer may compare them but must not blindly merge.
  Findings are typed as FACT / SOURCE / INFERENCE / HYPOTHESIS /
  RECOMMENDATION. Research never directly changes approved policy and
  never submits trades. See `docs/architecture/research-sources.md`.

## D-0022 — Language/runtime: Python

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** Implementation language is **Python** for the trading
  engine, routines, scheduler, state layer, notifications, Perplexity
  and Capitol Trades research, backtesting, and verification tools.
- **Rationale:** Mature Alpaca SDK (`alpaca-py`), broad backtesting
  ecosystem (`vectorbt`, `backtrader`, `zipline-reloaded`),
  first-class support for TZ-aware scheduling
  (`ZoneInfo`, `APScheduler`), simple SQLite integration, easy
  Telegram bot libraries.
- **Scope:** Applies to all new code in this repo. Third-party CLIs
  used from Python (curl, jq) are allowed for ad-hoc scripts but not
  for production logic.

## D-0023 — Scheduler: persistent TZ-aware process on America/Chicago

- **Date:** 2026-09-14
- **Status:** APPROVED (Option A per `scheduler-design.md`)
- **Approved by:** Controller
- **Decision:** Scheduling runs inside a persistent Python process
  using an America/Chicago TZ-aware scheduler (concrete choice
  APScheduler with `ZoneInfo("America/Chicago")` — swappable if a
  better option is found before implementation). DST is handled
  automatically. The routine bodies from `routines/*/prompt-proposed.md`
  become tasks the scheduler invokes.
- **Rationale:** Eliminates the DST drift of UTC-only crons on the
  existing trigger runtime. Colocates the scheduler with the ladder
  engine and the SQLite state store.
- **Migration:** The existing account-level Routines
  (trigger runtime) are NOT the target scheduler. On APPLY, they will
  be disabled (or reduced to no-op) and the Python process takes over.
  Migration steps are documented in `scheduler-design.md §4`.
- **Deployment target (host):** TBD — see "Remaining decisions"
  below. Not blocking design.

## D-0024 — State store: SQLite behind a repository abstraction

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** MVP state store is a local **SQLite** database file,
  accessed exclusively through a **repository abstraction** (typed
  Python interface). Direct SQL from routine or engine code is
  forbidden — everything goes through the repository.
- **Rationale:** SQLite gives ACID writes, easy testability, simple
  backup, and no operational dependency. The repository abstraction
  keeps swapping to Postgres/KV/etc. cheap if scale requires it later.
- **Contract:** the fields and invariants listed in
  `docs/architecture/state-management.md` are the source of truth for
  what the repository must expose.
- **Migration policy:** if the store is ever migrated, the new
  implementation must pass the same repository contract tests before
  APPLY.

## D-0025 — Approval transport: Telegram bot with inline buttons

- **Date:** 2026-09-14
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** Ladder proposals are delivered to the Controller by a
  **Telegram bot** message that carries an inline keyboard with
  `Approve` and `Reject` buttons. The bot writes the answer, its
  timestamp, and the Controller's Telegram user id to the state
  store; the trading engine reads it from there.
- **D-0007 enforcement:** the engine, not the bot, is the authority.
  Regardless of when the Approve button is pressed, the engine re-checks
  immediately before order submission that (a) `now − approval_received_at ≤ 5 minutes` AND (b) `abs(current_last_trade − trigger)/trigger ≤ 0.005` AND (c) the trigger price still exceeds the active protective floor. If any fails, the proposal is marked
  `blocked_price` / `blocked_expired` / `blocked_floor_priority` and
  no order is submitted; a new approval is required.
- **Authorization:** the bot must accept approvals only from
  Controller-authorized Telegram user id(s). Any Approve/Reject press
  from another user id is discarded and logged.
- **Failure semantics:** a Telegram send failure never triggers an
  order retry (CLAUDE.md §6). If the message can't be delivered, the
  proposal is marked `notification_failed` and a CRITICAL log entry
  is emitted; the Controller receives no order until re-notification
  succeeds.
- **Rationale:** Fastest, simplest mobile approval loop that satisfies
  D-0003 and D-0007 without building a web UI.

## D-0026 — Trading universe: dynamic / market-adaptive, symbol-agnostic engine (TSLA is TEST-ONLY)

- **Date:** 2026-09-14 (revised same-day per Controller clarification)
- **Status:** APPROVED (architectural principle)
- **Approved by:** Controller
- **Decision:** The trading universe is NOT hardcoded and NOT limited
  to any single symbol. The system supports a **dynamically generated
  universe** that changes as market conditions change.
  - The strategy engine is **fully symbol-agnostic**. It has no
    TSLA constants or any other hardcoded ticker. It consumes the
    current approved universe as an input.
  - **Universe discovery / selection** is a separate subsystem from
    strategy execution. Its job is to answer: *"What are the best
    trading opportunities available today under the approved strategy
    and risk rules?"*
  - The universe-generation mechanism, screening criteria, ranking
    rules, refresh frequency, and approval process are **NOT** defined
    yet and **must not be invented**. That work is a separate future
    decision (opportunity-ranking criteria are also deferred).
  - The architecture must support future dynamic selection **without**
    changing the strategy engine.
- **TSLA is TEST-ONLY.** TSLA is used as a development / testing symbol
  in the live `tsla-paper-trading-monitor` routine and in fixtures /
  simulations only. TSLA must NOT become:
  - the production trading universe,
  - the default trading symbol,
  - a hardcoded production candidate,
  - a default recommendation,
  - a fallback symbol if universe discovery fails.
- **No-universe behavior:** If a valid production universe cannot be
  generated at run time, the system MUST NOT fall back to TSLA and
  MUST NOT trade. The engine reports the empty-universe condition,
  raises an OPTIONAL/IMPORTANT notification per severity, and waits.
  Silence is preferable to fabricated candidates.
- **Guardrail:** Dynamic selection does NOT grant permission for
  autonomous trading. All existing approval (D-0003, D-0007), execution
  (D-0001, D-0004, D-0008), and risk controls remain authoritative.
  A new symbol entering the universe is a **candidate**, not an
  authorized trade — the initial entry still requires Controller
  approval.
- **Supersedes:** D-0013's "TBD" — architectural principle now
  approved; concrete selection mechanism and opportunity-ranking
  criteria remain TBD.
- **Application:** the existing `tsla-paper-trading-monitor` routine
  remains TSLA-scoped only because TSLA is the test symbol; the
  underlying engine has no TSLA anywhere in its logic. There is no
  interim "approved universe = ['TSLA']" for production.

## D-0021 — Market-open anchor and pre-market research anchor

- **Date:** 2026-09-14
- **Status:** APPROVED (schedule anchors)
- **Approved by:** Controller
- **Decision:**
  - `tsla-paper-trading-monitor` first pass at **08:30 America/Chicago**
    (US regular-session open), then hourly on the half-hour through
    14:30 CT. Cron under a TZ-aware scheduler: `30 8-14 * * 1-5`.
  - `capitol-trades-copy-ro-khanna` runs at **07:00 America/Chicago**.
- **Rationale:** The approved market-open time is 08:30 CT, not
  09:00. The Controller directed that this must not be silently
  changed. Pre-market research at 07:00 CT lands findings before the
  open, before the monitor's first pass.
- **Enforcement:** every schedule proposal must show cron +
  intended CT wall-clock + current-DST UTC equivalent. See
  `timezone-audit.md`.

## D-0020 — Timezone: America/Chicago, DST-aware, never a fixed-UTC schedule

- **Date:** 2026-09-14
- **Status:** APPROVED (clarification of D-0005)
- **Approved by:** Controller
- **Decision:** All routine schedules are expressed in America/Chicago
  and must be run under a TZ-aware scheduler. The current live
  triggers store fixed UTC cron expressions, which drift by an hour
  across DST transitions. See
  `docs/trading/timezone-audit.md` for the audit and the
  intended CT wall-clock times each schedule should produce.
- **Enforcement:** whenever a schedule is proposed, reviewed, or
  changed, present the cron expression together with (a) the intended
  America/Chicago wall-clock time and (b) the current-DST equivalent
  UTC time. Do not silently convert.

## D-0004 — Trailing-floor thresholds are compounded 5% steps

- **Date:** 2026-09-13
- **Status:** APPROVED
- **Approved by:** Controller
- **Context:** The source spec showed two arithmetic patterns for the
  trailing thresholds after activation. The example numbers ($115.50,
  $121.28, ~$115.22) match a compounded 5% ratchet, not a linear
  +5% step off the weighted-average entry.
- **Decision:** Trailing thresholds compound from the previous threshold:
  activation = avg_entry × 1.10; next = previous × 1.05. The trailing
  floor at each threshold is 5% below the market price used at that
  threshold (i.e. threshold × 0.95 when the threshold is exactly reached).
- **Rationale:** Matches the numerical examples in the source spec.
- **Supersedes:** none. Clarifies D-0001.

## D-0003 — Controller approval required for entries and ladders

- **Date:** 2026-09-13
- **Status:** APPROVED
- **Approved by:** Controller
- **Decision:** The system may automatically monitor, compute, detect
  triggers, prepare orders, and notify. It must not submit initial
  entries, Ladder 1, Ladder 2, or discretionary entries without
  Controller approval. Approved protective exits (original Floor,
  activated Trailing Floor) may execute automatically at their approved
  triggers. See `execution.md`.
- **Rationale:** Human-in-the-loop for offensive orders; deterministic
  automation for defensive exits.
