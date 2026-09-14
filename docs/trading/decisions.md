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

## D-0011 — Ladder trigger debounce — APPROVED

- **Proposed:** 2026-09-14 (analysis in `docs/trading/debounce-analysis.md`)
- **Refined:** 2026-09-14 (asymmetric re-arm added after Controller review)
- **Approved on:** 2026-09-14
- **Approved by:** Controller
- **Status:** **APPROVED**
- **Trigger-source half:** the trigger-source part of the original
  D-0011 (trade prints vs bars vs quote midpoint) is settled by
  D-0012 (Alpaca Last Trade).

### Approved policy wording

> Each Ladder level (Ladder 1, Ladder 2) has, per trade, a state
> machine over the following states:
> `IDLE`, `PROPOSAL_PENDING`, `APPROVED`, `EXECUTED`, `REJECTED`,
> `BLOCKED_EXPIRED`, `BLOCKED_PRICE`, `BLOCKED_FLOOR_PRIORITY`.
>
> **Terminal states (no further activity on this level for the trade):**
> `EXECUTED`, `REJECTED`, `BLOCKED_FLOOR_PRIORITY`.
>
> **Non-terminal block outcomes:**
> - `BLOCKED_EXPIRED` (the D-0007 5-minute clock ran out with no
>   Controller response) → returns the level to `IDLE` with
>   `armed = True`. The next scheduled market check may create a new
>   proposal if the trigger condition still holds. Expiration is a
>   human-availability event, not a market signal; on the approved
>   hourly cadence (D-0021) this bounds notifications to at most one
>   per hourly check while the trigger holds.
> - `BLOCKED_PRICE` (D-0007 re-check found
>   `abs(current − trigger)/trigger > 0.005` at submit time) → returns
>   the level to `IDLE` with `armed = False`. Re-arm requires the
>   Alpaca Last Trade price to reach `trigger × 1.005` on some
>   subsequent scheduled check. The 0.005 re-arm margin reuses the
>   ±0.5% material-move band already approved in D-0007. No new
>   debounce/re-arm numeric value is introduced.
>
> **Proposal creation gate:**
> A new proposal is created only when the level is in `IDLE` AND
> `armed == True` AND `p_last ≤ trigger` AND
> `trigger > active_floor_price`.
> `armed` transitions to `False` at the moment a proposal is created.
>
> **Idempotency:**
> Alpaca `client_order_id = proposal.id` (D-0025) provides
> broker-side idempotency, so a duplicate submit attempt from any
> source (retry, race, restart) is refused at Alpaca even before the
> state machine catches it.
>
> **Persistence:**
> All debounce state (per trade, per level) is persisted in SQLite
> via the D-0024 repository abstraction and survives process restart
> and broker reconciliation. On restart, any proposal older than
> 5 minutes with no recorded approval is transitioned to
> `BLOCKED_EXPIRED` (and thus back to IDLE with `armed = True`)
> before the engine acts.
>
> **Future-work note:** the asymmetric treatment of `BLOCKED_EXPIRED`
> vs `BLOCKED_PRICE` is calibrated to the current hourly scheduler
> cadence (D-0021). If cadence ever tightens to sub-minute intervals,
> this rule should be revisited as a follow-up D-0011 revision.

### Rationale (preserved)

- Aligned with the already-approved strategy (`strategy.md §3` —
  "each Ladder can trigger only once per trade" is a state machine).
- State handles duplicate proposals, approvals, and orders;
  asymmetric re-arm handles market-driven oscillation
  (`BLOCKED_PRICE`) while preserving responsiveness after a temporary
  Controller absence (`BLOCKED_EXPIRED`).
- Reuses D-0007's approved ±0.5% material-move band as the re-arm
  threshold; no invented constants.
- Deterministic and replayable — behavior is a function of the ordered
  tick sequence and the per-level state.
- Survives restart via SQLite (D-0018 / D-0024) and integrates
  cleanly with reconciliation.

### Cross-references

- Full analysis and worked examples: `docs/trading/debounce-analysis.md`.
- State fields: `docs/architecture/state-management.md` §Approval
  workflow state and §Ladder trigger/execution state.
- Approval loop: `docs/architecture/telegram-approval.md`.

### Change control

Any modification to this rule (including additional states, different
re-arm treatment for `BLOCKED_EXPIRED`, or a different numeric re-arm
threshold) requires a new dated entry in this decision log and
Controller approval per CLAUDE.md §9.

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

## D-0026 — GitHub-native data sources found and empirically verified

- **Date:** 2026-09-14
- **Status:** Controller redirected away from blocked financial-data
  domains toward GitHub/public-repository-reachable sources only. This
  entry records real, verified findings from that redirect. Still Phase
  2, research/verification scope; full acquisition still NOT authorized;
  D-0026 mechanism and every numeric parameter remain PROPOSED / NOT
  APPROVED; Phase 3 remains NOT approved.
- **Method:** used this environment's sanctioned `add_repo` mechanism
  (read-only, anonymous git clone of public repos — not a workaround of
  the blocked network policy) to actually clone and inspect candidate
  repositories, plus direct `raw.githubusercontent.com` fetches. Real
  files were fetched and read; nothing in this entry is inferred from
  search-result summaries alone where a direct check was possible.
  Cloned repositories were deleted from local disk after inspection; no
  bulk data was retained or committed to this project.
- **Market-regime data — SOLVED, improved:** `github.com/datasets/
  finance-vix` verified directly — 9,272 real rows, daily VIX OHLC from
  1990-01-02 through **2026-09-11** (current), licensed **PDDL (public
  domain)** — cleaner than the FRED dependency it replaces and than
  every option evaluated in prior passes.
- **Reference/identity data — solved for current-snapshot only:**
  `datasets/s-and-p-500-companies` (verified: real CSV including CIK
  directly) and `datasets/nasdaq-listings` (verified reachable), both
  PDDL-licensed. Does not solve point-in-time historical membership —
  only a clean, free, current identity layer.
- **Bulk historical OHLCV — PARTIALLY solved, with a new disclosed
  tradeoff:** `eliangcs/pystock-data` — cloned and verified directly:
  517 MB of real committed daily price data, **2009-01-01 through
  2017-03-31 only** (project frozen since), licensed **CC BY-SA 4.0**
  (no practical restriction for internal, non-redistributed use).
  **New gap introduced by this pivot:** no coverage after March 2017 —
  misses 2018 vol spike, 2020 COVID crash, 2022 bear market, and
  anything current. This is a real tradeoff versus the originally
  intended Stooq (which would have offered ongoing coverage, had its
  terms cleared), disclosed explicitly, not hidden.
- **Delisted-security test — run empirically, not assumed, on 5 real
  symbols (not generalized from one case, per standing project
  discipline):** Family Dollar (FDO, delisted 2015) **confirmed found**
  — 1,564 real price rows through the archive's cutoff date. RadioShack
  (RSH), Blockbuster (BBI), Dell (DELL), and Heinz (HNZ) **confirmed
  absent** — zero rows for all four. Result: **1 of 5 (20%) — a real
  number from a real small test, explicitly not extrapolated to a
  dataset-wide rate** without a full-scale run. Plausible (disclosed,
  not confirmed) explanation: the initial batch's price-bearing universe
  was 1,980 symbols, growing to 5,981 by March 2017 — suggesting a
  large/mid-cap-oriented starting universe that broadened over time,
  rather than full-market coverage from day one.
- **Cross-reference:** `docs/trading/github-native-data-sources.md`
  (full findings, licenses, structure, the delisted-security test, and
  the recommended path forward).
- **What did NOT change:** the D-0026 architecture, approval workflow,
  and every prior "still unresolved" item (true point-in-time universe
  membership, true spread/quote data, full-scale delisted-coverage
  measurement) remain exactly as before — this entry adds a working,
  verified data-access path; it does not close those gaps. No numeric
  parameter approved. No live routine, scheduler, cron, or dependency
  touched. TSLA not used as any calibration baseline or fallback.
- **Decision required from Controller:** (1) whether the March-2017
  coverage ceiling is an acceptable tradeoff versus continuing to seek
  access to Stooq/SEC/FRED directly (e.g., via the Controller's own
  machine); (2) whether to authorize extracting and canonicalizing
  `eliangcs/pystock-data`'s **full** contents (only small samples were
  inspected in this pass) into the local canonical dataset; (3) whether
  to independently verify `JerBouma/FinanceDatabase` and
  `zyhe16/top-us-stock-tickers` (surfaced but not tested this pass) as
  supplementary identity sources.

## D-0026 — Data-acquisition pilot: environment cannot reach any data source

- **Date:** 2026-09-14
- **Status:** Controller authorized a small, controlled Phase 2
  data-acquisition pilot (explicitly not Phase 3). This entry records
  that the pilot **could not execute**. Phase 2 remains APPROVED
  (research/design + now-attempted acquisition scope); D-0026 mechanism
  and every numeric parameter remain PROPOSED / NOT APPROVED; Phase 3
  remains NOT approved. Full acquisition and numeric calibration remain
  **NOT authorized**.
- **What happened:** tested direct retrieval (via `curl`, a different
  code path than the earlier `WebFetch` attempts) against the actual
  data-serving endpoints — not just terms pages — for every source in
  the approved architecture: Stooq (two domains), SEC EDGAR (two
  subdomains), Nasdaq Trader, FRED, and Yahoo's chart endpoint. **All
  blocked**, with the proxy's own log recording the identical cause for
  every failure: `"gateway answered 403 to CONNECT (policy denial or
  upstream failure)"`. Only `raw.githubusercontent.com` was reachable.
  This is a **systemic, environment-level network egress policy**
  (confirmed via the proxy's own allowlist, which includes only
  `api.anthropic.com`, `registry.npmjs.org`, `pypi.org`, and similar —
  no financial-data domain), not a per-source or per-page restriction,
  and not something addressable by retrying different URLs.
- **What was still produced, honestly, without fabrication:**
  - Pilot instrument selection (8 documented cases, deliberately not
    TSLA-only and not TSLA at all — AAPL, an unfixed-in-advance
    small/mid-cap class, SPY, META/FB for the ticker-change case, LEH/BBBY
    for the delisted case, KO/GE for long-history depth, a to-be-selected
    2025 IPO, TQQQ for the leveraged-ETF exclusion test, and a
    known-split case).
  - A refined, five-way canonical identity model (company identity via
    CIK / security-instrument identity via a new surrogate key / ticker
    history / listing-exchange identity / trading history) — this
    **corrects and supersedes** the simpler CIK-anchored design in
    `free-root-data-source-recommendation.md §8`, which risked
    conflating "company" and "tradable security" as always one-to-one,
    per the Controller's own instruction to not assume CIK alone
    represents every instrument.
  - A WHAT WE KNOW / WHAT WE CAN INFER / WHAT WE CANNOT KNOW decomposition
    of point-in-time universe membership — this did not require live
    data and stands as a real finding.
  - Ten PASS/FAIL data-quality gates, defined for future evaluation once
    real pilot data exists.
  - The `DataCoverageLog` table structure, populated with real values
    only where retrieval actually succeeded (confirmation that the
    community GitHub delisted-list is reachable and lists the case-E
    delisted symbols) and explicitly marked "NOT RETRIEVED" everywhere
    else, with the reason given once rather than repeated as if it
    varied by symbol.
- **Final decision block:** STOOQ PILOT = INCONCLUSIVE (not FAIL — this
  is not a finding about Stooq itself); DELISTED PRICE HISTORY =
  INCONCLUSIVE; POINT-IN-TIME UNIVERSE = PARTIAL (this one finding did
  not depend on live retrieval); CANONICAL IDENTITY = INCONCLUSIVE
  (design complete, empirically untested); FULL ACQUISITION = NOT
  AUTHORIZED; NUMERIC D-0026 CALIBRATION = NOT AUTHORIZED.
- **Cross-reference:** `docs/trading/data-acquisition-pilot.md` (full
  pilot design, exact curl evidence of the network blocker, refined
  identity model, quality gates, final decision block).
- **What did NOT change:** the approved free architecture (Stooq
  primary; SEC EDGAR/Nasdaq Trader/FRED/curated leveraged-list
  secondary; Yahoo optional) is unchanged in shape — this entry records
  an execution blocker, not a design or provider revision. No live
  routine, scheduler, cron, or dependency touched. No numeric parameter
  approved. No data fabricated. TSLA not used as any calibration
  baseline or fallback. No attempt made to circumvent the network policy.
- **Decision required from Controller:** how to obtain an execution
  environment whose network egress can actually reach the approved data
  sources — options are the Controller's own machine, a differently-
  configured session with a broader or explicitly-allowlisted network
  policy (if available and the Controller chooses to request it), or
  another execution context the Controller controls. **This pilot cannot
  be successfully re-run in an identically-configured session** — the
  design is sound; the environment cannot execute it.

## D-0026 — Final verification pass: legal-terms block confirmed, Yahoo downgraded

- **Date:** 2026-09-14
- **Status:** Controller approved the free-only architecture
  **directionally**; explicitly withheld authorization to begin data
  acquisition pending one final verification pass. This entry records
  that pass's findings. Phase 2 remains APPROVED (research/design scope,
  unchanged); D-0026 mechanism and every numeric parameter remain
  PROPOSED / NOT APPROVED; Phase 3 remains NOT approved. Data acquisition
  is **still not authorized to begin**.
- **Environment constraint disclosed:** attempted to fetch Stooq's and
  Yahoo's primary terms-of-use pages directly via both `WebFetch` and raw
  `curl` (6 distinct URLs across stooq.com, stooq.pl, legal.yahoo.com,
  guce.yahoo.com, policies.yahoo.com, web.archive.org). **All blocked by
  this session's network egress proxy** (confirmed via
  `curl → CONNECT tunnel failed, response 403`), not a content or
  refusal issue. Classified honestly as unverified rather than inferred
  from secondary sources.
- **Stooq:** every legal/licensing item (automated retrieval, bulk
  retrieval, local storage, research use, commercial-use restriction,
  redistribution, derived-metric retention) remains **UNCLEAR — VERIFY
  DIRECTLY**; no search-indexed quotation of Stooq's actual terms text
  was found in either research pass. Only technical facts (CSV download
  mechanism, an observed but undocumented daily rate-limit message)
  remain confirmed, unchanged from before.
- **Yahoo — two new findings that downgrade its role:**
  1. A search-surfaced (not self-fetched) quotation of Yahoo's actual
     ToS bars commercial exploitation without written permission and
     grants only a "personal... non-exclusive license" — consistent
     with internal research use, but not independently confirmed by
     Claude.
  2. **New: a March 2025 report (via `yfinance` GitHub issue tracking)
     indicates Yahoo may now gate historical data retrieval behind a
     paid plan**, with only current prices remaining free — a capability
     risk not known at the time of the prior document, separate from the
     legal-terms question.
  3. **Confirmed (previously only "unconfirmed"): Yahoo does not provide
     data for delisted firms at all** — a direct third-party statement,
     closing an open item from the prior document in the negative.
  4. Multiple 2025 GitHub issues show `yfinance` throwing
     "possibly delisted" errors for clearly-listed major symbols
     (AAPL, TSLA, ^GSPC), corroborating general endpoint instability with
     fresh, dated evidence.
- **Survivorship-bias four-part decomposition performed, as requested:**
  (A) historical identity coverage — solvable free, unchanged; (B)
  point-in-time universe membership on a specific date — **not solvable
  free**, confirmed unchanged; (C) price coverage for currently-known
  names — solvable, Yahoo's specific role now more uncertain; (D) price
  coverage specifically for delisted names **after** identifying them —
  **still not confirmed solvable free**, and Yahoo is now a **confirmed
  no** for this specific item rather than an open question.
- **Bias-control design (Controls A-D) specified:** Control A
  (current-survivor baseline, explicit bias label), Control B (expanded
  identity universe with measured partial delisted-price coverage),
  Control C (compare conclusions with vs. without the retrievable
  delisted subset), Control D (mandatory `DataCoverageLog` disclosure —
  total candidates / % with full history / % delisted-with-history / %
  delisted-without — attached to every future evidence package).
- **Strategy mechanics reconfirmed unchanged:** D-0011, D-0012, D-0007,
  ladder 5/8/10 spacing, D-0026 architecture, CRASH-tightens (not
  EMPTY), EMPTY-on-failure handling — all explicitly checked and
  untouched.
- **Cross-reference:** `docs/trading/free-data-verification-pass.md`
  (full analysis; final decision block; expanded evidence checklist —
  adds two new items to `historical-data-calibration-plan.md §13`:
  independent confirmation of Stooq's terms, and re-verification of
  Yahoo's current free-tier capability — both required before any
  Stooq/Yahoo-sourced evidence supports a numeric parameter approval).
- **What did NOT change:** Norgate remains rejected/superseded; the
  directionally-approved architecture (Stooq root; SEC EDGAR, Nasdaq
  Trader, FRED, curated leveraged/inverse list as secondary) is
  unchanged in shape — only Yahoo's scope within it is narrowed pending
  re-verification. No data purchased or downloaded. No code written. No
  live system touched. TSLA not used as any form of fallback or
  calibration shortcut.
- **Decision required from Controller:** how to resolve the two UNCLEAR
  legal-terms items given this environment cannot reach the primary
  sources — options include manual verification by the Controller
  directly, or deferring the check to a future session/tool with
  unrestricted web access. **Data acquisition remains not authorized
  until this is resolved**, per the Controller's own explicit condition
  for this verification pass.

## D-0026 — Norgate recommendation REJECTED; free-only architecture recommended

- **Date:** 2026-09-14
- **Status:** Supersedes the Norgate recommendation below. Phase 2
  remains APPROVED (research/design scope, unchanged); D-0026 mechanism
  and every numeric parameter remain PROPOSED / NOT APPROVED; Phase 3
  remains NOT approved.
- **Controller instruction:** no paid data provider or subscription of
  any kind. Norgate Data, Databento, Polygon/Massive, Tiingo, and every
  other commercial option are **rejected outright** — none will be
  purchased.
- **New research performed (evidence-based, not assumed):** compared SEC
  EDGAR, the Nasdaq Trader Symbol Directory, Stooq, Yahoo Finance
  (`yfinance`), Alpha Vantage's free tier, FRED, the Kenneth French Data
  Library, and a community-maintained SEC-EDGAR-derived delisted-stocks
  GitHub dataset, against the same requirement checklist as the rejected
  paid-provider comparison.
- **Recommended free architecture:**
  - FREE ROOT SOURCE: **Stooq** (bulk historical OHLCV + volume).
  - FREE SECONDARY SOURCES: SEC EDGAR (CIK identity, SIC sector, former
    names, delisted-symbol identification via the GitHub dataset),
    Nasdaq Trader Symbol Directory (current whole-market symbol/ETF
    reference), Yahoo Finance (cross-validation/gap-fill), FRED (VIXCLS
    market-regime series), a manually-curated leveraged/inverse ETF
    exclusion list (zero-cost, unavoidable regardless of provider).
  - LOCAL DATA STORE: same canonical-dataset design as the rejected
    Norgate document (Instrument/InstrumentHistory/DailyBar/etc.), now
    CIK-anchored for identity, plus a new `DataCoverageLog` table that
    measures and discloses, per delisted symbol, whether pre-delisting
    price history was actually retrievable.
- **Central honest finding — the survivorship-bias gap:** free sources
  can identify *which* securities were delisted and *when*
  (SEC-EDGAR-derived, reasonably complete, free), but **could not be
  confirmed** to provide their *pre-delisting price history* in bulk, for
  free, from any researched source. This is disclosed as the single most
  consequential limitation of the free architecture, not glossed over.
  Mitigation: measure and report the actual coverage rate empirically
  during Phase 2 rather than assume completeness.
- **Answer to "can we calibrate credibly on free data alone":** **YES,
  WITH LIMITATIONS.** The core strategy-mechanics calibration and the
  market-regime data (FRED VIXCLS — arguably stronger than the rejected
  document's own fallback proposal) are well supported. The
  survivorship-bias tier is only partially, measurably mitigated — any
  resulting calibration evidence is capped below the "Tier 1" full
  point-in-time fidelity the earlier calibration methodology
  (`historical-data-calibration-plan.md §4`) described, unless the
  measured coverage rate later proves sufficient.
- **Other confirmed gaps, unchanged in kind from the rejected document:**
  no true historical bid-ask spread/quotes from any free source (the
  labeled range-proxy is now the primary and only spread signal, not a
  fallback); no true point-in-time index/universe constituency product;
  no comprehensive free corporate-actions calendar; no leveraged/inverse
  ETF flag (curated list required regardless of provider).
- **Licensing caveats disclosed:** Stooq and Yahoo/yfinance both carry
  "personal/non-commercial use" caveats (Yahoo's explicitly documented;
  Stooq's inferred from a third-party academic summary, flagged for
  independent re-verification); SEC EDGAR, Nasdaq Trader, FRED, and the
  Kenneth French Data Library carry materially lower licensing risk as
  public government/academic sources.
- **Cross-reference:** `docs/trading/free-root-data-source-recommendation.md`
  (full research, coverage matrix against the A-T checklist, 15-step
  Phase 2 execution plan, 11-point Phase 2 exit criteria, explicit
  comparison table against the rejected Norgate option).
- **What did NOT change:** Phase 2's approved scope (research/design
  only; no live changes; D-0011/D-0012/D-0007/D-0021/D-0022/D-0023/
  D-0024/D-0025 all unchanged); TSLA remains TEST-ONLY, never used as a
  calibration baseline or fallback anywhere in the new research; no
  numeric parameter approved; no data purchased or downloaded; no code
  written.
- **Decisions still required from Controller:** confirm or revise the
  free-architecture recommendation in §20 of the new document;
  independently re-verify Stooq's and Yahoo's actual terms of use before
  relying on either; decide, once the delisted-price-history coverage
  rate is actually measured (not before), whether the resulting
  confidence level is sufficient or whether the "no paid provider"
  constraint should be revisited for that narrow purpose — explicitly
  not decided now.

## D-0026 — Phase 2 approved (research/data-collection scope only); root data source recommended

- **Date:** 2026-09-14
- **Status:** Phase 2 **APPROVED** (scope: historical data collection +
  offline calibration engine, research/design only). D-0026's mechanism
  and every numeric parameter remain **PROPOSED / NOT APPROVED**. Phase 3
  (live implementation) remains explicitly **NOT approved**.
- **Approved by:** Controller
- **Scope of the Phase 2 approval, explicitly bounded by the Controller:**
  does NOT authorize live Dynamic Universe implementation, changes to the
  live trading strategy, scheduler changes, cron changes, live routine
  changes, production universe changes, order execution, broker trading
  changes, automatic trading based on calibration results, or moving any
  D-0026 numeric parameter from PROPOSED/TBD to APPROVED. D-0011, D-0012,
  D-0007, D-0021, D-0022, D-0023, D-0024, D-0025 unchanged. TSLA remains
  TEST-ONLY, never a production or calibration-baseline fallback.
- **New requirement from Controller:** a durable root data resource /
  primary data provider covering the whole relevant US equity/ETF
  universe in bulk, so new symbols never require a fresh source search.
- **Research performed (evidence-based, official docs + independent
  reviews, not assumed):** compared Alpaca, Polygon/Massive, Tiingo,
  Norgate Data, Databento, Nasdaq Data Link, and Alpha Vantage against the
  full requirement list (delisted coverage, sector/GICS, point-in-time
  universe history, bulk access, cost, licensing).
  - **Confirmed disqualifying gap in every broker-style API** (Alpaca,
    Polygon, Alpha Vantage): no or weak delisted-symbol coverage —
    Polygon independently reported as "spotty at best" for delisted
    tickers by a third-party review; Alpha Vantage explicitly documented
    as having no delisted coverage at all.
  - **Recommended root source: Norgate Data** (US equities Platinum
    package, $630/year) — the only researched provider whose core stated
    purpose is survivorship-bias-free systematic-trading data, and the
    only one confirmed to include delisted securities, sector
    classification, AND point-in-time index/universe membership together,
    with 30+ years of history and genuinely bulk (whole-market, not
    per-symbol) access via Python.
  - **Confirmed gaps in Norgate:** no tick-level quotes/spread (daily-bar
    product); no confirmed leveraged/inverse-ETF-specific flag; Windows-
    native local-database access model (operational, not data, gap);
    exact stable-identifier mechanism unconfirmed. Addressed via a small
    curated leveraged/inverse exclusion list (needed regardless of root
    provider) and an optional secondary tick-quote source (Databento
    preferred, or Alpaca SIP), cost-gated pending Controller decision.
  - **Alpaca's role going forward:** remains the live execution venue and
    the source for D-0012's live Last Trade trigger price — both
    unchanged by this document — but is disqualified as the historical
    calibration root source.
- **Cross-reference:** `docs/trading/root-data-source-recommendation.md`
  (full comparison, canonical dataset design, symbol-identity design,
  bulk-ingestion realism check, refresh strategy, cost analysis, 13-step
  Phase 2 execution plan, 11-point Phase 2 exit criteria).
- **Decisions still required from Controller:** confirm or revise the
  Norgate recommendation; authorize the $630/yr subscription cost; decide
  the tick-quote secondary source (and its cost) or defer to the labeled
  range-proxy fallback; confirm provisioning a Windows environment for
  the Norgate Updater sync step; review Norgate's actual licensing terms
  once obtained. No data has been purchased or downloaded; no code has
  been written; nothing live has been touched.

## D-0026 mechanism — historical-data & calibration methodology (still PROPOSED)

- **Date:** 2026-09-14
- **Status:** PROPOSED / NOT APPROVED — Phase 1 (planning) only; no data
  collected, no code written
- **Proposed by:** Claude, per Controller direction to design the
  historical-data acquisition and calibration/backtesting methodology
  before any D-0026 numeric parameter is approved
- **Repository check performed first:** confirmed (full file listing) the
  repo contains 35 files, all documentation/config — zero data files,
  zero code, zero dependency manifests. Restated as FACT, not assumed.
- **Data-source research performed:** checked Alpaca's actual documented
  historical-data capabilities (via Alpaca's own docs and community
  forum, cited in the plan doc) rather than assuming. Key confirmed
  findings:
  - Alpaca provides ~7yr (SIP) / ~5yr (IEX) historical daily bars,
    historical quotes/trades endpoints, and a Corporate Actions API.
  - Alpaca does **not** appear to provide point-in-time historical
    universe/tradability-status history (only a current snapshot) —
    independently corroborated by multiple community-forum reports of
    broken/empty historical data for delisted or renamed symbols.
  - Alpaca does **not** appear to provide sector/GICS classification.
  - True historical bid/ask quotes may exist (better than the
    second-pass document assumed, which discussed only a range proxy),
    but IEX-only coverage is ~2.5% of volume and SIP requires a paid
    subscription — a real cost decision, not assumed away.
- **What the new document specifies (Phase 1, design only):** full data
  requirements per dataset (mandatory/optional, point-in-time and
  survivorship/look-ahead-bias analysis); an explicit spread-proxy
  honesty analysis (labeled approximation, never presented as true
  spread); a walk-forward multi-regime backtest split design (rejecting
  a fixed "last 3 years" default); a non-circular regime-segmentation
  validation method; a per-parameter calibration plan (data / hypothesis
  / candidate forms / metrics / failure mode / approval evidence /
  rejection evidence) for every §3 parameter, with Top-N explicitly
  flagged as needing operational (not just market) data; a
  strategy-mechanics-preservation section restating that the approved
  Ladder/Floor/D-0007/D-0011 logic is a frozen input, never adjusted to
  make the universe selector look better; a full metrics catalog split
  into universe-quality / strategy-outcome / execution-quality / risk
  categories; a non-TSLA control-experiment design (stage-A/B survivor
  set and random-N-per-day, explicitly not TSLA); sensitivity-testing
  design; overfitting/data-snooping safeguards (pre-registered metrics,
  single untouched holdout, no peeking); an expanded PROPOSED→APPROVED
  acceptance checklist; an explicit failure/insufficient-data policy
  table (every branch keeps the system PROPOSED/TBD, never "pick a
  reasonable number"); and a three-phase implementation boundary
  (Phase 1 planning — current; Phase 2 data collection + offline
  calibration engine; Phase 3 live implementation), each transition
  gated by explicit Controller approval.
- **What did NOT change:** no numeric parameter approved; D-0026
  architectural principle and mechanism both remain PROPOSED as
  previously recorded; TSLA test-only and no-fallback rules restated
  and honored (TSLA is explicitly excluded even as a calibration
  baseline).
- **Cross-reference:** `docs/trading/historical-data-calibration-plan.md`
  (this entry's subject document).
- **Decision required:** Controller decides whether to authorize Phase 2
  (historical-data acquisition + offline calibration engine build) —
  see the plan document §15/§16 for the specific sub-decisions (external
  data-source selection for point-in-time universe history and sector
  classification; whether to authorize a paid SIP data subscription).
  This document does not authorize Phase 2 on its own.

## D-0026 mechanism — third-pass configurable redesign (still PROPOSED)

- **Date:** 2026-09-14
- **Status:** PROPOSED / NOT APPROVED (the mechanism inside D-0026; the
  D-0026 architectural principle itself remains APPROVED as originally
  recorded above)
- **Proposed by:** Claude, per Controller direction to avoid freezing
  arbitrary numeric thresholds and instead redesign around configurable,
  data-calibratable parameters
- **What changed from the first-pass mechanism:**
  - Selection restructured into an explicit 9-stage pipeline (A.
    tradability → B. data quality → C. execution quality → D.
    strategy-mechanics fit → E. regime adaptation → F. ranking → G.
    concentration → H. Top-N → I. persist snapshot), replacing the
    first pass's single weighted composite score.
  - Every numeric threshold (liquidity floor, spread cap, ATR% band,
    regime thresholds, ranking weights, sector cap, correlation
    threshold, Top-N, warm-up/staleness) is documented as a
    **configurable parameter** with its controlling purpose, the
    strategy risk it addresses, required calibration data, and a
    recommended **form** (percentile-relative, liquidity-adjusted,
    formula-derived from Ladder/Floor geometry, or regime-dependent) —
    but **no specific value is proposed**.
  - CRASH-regime handling reversed from "return EMPTY" to "tighten
    execution-quality and volatility thresholds" — a laddered strategy
    often has its best entries during drawdowns, existing positions are
    already protected regardless of regime, and Controller approval
    already gates every new entry.
  - New-symbol one-scheduler-cycle delay dropped as unjustified —
    redundant with the existing gap between the universe refresh and
    the first strategy check.
  - The proposed "universe-dropped" execution veto on an
    approved-but-unsubmitted proposal is withdrawn; the existing
    D-0007 re-check remains the sole execution-time gate.
  - Introduced the `ApprovedUniverseSnapshot` contract as the sole
    interface between the universe subsystem and the symbol-agnostic
    strategy engine (`architecture/universe.md §2`).
  - Added a historical-data calibration process (data required, period,
    bar timeframe, metrics, comparison methodology, overfitting
    safeguards, and the evidence bar a parameter must clear before
    moving from PROPOSED to APPROVED) — `universe-selection-analysis.md
    §6`.
- **What did NOT change:** TSLA test-only, no fallback, symbol-agnostic
  engine, universe subordinate to strategy/risk/Controller/execution,
  Perplexity and Capitol Trades remain non-gating research annotations,
  existing positions unaffected by universe membership changes.
- **Cross-references:** `docs/trading/universe-selection-analysis.md`
  (third pass, the current proposed mechanism), `docs/trading/
  universe-parameter-validation.md` (second-pass evidence trail behind
  these design choices), `docs/architecture/universe.md` (engine
  boundary, updated to match).
- **Decision required:** Controller review of the principles (§7A) and
  architecture (§7B) in `universe-selection-analysis.md` — these are
  presented as safe to approve as *direction*. No numeric parameter is
  ready for approval; per Controller instruction, none should be forced
  before the historical-data calibration process (§6) is authorized and
  run.

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

## D-0027 — Final GitHub-native recency search: no free dataset closes the 2018-2026 gap

- **Date:** 2026-09-14
- **Status:** RESEARCH FINDING (not a policy/strategy decision; recorded
  for the append-only research trail per project convention)
- **Recorded by:** Claude, on Controller's explicit "one final
  GitHub-native research pass" request
- **Context:** Controller rejected `eliangcs/pystock-data` (2009-2017) as
  a sole D-0026 ROOT source due to lack of modern-regime coverage and
  requested one final targeted search for a free, GitHub-hosted, US
  equity/ETF OHLCV dataset reaching 2018-2026, with every candidate
  empirically verified (not taken from search-result descriptions).
- **Decision/Finding:** No new candidate qualifies.
  `irachex/open-stock-data` documents a GitHub-Releases-based bars
  pipeline but empirically has **zero published Release tags**
  (`git ls-remote --tags` returns nothing) — the pipeline has never
  actually produced retrievable data, disqualifying it on data-existence
  grounds, not license or quality grounds.
  `hanurd25/stock-data-collector` has real data (verified: `AAPL.csv`,
  20.6 MB) but only ~10 weeks of 1-minute bars for a handful of tickers
  (2026-07-02 to present) — no historical depth.
  `blumenty/stock-data-automation` explicitly retains only a rolling
  50-day window and depends on the paid Polygon.io API (with an exposed
  key in its own README) for its S&P 500 half — disqualified as
  non-historical and paid-provider-dependent.
  `PCnslt/stock-market-data` and `SteelCerberus/us-market-data` were
  eliminated on fit (wrong data shape; self-disclosed low quality)
  without needing a clone.
  **Conclusion: C — no free GitHub-hosted dataset found is good enough**
  to close the 2018-2026 recency gap. `eliangcs/pystock-data` remains the
  best available free root, valid only as a disclosed **pre-2018
  historical control**, not as a modern-regime-capable sole calibration
  source.
- **Rationale:** Exhaustive, empirically-verified search of the
  GitHub-reachable candidate space (within this environment's confirmed
  reachable hosts: `raw.githubusercontent.com` and anonymous git clone)
  found no dataset combining real historical depth with current
  maintenance, free cost, and broad-market symbol coverage.
- **Full detail:** `docs/trading/github-native-data-sources.md` §5.
- **Supersedes:** none. Extends the finding in the same document's §§1-4
  (prior pass). Does not change D-0026 principles, which remain
  PROPOSED / NOT APPROVED.
- **Not authorized by this finding:** full data acquisition, canonical
  dataset construction, calibration code, any numeric D-0026 parameter,
  or any live/production/strategy change. Phase 3 remains NOT approved.

## D-0028 — Composite free-data architecture research: still blocked, two layers materially improved

- **Date:** 2026-09-14
- **Status:** RESEARCH FINDING (not a policy/strategy decision; recorded
  for the append-only research trail per project convention)
- **Recorded by:** Claude, on Controller's explicit request to
  investigate whether multiple free sources can be COMBINED into a
  defensible D-0026 calibration dataset, after D-0027 established no
  single free source is sufficient
- **Context:** Controller asked for a layer-by-layer investigation
  (identity, point-in-time universe, OHLCV, delisted securities, regime,
  corporate actions) of whether a composite of several free sources can
  close the gaps D-0027 identified, with every candidate empirically
  verified.
- **Decision/Finding:** Verdict **C — no free composite solution**, with
  two layers materially improved over the prior state of this thread:
  - **New, verified, real point-in-time universe-membership source:**
    `fja05680/sp500` — daily S&P 500 constituent lists from 1996-01-02
    through 2026-08-18, MIT licensed, actively maintained (last commit
    2026-09-07), with embedded delisting-timing markers verified against
    three known real-world events (Family Dollar, H.J. Heinz,
    RadioShack) that all matched the historically correct period. This
    is the first genuine point-in-time universe data this entire
    research thread has produced — but it is scoped to the S&P 500 only,
    not the full US equity/ETF market.
  - **Improved identity layer:** `jadchaar/sec-cik-mapper` (CIK↔ticker,
    ~9,700 tickers, MIT, though stale since Feb 2025) and
    `JerBouma/FinanceDatabase` (112,690 equities across 84 exchanges,
    reference/categorization only) add breadth, but remain
    current-snapshot only — no ticker-history-over-time source was
    found anywhere.
  - **Layer D (bulk delisted-securities dataset) disqualified:** both
    `EpicSaber/delisted-stocks-list` and
    `BlackFalconData-org/delisted-stocks-list` were cloned and found to
    contain only a `README.md` each — both are marketing fronts for the
    same paid/metered Apify actor, not free static datasets.
  - **Layer C (bulk modern OHLCV) remains unresolved** — `piekstra/market-data`
    has no committed data (requires a paid/user-supplied API), and
    `vijinho/sp500` is index-level only and frozen at 2018-12-21. Three
    Hugging Face-hosted candidates found by search
    (`elkassabgi/hfdatalibrary`, `mito0o852/OHLCV-1m`,
    `paperswithbacktest/Stocks-Daily-Price`) look potentially promising
    by description but **could not be verified**: `huggingface.co`
    returns the same `CONNECT tunnel failed, response 403` this
    environment already returns for Stooq/SEC/Yahoo/FRED. This is
    reported as an environment-access gap, not a disqualification on
    merits — it is the single most useful thing to verify next if the
    Controller can reach it from an unblocked network.
  - A new, previously undocumented finding for `pystock-data`: its
    `prices.csv` carries both raw (`close`) and split/dividend-adjusted
    (`adj_close`) prices as separate, clearly labeled columns, resolving
    part of the corporate-actions question for its own 2009-2017 window.
- **Addendum (2026-09-14, same-day follow-up):** the Controller
  requested one final targeted pass to resolve the three Hugging Face
  candidates specifically. `huggingface.co` and all its subdomains
  (`hf.co`, `cdn-lfs*`, `datasets-server`) are confirmed blocked with
  the same policy signature as every other blocked financial domain,
  verified via both `curl` and `WebFetch` independently.
  `elkassabgi/hfdatalibrary` has a public GitHub mirror of its pipeline
  and metadata (not price data) that this environment could reach — a
  real, active, 1,391-ticker pipeline, but its own documentation, cross-
  checked with a direct ticker-list test (FDO/RSH/HNZ/BBI all absent;
  AAPL/MSFT/TSLA/DELL present), confirms it excludes delisted names from
  before ~2021, and its actual bars require registration at a blocked
  domain — **CONDITIONAL, not approvable as ROOT**.
  `paperswithbacktest/Stocks-Daily-Price` is confirmed **paid** (its own
  client library requires a paid API key or subscription-linked HF
  token) — **REJECTED** outright. `mito0o852/OHLCV-1m` has no reachable
  mirror and remains **UNVERIFIED**. This does not change the verdict
  below — full detail in `docs/trading/github-native-data-sources.md`
  §7. Per the Controller's own instruction not to search indefinitely,
  this concludes the free GitHub/Hugging-Face-hosted OHLCV search for
  D-0026.
- **Rationale:** Even with the composite, the layer that gates
  defensible calibration — broad-market OHLCV reaching into 2018-2026 —
  has no free, verified, reachable source. The one real universe-membership
  improvement found is scoped to large-caps only; using it as a stand-in
  for the full tradable universe would violate the Controller's standing
  instruction against treating a fixed symbol list as equivalent to
  Dynamic Universe Selection.
- **Full detail:** `docs/trading/github-native-data-sources.md` §6
  (composite data model, join-feasibility table, point-in-time test,
  survivorship-bias test, quality ranking, three composite candidate
  designs, and the required final output block).
- **Supersedes:** none. Extends D-0027 and the prior-pass findings in
  the same document's §§1-5. Does not change D-0026 principles, which
  remain PROPOSED / NOT APPROVED.
- **Not authorized by this finding:** full data acquisition, canonical
  dataset construction, calibration code, any numeric D-0026 parameter,
  or any live/production/strategy change. D-0011, D-0012, D-0021 through
  D-0025, and the D-0026 architecture itself are unchanged. Phase 3
  remains NOT approved.
