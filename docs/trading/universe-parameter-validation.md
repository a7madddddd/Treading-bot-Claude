# D-0026 — Parameter Validation Analysis (Second Pass)

**Status: PROPOSED / NOT APPROVED.** This document does not change D-0026's
approved architectural principles (recorded in `decisions.md`). It re-examines
every numeric parameter from `universe-selection-analysis.md` against evidence,
not convention. Where evidence is insufficient, the parameter is marked
**INSUFFICIENT EVIDENCE — KEEP PROPOSED** rather than guessed.

**Repository check performed before writing this document:** the repo contains
**no historical market data** — no bars, no CSVs, no price history of any kind.
This is a FACT, verified by directory search. It directly bounds what Task 3
can honestly deliver.

---

## TASK 1 — Strategy compatibility: what does OUR strategy actually need?

Re-derive candidate characteristics from the mechanics of the approved
strategy (`strategy.md`), not from generic screening folklore.

### 1.1 The strategy's shape

- Fixed-percentage laddered entries: 0%, −5%, −8%, −10% (Floor), all measured
  from the **frozen initial fill price** (D-0001, D-0009).
- Fixed share sizing: 10 / 10 / 20, uncorrelated with volatility, ADV, or
  price of the specific symbol.
- Compounded trailing floor once +10% is reached (D-0004, D-0008).
- Every ladder needs Controller approval within a 5-minute / ±0.5% window
  (D-0007) — **re-checked against the Alpaca Last Trade at submit time**
  (D-0012).
- Hourly market checks (D-0021): 08:30–14:30 CT, 7 samples/day.

### 1.2 What characteristics matter, and why — derived from the mechanics

| Characteristic | Why it matters to THIS strategy specifically |
|---|---|
| **Execution quality (spread, depth)** | Every fill is a market order. A wide spread directly changes the *actual* `original_initial_entry_fill_price` — which is frozen and never recalculated (D-0001). A bad initial fill poisons every subsequent ladder/floor level for the entire trade. This is a stronger requirement here than in strategies that use limit orders or that re-anchor to VWAP. |
| **Liquidity relative to our own order size** | Our orders are fixed and *small* in absolute terms (10, 10, 20 shares). This is unusual: most screening literature assumes the trader's order size scales with account size or conviction. Ours does not. This means our liquidity requirement should be judged **relative to whether 10–40 shares can move the price**, not against a generic "institutional-grade ADV" bar. A $5M ADV$ stock can absorb a 40-share order without issue; a $25M requirement may be *stricter than what our order size actually needs*. |
| **Volatility magnitude, specifically around the 5%/8%/10% band** | The strategy's edges are fixed distances: −5%, −8%, −10%. A symbol needs to move through this band in a way that lets Ladder 1 and Ladder 2 each have a *chance* to fire before Floor does. If daily volatility is much smaller than 5%, Ladder 1 essentially never fires (dead capital, wasted candidate slot). If daily volatility is much larger than 10%, Floor tends to fire directly from the initial entry, skipping the laddering thesis entirely (bypasses the "small-add / big-add on confirmation" logic that is the actual point of the strategy). **This is a strategy-specific volatility requirement, not a generic "moderate volatility" screen** — it must be calibrated to the 5/8/10 spacing, which nothing in general screening literature would derive for us. |
| **Trend / price stability at entry** | The strategy has no signal-based entry logic — entry timing is decided by the Controller approving an initial-entry proposal, not by a trend filter internal to the ladder math. A "trend gate" is a genuine *addition* on top of the approved strategy, useful for candidate quality but not derived from the strategy's own math. Must be flagged as a discretionary overlay, not a strategy requirement. |
| **Mean-reversion tendency vs. persistent-trend tendency** | The ladder strategy is implicitly a **mean-reversion bet on drawdowns, trend-following on the way up** (via trailing floor). A symbol that trends persistently downward without reverting will walk through Ladder 1 → Ladder 2 → Floor in sequence with no recovery — this is the strategy working as designed for a *losing* trade, not evidence the symbol was mis-screened. This tells us: **volatility structure matters more than directional trend** for candidate selection; a trend filter is a risk-reduction overlay, not a core requirement. |
| **Holding-period behavior (multi-day)** | Nothing in the approved strategy closes a position intraday. A trade can span many sessions. This means daily-bar-based metrics (ADV$, ATR, SMA20) are the *right granularity* — intraday microstructure metrics (1-min momentum, order-book depth) are not needed, confirming the Task 2 exclusion in the first analysis, now derived from the holding-period fact rather than asserted. |

### 1.3 What this means concretely

The two load-bearing filters, derived directly from the strategy's mechanics
(not convention), are:

1. **Execution quality relative to fixed, small order size** (spread + minimum
   absolute liquidity floor low enough that 40 shares is immaterial — not a
   "big enough for anyone" floor).
2. **Volatility calibrated to the 5%/8%/10% spacing** — this is a genuinely
   strategy-specific number that generic screening thresholds (built for
   different position-sizing models) will get wrong if copied uncritically.

Everything else (trend gate, RVOL, sector cap, correlation guard) is a
**risk-shaping overlay**, valuable but not derived from the strategy's own
math — and should be labeled as such rather than presented with equal
confidence.

---

## TASK 2 — Parameter sensitivity: gains and losses per alternative

For each parameter: what each choice buys and costs, **specifically for this
strategy**, not in the abstract.

### 2.1 Minimum price: $2 / $5 / $10 / $20

| Value | Gains | Costs |
|---|---|---|
| **$2** | Widest candidate pool; captures early-stage momentum names | Tick-size effects become large relative to price (a $0.01 tick is 0.5% of a $2 stock) — this **directly corrupts the ±0.5% D-0007 re-check band**, since a single tick can be the entire tolerance. Also correlates with wider % spreads and manipulation risk. |
| **$5** | Reduces tick/spread distortion materially vs $2 | Still permits genuinely thin, high-risk names; FINRA's informal "penny stock" line is $5, so this still admits borderline names |
| **$10** | Tick size is ≤0.1% of price, negligible interaction with the 0.5% D-0007 band; institutional-quality names dominate above this line | Excludes some legitimately liquid mid-cap names in the $5–10 range purely on price, not on any real risk factor |
| **$20** | Very low tick-distortion risk | No longer clearly justified by anything in our strategy's mechanics — this starts optimizing for "large-cap comfort" rather than a specific identified risk |

**Strategy-specific reasoning that generic advice would miss:** the binding
constraint is not "avoid penny stocks" in the abstract — it's **protecting the
±0.5% D-0007 window from being dominated by tick size**. At $5, a $0.01 tick
is 0.2% of price — already up to 40% of the entire ±0.5% tolerance band on a
single tick. At $10, a tick is 0.1% — 20% of the band. This is a real,
derivable interaction specific to D-0007, not present in generic screening
literature.

**Verdict:** $5 is not clearly wrong, but the D-0007 tick-interaction argument
favors **$10** as the evidence-preferred floor. Still: **INSUFFICIENT EVIDENCE
to fix an exact number** — the actual interaction depends on the real
distribution of spreads/ticks for candidate symbols, which we do not have
(§ Task 3). Recommend keeping PROPOSED, but flag $10 as better-justified than
the original $5.

### 2.2 ADV$ (average dollar volume): $10M / $25M / $50M / $100M

The key insight from Task 1: our order size is fixed and tiny (10–40 shares).
A $50 stock × 40 shares = $2,000 notional. Even a modest $10M ADV$ name trades
$10M/day — our worst-case order is **0.02% of one day's volume**. Standard
institutional screening thresholds ($25M+) are calibrated for position sizes
that are a meaningful fraction of ADV — ours never is.

| Value | Gains | Costs |
|---|---|---|
| **$10M** | Vastly larger candidate pool; our order impact is negligible regardless | May include names with momentarily thin books intraday even if daily ADV is adequate (ADV is an average, not a guarantee of depth at any instant) |
| **$25M** | Comfortable safety margin; excludes truly illiquid names | No evidence this improves *our* fills meaningfully over $10M, given how small our orders are |
| **$50M** | Very safe | Actively discards candidates for no risk reason specific to us — this threshold makes sense for a strategy trading meaningful size, not ours |
| **$100M** | Institutional-grade only | Same critique, amplified — over-filtering without a derivable reason |

**Verdict:** the original $25M/$50M proposal was **not derived from our order
size** — it was copied from generic institutional screening conventions. Given
Task 1's analysis, **$10M is better justified** as a floor because our
position sizes make market-impact concerns essentially moot at any ADV above
that level. The real risk is intraday depth-at-a-moment, which ADV$ doesn't
directly measure — spread is a better proxy for that (see below).
**INSUFFICIENT EVIDENCE to fix an exact number without real spread/depth
data for candidate symbols — keep PROPOSED, but flag that the original
$25M/$50M figures are demonstrably too conservative for our specific order
size and should not be adopted as-is.**

### 2.3 Spread: 5 bps / 10 bps / 20 bps

This is the parameter most directly load-bearing for D-0001 (frozen initial
fill price) per Task 1.

| Value | Gains | Costs |
|---|---|---|
| **5 bps** | Tightest execution quality; minimizes distortion of the frozen entry reference | Excludes many perfectly tradeable mid-cap names that have 8–15 bps spreads in normal conditions; may leave the universe too thin |
| **10 bps** | Reasonable balance | Still permits enough slippage that a single fill could already consume 20% of the entire ±0.5% D-0007 tolerance |
| **20 bps** | Widest pool | On a $10 stock, 20 bps = $0.02 — small in absolute terms but is 40% of the 0.5% D-0007 band; on a $50 stock, 20bps = $0.10, more digestible |

**Strategy-specific point generic advice misses:** spread should arguably be
evaluated **relative to price**, but ALSO relative to how much of the D-0007
±0.5% band it consumes, which varies by price level in a way that a flat bps
cutoff obscures. A $200 stock with 10bps spread has $0.20 absolute spread —
trivial next to a 5% ($10) ladder gap. A $10 stock with 10bps spread has
$0.01 absolute spread — same bps, far smaller absolute impact on fill
quality relative to the ladder's dollar gaps.

**Verdict:** the metric itself (bps of price) is defensible, but the specific
cutoff (10bps) is unvalidated. **INSUFFICIENT EVIDENCE — keep PROPOSED.**

### 2.4 ATR%: various ranges

This is the parameter Task 1 identified as genuinely strategy-derived (unlike
ADV$, which was borrowed from generic convention). Reasoning:

- Ladder 1 needs the symbol to plausibly move −5% from entry within the
  trade's expected lifetime.
- Ladder 2 needs −8%.
- Floor is −10%.
- If daily ATR% is very low (e.g., 0.5%), reaching −5% requires roughly
  `5% / 0.5% ≈ 10` "average days" of adverse, compounding movement in one
  direction — statistically rare for names without a trend. Ladder 1 would
  almost never fire; the symbol occupies a universe slot without offering the
  strategy's core mechanism a chance to operate.
- If daily ATR% is very high (e.g., 10%), a single bad session can blow
  through Ladder 1, Ladder 2, and Floor in one day (Floor is only 2 ATRs
  away) — the "small add, then confirm, then bigger add" thesis never gets
  the multi-session chance to prove out; the trade is effectively a coin-flip
  stop-out.

| Range | Gains | Costs |
|---|---|---|
| **1.0%–4%** | Wider pool | Upper end still risks Floor within 2-3 sessions on a genuine adverse move; may not leave room between Ladder 2 and Floor |
| **1.5%–6%** (original proposal) | Reasonable middle | Not derived from an explicit "how many ATRs of room does the strategy need" calculation — asserted, not proven |
| **2%–5%** | Tighter band directly reasoned from spacing (see below) | Smaller candidate pool |

**A derivable calculation Task 1 enables:** the gap from Ladder 2 (−8%) to
Floor (−10%) is only 2 percentage points. If a symbol's daily ATR% exceeds
~2%, a single adverse session can move the price the entire Ladder-2-to-Floor
distance, effectively removing the Controller's opportunity to review and
approve (or reject) a considered decision between those two levels — this
directly conflicts with the "Controller approval, not blind confirmation"
spirit of D-0003. This suggests an **upper bound derived from strategy
geometry: ATR% should stay meaningfully below the 2-point Ladder2-to-Floor
gap**, e.g., ATR% < ~2% is the geometrically clean cutoff, not the ~6%
originally proposed.

This is a genuine finding: **the original 6% upper bound is likely too loose**
— it was proposed by analogy to generic "volatile but tradeable" conventions,
not derived from the actual 2-point gap between Ladder 2 and Floor. A tighter
upper bound (order of 2–3%) is better justified by the strategy's own
geometry.

**Verdict:** the *lower* bound (~1.5%, ensuring Ladder 1 is reachable) is
reasonably justified by Task 1's reasoning. The *upper* bound needs
re-examination — evidence favors something closer to **2–3%** rather than
6%, derived from the Ladder2-Floor gap. Still, exact calibration needs real
data (do symbols with ATR% 2-3% actually produce a healthy distribution of
Ladder-1-only vs Ladder-1+2 vs Floor outcomes?). **INSUFFICIENT EVIDENCE for
an exact number — keep PROPOSED, but flag the original 6% upper bound as
likely miscalibrated; recommend the range be reconsidered toward 1.5%–3%
pending backtest evidence.**

### 2.5 Trend gate: `price ≥ SMA20 × 0.98`

Per Task 1, this is a discretionary overlay, not derived from the approved
strategy's own math (the strategy has no trend-entry logic — it's the
Controller who decides when to approve an initial entry). Its purpose is
candidate-quality risk reduction, not strategy compliance.

**Alternatives:**
- No trend gate at all — let the Controller's judgment on each initial-entry
  proposal serve as the human trend filter.
- Keep as a soft signal (part of the score) rather than a hard filter — this
  avoids silently removing legitimate laddering candidates (a stock in
  genuine drawdown that has NOT yet reverted is *exactly* the kind of
  candidate the ladder strategy targets — a hard trend gate could
  systematically exclude the strategy's best fit).

**This is a real tension Task 1 surfaces:** a hard trend-gate filter is in
mild conflict with the strategy's own purpose. The ladder strategy exists
*because* the Controller sometimes wants to buy into (and average down on) a
drawdown. Filtering out anything already below its 20-day average could
remove some of the most strategy-relevant candidates.

**Verdict:** recommend converting this from a hard filter to (at most) a
soft scoring input, or dropping it. **INSUFFICIENT EVIDENCE either way** —
but the direction of the recommendation (soften or drop) is now justified,
where before it was asserted.

### 2.6 Universe size (Top-N): 10 / 20 / 30 / 50

| Value | Gains | Costs |
|---|---|---|
| **10** | Smaller Telegram/candidate review burden for Controller; simpler to reason about | Given the approved 7-checks/day cadence and one-symbol-at-a-time approval workflow, a small N may still be more than the Controller can meaningfully track if multiple candidates trigger simultaneously |
| **20** (original) | Moderate | Not derived from anything specific to our workflow — appears to be a round-number convention |
| **30–50** | Larger opportunity surface | No mechanism currently exists to handle concentration across many simultaneous open trades — portfolio-level risk limits are still TBD (`risk-management.md §5`). Admitting 30-50 candidates before that exists is arguably premature. |

**Strategy-specific point:** N should be bounded above by what the Controller
can actually review and approve without alert fatigue (D-0007's 5-minute
approval window makes this concrete — if 15 Ladder triggers fire in the same
hourly check across 15 different open trades, the Controller has a hard
5-minute window per proposal to respond to each). This is a genuine
operational constraint the original analysis did not quantify.

**Verdict:** **INSUFFICIENT EVIDENCE for an exact N**, but the constraint that
should drive it (Controller's realistic response capacity under D-0007's
5-minute window, multiplied across however many symbols have open trades
simultaneously) is now identified. Recommend N stay conservative (10-15)
until the portfolio-level risk limits and Controller's actual usage pattern
are known.

### 2.7 Sector cap: 1 / 2 / 3 / no cap

Without portfolio-level risk limits (still TBD), concentration is currently
unconstrained. A sector cap is a partial substitute, not a designed risk
control.

| Value | Gains | Costs |
|---|---|---|
| **1** | Maximum diversification across the universe | May force exclusion of the single best-scoring names in favor of weaker names from underrepresented sectors, actively hurting quality for the sake of a diversification target that hasn't been sized against actual portfolio risk |
| **2** (original) | Some diversification | Arbitrary — not derived from any dollar-risk calculation |
| **3** | Looser | Same critique |
| **No cap** | Score-pure selection | Concentration risk fully unaddressed |

**Verdict:** this entire parameter is **downstream of the still-TBD
portfolio-level risk limits**. Setting a sector cap before those limits exist
is solving the wrong-ordered problem — the correct sequence is: define
portfolio-level max simultaneous positions / max sector dollar exposure
first (a Controller decision, `risk-management.md §5`), then derive a sector
cap from that, not the reverse. **INSUFFICIENT EVIDENCE — and structurally,
this parameter should wait on the portfolio risk-limit decision rather than
be set independently.**

### 2.8 Correlation limit: 0.70 / 0.80 / 0.85 / 0.90

Same structural issue as sector cap — this is a proxy for a risk limit that
doesn't formally exist yet. Additionally: computing pairwise 60-day return
correlation across a candidate pool is itself a design decision (window
length, return frequency) that has not been validated with real data.

**Verdict:** **INSUFFICIENT EVIDENCE**, and same structural point as 2.7 — a
correlation guard is a substitute for a portfolio risk limit that should be
decided first.

---

## TASK 3 — Historical/backtest feasibility

### What the repository actually contains

**FACT** (verified by directory listing before writing this document): the
repository contains zero historical market data — no bars, no OHLCV files, no
volume history, no spread history, no sector/GICS mapping, nothing. The only
data-adjacent content is the four routine prompt files (which contain live
API call instructions, not stored data) and the documentation tree itself.

### Consequence

**We cannot run the comparative experiment described in the task as of right
now.** Any numbers claiming to show "Configuration A produced 40 valid
signals vs Configuration B's 25" would be fabricated. I will not produce
invented results.

### What data would be required, and how to get it (design only — not executed)

To run the deterministic universe-selection experiment properly:

1. **Daily OHLCV bars** for a broad US equity universe (e.g., Russell 3000
   constituents or all Alpaca-tradable symbols), spanning at least 2–3 years
   to cover multiple regimes (a strong bull period, at least one meaningful
   correction/selloff, at least one low-volatility grind).
   - Source: Alpaca's historical bars API (`/v2/stocks/{symbol}/bars`) is the
     natural fit since it's our execution venue's own data — same asset
     coverage, no cross-venue symbol-mapping issues.
2. **Sector/industry classification** (GICS or a free equivalent) for the
   concentration analysis (Task 1 / §2.7).
3. **Historical spread proxies.** True historical bid-ask spread data is
   often a paid add-on; a defensible proxy is `(high-low)/close` per bar as a
   rough intraday-range stand-in, clearly labeled as an approximation, not a
   substitute for real quoted spread.
4. **A frozen, versioned snapshot of the approved strategy logic** (Ladder
   math, trailing floor math, D-0007 rules) as a pure function operating on a
   price series — this is the "backtest engine" and does not yet exist in
   this repository (no code has been written per Controller instruction).

### Deterministic experiment design (design only, not run)

```
For each candidate universe-selection CONFIG in {A, B, C, ...}:
  For each historical trading day D in the test period:
    universe(D, CONFIG) = apply CONFIG's filters+ranking to data as of D's
                           pre-market snapshot (no look-ahead: only data
                           available before D's open)
    For each symbol in universe(D, CONFIG):
      simulate the frozen approved strategy (Ladder/Floor/Trailing math)
      forward from D using actual subsequent daily bars
      record: did Ladder1 fire? Ladder2? Floor? Trailing activate?
              realized dollar outcome under the approved sizing (10/10/20)
  Aggregate per CONFIG across the whole test period:
    - candidate count (daily avg, distribution)
    - signal count (Ladder1/Ladder2/Floor trigger counts)
    - "wasted slot rate" = candidates that never triggered anything
    - "Floor-without-warning rate" = trades that hit Floor without Ladder1
      ever having a chance to fire first (single-day gap risk)
    - estimated execution quality (using the high-low proxy from spread)
    - universe churn (day-over-day turnover %)
    - universe stability across explicitly-labeled regime sub-periods
      (bull / selloff / low-vol, identified by realized SPY volatility
      and drawdown in that sub-period)
  Compare CONFIGs on the same fixed test period — the only variable
  changing between runs is the universe-selection CONFIG; the strategy
  logic itself is identical and frozen across all runs.
```

This design satisfies the Controller's requirement that the strategy itself
never changes during the experiment — only universe selection varies.

### What this experiment WOULD measure, mapped to the Controller's requested metrics

| Requested metric | How the experiment design measures it |
|---|---|
| Number of candidates | `len(universe(D, CONFIG))` per day |
| Number of valid strategy signals | Ladder1/Ladder2/Floor trigger counts |
| Signal quality | Distribution of outcomes (Ladder1-only vs Ladder1+2 vs Floor-hit) |
| Liquidity | ADV$ distribution of admitted symbols, cross-checked against realized fill-quality proxy |
| Estimated execution quality | high-low range proxy at simulated fill times |
| False/weak candidates | Candidates with zero triggers over their whole universe tenure ("wasted slots") |
| Concentration | Sector distribution of admitted symbols over time |
| Opportunity coverage | Compare admitted set against a broader unfiltered reference set — what fraction of "would-be good outcomes" (measured post-hoc) were actually admitted |
| Missed opportunities | Symbols excluded by CONFIG that, in hindsight, produced clean Ladder1→recovery outcomes |
| Turnover | Day-over-day set difference |
| Universe stability | Same, aggregated |
| Regime behavior | Same metrics computed separately within labeled sub-periods |

### Bottom line for Task 3

**INSUFFICIENT EVIDENCE — no historical data currently exists in this
project. The experiment above is a valid design but has not been run. No
parameter in this document should be treated as backtest-validated. This
should be the top implementation priority once Controller authorizes moving
past planning mode**, because it is the only way to convert any of these
PROPOSED numbers into evidence-based ones.

---

## TASK 4 — Market regimes

### Re-examining "CRASH → EMPTY" specifically, as instructed

**The Controller's challenge is well-founded.** Re-examining without
defaulting to the "sounds safe" answer:

**Argument for CRASH → EMPTY (the original proposal):**
- A laddered long-only strategy is structurally exposed to gap risk in a
  waterfall decline — the −5%/−8%/−10% band can be crossed in a single
  session, collapsing the Controller's ability to make considered per-ladder
  decisions (same geometric point as Task 2 §2.4's ATR analysis, at market
  level instead of symbol level).
- No portfolio-level risk limits exist yet (`risk-management.md §5` is TBD)
  — in the absence of a max-concurrent-positions or max-daily-loss circuit
  breaker, refusing new entries during a confirmed broad selloff is a
  reasonable stand-in safety measure.

**Argument AGAINST CRASH → EMPTY (taking the Controller's challenge
seriously):**
- **Broad selloffs are exactly when many quality names get oversold and
  offer the best risk/reward entries** — this is a real, well-documented
  market phenomenon, not a fringe view. A rule that says "find no
  opportunities during the exact conditions most likely to produce good
  entries" could be systematically leaving value on the table.
- The strategy's own Ladder mechanism is *designed* to handle drawdowns
  gracefully (small add, confirm, bigger add) — refusing to ever apply this
  mechanism during a drawdown is close to disabling the strategy's core use
  case precisely when it might matter most.
- Crucially: **CRASH→EMPTY does not close any existing positions** (per the
  approved D-0026 §9 rule) — it only blocks *new* entries. So the actual risk
  being defended against is "should the Controller be offered new entry
  candidates during a crash," not "should existing trades be protected" (they
  already are, via Floor/Trailing regardless of regime).
- Given that **every new entry still requires Controller approval (D-0003)**,
  the human is already the safety valve for "is now a good time to add a new
  position." An automatic EMPTY rule removes information (the existence of
  quality candidates) from the Controller rather than letting the Controller
  decide with full information.

### Recommendation on this specific point

**I now recommend against an automatic CRASH → EMPTY rule**, reversing the
first-pass recommendation, for the reasons above. The stronger design is:

- The universe subsystem **tightens** thresholds during detected high-vol/
  selloff regimes (smaller N, tighter execution-quality requirements, since
  spreads widen and slippage risk increases in stress) — but does **not**
  zero out the candidate list purely because volatility or drawdown crossed
  a threshold.
- A **true data outage or computation failure** still returns EMPTY — that
  rule is unrelated to market regime and remains fully justified (§Task on
  failure safety, unchanged from the first analysis).
- The Controller retains full visibility into whatever candidates survive
  tightened selloff-era filters, and decides per-proposal whether now is the
  right time to add exposure — consistent with D-0003's human-in-the-loop
  design.

This is a genuine reversal from the first-pass analysis, driven by taking
the Controller's specific challenge seriously rather than defending the
original recommendation.

### Regime-specific parameter adaptation — what should adapt vs. stay constant

| Regime | Should parameters adapt? | Reasoning |
|---|---|---|
| **Normal** | Baseline (whatever gets approved) | — |
| **High volatility** | Tighten execution-quality filters (spread cap); consider reducing N | Wider spreads in stressed markets directly threaten the D-0001 frozen-reference fill quality (Task 1's load-bearing concern) |
| **Low volatility** | Possibly widen ATR% lower bound tolerance slightly, since even "normal" names will show lower ATR% | Otherwise the universe could shrink for reasons unrelated to actual candidate quality |
| **Broad selloff** | Tighten execution quality, do NOT zero out (see above) | Reversed from first-pass — see reasoning above |
| **Strong bullish market** | Consider whether the trend-gate overlay (§2.5) should be de-emphasized — most things will pass a positive-trend filter in a strong bull market, making it a weak discriminator precisely when it matters least | Task 1 already flagged the trend gate as discretionary, not strategy-derived; in a strong bull market it becomes even less informative |
| **Sector rotation** | The correlation guard (§2.8) becomes more valuable here specifically — rotation creates exactly the kind of correlated-cluster risk that guard targets | But per §2.8 this parameter itself needs the underlying portfolio risk-limit decision first |

**All specific numeric adaptations above are INSUFFICIENT EVIDENCE for exact
values** — the direction (tighten execution quality in stress, don't zero the
universe) is justified by the reasoning above; the magnitude is not yet
justified by any data.

---

## TASK 5 — Ranking model: challenge the weighted score

### Re-examining the five dimensions for redundancy

- **Liquidity (ADV$) and Spread**: correlated in practice (higher ADV$ names
  tend to have tighter spreads), but they are not the same signal — a name
  can have high ADV$ with occasionally wide spreads around news events, or
  moderate ADV$ with a stable tight spread (a dividend aristocrat, say).
  **Not fully redundant, but correlated enough that combining both into a
  weighted score partially double-counts "is this name easy to trade."**
- **ATR% and Trend**: largely independent — a name can be high-ATR and
  trending, high-ATR and range-bound, low-ATR and trending, or low-ATR and
  flat. Genuinely orthogonal information.
- **RVOL**: distinct from ADV$ (RVOL is *today's* activity relative to the
  name's own baseline; ADV$ is the baseline itself). Not redundant with
  anything else in the list.

### Are any of these better as hard filters than score components?

**Yes — this is the strongest finding of Task 5.** Revisiting Task 1's
conclusion: **execution quality (spread) and strategy-band-compatible
volatility (ATR%) are the two load-bearing, strategy-derived requirements.**
Everything else (liquidity beyond the "won't move on our order size" floor,
trend, RVOL) is a secondary quality signal.

This argues for **restructuring rather than reweighting**: use spread and
ATR% (and a minimal ADV$ floor) as **hard filters** (pass/fail, derived from
strategy mechanics), and use liquidity-beyond-floor, trend, and RVOL as
**score components only among filter-survivors** — which is actually closer
to what the first-pass design already proposed structurally (hard filters
first, then score), except the first pass put *too many* things in the hard
filter stage (including ADV$ at a too-high bar) and the weighted score
underneath still mixed a load-bearing filter (ATR%) with secondary signals
(trend, RVOL) at comparable weight (0.20 each) — implying they matter
equally, which Task 1 shows they do not.

### Comparing the five approaches requested

**A. Weighted score (original proposal).**
Simple, single-number rankable output. Weakness demonstrated above: treats
load-bearing and secondary signals as comparable in weight without
derivation; a mediocre-but-acceptable-on-everything name can outscore a
name that's excellent on the two things that actually matter (spread, ATR%
fit) but middling on a secondary dimension (RVOL).

**B. Percentile ranking (rank each dimension, average the ranks).**
Similar structural weakness to A — still implicitly weights all dimensions
equally (or per arbitrary weights) unless combined with hard filters first.
Marginal benefit over raw weighted score: robust to outliers/scale
differences between dimensions. Doesn't fix the "combines load-bearing and
secondary signals" problem.

**C. Multi-stage ranking (tiered gates, then rank survivors by a secondary
metric).**
E.g.: Stage 1 = hard pass/fail on spread + ATR% band + minimal ADV$ floor
(the two load-bearing, strategy-derived filters from Task 1, plus the bare
liquidity floor). Stage 2 = rank survivors by a simple secondary score
(liquidity-beyond-floor + trend-as-soft-signal + RVOL). This directly
implements Task 1 and Task 5's finding: **don't let secondary signals
compete with load-bearing ones on equal footing.**

**D. Pareto / constraint-based selection.**
Select the set of symbols on the Pareto frontier across multiple objectives
(e.g., maximize liquidity while minimizing spread while keeping ATR% near
the strategy-optimal band) rather than collapsing to one score. Strength:
avoids arbitrary weight choices entirely for the secondary dimensions.
Weakness: harder to explain to a human reviewer at a glance ("why is symbol
X ranked above symbol Y" has a more complex answer than "higher score"), and
harder to produce a strict Top-N ordering when the frontier itself has many
members — needs a secondary tie-break rule anyway, which reintroduces some
of the weighting problem.

**E. Alternative not in the original list — threshold-then-diversify.**
Take all Stage-1 survivors (C's approach), then instead of ranking by a
secondary score, apply diversification constraints (sector cap, correlation
guard) directly as the *admission* mechanism, with ties broken by liquidity.
This treats "diversification" as a hard structural requirement rather than
letting a composite score implicitly and opaquely trade off "high score" vs
"diversification" against each other.

### Recommendation: which ONE approach

**Recommend C (multi-stage: hard filters on the two strategy-derived,
load-bearing dimensions — spread and ATR% fit — plus a bare liquidity floor,
then rank survivors by a secondary score built from liquidity-beyond-floor,
trend-as-soft-signal, and RVOL), with sector cap and correlation guard
applied as admission constraints during the ranking step (borrowing E's
insight), not as pre-filters and not folded into the score.**

Reasoning: this is the only option that (1) respects Task 1's finding that
spread and ATR% are strategy-derived and load-bearing while trend/RVOL/
liquidity-beyond-floor are secondary, (2) avoids arbitrarily equal weighting
across signals of clearly different importance, and (3) keeps diversification
as an explicit, auditable admission rule rather than an implicit tradeoff
buried inside a single score. It's a structural correction, not a
re-weighting of the same five-dimension formula.

**This changes the mechanism proposed in the first-pass document**
(`universe-selection-analysis.md §3`), which used a single five-dimension
weighted score. That section should be treated as superseded by this
finding, pending Controller review.

**Still INSUFFICIENT EVIDENCE** for the exact secondary-score weights or
exact hard-filter cutoffs — but the *structure* (multi-stage, not
single-weighted-score) is now justified by reasoning, independent of having
backtest data.

---

## TASK 6 — ETFs

Per-category analysis (not a blanket yes/no):

| Category | Fit with our strategy | Recommendation |
|---|---|---|
| **Broad index ETFs (SPY, QQQ, IWM, DIA)** | Very low idiosyncratic volatility; the whole point of these instruments is to average out single-name risk — this typically works *against* Task 1's ATR% requirement (they often sit below the useful volatility band for the 5/8/10 ladder spacing, especially SPY/DIA in calm periods). Extremely liquid, tightest spreads available — excellent on execution quality, weak on "can the ladder mechanism actually engage." | Marginal fit. Could be included as low-volatility-day filler but shouldn't be expected to be a strong candidate source. |
| **Sector ETFs (XLF, XLE, XLK, etc.)** | Sit between broad-index and single-name in volatility — more idiosyncratic movement than SPY, still diversified within the sector. Reasonable fit for the strategy's volatility needs. Also directly useful for Task 4's sector-rotation regime, where sector-level moves are the actual driver. | Reasonable fit — include. |
| **Leveraged/inverse ETFs (TQQQ, SQQQ, UVXY, etc.)** | As established in the first-pass analysis and unchanged here: daily-rebalancing decay and volatility-drag mechanics mean a −10% move in the leveraged ETF does not correspond to a −10% move in anything the Controller can reason about using ordinary price-return intuition, and the compounding math actively works against the assumption that Ladder/Floor percentages mean what they normally mean. | **Exclude.** This is the one category where the first-pass conclusion is reinforced, not weakened, by deeper analysis — the incompatibility is structural (daily rebalancing math), not a matter of degree. |
| **Single-country / thematic ETFs (ARKK, EWZ, etc.)** | Volatility and liquidity vary enormously by specific fund; some are effectively single-stock-concentration in disguise (a thematic fund dominated by 2-3 holdings). | Requires per-fund evaluation, not a blanket category rule — likely handled adequately by the existing correlation guard once the general equity screening filters apply to them the same way they apply to single stocks. No special-case rule needed beyond the general filters plus the correlation guard. |

**Verdict:** ETFs should NOT be blanket-included or blanket-excluded.
Recommend: single stocks + sector ETFs pass through the same general filter
pipeline; broad-index ETFs are permitted but will naturally rank low on the
ATR%-fit dimension in most conditions (self-limiting, no special rule
needed); leveraged/inverse ETFs are excluded by an explicit category rule
(the one hard categorical exclusion that survives scrutiny); thematic/
single-country ETFs pass through the general pipeline with no special
casing, relying on the correlation guard for the concentration risk they can
pose.

---

## TASK 7 — Universe refresh: challenging the 07:00 CT proposal

### Re-examining alternatives

**Pre-market (07:00 CT, original proposal).**
Uses fully-settled prior-session bars — deterministic, reproducible, no
look-ahead risk. Weakness: a full session old by the time trading begins;
cannot react to pre-market news/gaps before the 08:30 open.

**At market open (08:30 CT, same time as the first strategy check).**
Would need either (a) prior-session bars still (same data, just refreshed
later — no real benefit over 07:00, just less lead time for the Controller
to see the universe-delta notification before trading starts) or (b) live
opening-print data (introduces look-ahead/noise risk — the opening print is
often the most volatile, least representative data point of the day).
**Worse than 07:00 CT on both counts** unless there's a specific reason to
want same-day data, which there isn't since we use daily bars.

**Hourly (matching the strategy engine's own cadence).**
Would mean the "candidate pool" itself changes mid-day while trades may be
active. This conflates two different clocks: the strategy engine's hourly
*monitoring* cadence exists to check triggers on **already-selected**
symbols; it was never meant to imply the underlying candidate list should
also reshuffle hourly. Given the universe uses daily-bar-derived metrics
(ATR, ADV$, SMA20 — all Task 1 confirmed as the right granularity for this
multi-day-holding strategy), recomputing these every hour would mostly
produce the *same* numbers with noise, not genuine new information —
churning the universe without a corresponding data reason.

**At specific intraday times (e.g., a single 11:30 CT midday check).**
Only useful if something meaningfully new happened intraday that the 07:00
snapshot couldn't see (e.g., a large gap, a volume spike). This is a real
consideration, but note: Task 4 already established that our failure/regime
handling for "something dramatic happened" should be about *tightening
filters*, not making the *scan itself* more frequent. A midday re-scan adds
complexity (a second full pipeline run) to solve a problem (missing an
intraday development) that a simpler mechanism could address (see hybrid,
below).

**Regime-based / event-triggered refresh.**
Adds real complexity (defining what triggers an off-cycle refresh) for a
benefit that's still hypothetical without evidence that the 07:00-only
snapshot actually misses meaningful opportunities in practice.

### Recommendation

**Keep 07:00 CT as the primary refresh — this survives the challenge.** The
core reasoning (deterministic, daily-bar-appropriate granularity, aligned
with the existing pre-market Capitol Trades slot) holds up. The original
proposal was not wrong on this point.

**However:** the optional 11:30 CT midday refresh from the first-pass
document should be **explicitly deprioritized rather than left as a
"maybe."** Given Task 1's granularity finding (daily bars are the right
resolution for this multi-day strategy) and the churn-without-signal concern
above, a midday universe refresh doesn't have a clear justification unless
future evidence (from the Task 3 experiment, once data exists) shows the
07:00 snapshot is missing something material. Recommend explicitly labeling
it **not recommended pending evidence**, rather than "optional, disabled by
default" (which implies rough parity between the two options — it isn't a
close call based on this analysis).

**Cardinal rule reaffirmed, unchanged:** universe refresh is a data
operation on a candidate list; it never creates, modifies, or cancels an
order by itself. Only the strategy engine, through the existing D-0003/
D-0007 approval workflow, does that.

---

## TASK 8 — New symbols / dropped symbols: challenging the proposed handling

### 8.1 "New symbol waits one scheduler cycle" — is this necessary?

**Re-examining the original justification:** the first-pass reasoning was
"gives the human a chance to see the new candidate in the universe delta
notification before the engine can propose an entry." Testing this against
what actually happens operationally:

- The universe-delta notification (IMPORTANT level) fires at 07:00 CT.
- The first strategy check is at 08:30 CT — 90 minutes later.
- **The Controller already has 90 minutes of lead time before the first
  possible proposal on any newly added symbol**, even without an artificial
  one-day delay, simply because of the existing schedule gap between the
  universe refresh and the first strategy tick.

This means the one-scheduler-cycle delay is **largely redundant** with
protection that already exists from the schedule structure itself. The
delay's only *additional* effect is preventing a same-day initial-entry
proposal even if the Controller reviews the 07:00 notification and would
have been ready to approve an entry at 08:30.

**Does the delay violate or protect the approval model?** It doesn't violate
anything — the Controller still approves every initial entry regardless
(D-0003 is untouched either way). But it does add a full day of *latency* to
what the Controller's own judgment could otherwise act on same-day, without
a clearly identified risk it's protecting against beyond what the 90-minute
gap already provides.

**Recommendation: drop the one-scheduler-cycle delay as unnecessary.** The
90-minute gap between the 07:00 refresh and the 08:30 first check already
gives the Controller visibility before any possible proposal. An explicit
extra delay adds latency without a distinct, identified benefit. If the
Controller wants an extra caution period for genuinely new (never-before-
seen) symbols specifically — as opposed to symbols cycling back into the
universe — that's a reasonable, separate policy to consider explicitly, but
it shouldn't be conflated with "any day-to-day universe change."

### 8.2 "Pending proposal + universe dropped → cancel/block" — re-examine for approval-model violations

Re-checking each branch from the first-pass table against D-0003/D-0007:

- **PROPOSAL_PENDING → cancel.** No Controller decision has been made yet
  (proposal is un-answered). Cancelling an un-decided proposal does not
  override any Controller decision — it removes an option before a decision
  was made. **No violation.**
- **APPROVED, not yet submitted → block + notify CRITICAL, wait for
  Controller.** This is the one to scrutinize most carefully: **the
  Controller already approved this trade.** Automatically blocking it (even
  temporarily, pending re-confirmation) means the universe subsystem is
  overriding a Controller decision that was already made, based on a
  data-pipeline event (a symbol falling out of a screening list) that has
  nothing to do with the trade's own merit or the D-0007 price/time
  re-check, which is the *only* re-validation the approved policy actually
  specifies (D-0007).
  
  **This is a genuine finding: the original proposal's handling of this
  specific branch was arguably too aggressive.** D-0007 already defines
  exactly what re-validation an approved-but-unsubmitted proposal must pass
  (5 minutes, ±0.5%) before execution. Introducing a *second*, universe-
  membership-based veto on top of an already-Controller-approved trade adds
  a new gating condition that was not part of the approved execution policy,
  and does so via the universe subsystem — which per D-0026's own core
  principle is supposed to be *separate from* and *subordinate to*
  strategy/execution decisions, not a source of new blocking authority over
  an approved trade.

  **Revised recommendation:** an approved-but-unsubmitted proposal should
  proceed through the **existing** D-0007 re-check only. Universe membership
  changes should not introduce an additional block on an already-Controller-
  approved decision. (The CRITICAL notification informing the Controller that
  the symbol left the universe between approval and submission is still
  worth keeping — informational, not blocking.)

- **Open position (no ladders / with ladders filled) → untouched.** This
  matches the Controller's explicit instruction ("existing positions must
  not be closed merely because a symbol leaves the universe") and D-0026's
  stated principle. **No violation — confirmed correct, unchanged.**

### Summary of Task 8 findings

Two concrete corrections to the first-pass design, both in the direction of
**less** universe-driven intervention in already-Controller-decided matters:

1. Drop the new-symbol one-cycle delay (redundant with the existing 90-minute
   schedule gap).
2. Do not add a universe-membership block on an approved-but-unsubmitted
   proposal beyond the existing D-0007 re-check; keep the notification,
   drop the block.

---

## TASK 9 — Research sources: challenging the proposed architecture

### Re-examining the proposed pipeline

```
Market-data universe
        ↓
Quantitative candidate selection
        ↓
Strategy evaluation
        ↓
Research enrichment
        ↓
Controller proposal
        ↓
Controller approval
        ↓
D-0007 re-check
        ↓
Execution
```

### Does "Research enrichment" belong between Strategy evaluation and
Controller proposal, or somewhere else?

Testing the placement: research context (Perplexity summary, Capitol Trades
disclosures for that ticker) is only *useful* to the Controller at the
moment of deciding on a specific proposal — before a Ladder trigger has
actually fired, showing research on all 10-20 universe members would be
noise (most won't trigger anything this cycle) and would waste Perplexity
API calls speculatively. **Attaching research enrichment specifically to
the proposal (after a trigger has fired and a specific symbol/action is
under consideration) is the more efficient placement, and this is exactly
where the proposed pipeline puts it.** The original placement survives this
challenge.

### Is there a safer separation?

One refinement worth flagging: the pipeline as drawn implies research
enrichment sits *inside* the same flow as strategy evaluation, which could
be misread as research having *sequential* influence (i.e., "research runs
after strategy decides, so maybe it can still veto"). To make the separation
airtight and match D-0019's "independent evidence" principle exactly, it's
clearer to draw research as a **parallel, non-blocking annotation** rather
than a pipeline stage in series:

```
Market-data universe
        ↓
Quantitative candidate selection (hard filters + multi-stage ranking, Task 5)
        ↓
Strategy evaluation (Ladder/Floor/Trailing trigger detection, D-0001..D-0012)
        ↓
Ladder/Entry trigger fires  ─────┐
        ↓                       │ (parallel, non-blocking, best-effort)
Controller proposal  ◄───────────┘  Research enrichment (Perplexity +
        ↓                            Capitol Trades context, if available)
Controller approval
        ↓
D-0007 re-check
        ↓
Execution
```

**Key structural point, made explicit:** if the research call fails, times
out, or is unavailable, the proposal is sent to the Controller **without**
the research context, not delayed or blocked. This is consistent with the
existing `research-sources.md §5` health/integrity rule ("if Perplexity
errors, log IMPORTANT; the routine continues without inventing a summary")
and should be stated explicitly here too, since a "pipeline stage" framing
risks implying research is on the critical path.

### Verdict

**The proposed architecture's placement (research after trigger, before
Controller sees the proposal) is correct and survives the challenge.** The
one improvement is representational/structural: draw and document it as a
parallel, best-effort annotation rather than a serial pipeline stage, to
make the non-blocking, non-gating nature airtight rather than implied.

---

## TASK 10 — FINAL RECOMMENDATION

### A. APPROVED PRINCIPLES (already recorded in `decisions.md`, unchanged by this document)

- Trading universe is dynamic and market-adaptive; not hardcoded.
- Strategy engine is fully symbol-agnostic; no TSLA (or any) constants.
- TSLA is TEST-ONLY — never a production default, candidate, or fallback.
- Universe discovery/selection is architecturally separate from strategy
  execution.
- A symbol in the universe is a candidate only — never an authorization to
  trade. All approval (D-0003), risk, Ladder, Floor, Trailing, and D-0007
  controls remain fully authoritative regardless of universe membership.
- If a valid universe cannot be generated, the system returns EMPTY and does
  not trade — never falls back to TSLA, never invents symbols, never reuses
  a stale universe for that purpose.
- Perplexity and Capitol Trades remain independent research sources per
  D-0019; they do not automatically generate, rank, or authorize trades.
- Existing positions are never closed merely because a symbol leaves the
  universe.

### B. PROPOSED MECHANISM (revised by this analysis — supersedes the
first-pass mechanism pending Controller review)

- **Refresh:** single daily refresh at 07:00 America/Chicago using prior-
  session complete daily bars. Midday refresh: **not recommended** pending
  evidence (reversed from "optional/disabled by default").
- **Selection structure:** multi-stage, not single weighted score (Task 5
  finding). Stage 1 = hard filters on the two strategy-derived, load-bearing
  dimensions (execution quality / spread, and strategy-band-compatible
  volatility / ATR%), plus a bare minimum liquidity floor sized to our own
  fixed small order size, not generic institutional convention (Task 1, Task
  2.2). Stage 2 = rank Stage-1 survivors by a secondary score built from
  liquidity-beyond-floor, RVOL, and (optionally, as a soft signal only, not
  a hard gate) trend. Sector cap and correlation constraints are applied as
  explicit admission rules during Stage 2, not folded into the score (Task
  5's "threshold-then-diversify" insight).
- **Regime handling:** tighten execution-quality thresholds under detected
  high-volatility/selloff conditions; do **not** return EMPTY purely because
  of a volatility/drawdown regime (Task 4 — this reverses the first pass's
  CRASH→EMPTY default). EMPTY remains reserved for genuine data/computation
  failures (unchanged, Task Failure-safety section — not revisited by this
  document, stands from the first pass).
- **ETFs:** general filter pipeline applies to single stocks, sector ETFs,
  and thematic/single-country ETFs alike (with the correlation guard doing
  the concentration work for thematic funds); broad-index ETFs are permitted
  but will typically self-select out on the ATR%-fit dimension; leveraged/
  inverse ETFs are the one explicit categorical exclusion, justified by
  structural (not merely conventional) incompatibility with fixed-percentage
  ladder math (Task 6).
- **New symbols:** no artificial one-cycle delay — the existing 90-minute
  gap between the 07:00 refresh and the 08:30 first strategy check already
  gives the Controller visibility before any possible proposal (Task 8.1,
  reversed from the first pass).
- **Dropped symbols:** PROPOSAL_PENDING → cancel with notification (unchanged).
  APPROVED-but-unsubmitted → proceed through the **existing** D-0007
  re-check only; do not add a separate universe-membership block; keep an
  informational CRITICAL notification (Task 8.2, revised — the first pass's
  automatic block on this branch is withdrawn as an unjustified extra gate
  on an already-Controller-approved decision). Open positions (with or
  without ladders filled) → untouched, governed only by approved protective
  controls (unchanged).
- **Research placement:** parallel, best-effort, non-blocking annotation
  attached to the Controller's proposal message after a trigger fires — not
  a serial pipeline stage, and explicitly non-blocking if the research call
  fails (Task 9, structural clarification of the first pass, same net
  placement).

### C. PROPOSED PARAMETERS (still require Controller approval; this
document does not fix them, only narrows the reasoning)

| Parameter | First-pass proposal | This analysis's directional finding |
|---|---|---|
| Minimum price | $5 | Evidence (D-0007 tick-interaction) favors something closer to $10; not confirmed |
| ADV$ floor | $25M / $50M | Evidence (fixed small order size) favors something much lower, plausibly ~$10M; original figures likely over-conservative for our specific order size |
| Spread cap | 10 bps | Metric (bps of price, checked against D-0007 band consumption) is defensible; exact cutoff unconfirmed |
| ATR% band | 1.5%–6% | Lower bound (~1.5%) reasonably justified; upper bound likely too loose — geometry of the Ladder2-to-Floor gap (2 points) suggests something closer to 2–3%, not 6% |
| Trend gate | Hard filter, `price ≥ SMA20 × 0.98` | Recommend softening to a scoring input or dropping — a hard trend gate is in tension with the strategy's own drawdown-buying purpose |
| Universe size (N) | 20 | Recommend conservative (10–15) until portfolio-level risk limits and real Controller response-capacity under D-0007's 5-minute window are known |
| Sector cap | 2 | Structurally downstream of the still-TBD portfolio-level risk limits; sequencing issue, not just a number issue |
| Correlation limit | 0.85 | Same structural point as sector cap |
| Composite score weights | 0.30/0.20/0.20/0.20/0.10 | Superseded by the Task 5 restructuring (multi-stage, not single weighted score) — the specific weights no longer apply to the recommended mechanism |
| Refresh time | 07:00 CT primary | Survives challenge, kept |
| Midday refresh | Optional/disabled by default | Downgraded to not-recommended pending evidence |
| Stale-data threshold | 15 min | Not revisited by this document — carried over from the first pass |
| Missing-data failure ratio | 50% | Not revisited by this document — carried over from the first pass |
| CRASH → EMPTY | Yes | **Reversed** — regime should tighten filters, not zero the universe, per Task 4 |

### D. PARAMETERS THAT REQUIRE EMPIRICAL VALIDATION (cannot be responsibly
set without the Task 3 experiment)

- Exact minimum price cutoff
- Exact ADV$ floor
- Exact spread cap
- Exact ATR% band (both bounds)
- Exact universe size N
- Exact secondary-score weights (liquidity-beyond-floor / RVOL / trend)
- Whether the trend gate should be dropped entirely or retained as a soft
  signal, and if retained, its weight
- Exact regime-detection thresholds (what counts as "high volatility,"
  what counts as a "selloff" worth tightening filters for)
- Stale-data threshold and missing-data failure ratio (carried over,
  unvalidated by either analysis pass)
- Whether a midday refresh would in fact capture missed opportunities the
  07:00 snapshot doesn't — currently argued against on reasoning, not data

### E. DATA WE NEED TO COLLECT

1. Daily OHLCV bars for a broad US-equity + ETF universe, 2–3 years,
   sourced from Alpaca's historical bars API (same venue as execution — no
   symbol-mapping mismatch risk).
2. Sector/industry classification mapping (GICS or free equivalent) for the
   concentration analysis.
3. A high-low-range proxy for historical spread (true quoted-spread history
   is typically a paid add-on; the proxy must be clearly labeled as an
   approximation).
4. A frozen, versioned implementation of the approved strategy's Ladder/
   Floor/Trailing math as a pure function over a price series — this is the
   backtest engine itself, and per Controller instruction, has not been
   built (no code has been written in this planning phase).
5. A labeled set of historical regime sub-periods (bull / selloff / low-vol)
   within the test window, derived from realized SPY volatility and
   drawdown, for the Task 4 regime-behavior comparison.

### F. EXPERIMENT / BACKTEST PLAN

As designed in Task 3 above: fix the strategy logic, vary only the universe-
selection CONFIG, run multiple CONFIGs across the same historical period
including multiple regimes, and compare on: candidate count, signal count,
signal quality (Ladder1-only vs Ladder1+2 vs Floor-hit distribution),
execution-quality proxy, wasted-slot rate, concentration, opportunity
coverage vs a broader reference set, turnover, and regime-specific behavior.
**This experiment has not been run. No results exist. Building it is the
top priority once the Controller authorizes moving past the planning phase**
— it is the only way to convert any Section C parameter into an
evidence-based, approvable number rather than a defensible-but-unvalidated
proposal.

### G. OPEN DECISIONS FOR CONTROLLER

1. Approve or modify the **structural** changes in this document (Section B)
   — these are reasoning-based revisions to the first-pass mechanism, not
   backtest-validated, but they correct specific identified flaws:
   - Multi-stage selection instead of single weighted score (Task 5).
   - CRASH does not mean EMPTY; regime tightens filters instead (Task 4).
   - Drop the new-symbol one-cycle delay (Task 8.1).
   - Drop the universe-membership block on approved-but-unsubmitted
     proposals; rely on existing D-0007 re-check only (Task 8.2).
   - Trend gate downgraded from hard filter to (at most) soft signal
     (Task 2.5).
   - Deprioritize midday refresh from "optional" to "not recommended
     pending evidence" (Task 7).
2. Decide the **sequencing question**: should sector cap / correlation
   guard wait for the still-TBD portfolio-level risk limits
   (`risk-management.md §5`) to be decided first, given they are structurally
   downstream of that decision (Task 2.7, 2.8)?
3. Authorize building the backtest engine and collecting the historical
   data described in Section E — this is a **planning-to-implementation
   boundary decision**, not itself an implementation step, but it's the
   necessary next action to produce evidence for Section D's open
   parameters.
4. Decide whether ETF categories should be included at all in the MVP
   universe, or restricted to single stocks initially, deferring the sector-
   ETF/thematic-ETF inclusion to a later phase (Task 6 recommends inclusion,
   but this is a scope decision, not just a parameter).
5. Decide the exact numeric values in Section C once Section F's experiment
   produces evidence — none should be approved as final numbers today.

---

## What I recommend the Controller approve now, keep PROPOSED, or defer to backtesting

**Approve now (structural findings, reasoning-based, not requiring
backtest data to be sound):**
- Multi-stage selection structure over single weighted score.
- CRASH → tighten, not EMPTY.
- Drop new-symbol one-cycle delay.
- Drop the extra universe-membership block on approved-but-unsubmitted
  proposals (rely on existing D-0007 only).
- Trend gate downgraded to soft-signal-or-dropped.
- Research placement as parallel/non-blocking (clarifies, doesn't change,
  the first pass).
- Leveraged/inverse ETF exclusion (the one ETF-category finding backed by a
  structural, not conventional, argument).

**Keep PROPOSED (directionally reasoned, but not numerically confirmed):**
- Minimum price, ADV$ floor, spread cap, ATR% band bounds, universe size N,
  secondary-score weights.

**Defer to backtesting entirely (no defensible number without the Task 3
experiment):**
- Every exact numeric threshold in Section D.
- Sector cap and correlation limit specifically, pending the separate
  portfolio-risk-limit sequencing decision (Item 2 above) as well as
  backtest evidence.

**No live routine touched. No cron created. No orders submitted. No
dependencies installed. No approved trading-policy semantics changed.
D-0026's mechanism remains unapproved.**
