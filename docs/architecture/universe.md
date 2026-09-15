# Trading Universe — architectural principle (D-0026)

The trading universe is **dynamic / market-adaptive**. The engine is
**symbol-agnostic**. A configurable, multi-stage selection mechanism has
been proposed (PROPOSED / NOT APPROVED) — see
`../trading/universe-selection-analysis.md` (third pass — configurable
design) and `../trading/universe-parameter-validation.md` (evidence trail
behind the design choices). No exact numeric parameter is approved; the
pipeline shape and the engine-boundary contract below are what this file
documents.

---

## 1. Boundary

Two subsystems, separated by a single contract:

```
┌───────────────────────────────────────────────────┐
│  Universe subsystem (mechanism PROPOSED, see        │
│  ../trading/universe-selection-analysis.md)          │
│                                                      │
│  A. Tradability/structural  →  B. Data quality      │
│  →  C. Execution quality    →  D. Strategy-mechanics │
│  →  E. Regime adaptation    →  F. Ranking            │
│  →  G. Concentration        →  H. Top-N              │
│  →  I. Persist dated snapshot                        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼  ApprovedUniverseSnapshot
┌───────────────────────────────────────────────────┐
│  Strategy engine        │   ← ladder + floor + trailing │
│  (D-0001..D-0024)       │      approval workflow         │
└─────────────────────────┘
```

Nothing about the strategy engine depends on the identity of the
symbols, the filters used, the ranking formula, or the detected market
regime. Nothing about the universe subsystem depends on how the
strategy computes triggers.

## 2. Contract from the strategy engine's side — `ApprovedUniverseSnapshot`

The engine consumes a dated, immutable **`ApprovedUniverseSnapshot`**
(full field list and rationale in `../trading/universe-selection-
analysis.md §4`). Conceptually:

```
ApprovedUniverseSnapshot(
    snapshot_id, snapshot_at, regime,   # regime is informational only —
    symbols: [                          # the engine never branches on it
        { symbol, rank, score_summary, first_seen_by_universe_at, sector },
        ...
    ],
    is_empty, empty_reason,
)
```

The engine's contract with this object is deliberately thin:

- It knows a **date-stamped list of symbols** it may evaluate for new
  initial entries. It knows nothing about how the list was produced.
- If `is_empty`, it creates zero new initial-entry proposals this cycle
  and logs `empty_reason` — no other action.
- **The snapshot has zero authority over already-open trades.** A
  symbol's removal from a later snapshot never closes a position, never
  blocks a submission already past Controller approval pending only the
  existing D-0007 re-check, and never overrides any approved protective
  control (see §3 and the Guardrail below).

The engine reads the current snapshot on every routine tick. Symbols
present but with no active trade are candidates for a new initial
entry (still gated by D-0003 Controller approval). Symbols with an
active trade continue under the existing ladder policy until the trade
closes; if a symbol drops out of the universe **while a trade is
open**, the engine does not force-close — it lets the existing
protective floor and trailing floor manage exit.

## 3. Guardrail (must not be relaxed by any universe mechanism)

- A symbol appearing in the universe **is a candidate**, not an
  authorized trade.
- Every initial entry still requires Controller approval (D-0003).
- Every Ladder 1 / Ladder 2 still requires Controller approval + the
  5-min / ±0.5% re-check (D-0007).
- Every trade still uses the frozen original reference and threshold-based
  trailing (D-0001, D-0004, D-0008, D-0009).
- All risk limits (once approved) apply portfolio-wide across whatever
  symbols the universe currently contains.

## 4. What is proposed (mechanism) vs. what remains open

A concrete mechanism **shape** has been proposed (not approved) in
`../trading/universe-selection-analysis.md`:

- **Selection pipeline:** the 9-stage A→I pipeline in §1 above.
- **Ranking approach:** multi-stage (hard filters on load-bearing
  dimensions — spread, ATR% — then ranking among survivors), not a
  single weighted composite score. Stage F's architectural *boundary*
  (objective, exclusions, `INV-F-REGIME-BLIND`, ordinal-only
  representation, tie-break, null baseline, `score_summary` contract)
  is Controller-approved — see `../trading/stage-f-ranking-architecture.md`.
  No ranking metric, weight, or numeric parameter is approved; Stage F
  is not implemented or wired into the pipeline.
- **Refresh cadence:** primary daily refresh aligned with the existing
  07:00 CT pre-market slot; a midday refresh is not recommended pending
  evidence.
- **Persistence:** dated, immutable snapshots via the D-0024 SQLite
  repository abstraction.
- **Data sources:** Alpaca (`/v2/assets`, bars, quotes) for quantitative
  screening; Perplexity and Capitol Trades remain research-only,
  non-gating annotations (D-0019).

Still genuinely open, deliberately NOT decided anywhere yet:

- **Every numeric threshold and formula constant** — liquidity floor,
  spread cap, ATR% band, regime-classification thresholds, ranking
  weights, sector cap, correlation threshold, Top-N, warm-up/staleness.
  See `../trading/universe-selection-analysis.md §3` for the full
  parameter catalog (what each controls, why, and its recommended
  *form* — percentile-relative, liquidity-adjusted, formula-derived,
  or regime-dependent — with no value proposed).
- **Historical-data calibration** — no historical market data exists in
  this repository yet; see `../trading/universe-selection-analysis.md
  §6` for the data and process required before any parameter can move
  from PROPOSED to APPROVED.
- **Approval flow** for the mechanism itself (Controller review of the
  whole snapshot vs. per-symbol) — not yet decided.

None of the open items are needed for the engine to be built
symbol-agnostic — the engine's boundary (§2) is already fully specified
and does not change regardless of how the open items above resolve.

## 5. TSLA is TEST-ONLY (not a production universe)

TSLA is used only as a development / testing symbol — in the
`tsla-paper-trading-monitor` routine (test symbol) and in fixtures /
simulations. TSLA is NOT the production trading universe, the default
trading symbol, a hardcoded production candidate, a default
recommendation, or a fallback if universe discovery fails.

- The strategy engine has **no TSLA constants** anywhere in its logic.
- No "interim static production config = ['TSLA']" exists. If someone
  reads that in an older draft, it is superseded by this section and
  by D-0026.
- Fixtures, unit tests, property tests, and the deterministic simulator
  MAY use TSLA (or any symbol) as canned input data.

## 6. No-universe behavior — hard rule

If the universe subsystem cannot produce a valid production universe:

- The engine MUST NOT fall back to TSLA.
- The engine MUST NOT trade.
- The engine reports the empty-universe condition and waits.
- Notification severity: OPTIONAL for transient (single-run) emptiness;
  IMPORTANT if empty across a full trading day; CRITICAL if the
  underlying universe subsystem is broken (parser failure, data source
  down, credentials failure).

Silence is preferable to fabricated candidates. There is no "always
have something to trade" mode.

## 7. Purpose the universe subsystem must serve

It should be able to answer, at run time:

*"Among the currently tradable US stocks and eligible ETFs, which
symbols currently offer the best opportunities for the approved
strategy, after liquidity, execution quality, volatility, market
regime, concentration, and risk constraints?"*

That includes:
- Discovering and filtering candidates (the A→I pipeline in §1),
- Ranking survivors so the strongest eligible opportunities are
  returned (§4 above), and
- **Adapting to market conditions without adapting the strategy** — a
  high-stress or selloff regime tightens the execution-quality and
  volatility thresholds used by stages C/D; it does **not**
  automatically empty the universe. Existing positions are already
  protected by Floor/Trailing regardless of regime, and Controller
  approval already gates every new entry — so an automatic
  regime-triggered EMPTY would remove information from the Controller
  rather than let the Controller decide with full information. Only a
  genuine data/computation failure, or a legitimately empty
  filter-survivor set, produces EMPTY (§6).

The exact **ranking criteria and every numeric threshold** are
deliberately not fixed here — see §4 and
`../trading/universe-selection-analysis.md §3` and `§6` for the
parameter catalog and the calibration process required before any
value is proposed for approval.
