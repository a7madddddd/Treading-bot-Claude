# D-0026 — Dynamic Universe Selection Mechanism (Third Pass — Configurable Design)

**Status: PROPOSED / NOT APPROVED.** This document supersedes the fixed-number
framing of the original first-pass design (numbers like "$25M ADV$", "10 bps
spread", "N=20" have been removed from the recommended mechanism). It
incorporates the findings of the second-pass evidence review
(`universe-parameter-validation.md`) and restructures the mechanism around
**configurable parameters calibrated from historical data**, not constants
picked by convention.

The underlying D-0026 architectural principle — dynamic, symbol-agnostic
engine; TSLA test-only; EMPTY on failure — remains APPROVED as recorded in
`decisions.md`. This document proposes the **mechanism** inside that
principle. Nothing here is approved. No numeric threshold is frozen.

Read alongside:
- `universe-parameter-validation.md` — the evidence trail behind the design
  choices in this document (why spread/ATR% are load-bearing, why a single
  weighted score was rejected, why CRASH→EMPTY was reversed, etc.)
- `architecture/universe.md` — the engine/universe boundary contract
- `architecture/state-management.md` — the persistence contract

---

## 0. The question the universe must answer, every day

> *"Among the currently tradable US stocks and eligible ETFs, which symbols
> currently offer the best opportunities for the approved strategy, after
> liquidity, execution quality, volatility, market regime, concentration, and
> risk constraints?"*

This is a **relative, adaptive** question — not "which symbols pass a fixed
checklist." The mechanism below is designed so the answer changes as market
conditions change, without any code or policy change to the strategy engine.

---

## 1. Selection pipeline — stages and why each exists

```
A. Tradability / structural eligibility
        ↓
B. Data quality
        ↓
C. Execution-quality constraints
        ↓
D. Strategy-mechanics compatibility
        ↓
E. Market-regime adaptation
        ↓
F. Opportunity ranking
        ↓
G. Sector / concentration / correlation constraints
        ↓
H. Top-N selection
        ↓
I. Persist dated universe snapshot
        ↓
   ApprovedUniverseSnapshot  →  Strategy Engine (symbol-agnostic)
```

### A. Tradability / structural eligibility

**What it does:** removes symbols the system cannot or should not trade at
all, independent of any market condition — Alpaca `tradable=true` /
`status=active`, exchange whitelist, category exclusions (OTC, leveraged/
inverse ETFs, halted).

**Why it exists first:** these are binary, non-market-dependent facts. A
symbol that isn't tradable through our own broker, or that is structurally
incompatible with fixed-percentage ladder math (leveraged/inverse ETFs — see
`universe-parameter-validation.md` Task 6), should never reach any later,
more expensive stage. This is the cheapest possible filter and should run
first for that reason alone, independent of anything data-quality or
market-condition related.

**Configurable or fixed:** the *category* exclusions (OTC, leveraged/inverse,
halted) are structural findings, not calibratable numbers — they don't
belong in the parameter catalog in §3. The exchange whitelist is
configuration but essentially static (doesn't need market-data calibration).

### B. Data quality

**What it does:** removes symbols where the required inputs for later stages
are missing, stale, or unreliable — insufficient bar history (warm-up),
missing spread/quote data, stale timestamps.

**Why it exists second:** every stage after this one performs numeric
computation (ATR, ADV$, spread, trend, regime fit). Running those
computations on bad data produces silently wrong eligibility decisions, which
is worse than an explicit exclusion. This stage exists to make "I don't have
good enough data on this symbol" an explicit, auditable outcome rather than
an implicit source of noisy scores.

**Configurable or fixed:** warm-up period (minimum days of history) and
staleness threshold are genuine calibratable parameters — see §3.

### C. Execution-quality constraints

**What it does:** filters on spread and liquidity **relative to our own
order size**, not generic institutional screening convention.

**Why it exists here, as a hard filter, not a score input:** per
`universe-parameter-validation.md` Task 1 and Task 5, spread quality is
**load-bearing** — a bad fill directly corrupts `original_initial_entry_fill_price`,
which is frozen for the life of the trade (D-0001, D-0009) and never
recalculated. A symbol with unacceptable execution quality should never
reach ranking; averaging a bad spread into a composite score (as the
first-pass design did) lets a symbol "buy its way past" a real risk with a
good score elsewhere. This must be a hard gate.

**Configurable or fixed:** liquidity floor and spread cap are the two
parameters most in need of calibration against our own actual order size
(10/10/20 shares) rather than generic convention — see §3.

### D. Strategy-mechanics compatibility

**What it does:** filters on volatility (ATR%) calibrated specifically to the
approved Ladder/Floor spacing (0%, −5%, −8%, −10%), and — as a soft signal
only, not a hard gate per the second-pass reversal — trend context.

**Why it exists here, separately from execution quality:** this is the
*other* load-bearing, strategy-derived filter (per Task 1/Task 5): too little
volatility and Ladder 1 essentially never fires (wasted candidate slot); too
much volatility relative to the 2-point Ladder2-to-Floor gap and a single bad
session can blow through the whole ladder without giving the Controller a
real chance to review each step (see `universe-parameter-validation.md`
§Task 2.4 for the geometric reasoning). This is kept as its own stage,
distinct from execution quality, because the two answer different questions
("can we get a good fill" vs. "does this symbol's volatility fit our fixed
percentage spacing") and calibrating them together would obscure which one
is driving an exclusion.

**Configurable or fixed:** ATR% band (lower and upper) is the parameter most
directly derivable from strategy geometry, and therefore the best candidate
for a **formula-based** (not constant) threshold — see §3 and §5.

### E. Market-regime adaptation

**What it does:** classifies today's broad-market condition (e.g. via a
volatility/drawdown proxy on a broad index) and adjusts the **thresholds**
used by stages C and D — not the strategy, not stage D's underlying logic,
just the numeric inputs.

**Why it exists as a distinct stage, after D and before F:** per
`universe-parameter-validation.md` Task 4, the earlier "CRASH → EMPTY" idea
is **rejected** — a laddered strategy often has its best entries during
broad drawdowns, existing positions are already protected by Floor/Trailing
regardless of regime, and Controller approval already gates every new entry.
The correct response to a high-volatility or selloff regime is to
**tighten** execution/volatility thresholds (since spreads widen and
single-session moves get larger in stress, which is exactly what stages C
and D are already measuring) — not to zero the candidate list. Placing this
as an explicit stage, rather than folding it into C/D's constants, makes the
adaptation auditable: "today's thresholds were regime-adjusted because
regime=HIGH_VOL" is a loggable, explainable fact.

**Configurable or fixed:** regime classification thresholds and the
per-regime adjustment magnitudes are calibratable parameters — see §3.

### F. Opportunity ranking

**What it does:** among symbols that survived stages A–E, produces an
ordering — NOT via a single weighted composite score (rejected in the
second-pass analysis, Task 5), but via secondary signals (liquidity beyond
the execution-quality floor, relative volume, trend-as-soft-signal) applied
only to symbols that already passed the load-bearing hard gates.

**Why it exists after the hard filters, not combined with them:** this is
the direct implementation of the Task 5 finding — mixing load-bearing
filters (spread, ATR% fit) into the same weighted formula as secondary
quality signals (trend, RVOL) implies they matter equally, which the
strategy's own mechanics don't support. Keeping ranking as a separate,
later stage that only operates on filter-survivors avoids a name "buying
its way" into the universe on a strong secondary score despite failing a
load-bearing requirement.

**Configurable or fixed:** the secondary-score composition (which signals,
what relative emphasis) is a calibratable parameter set — see §3.

### G. Sector / concentration / correlation constraints

**What it does:** applies diversification rules during admission — a
per-sector cap and a pairwise-correlation guard among symbols about to be
admitted — rather than as a factor inside the ranking score (per Task 5's
"threshold-then-diversify" recommendation).

**Why it exists after ranking, not before or folded in:** diversification
should act on the *already-ranked* candidate list ("of my best candidates,
avoid clustering") rather than distort the ranking itself. Applying it as an
explicit, separate admission rule keeps the reasoning auditable: a symbol
excluded here was excluded specifically for concentration reasons, not
because it scored poorly.

**Important open sequencing issue (carried from the second pass,
unresolved):** both parameters are partial substitutes for the still-TBD
portfolio-level hard risk limits (`risk-management.md §5`). Setting them
independently, before those limits exist, risks solving the concentration
problem in the wrong order. This stage's existence in the pipeline is
recommended; its exact parameters should likely wait on the portfolio-limit
decision — flagged again in §6/F below.

**Configurable or fixed:** sector cap and correlation threshold — see §3.

### H. Top-N selection

**What it does:** truncates the diversification-constrained, ranked list to
a final count.

**Why it exists as its own stage:** N is not really a screening parameter —
it's an **operational capacity constraint** tied to how many simultaneous
candidates the Controller can meaningfully review and act on within the
D-0007 5-minute approval window if multiple trigger simultaneously (per
Task 2.6). Keeping it as the final, separate stage makes this distinction
clear: N is bounded by human/operational capacity and (once they exist)
portfolio-level risk limits, not by "how many symbols look good today."

**Configurable or fixed:** N — see §3, and note it is explicitly tied to an
operational constraint that itself needs to be measured (Controller's real
response capacity), not just picked.

### I. Persist dated universe snapshot

**What it does:** writes the final admitted list, with full provenance
(scores, which stage excluded any borderline symbol, regime classification
used, timestamp) to the SQLite state store (D-0024), and constructs the
`ApprovedUniverseSnapshot` object the strategy engine consumes (§4).

**Why it exists as the final stage:** this is the hard boundary between
"universe subsystem" and "strategy engine" that D-0026 requires. Everything
before this point is the universe subsystem's internal business; everything
after this point is what the symbol-agnostic strategy engine is allowed to
see. Persisting a dated, immutable snapshot (rather than a live-queryable
view) also gives us reproducibility for later backtesting/calibration (§6)
and for post-hoc audit of "why was symbol X in today's universe."

---

## 2. Failure behavior (unchanged from earlier passes, restated for completeness)

Any hard failure at stages A–I (data source unreachable, ranking computation
error, insufficient data breadth, stale data beyond threshold) results in an
**EMPTY** `ApprovedUniverseSnapshot`. Zero candidates surviving the filters
legitimately (not an error, just no fit today) is also a valid, distinct
EMPTY outcome — logged at a lower severity than a genuine failure.

**EMPTY means:** no new initial-entry proposals today. It does NOT mean:
fall back to TSLA, invent symbols, or reuse a stale prior snapshot as if it
were current. Existing open positions are entirely unaffected — they
continue under their approved protective controls (Floor, Trailing)
regardless of universe state.

---

## 3. Configurable parameter catalog

For every parameter: what it controls, why the strategy needs it, what
failure/risk it prevents, what data is required to calibrate it, and its
recommended **form** (absolute constant / percentile-relative /
volatility-adjusted / liquidity-adjusted / regime-dependent). **No value is
proposed as a number in this section.** Values belong to the calibration
process in §6, not to design documents.

### 3.1 Liquidity floor (execution-quality stage, C)

- **Controls:** the minimum trading activity (e.g. average dollar volume)
  required for a symbol to be considered for execution-quality evaluation.
- **Why the strategy needs it:** our order sizes are small and fixed (10,
  10, 20 shares); the risk isn't "can institutions trade this size," it's
  "can 40 shares be filled without materially moving the price or paying an
  unusual premium."
- **Risk it prevents:** market impact on our own fills; wide effective
  spread on thin names that only shows up intraday, not in a daily average.
- **Calibration data needed:** historical daily volume and, ideally,
  intraday volume profile, for a broad symbol universe, to determine at what
  liquidity level a 10–40 share order stops being distinguishable from
  market noise.
- **Recommended form:** **liquidity-adjusted, relative to our own order
  size** rather than an absolute institutional-convention number (e.g.
  "our worst-case order value must be below X% of a recent representative
  bar's dollar volume") — not a flat "$25M ADV$" style constant.

### 3.2 Spread cap (execution-quality stage, C)

- **Controls:** the maximum acceptable bid-ask spread (as % of price, or in
  relation to the D-0007 approval band) for a symbol to pass execution-
  quality screening.
- **Why the strategy needs it:** spread directly degrades the fill price
  that becomes the frozen `original_initial_entry_fill_price` — an error
  here propagates through the entire trade's Ladder/Floor levels.
- **Risk it prevents:** a materially mispriced initial entry that silently
  shifts every subsequent trigger level away from what the Controller
  believed they were approving.
- **Calibration data needed:** historical or live quoted spread (true
  quote data, not just a high-low proxy) across a broad symbol set, and
  ideally its distribution around the same times of day our routine
  actually executes (D-0021's 08:30–14:30 CT checks).
- **Recommended form:** **percentile-relative within today's eligible pool**,
  cross-checked against the fixed 0.5% D-0007 re-check band (the spread
  should consume only a bounded fraction of that band) rather than a flat
  bps constant applied uniformly across all price levels.

### 3.3 ATR% band (strategy-mechanics stage, D)

- **Controls:** the acceptable range of a symbol's recent volatility
  (expressed as ATR as % of price) for the Ladder/Floor spacing to
  function as intended.
- **Why the strategy needs it:** too low, and Ladder 1 (−5%) essentially
  never fires; too high relative to the 2-point Ladder2-to-Floor gap, and a
  single session can skip past the Controller's intended step-by-step
  review (`universe-parameter-validation.md` Task 2.4).
- **Risk it prevents:** wasted candidate slots (never-triggering, low-ATR
  names) and "no-warning Floor hits" (excessive-ATR names that jump straight
  from entry to Floor).
- **Calibration data needed:** historical daily bars, specifically to
  measure, for a range of ATR% values, the empirical distribution of
  outcomes (Ladder1-only / Ladder1+2 / Floor-without-Ladder2-having-had-a-
  chance) when the frozen approved strategy logic is simulated forward.
- **Recommended form:** **strategy-geometry-derived formula, not a
  convention-based constant.** The upper bound in particular should be
  expressed as a function of the Ladder2-to-Floor gap (currently 2
  percentage points) rather than picked independently — e.g. structured as
  "upper bound approximately K × (Floor% − Ladder2%)" for some calibratable
  K, so that if the approved Ladder/Floor spacing ever changes (a separate,
  Controller-gated policy decision), the volatility band updates
  automatically rather than silently going stale. The lower bound should
  similarly be tied to "how many sessions of typical movement are needed to
  plausibly reach Ladder 1," not a flat percentage.

### 3.4 Regime classification thresholds and per-regime adjustments (regime stage, E)

- **Controls:** what counts as NORMAL / HIGH VOL / LOW VOL / SELLOFF today,
  and how much stages C/D's thresholds tighten or loosen in each bucket.
- **Why the strategy needs it:** execution quality and single-session
  volatility both plausibly worsen in stress; adapting thresholds (not
  emptying the universe) keeps the candidate quality bar consistent across
  regimes rather than letting stress conditions silently admit worse
  candidates under the same nominal thresholds.
- **Risk it prevents:** admitting candidates in a high-vol regime whose
  execution quality or volatility profile would have failed under normal
  conditions, simply because the fixed thresholds weren't regime-aware; also
  prevents the opposite failure mode (empty universe during legitimate
  opportunity-rich drawdowns), corrected from the first-pass design.
- **Calibration data needed:** a broad-market volatility/drawdown proxy
  (e.g. realized volatility on a market index) across a multi-year period
  covering multiple real regimes, to determine what regime-detection
  thresholds actually separate meaningfully different execution/volatility
  environments (rather than reacting to noise).
- **Recommended form:** **regime-dependent**, by construction — this
  parameter *is* the regime-adaptation mechanism. The regime-detection
  threshold itself should likely be **percentile-relative to trailing
  history** (e.g., "today's realized volatility is in the top decile of the
  trailing year") rather than an absolute VIX-style level, so it remains
  meaningful without needing to be manually re-tuned as long-run market
  volatility drifts over multi-year horizons.

### 3.5 Ranking / secondary-score composition (ranking stage, F)

- **Controls:** how filter-survivors are ordered — which secondary signals
  (liquidity beyond the floor, RVOL, trend-as-soft-signal) contribute, and
  their relative emphasis.
- **Why the strategy needs it:** among symbols that already meet the
  load-bearing bars, we still need to order them if the survivor count
  exceeds N; the ordering should favor genuinely better opportunities, not
  arbitrary tie-breaking.
- **Risk it prevents:** admitting a materially weaker candidate over a
  materially stronger one when both pass the hard gates, purely due to
  ranking order.
- **Calibration data needed:** the same historical-bars dataset as §3.3,
  used to test whether symbols ranked higher by a given secondary-score
  formula actually produced better realized outcomes (using the frozen
  strategy simulation) than symbols ranked lower, among filter-survivors.
- **Recommended form:** **percentile-based within today's filter-survivor
  pool** (not absolute), so the ranking is self-normalizing across days
  with different overall market character, consistent with the second-pass
  recommendation to avoid a single opaque weighted score and instead use
  transparent, auditable secondary ranking among filter-survivors only.

### 3.6 Sector cap (concentration stage, G)

- **Controls:** the maximum number of admitted symbols from the same
  sector/industry classification.
- **Why the strategy needs it:** without it, a single sector-wide move
  could simultaneously trigger Ladder 2 (or Floor) across multiple
  concurrently-held trades, compounding correlated risk that the
  per-trade Ladder/Floor math was never designed to account for across
  multiple positions at once.
- **Risk it prevents:** concentrated, correlated drawdown risk across
  simultaneously open trades.
- **Calibration data needed:** this parameter is **structurally entangled**
  with the still-TBD portfolio-level hard risk limits (`risk-management.md
  §5`) — see §6/F. Historical sector-correlation data helps validate a
  chosen cap, but the cap's *purpose* (bounding aggregate portfolio risk)
  can't be fully specified until the portfolio-level limits exist.
- **Recommended form:** likely **derived from the portfolio-level risk
  limit** once approved (e.g., expressed as a function of "max acceptable
  simultaneous same-sector dollar exposure" divided by typical per-trade
  sizing), rather than an independently chosen headcount.

### 3.7 Correlation threshold (concentration stage, G)

- **Controls:** the maximum acceptable pairwise historical return
  correlation between an already-admitted symbol and a new candidate.
- **Why the strategy needs it:** catches theme-driven clustering that
  sector classification alone misses (e.g. two different-sector names that
  move together on a common driver).
- **Risk it prevents:** same concentration risk as §3.6, via a different
  and complementary measurement.
- **Calibration data needed:** historical return series for computing
  rolling pairwise correlations, plus the same portfolio-risk-limit
  dependency as §3.6.
- **Recommended form:** **percentile-relative** (e.g., "reject a pairing in
  the top decile of pairwise correlation observed across the current
  candidate pool") rather than a fixed correlation coefficient, so the
  threshold adapts to whatever the typical correlation structure of the
  admitted pool looks like on a given day (which itself varies by regime —
  correlations tend to rise in stress).

### 3.8 Top-N (final selection stage, H)

- **Controls:** how many symbols the strategy engine ultimately receives
  today.
- **Why the strategy needs it:** bounds the number of simultaneous
  candidates against the Controller's realistic capacity to review D-0007-
  gated proposals, and (once they exist) portfolio-level risk limits.
- **Risk it prevents:** approval fatigue / alert overload if too many
  triggers fire in the same scheduler tick across too many concurrently
  eligible symbols; under-utilization of the strategy if too small.
- **Calibration data needed:** **not primarily a market-data question** —
  this needs operational data: how often do multiple symbols actually
  trigger in the same hourly check, and how does the Controller's real
  approval throughput compare to that, once the system is actually running
  (even in a dry-run/shadow mode). Historical market-data backtesting can
  estimate "how many candidates would plausibly trigger per day" but cannot
  by itself answer "how many can a human safely review."
- **Recommended form:** likely a **regime-dependent** ceiling (tighter in
  high-vol regimes, when more symbols are likely to trigger simultaneously)
  bounded above by an operational-capacity constant that should be set from
  observed Controller usage, not guessed in advance.

### 3.9 Warm-up period and staleness threshold (data-quality stage, B)

- **Controls:** minimum history required before a newly listed/tradable
  symbol is eligible, and how old market data can be before a refresh is
  refused.
- **Why the strategy needs it:** ATR/ADV$/trend/regime computations need
  enough history to be statistically meaningful; stale data anywhere in the
  pipeline produces silently wrong eligibility and ranking decisions.
- **Risk it prevents:** admitting a symbol on noisy, insufficient history;
  trading decisions built on outdated prices.
- **Calibration data needed:** sensitivity analysis on how much history the
  ATR/ADV$/trend calculations need before they stabilize (this is closer to
  a statistical/engineering question than a strategy-outcome question, and
  can likely be answered analytically rather than needing a full backtest).
- **Recommended form:** likely closer to a **fixed engineering constant**
  (bounded by how many bars a rolling ATR/SMA calculation needs to be
  statistically stable) rather than something that needs strategy-outcome
  calibration — flagged as the one parameter category in this catalog that
  may not require the full backtest process in §6, only a simpler
  statistical-stability check.

---

## 4. The `ApprovedUniverseSnapshot` contract (engine boundary)

Per the Controller's explicit requirement, the strategy engine must receive
something conceptually like:

```
ApprovedUniverseSnapshot(
    snapshot_id,
    snapshot_at,              # timestamp, America/Chicago
    regime,                   # classification used for this snapshot,
                               # informational only — engine does not
                               # branch on it
    symbols: [
        {
            symbol,
            rank,
            score_summary,     # for audit/notification purposes only
            first_seen_by_universe_at,
            sector,
        },
        ...
    ],
    is_empty: bool,
    empty_reason: str | null,  # "no_candidates" | "data_failure" |
                                # "computation_failure" | ...
)
```

The strategy engine's only contract with this object:

- It knows a **date-stamped list of symbols** it is allowed to evaluate for
  new initial entries.
- It knows nothing about **how** the list was produced — not the filters,
  not the ranking formula, not the regime detector, not the data source.
- If `is_empty`, it creates zero new initial-entry proposals for the cycle,
  and logs `empty_reason` for observability — but takes no other action.
- **This snapshot has zero authority over already-open trades.** Per D-0026
  and the second-pass analysis (Task 8), a symbol's removal from a
  subsequent snapshot never closes a position, never blocks a submission
  already past Controller approval and pending only the existing D-0007
  re-check, and never overrides any approved protective control.

This is the concrete mechanism that lets the universe change daily "without
changing the strategy engine," as requested — the engine's code has zero
knowledge of tickers, sectors, screening logic, or regimes; it only consumes
this typed object.

---

## 5. Dynamic vs. fixed — summary table

| Parameter | Recommended form | Why not a flat constant |
|---|---|---|
| Liquidity floor | Liquidity-adjusted to our own order size | Generic institutional thresholds ($25M+) assume position sizes we don't use (Task 2.2 finding) |
| Spread cap | Percentile-relative + cross-checked vs. D-0007 band | Flat bps caps ignore price-level effects and the actual approval-tolerance interaction |
| ATR% band | Formula derived from Ladder2-to-Floor gap | The correct band is a function of *our own* ladder spacing, not a generic volatility convention |
| Regime thresholds | Percentile-relative to trailing history | An absolute vol level drifts out of relevance as long-run market volatility shifts over years |
| Ranking / secondary score | Percentile-relative within today's survivor pool | Self-normalizes across differing daily market character; avoids one opaque global formula |
| Sector cap | Derived from (future) portfolio risk limit | Currently a proxy for a risk control that doesn't exist yet; shouldn't be set independently |
| Correlation threshold | Percentile-relative within candidate pool | Correlation structure itself shifts by regime; a fixed coefficient doesn't track that |
| Top-N | Regime-dependent ceiling, capped by measured operational capacity | Partly a market question, partly a "how much can the Controller actually review" question — the latter isn't a market-data question at all |
| Warm-up / staleness | Likely a fixed engineering constant | Driven by statistical stability of rolling calculations, not by strategy-outcome calibration |

---

## 6. Historical-data calibration process (design only — nothing built, nothing run)

### What we currently have

**FACT, reverified:** zero historical market data in this repository.

### 6.1 Historical data required

- **Daily OHLCV bars**, broad US equity + eligible-ETF universe (e.g. all
  Alpaca-tradable symbols meeting stage-A structural eligibility),
  sourced from Alpaca's historical bars API (same venue as execution).
- **Minimum period: at least 3 full years**, chosen specifically to contain
  more than one real market regime (at minimum one genuine broad-market
  correction or selloff, one sustained low-volatility period, and one
  sustained bullish trend) — a shorter window risks calibrating parameters
  against a single, unrepresentative regime.
- **Bar timeframe: daily bars as the primary series**, consistent with
  Task 1's finding that this strategy's holding period and relevant metrics
  (ATR, ADV$, SMA, RVOL) are all naturally daily-granularity. Intraday bars
  (e.g. hourly) are only needed if we later validate the fill-quality proxy
  more precisely (using intraday high-low range around our actual 08:30–
  14:30 CT check times), which is a secondary, lower-priority data need.
- **Sector/industry classification** (GICS or a free equivalent) for the
  concentration-parameter analysis.
- **A spread history proxy** — true historical quoted spread is often a
  paid data product; absent that, a high-low-range-based proxy must be
  explicitly labeled as an approximation in any resulting calibration, not
  presented as validated execution-quality data.
- **A broad-market volatility/drawdown series** (e.g. realized volatility
  and drawdown on a market-index proxy) for the regime-classification
  calibration in §3.4.

### 6.2 Metrics to measure per candidate parameter set

For each proposed configuration of the parameters in §3, run the frozen,
versioned approved strategy logic forward from each historical day's
universe and measure:

- Candidate count (daily distribution)
- Signal count and type (Ladder1-only / Ladder1+2 / Floor-hit / Trailing
  activation) distribution
- "Wasted slot" rate (candidates that never triggered anything while in the
  universe)
- "No-warning Floor" rate (trades reaching Floor without Ladder1 or Ladder2
  ever having fired — the failure mode §3.3's upper ATR% bound targets)
- Execution-quality proxy at simulated fill times
- Universe turnover / day-over-day churn
- Sector/correlation concentration over time
- Opportunity coverage vs. a broader, less-filtered reference set (what
  fraction of hindsight-good outcomes were actually admitted)
- Regime-segmented versions of all of the above (separately for labeled
  bull / selloff / low-vol sub-periods)

### 6.3 How to compare candidate parameter sets

- **Fix the strategy logic** identically across every comparison run — the
  only variable that changes between runs is the universe-selection
  configuration (per the Controller's explicit requirement, restated from
  the second-pass Task 3 design).
- Compare configurations **pairwise on the same historical period**, not by
  looking at absolute numbers from separate, differently-windowed runs.
- Report **regime-segmented** results, not just an aggregate — a
  configuration that looks good in aggregate could be doing so entirely on
  the strength of one regime and poorly in another; the aggregate would
  hide that.
- Prefer configurations that show **stable, explainable** behavior across
  the tested regimes over configurations that show the single best
  aggregate number — a configuration that swings wildly between regimes is
  itself a red flag even if its best-case number is attractive.

### 6.4 How to avoid overfitting

- **Split the historical period**: calibrate candidate parameter *forms*
  and rough ranges on an in-sample period, then validate on a held-out
  out-of-sample period the calibration process never touched. A parameter
  set that performs well in-sample but degrades materially out-of-sample is
  evidence of overfitting, not of a good parameter.
- **Prefer fewer, more robust parameters over many finely-tuned ones** — the
  formula-based recommendations in §3 (e.g., ATR% band as a function of the
  Ladder2-to-Floor gap, rather than an independently fit constant) are
  partly motivated by overfitting resistance: a formula tied to strategy
  geometry has a built-in economic rationale and is less likely to be an
  artifact of the specific historical sample than a freely-fit numeric
  constant.
- **Sensitivity-test around any proposed value** — a parameter whose
  measured outcomes change drastically for small changes in its value is a
  sign of a fragile, overfit choice; prefer values that sit on a stable
  plateau of the outcome metrics.
- **Cross-validate across sub-periods**, not just one in-sample/out-of-
  sample split, given we specifically want multi-regime robustness (§6.1).
- **Be explicit about what was NOT tested** — any parameter proposed for
  approval should state which regimes/periods it was and was not validated
  against, so the Controller can weigh residual uncertainty explicitly
  rather than have it hidden behind a single performance number.

### 6.5 Evidence required before a parameter moves from PROPOSED to APPROVED

A parameter should only be proposed for approval (with a specific value or
formula-with-calibrated-constants) once:

1. The calibration process in §6.1–6.4 has actually been run (not
   estimated, not assumed) using real historical data.
2. Results are reported regime-segmented, not just in aggregate.
3. Out-of-sample validation confirms the in-sample finding holds up (§6.4).
4. A sensitivity analysis shows the chosen value sits on a stable region of
   the outcome metrics, not a narrow local optimum.
5. The specific evidence (not just a conclusion) is presented to the
   Controller for review — consistent with CLAUDE.md §4's requirement that
   FACT/ASSUMPTION/HYPOTHESIS/RECOMMENDATION be clearly distinguished.

**No parameter in this document currently meets this bar.** All are
PROPOSED forms/formulas awaiting the calibration process itself to be
authorized and run.

---

## 7. Final recommended D-0026 mechanism

### A. PRINCIPLES — safe to approve now

(Restated from `decisions.md` D-0026, unchanged, reaffirmed by this
document):

1. The trading universe is dynamically generated from current market
   conditions — never a hardcoded or static production list.
2. The strategy engine is completely symbol-agnostic and receives only an
   `ApprovedUniverseSnapshot` — it has no knowledge of how symbols were
   selected.
3. TSLA is TEST-ONLY. It is never a production default, candidate, or
   fallback under any circumstance, including universe-generation failure.
4. Universe generation failure of any kind (data outage, computation error,
   insufficient data breadth, stale data) produces an **EMPTY** snapshot —
   never TSLA, never a fabricated symbol list, never a reused prior-day
   snapshot presented as current.
5. A market regime classified as high-stress / selloff (CRASH) does **not**
   automatically mean EMPTY. It tightens execution-quality and volatility
   thresholds (stages C/D via stage E). Only genuine data/computation
   failures, or a legitimately empty filter-survivor set, produce EMPTY.
6. Selection uses a **multi-stage** pipeline (hard structural/quality/
   strategy-mechanics gates first, then ranking among survivors, then
   diversification constraints, then a final count) — not a single opaque
   weighted composite score.
7. Spread (execution quality) and ATR% (strategy-mechanics fit) are
   load-bearing, hard-gate constraints because they interact directly with
   the approved Ladder/Floor mechanics (frozen entry reference, fixed
   percentage spacing) — they are not secondary ranking inputs.
8. Leveraged and inverse ETFs are structurally excluded — their
   daily-rebalancing mechanics are incompatible with fixed-percentage
   ladder math, independent of any calibratable threshold.
9. No artificial delay is imposed on newly discovered symbols beyond what
   the existing schedule already provides (the gap between the universe
   refresh and the first strategy check) — no evidence supports an
   additional one-cycle hold.
10. A symbol leaving the universe never introduces an additional execution
    veto beyond the existing D-0007 re-check. It never cancels an
    already-open position, and it never overrides a Controller decision
    already made — only un-decided (still-pending) proposals are cancelled,
    with notification.
11. Perplexity and Capitol Trades remain independent research/context
    sources (D-0019). They never modify universe membership, ranking,
    approval, or execution — at most, they annotate a Controller-facing
    proposal message, as a parallel, non-blocking, best-effort addition.
12. Universe selection is strictly subordinate to the approved strategy,
    the existing risk controls, Controller approval (D-0003), and the
    execution-time D-0007 re-check. It can narrow what the strategy engine
    is allowed to consider; it can never widen or override the strategy's,
    Controller's, or execution layer's authority.

### B. ARCHITECTURE — safe to approve now

1. **Pipeline order:** A (tradability) → B (data quality) → C (execution
   quality) → D (strategy-mechanics fit) → E (regime adaptation, adjusting
   C/D's thresholds) → F (ranking among survivors) → G (concentration
   constraints) → H (final count) → I (persist dated snapshot). Rationale
   for each stage's existence and position is in §1.
2. **Engine boundary:** the strategy engine consumes only the
   `ApprovedUniverseSnapshot` object defined in §4. It contains zero
   selection logic, zero data-source knowledge, and zero regime awareness.
3. **Persistence:** every snapshot is dated, immutable once written, and
   stored via the D-0024 repository abstraction (SQLite for MVP) — this
   gives both operational auditability and the reproducibility needed for
   the calibration process in §6.
4. **Refresh cadence:** primary daily refresh aligned with the existing
   pre-market schedule slot (07:00 America/Chicago per D-0021); a midday
   refresh is not recommended pending evidence that the daily-bar-based
   morning snapshot actually misses materially important intraday
   developments (`universe-parameter-validation.md` Task 7).
5. **Failure handling:** any hard failure at any pipeline stage collapses
   to an EMPTY snapshot with a logged, specific `empty_reason` — never a
   silent fallback of any kind.

### C. CONFIGURABLE PARAMETERS — keep TBD

All parameters catalogued in §3 remain **TBD in exact value or formula
constant**. Their recommended **forms** (percentile-relative,
liquidity-adjusted, formula-derived, regime-dependent, or — for warm-up/
staleness only — likely a fixed engineering constant) are proposed for
Controller review, but **no specific number is proposed for approval in
this document.**

### D. HISTORICAL DATA REQUIRED

See §6.1. Summary: daily OHLCV bars (≥3 years, multi-regime), sector
classification, a labeled spread proxy, and a broad-market volatility/
drawdown series — none of which currently exist in this repository.

### E. CALIBRATION / BACKTEST PLAN

See §6.2–§6.5. Summary: fix the strategy, vary only universe-selection
configuration, measure a defined metric set, compare regime-segmented
results, validate out-of-sample, sensitivity-test before proposing any
value for approval.

### F. FUTURE DECISIONS REQUIRED

1. Authorize collection of the historical dataset in §6.1 (a
   planning-to-implementation boundary decision, not itself an
   implementation step).
2. Authorize building the frozen, versioned strategy-simulation engine
   needed to run the calibration process (also a boundary decision — no
   code has been written under the current planning-mode instruction).
3. Decide the **sequencing** of §3.6/§3.7 (sector cap, correlation
   threshold) relative to the still-TBD portfolio-level hard risk limits
   (`risk-management.md §5`) — should concentration parameters wait until
   that decision exists?
4. Decide the scope of ETF inclusion for the MVP universe (all eligible
   categories from day one, or single-stocks-only initially with ETF
   categories phased in later) — a scope decision, not a numeric one.
5. Decide whether stage-B's warm-up/staleness parameters should be
   calibrated via the full backtest process or via the lighter statistical-
   stability check noted in §3.9.
6. Once calibration evidence exists (§6.5's five conditions met), review
   and approve specific parameter values/formula constants — explicitly
   NOT before then.

### G. FINAL PROPOSED D-0026 POLICY WORDING

> **D-0026 — Dynamic Universe Selection Mechanism (configurable design).**
>
> The production trading universe is generated daily by a multi-stage
> pipeline — tradability, data quality, execution-quality, strategy-
> mechanics compatibility, regime-adjusted thresholds, opportunity ranking,
> concentration constraints, and final count — producing a dated, immutable
> `ApprovedUniverseSnapshot` that is the sole interface to the fully
> symbol-agnostic strategy engine.
>
> Spread and ATR% are load-bearing hard filters, evaluated before any
> ranking, because they interact directly with the approved Ladder/Floor
> mechanics (frozen entry reference; fixed percentage spacing). Ranking
> among filter-survivors uses secondary signals (liquidity beyond the
> execution-quality floor, relative volume, trend context) and does not
> re-litigate the hard filters. Diversification constraints (sector,
> correlation) are applied as admission rules on the ranked, filter-
> survivor list, not folded into a single score.
>
> A high-stress or selloff market regime **tightens** execution-quality and
> volatility thresholds; it does **not** automatically empty the universe.
> Only genuine data or computation failure, or a legitimately empty
> filter-survivor set, produces an EMPTY snapshot. EMPTY means no new
> initial-entry proposals for that cycle; it never triggers a TSLA fallback,
> a fabricated symbol list, or reuse of a prior snapshot as current.
> Existing open positions are wholly unaffected by universe state and
> continue under their approved protective controls.
>
> Leveraged and inverse ETFs are structurally excluded. Newly discovered
> symbols are not subject to any artificial hold beyond the schedule's
> existing gap between refresh and the first strategy check. A symbol
> leaving the universe never closes an open position, never adds an
> execution veto beyond the existing D-0007 re-check, and never overrides a
> Controller decision already made.
>
> Perplexity and Capitol Trades remain independent, non-blocking research
> annotations on Controller-facing proposals; they never influence universe
> membership, ranking, approval, or execution.
>
> **Every numeric threshold and formula constant referenced by this
> mechanism (liquidity floor, spread cap, ATR% band, regime-classification
> thresholds, ranking composition, sector cap, correlation threshold, N, and
> warm-up/staleness) is explicitly TBD**, to be calibrated via the
> historical-data process in §6 before any specific value is proposed for
> Controller approval. This document approves the **mechanism's shape**, not
> its numbers.

---

## 8. Status

**PROPOSED / NOT APPROVED.** The principles in §7A and the architecture in
§7B are presented as safe to approve as **direction**, per the Controller's
request — but this document does not itself mark D-0026 APPROVED; that
remains the Controller's explicit action, recorded in `decisions.md`. No
parameter value or formula constant in §3 is proposed for approval. No code
has been written. No historical data has been collected. No live routine has
been touched.
