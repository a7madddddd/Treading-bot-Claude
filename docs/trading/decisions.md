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

## D-0011 — Ladder trigger source and debounce remain TBD

- **Date:** 2026-09-14
- **Status:** DEFERRED (explicit TBD)
- **Approved by:** Controller (as a deferral)
- **Decision:** Trigger source (trade prints vs bars vs quote midpoint)
  and any debounce policy (e.g. "two consecutive prints below trigger")
  remain TBD. Do NOT implement a debounce yet.
- **Rationale:** Trigger definition materially affects both signal
  frequency and safety; must be explicitly defined before code lands.

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
