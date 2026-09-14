# D-0026 — Dynamic Universe Selection Mechanism — Analysis (PROPOSED / NOT APPROVED)

Analytical work for the D-0026 selection mechanism.
**Nothing in this document is APPROVED.** Every numeric threshold and
knob is a **PROPOSED value awaiting Controller approval**. The
underlying D-0026 architectural principle (dynamic, symbol-agnostic
engine; no TSLA fallback) remains as recorded in `decisions.md`.

---

## 1. Universe source — what is initially eligible for scanning

### Recommendation

Draw the base pool from **Alpaca's tradable US-listed equities and equity
ETFs**, then apply hard filters. Concretely:

- **Data source:** `GET /v2/assets` with `status=active` and
  `tradable=true`. Alpaca is our execution venue, so if a symbol is not
  tradable through Alpaca it must not enter our universe.
- **Include:** US common stocks and equity ETFs listed on **NASDAQ,
  NYSE, ARCA, BATS**.
- **Exclude by category** (PROPOSED — needs Controller approval):
  - **OTC / pink sheet** securities — thin liquidity, wide spreads,
    fragile fills that break the ladder math.
  - **Penny stocks** below a minimum price threshold (PROPOSED: below
    $5).
  - **Leveraged and inverse ETFs** (e.g. TQQQ, SQQQ, UVXY) — their
    daily-rebalance mechanics distort what a −5% / −8% / −10% level
    means, and the trailing-floor logic assumes conventional price
    behavior.
  - **Halted / delisted** — must skip on any run where broker status is
    not `active`.
  - **Newly IPO'd** — insufficient history to compute ATR, average
    volume, or the eligibility filters below (PROPOSED warm-up: 30
    calendar days since first Alpaca-tradable date).
  - **Symbols on Alpaca's easy-to-borrow / hard-to-borrow / no-short
    lists** are still fine for our approved long-only ladder, but
    should be flagged in the record for the Controller.
- **Include with care:**
  - **Broad-market equity ETFs** (SPY, QQQ, IWM, sector SPDRs) — fine
    mechanically, but they behave differently from single names
    (lower vol, tighter ranges). Whether to include them or restrict
    to individual stocks is a **Controller choice** (PROPOSED default:
    include).

### Why exclude leveraged/inverse ETFs

Their price decays via daily rebalancing. A −10% Floor on TQQQ during
a choppy sideways week can trigger even when the underlying index is
flat. The approved ladder logic (D-0001) is built assuming the fill
reference is a meaningful anchor; on a leveraged ETF it is not.

### FACT vs ASSUMPTION

- **FACT:** Alpaca exposes `/v2/assets` with `tradable`, `status`,
  `exchange`, and `easy_to_borrow` flags. This is documented API.
- **ASSUMPTION:** the filters above capture what the Controller
  intends. The exact minimum price / warm-up days / ETF-inclusion
  policy are proposals.

## 2. Market-day opportunity discovery — signals that actually matter

The approved strategy is: buy an initial slug, ladder on drawdown,
protective and trailing floor. It is **long-only, mean-reversion-tolerant
on entry, trend-following on exit**. That shape tells us which signals
are useful and which are noise.

### Signals I recommend using

1. **Average dollar volume (ADV$)** — 20 trading-day rolling. Below a
   minimum, fills at 40 shares are fine but the ladder rungs can move
   the tape on thin names. PROPOSED minimum: **$25M/day** for
   individual stocks, **$50M/day** for ETFs.
2. **Bid-ask spread as % of price** — quality-of-execution proxy.
   PROPOSED cap: **10 bps (0.10%)** median over the last hour of the
   prior session.
3. **20-day ATR as % of price** — controls how "explosive" the symbol
   is. Too low and no Ladder ever triggers; too high and Floor
   triggers on ordinary noise before Ladder 2 has a chance.
   PROPOSED band: **1.5% ≤ ATR% ≤ 6.0%**.
4. **20-day trend proxy** — SMA(20) slope, or price vs SMA(20). We do
   not want to enter a symbol that is in free-fall; we want a symbol
   that is stable-to-rising so the initial buy has a reasonable expected
   value before ladders even come into play. PROPOSED: `price ≥
   SMA(20) × 0.98` (i.e. within 2% below SMA20 or above it).
5. **Relative volume (RVOL)** — today's cumulative volume vs the
   trailing 20-day average at the same time-of-day. A moderate RVOL
   (say 1.0×–3.0×) suggests active but not manic tape.
6. **Sector membership** — for concentration constraints, not scoring
   directly (see §5).

### Signals I recommend NOT using

- **Very short-term momentum** (1-min / 5-min) — the strategy is not
  intraday-momentum; this would over-weight names that just had a
  spike.
- **News-based sentiment** as a screening filter — introduces
  unpredictable behavior, hallucination risk from LLM summaries, and
  hard-to-backtest signals. News and Capitol Trades stay research-only
  per D-0019 (see §6).
- **Gap-open filters** — they overlap with what ATR% already captures
  and add a discontinuity at the open that biases the pre-market run.
- **Options-implied vol / IV rank** — the strategy is on the stock
  itself; adding options data expands complexity without a clear
  edge for the ladder.

### Why this set

Each signal maps to a real risk the ladder faces:
- ADV$ → can we get filled without moving the tape?
- Spread → will our market buy at Ladder 1/2 pay a bad price?
- ATR% → will Floor blow through Ladder 2 before it can act?
- Trend → is the initial entry going into a knife?
- RVOL → is today's tape usable at all?

If a signal doesn't map to a real risk, it doesn't earn its complexity.

## 3. Ranking

### Recommendation

**Hard filters first, then a bounded composite score, then a
sector-constrained top-N.**

- **Hard filters** (a symbol either passes or does not — no partial
  credit):
  - Alpaca `tradable=true`, `status=active`.
  - Exchange whitelist (§1).
  - Not on the exclusion categories (§1).
  - Warm-up period satisfied.
  - Price ≥ min-price.
  - ADV$ ≥ min-ADV$.
  - Spread ≤ max-spread.
  - ATR% within band.
  - Trend condition satisfied.
- **Composite score** (only among symbols that passed hard filters):
  - Sub-scores in [0, 1] for each of: ADV$ (higher is better),
    Spread (tighter is better), ATR% (mid-band is best — an inverted-U
    around the median), Trend (positive slope preferred), RVOL
    (moderate preferred).
  - `score = w_liq × ADV$_sub + w_exec × Spread_sub + w_vol × ATR_sub
    + w_trend × Trend_sub + w_rvol × RVOL_sub`.
  - PROPOSED weights (**subject to Controller approval and later
    backtesting**): `w_liq = 0.30`, `w_exec = 0.20`, `w_vol = 0.20`,
    `w_trend = 0.20`, `w_rvol = 0.10`.
- **Sector-constrained top-N**:
  - Sort by composite score descending.
  - Walk the list and admit symbols subject to a per-sector cap
    (§5).
  - Stop when we have N symbols (PROPOSED N: **20**), or the list is
    exhausted.

### Should hard filters happen before scoring?

Yes. Scoring is expensive and meaningless for a symbol that fails a
tradability or execution-quality filter. Hard filters are the
"gatekeeper" — the whole point is that "which of the eligible symbols
scored highest" is a very different question from "which of all
symbols scored highest ignoring eligibility". We only care about the
first.

### Relative or absolute?

**Relative to today's eligible pool.** The composite score's
sub-scores are computed as within-pool ranks or percentiles, not
absolute cutoffs. This makes the score self-normalizing across regimes:
on a low-vol day, symbols with a lower absolute ATR% can still rank
well, because they are ranked within today's pool.

### How often should ranking refresh?

- **Nightly / pre-open recompute** using the prior session's completed
  bars — canonical, deterministic, reproducible.
- **Optional midday recompute** at ~11:30 CT if desired — but the
  Controller must approve because it introduces intraday churn.

## 4. Market regime

### Recommendation

**The universe mechanism should be regime-aware; the strategy engine
must not change.**

The universe is the right layer for regime adaptation because:
- Changing the strategy would touch approved policy (out of scope).
- Changing eligibility thresholds by regime keeps risk consistent
  across market conditions without altering how the ladder works when
  it does fire.

Proposed regime signals (**not approved**):
- **VIX level** (via a broad-market ETF proxy if VIX-direct data is
  not available on Alpaca IEX free tier).
- **SPY 20-day realized vol vs its 1-year median.**
- **SPY 5-day return** (for detecting rapid sell-offs).

Proposed regime buckets:

- **NORMAL:** default thresholds from §2 and §3.
- **HIGH VOL:** widen ATR% cap slightly, tighten trend requirement
  (require positive slope, not just `price ≥ SMA20 × 0.98`), reduce
  N (e.g. 10 candidates instead of 20).
- **CRASH / BROAD SELLOFF:** the universe subsystem returns
  **EMPTY** and no new proposals are made. Existing positions remain
  under their approved protective controls (Floor, Trailing Floor).
  Rationale: a laddered long strategy is structurally poor in a
  waterfall. Better to sit out than to buy the falling knife.
- **LOW VOL:** default thresholds; no change.

Regime detection thresholds are all **PROPOSED**. The Controller
should decide which of {NORMAL, HIGH VOL, CRASH, LOW VOL} deserve
different treatment.

### Important boundary

Regime awareness lives in the universe subsystem. The strategy engine
never sees "the regime"; it sees "the current approved universe". This
preserves the D-0026 separation.

## 5. Sector / concentration

### Recommendation

**Apply a per-sector cap during top-N admission** (§3).

- PROPOSED cap: **at most 2 symbols per GICS sector** for a top-20
  universe. Rationale: prevents a "5 semiconductor names, all trading
  together" concentration where one sector move dominates portfolio
  risk.
- Beyond raw sector, consider a **pairwise correlation guard**
  (PROPOSED): among admitted symbols, do not admit a new symbol whose
  60-day return correlation with any already-admitted symbol exceeds
  0.85. This is stricter than sector alone and catches theme-driven
  clusters (e.g. two AI names in different sectors).

### Trade-offs

- **Pro:** reduces the chance that a single market driver simultaneously
  triggers Ladder 2 on multiple open trades and blows through the
  portfolio-level dollar risk we haven't yet capped (portfolio-level
  hard risk limits are still TBD in `risk-management.md`).
- **Con:** removes some of the highest-ranked names when they cluster.
  A strong day for one sector could produce 5 names that would all
  score well, but we admit only 2. On a strict alpha view, that costs
  expected return.
- **Net:** for a paper-trading learning system without portfolio-level
  hard risk limits yet, the concentration guard is the right tradeoff.

## 6. Data sources

### Two lanes, kept separate

- **Lane A — Market data (drives eligibility, screening, ranking, and
  D-0012 trigger evaluation):**
  - Alpaca `/v2/assets` (universe list).
  - Alpaca `/v2/stocks/{symbol}/bars` (historical bars for ATR, ADV$,
    RVOL, SMA20).
  - Alpaca `/v2/stocks/{symbol}/snapshots` or
    `/v2/stocks/{symbol}/quotes/latest` (spread proxy).
  - Alpaca `/v2/stocks/{symbol}/trades/latest` (D-0012 Last Trade for
    triggers).
- **Lane B — Research (Perplexity, Capitol Trades — per D-0019):**
  - Independent evidence sources.
  - Structured findings deposited into the research log.

### Should research influence universe selection?

**No — not automatically.** Recommended architecture:

- **Candidate discovery:** driven purely by Lane A. Research does not
  add symbols to the candidate pool.
- **Candidate ranking:** driven purely by Lane A. Research does not
  change the score.
- **Post-screening context for the Controller:** yes. After Lane A
  produces the top-N, the Controller's proposal message (Telegram)
  MAY be enriched with a Lane B "context blurb" per symbol — recent
  news headlines, congressional trade disclosures, Perplexity
  summary. **This is information for the human, not a gating signal.**
- **Final trade decision:** stays with the Controller as always
  (D-0003).

### Why

The safest architecture is: research informs the human, deterministic
quant informs the machine. This preserves D-0019 (research is
independent evidence), D-0003 (Controller approves), and D-0026 (engine
is symbol-agnostic and deterministic).

## 7. Refresh frequency

### Recommendation

- **Primary refresh:** once per trading day at **07:00 America/Chicago**
  (aligned with the existing Capitol Trades pre-market slot in D-0021).
  Uses the prior session's fully-settled bars for deterministic
  results.
- **Optional midday refresh:** at 11:30 CT, using intraday data up to
  the prior half-hour. **Not enabled by default.** The Controller
  should decide whether the extra churn is worth it. If enabled, it
  runs on a separate schedule slot so it never overlaps with a
  strategy-engine tick on the same lock.

### Universe change ≠ trade

- If the 07:00 refresh drops symbol X and adds symbol Y:
  - Symbol X's **existing position** (if any) is untouched. Existing
    positions live under their approved protective controls until
    they exit naturally.
  - Symbol Y becomes eligible **as a candidate** on the next strategy
    tick. The Controller still has to approve the initial entry
    (D-0003).
  - A **universe delta notification** at IMPORTANT level informs the
    Controller: "added: Y, Z / removed: X, W".

## 8. New symbols

### Rules (PROPOSED)

- **Warm-up:** symbol must have ≥ **30 calendar days** of Alpaca-
  tradable history so ATR, ADV$, SMA20, RVOL are all computable
  robustly. Symbols that recently IPO'd fail this filter until day 30.
- **First universe entry:** produces a `first_seen_by_universe_at`
  timestamp in state. The engine does **not** immediately treat the
  symbol as an active trade — it becomes a **candidate**. Any initial
  entry still requires Controller approval (D-0003).
- **Stability guard:** on the same day a symbol enters the universe
  for the first time, its **initial entry proposal is deferred by one
  scheduler tick** (i.e. it appears in tomorrow's runs, not today's).
  This gives the human a chance to see the new candidate in the
  universe delta notification before the engine can propose an entry.
- **Rejection carry-over:** if the Controller rejected an initial-entry
  proposal on a symbol yesterday, the engine does not re-propose that
  symbol today unless it re-enters the universe after having left
  (i.e. a real regime change, not noise).

## 9. Symbol removal

### Rules

| State when symbol leaves the universe | What happens |
|---|---|
| No position, no open proposals | Symbol simply drops out. Not tradable next tick. |
| Open ladder proposal (PROPOSAL_PENDING for Ladder 1 or 2) | Proposal is cancelled with reason `universe_dropped`; Controller is notified IMPORTANT; the pending proposal cannot be approved after cancellation. |
| Approved proposal not yet submitted | Do NOT auto-cancel. This is a **human-approved trade**. Engine logs a warning and notifies the Controller CRITICAL: "Symbol dropped from universe between approval and submission — approve the drop or the trade." The engine holds the submission (blocked_universe_dropped) until the Controller responds. |
| Initial entry already open, no ladders yet | **Position is untouched.** Protective floor and (if activated) trailing floor continue to govern exit. No auto-liquidation on universe change. Controller is notified IMPORTANT. |
| Position open with one or both ladders filled | Same — position is untouched. All approved protective exits continue. Controller is notified IMPORTANT. |

### Hard rule

**Removing a symbol from the universe MUST NOT close an existing
position.** Only approved protective exits or an explicit Controller
decision close positions. The universe layer is upstream of and
subordinate to the execution layer's approved-exit rules.

## 10. Failure safety

### Behavior per failure mode

| Failure | Universe subsystem response |
|---|---|
| `/v2/assets` unreachable | Retry with bounded backoff (bounds PROPOSED); on final failure, return **EMPTY universe**; notify CRITICAL. |
| Bars API partial (some symbols missing bars) | Symbols with insufficient bars fail the eligibility filter individually. If ≥ 50% of the base pool has missing data (PROPOSED threshold), return EMPTY; notify CRITICAL. |
| Ranking computation error | Log CRITICAL; return EMPTY universe; do not attempt to substitute a previous day's universe (staleness > 1 session is not safe). |
| Zero candidates after filters | Return EMPTY universe; notify IMPORTANT (there was no error — the market simply has no eligible names today under current thresholds). |
| Stale market data (last bar > **15 minutes** old during session, PROPOSED) | Refuse the run; log CRITICAL; return EMPTY; try again at next slot. |
| Alpaca down at strategy-tick time | Strategy engine handles this per `execution.md §6` — this is orthogonal to universe. |
| Perplexity down | Universe subsystem does not care (Lane A only). Research notification may be missing on that day's proposals; log IMPORTANT. |
| Capitol Trades scraper broken | Same as Perplexity failure — universe untouched. |

### Cardinal rule

**Empty ≠ fall back to TSLA. Empty ≠ invent symbols. Empty ≠ use
yesterday's universe.**

An empty universe means: the strategy engine sees no candidates today
and MUST NOT create any initial-entry proposals. Existing positions
continue under their approved protective controls. The next scheduled
refresh tries again.

This is the concrete implementation of D-0026's "must not trade if a
valid universe cannot be generated" clause.

## 11. Architecture

### Recommended layered design

```
┌──────────────────────────────┐
│  Alpaca /v2/assets           │  data lane A (deterministic)
│  Alpaca bars + snapshots     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Eligibility Filter (hard)   │  §1, §2 hard filters
│  - tradable, active, exch    │
│  - warm-up, min price        │
│  - ADV$, spread, ATR%, trend │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Opportunity Scanner + Scorer│  §2, §3
│  - per-symbol sub-scores     │
│  - composite score           │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Regime + Concentration      │  §4, §5
│  - regime bucket             │
│  - sector cap                │
│  - correlation guard         │
│  - top-N admission           │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Approved Universe Store     │  SQLite (D-0024)
│  - dated snapshots           │
│  - delta from previous       │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Strategy Engine             │  D-0001..D-0011
│  (symbol-agnostic)           │  reads current universe as input
│  - triggers                  │
│  - state machine per level   │
│  - proposals                 │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  Telegram Approval (D-0025)  │
│  + Post-screening research   │  Perplexity / Capitol Trades
│  (Lane B, context blurb only)│  (advisory, non-gating)
└──────────────┬───────────────┘
               │  Controller approves
               ▼
┌──────────────────────────────┐
│  Execution Engine (paper)    │
│  D-0007 re-check on submit   │
└──────────────────────────────┘
```

### What each layer must NOT do

- **Eligibility filter** must not score; only accept/reject.
- **Scanner + scorer** must not admit symbols that failed hard filters.
- **Regime + concentration** must not modify per-symbol scores; it
  only decides admission.
- **Universe store** must not trigger orders on its own.
- **Strategy engine** must not query for new symbols outside the
  approved universe.
- **Research** must not modify the universe.
- **Execution** must not consult the universe at submit time — it
  consults state, approval, D-0007 re-check, and the active floor.

## 12. Configuration

### Should be configurable (env or config table via the repository)

- `min_price` (PROPOSED: $5)
- `min_dollar_volume_stocks` (PROPOSED: $25M/day)
- `min_dollar_volume_etfs` (PROPOSED: $50M/day)
- `max_spread_bps` (PROPOSED: 10)
- `atr_pct_min` (PROPOSED: 1.5%)
- `atr_pct_max` (PROPOSED: 6.0%)
- `warmup_days` (PROPOSED: 30)
- `candidate_count_N` (PROPOSED: 20)
- `sector_cap` (PROPOSED: 2 per GICS sector)
- `correlation_max` (PROPOSED: 0.85)
- `regime_buckets` and their per-bucket overrides
- `refresh_schedule` (primary 07:00 CT; optional midday 11:30 CT)
- Ranker weights (`w_liq`, `w_exec`, `w_vol`, `w_trend`, `w_rvol`)

### Should be hardcoded or captured in code review

- Exchange whitelist (NASDAQ, NYSE, ARCA, BATS).
- Exclusion of OTC / pink sheet / leveraged / inverse ETFs.
- Fail-safe rule: empty universe on any hard-fail path.
- No TSLA fallback (D-0026 §5).
- Cardinal rule: universe change never triggers an order.

## 13. Testing

Extends `verification-plan.md` with a new section devoted to the
universe subsystem:

- **Historical replay.** For each of the last N approved trading days,
  produce the universe using data as of that morning's 07:00 CT
  snapshot. Verify determinism: two runs of the same date produce the
  same universe.
- **Ranking stability.** For two consecutive days on a quiet market,
  the universe overlap should be high (e.g. ≥ 60%). If it churns
  wildly, the ranker weights are too sensitive.
- **Empty-universe test.** Force all sources to return errors;
  confirm the subsystem returns EMPTY, the strategy engine creates no
  proposals, and existing simulated positions are untouched.
- **API failure tests.** `/v2/assets` 500, timeout, malformed body,
  cut connection; confirm the retry-then-EMPTY path.
- **Stale-data test.** Feed bars whose last timestamp is > 15 min old;
  confirm refusal.
- **New symbol test.** Introduce a symbol whose first Alpaca-tradable
  date is 20 days ago; confirm rejected by warm-up. At 31 days,
  confirm accepted and produces `first_seen_by_universe_at`.
- **Removed symbol test.** Symbol leaves the universe on day D+1;
  simulated open position on that symbol is preserved and reports
  correct protective exits; open proposal is cancelled per §9;
  approved-but-not-submitted proposal is held per §9.
- **Regime test.** Feed high-vol day parameters; confirm regime bucket
  detected and thresholds adjusted; feed crash-day parameters; confirm
  EMPTY.
- **Restart/recovery.** Universe snapshot written to SQLite at 07:00;
  kill and restart the process before the 08:30 strategy tick;
  confirm strategy loads the same snapshot.
- **DST test.** Spring-forward and fall-back dates verify the 07:00 CT
  refresh actually fires at 07:00 CT under both CST and CDT.
- **Sector-cap test.** Feed a synthetic day where the top 5 scores are
  all semiconductors; confirm the sector cap admits at most 2 and
  fills the rest from other sectors.

## 14. Recommendation — what I would choose

### The mechanism

1. **07:00 CT once-per-day refresh.** Deterministic, uses complete
   prior-session bars, aligned with the approved pre-market slot.
2. **Hard filters first.** Alpaca tradability, exchange whitelist,
   exclusion categories, warm-up, min price, ADV$, spread, ATR%,
   trend.
3. **Composite score** over five sub-scores (ADV$, spread, ATR%,
   trend, RVOL), self-normalized within today's pool.
4. **Regime awareness in the universe layer only.** NORMAL / LOW VOL /
   HIGH VOL / CRASH buckets adjust thresholds. CRASH returns EMPTY.
5. **Sector cap and pairwise-correlation guard** during top-N
   admission.
6. **Top-N** (PROPOSED N = 20) written as a dated snapshot to SQLite.
7. **Strategy engine reads the current snapshot as input.** No
   TSLA anywhere.
8. **Research (Perplexity + Capitol Trades) is post-screening
   context**, delivered to the Controller with proposal messages.
   Not a gating signal, not a universe input.
9. **Fail-safe:** any failure → EMPTY universe. Empty means no new
   proposals; existing positions continue under approved protective
   controls.
10. **New / removed symbols** handled per §8 and §9. Removal never
    closes an open position; approval-in-flight is held for Controller
    review; pending proposals are cancelled with notification.

### Why this and not something else

- **Aligned with the approved strategy shape.** The signals map to
  concrete ladder risks, not generic "what's hot today" heuristics.
- **Symbol-agnostic engine preserved.** The engine's inputs are
  `current_universe` and per-symbol market data. It has zero symbol
  logic.
- **No invented policy shortcuts.** Every threshold is a proposal.
- **Deterministic and testable.** Same data → same universe.
- **Regime-aware without touching approved strategy.** Regime
  adaptation is a knob on the universe filter, not on the ladder math.
- **Research stays honest.** Perplexity and Capitol Trades remain
  research per D-0019; they do not gain quiet influence over trades.
- **Fail-safe by construction.** The "cardinal rule" is a single
  short branch: on any hard-fail path, return EMPTY.

### What I am not recommending

- Auto-execution based on Capitol Trades signals — violates D-0003 and
  D-0019.
- Perplexity as a candidate generator — introduces LLM hallucination
  risk into deterministic policy.
- Scanning "all US equities every hour" — burns broker rate limits
  and does not produce better trades than a curated top-N.
- A single monolithic ranker without hard filters — makes scoring
  effectively a soft filter, which is harder to reason about.

## 15. Relationship with the approved strategy

Unchanged.

- Universe subsystem answers: *which symbols are worth evaluating today?*
- Strategy engine answers: *given this symbol and its current market
  state, does the approved ladder logic want to enter, add, or exit?*
- Controller answers: *approve or reject this specific proposed trade.*
- Execution engine answers: *can this approved order safely be submitted
  under the D-0007 re-check and the active protective floor?*

No responsibility is merged. No approved policy semantics change.

## 16. Documentation review

### Existing D-0026 documentation

- `docs/trading/decisions.md` D-0026 — principle is APPROVED
  (dynamic, symbol-agnostic, TSLA test-only, no fallback). Mechanism
  is still TBD, which matches this document's status. No change
  needed to the D-0026 decision entry.
- `docs/architecture/universe.md` — §5-§7 already reflect the "TSLA
  is TEST-ONLY, no fallback" rules. Mechanism section is high-level;
  it can point to this file for the detailed proposal.
- `docs/trading/pre-apply-checklist.md` B15 — currently says
  "Universe subsystem design + Controller approval"; can be updated
  to point at this analysis as the design under review.

### Recommended follow-up updates (only after Controller review, not now)

- If Controller approves this design in whole or with modifications:
  supersede D-0026 with a new decision (or a follow-up D-XXXX) that
  records the approved mechanism, including which PROPOSED thresholds
  became APPROVED and which changed.
- Extend `state-management.md` with the universe snapshot table
  contract (columns: `snapshot_id`, `snapshot_at`, `symbol`, `rank`,
  `score`, `first_seen_by_universe_at`, `sector`, `notes`).
- Add a corresponding section to `verification-plan.md` for the
  universe tests in §13 above.

### Contradictions or stale references

- No TSLA-as-production references remain in the current docs; §5-§7
  of `universe.md` and D-0026 in `decisions.md` are consistent with
  this analysis.
- One minor tightening for later: `overview.md` §5 mentions the
  scheduler will pick up "the approved schedule"; when the universe
  refresh joins the schedule, that section should list it explicitly
  (07:00 CT primary, optional 11:30 CT). Not urgent.

## 17. Status

**PROPOSED / NOT APPROVED.** Every threshold, weight, cap, and
schedule slot in this document is a proposal awaiting Controller
approval. The D-0026 principle (dynamic, symbol-agnostic, TSLA
test-only, no fallback, empty-if-broken) remains APPROVED as recorded
in `decisions.md`.
