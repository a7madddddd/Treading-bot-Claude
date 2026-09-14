# Trading Universe — architectural principle (D-0026)

The trading universe is **dynamic / market-adaptive**. The engine is
**symbol-agnostic**. No implementation of a selection mechanism is
included here — the mechanism is deliberately deferred (D-0026 / D-0013).

---

## 1. Boundary

Two subsystems, separated by a single contract:

```
┌─────────────────────────┐
│  Universe subsystem     │   ← discovery + selection + refresh
│  (design deferred)      │      (D-0026: mechanism TBD)
└──────────┬──────────────┘
           │
           ▼  (typed record per symbol)
┌─────────────────────────┐
│  Strategy engine        │   ← ladder + floor + trailing +
│  (D-0001..D-0024)       │      approval workflow
└─────────────────────────┘
```

Nothing about the strategy engine depends on the identity of the
symbols. Nothing about the universe subsystem depends on how the
strategy computes triggers.

## 2. Contract from the strategy engine's side

The engine consumes an **approved universe**: an ordered set of symbol
records. For each symbol it may run the ladder policy independently.

A symbol record carries at minimum:

- `symbol` (e.g. "TSLA")
- `approved_at` — timestamp the Controller last approved this symbol
- `approved_by` — Controller id
- `notes` (free text) — why this symbol is in the universe
- `first_seen_by_universe_subsystem_at` — for provenance

The engine reads the current universe on every routine tick. Symbols
present but with no active trade are candidates for a new initial
entry (still gated by D-0003 Controller approval of the initial entry).
Symbols with an active trade continue under the existing ladder policy
until the trade closes; if a symbol drops out of the universe **while a
trade is open**, the engine does not force-close — it lets the existing
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

## 4. What is deliberately NOT decided here

- **Selection criteria** (fundamental screens, technical screens, news
  sentiment, factor loadings, options-implied signals, LLM-suggested
  candidates, etc.).
- **Ranking rules** (how many symbols, ordering, tie-breakers).
- **Refresh cadence** (daily, weekly, on-demand).
- **Approval flow** (Controller reviews the whole list; per-symbol
  approval; hybrid).
- **Data sources** (Alpaca screener, external data provider, curated
  file).
- **Persistence** (SQLite table via the same repository abstraction
  from D-0024 is the natural home, but the schema is not defined until
  the mechanism is).

None of these are needed for the engine to be built symbol-agnostic. All
must be defined before the universe subsystem itself is implemented.

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

## 7. Purpose the future universe subsystem must serve

It should be able to answer, at run time:

*"What are the best trading opportunities available today under the
approved strategy and risk rules?"*

That includes both:
- Discovering candidates (whatever data source, screener, or research
  the mechanism uses), and
- Ranking them so the strongest eligible opportunities are returned.

The **ranking criteria** are deliberately not defined here. That is a
separate future decision (see `docs/trading/decisions.md`
"opportunity-ranking criteria" — deferred).
