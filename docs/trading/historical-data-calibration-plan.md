# D-0026 — Historical Data & Calibration/Backtesting Methodology

**Status: PROPOSED / NOT APPROVED. Documentation only.** No historical data
has been collected. No code has been written. No live routine, scheduler,
or dependency has been touched. D-0026's mechanism remains PROPOSED as
recorded in `decisions.md`; this document does not change that status — it
specifies the methodology that would need to run **before** any numeric
parameter in `universe-selection-analysis.md §3` moves from PROPOSED to
APPROVED.

## Repository inspection performed before writing this document

**FACT** (verified by full file listing, not assumed): the repository
contains 35 files total, all Markdown documentation, `README.md`, and
`.env.example`. **Zero data files** (no CSV, Parquet, JSON data dumps),
**zero code** (no `.py`, no scripts), **zero dependency manifests** (no
`requirements.txt`, `pyproject.toml`). No data infrastructure of any kind
exists yet. This confirms the premise of the Controller's task and is
restated here as a checked fact, not an assumption.

---

## 1. Historical data requirements

For each dataset: why needed, required fields/timeframe/frequency,
point-in-time requirement, survivorship-bias concern, look-ahead-bias
concern, Alpaca sufficiency (checked against current documentation, not
assumed — see §2 for the source evidence), and mandatory/optional status.

### 1.1 Daily OHLCV bars

- **Why needed:** primary input for ADV$, ATR%, SMA/trend, and the frozen
  strategy simulation itself (Ladder/Floor/Trailing math operates on daily
  closes/fills in our approved policy).
- **Required fields:** open, high, low, close, volume, (ideally) VWAP,
  trade count — per bar.
- **Required timeframe:** multi-year, spanning several distinct market
  regimes (see §5 for exact period design — not "last 3 years" by default,
  per Controller instruction).
- **Required frequency:** one bar per trading day per symbol.
- **Point-in-time required?** Yes — bars must reflect what was actually
  knowable as of each simulated decision day; adjusted-for-splits handling
  must be applied consistently (see §1.9, Corporate Actions).
- **Survivorship bias concern:** **Yes, significant.** If the historical
  symbol universe is built from *today's* tradable-asset list, every
  delisted, acquired, or bankrupt company between then and now is silently
  excluded — this biases the "candidate pool" toward survivors and would
  make the calibration systematically overstate opportunity quality. This
  is addressed in §4.
- **Look-ahead bias concern:** Yes — a bar for day D must be usable only for
  simulating decisions *on or after* D closes; using D's close to inform an
  eligibility decision that would need to fire *during* D's session is a
  subtle but real trap (our own strategy checks intraday, at 08:30–14:30 CT,
  against the Last Trade — not the daily close — so the simulation must
  respect this distinction; see §8).
- **Alpaca sufficiency:** **FACT, confirmed via Alpaca's own documentation
  and community forum research (2026):** Alpaca provides approximately
  **7 years of historical data on the SIP feed** and **approximately 5
  years on the IEX feed**; IEX is free, SIP requires a paid "Algo Trader
  Plus" subscription. Each bar includes timestamp, open, high, low, close,
  volume, trade count, and VWAP. This is **sufficient for the primary daily
  OHLCV dataset**, subject to the survivorship-bias caveat in §4 (the
  bars-by-symbol endpoint does not by itself solve "which symbols existed
  and were tradable on each historical day").
  [Historical bars — Alpaca Docs](https://docs.alpaca.markets/us/reference/stockbars);
  [How to Fetch Historical Market Data with Alpaca's Trading API](https://alpaca.markets/learn/fetch-historical-data)
- **Mandatory or optional:** **Mandatory.**

### 1.2 Volume (see 1.1 — included in bars, called out separately per Controller's list)

- **Why needed:** ADV$ calculation (liquidity floor parameter, §7.1) and
  RVOL (ranking parameter, §7.5).
- **Required fields:** daily volume (already in the bar record, §1.1); for
  a more precise RVOL, an intraday volume profile would help but is not
  strictly required for a daily-granularity strategy (per Task 1 of the
  prior analysis pass, daily bars are the correct resolution for this
  multi-day-holding strategy).
- **Point-in-time / survivorship / look-ahead:** same considerations as
  §1.1 — this is the same dataset, not a separate acquisition.
- **Alpaca sufficiency:** Same as §1.1 — sufficient, same caveat.
- **Mandatory or optional:** Mandatory (bundled with §1.1).

### 1.3 Historical universe / asset metadata (which symbols were tradable on which historical dates)

- **Why needed:** to reconstruct, for each historical decision day, the set
  of symbols that were **actually tradable through our execution venue at
  that time** — not today's tradable list applied retroactively. This is
  the single most important input for avoiding survivorship bias (§4).
- **Required fields:** symbol, exchange, tradable status, active/inactive
  status, and — critically — the **date range** over which each status
  applied (a simple point-in-time snapshot of today's assets is not
  enough; we need historical status transitions).
- **Required timeframe:** matches the full calibration window (§5).
- **Required frequency:** ideally daily granularity for status changes;
  monthly may be an acceptable compromise if daily is unavailable (flagged
  as a gap to resolve, not assumed acceptable).
- **Point-in-time required?** Yes — this is the definition of the
  point-in-time requirement itself.
- **Survivorship bias concern:** This dataset **is the mitigation** for
  survivorship bias elsewhere, so by definition it must not itself be
  survivorship-biased (i.e., it must include delisted/inactive symbols'
  historical tradable windows, not just currently-active ones).
- **Look-ahead bias concern:** Yes — we must not use a symbol's *eventual*
  delisting date to retroactively exclude it from earlier decision days
  when it was still legitimately tradable.
- **Alpaca sufficiency: GAP, confirmed by evidence, not assumed.** Alpaca's
  Assets API (`GET /v2/assets`) returns a **current** snapshot of asset
  status (`tradable`, `status=active/inactive`) — it is a master list, not
  a point-in-time historical record of status *changes* over time. Multiple
  2025–2026 Alpaca community forum threads independently report that
  requesting historical bars for symbols currently marked `status=inactive`
  returns **empty data even for date ranges when the symbol should have
  been actively trading**, and separately that ticker-symbol changes can
  produce empty historical results around the change date. This confirms —
  from Alpaca's own user community, not from our assumption — that Alpaca's
  historical data access for delisted/renamed/inactive symbols is
  **unreliable in practice**, independent of whatever the Assets API
  nominally returns.
  [Delisted tickers — Alpaca Community Forum](https://forum.alpaca.markets/t/delisted-tickers/18227);
  [Get Historical Data for Inactive Stocks — Alpaca Community Forum](https://forum.alpaca.markets/t/get-historical-data-for-inactive-stocks/10097);
  [Empty historical bar data due to ticker change — Alpaca Community Forum](https://forum.alpaca.markets/t/empty-historical-bar-data-pro-v2-due-to-ticker-change/7288)
- **What this means concretely:** **Alpaca alone is likely NOT sufficient**
  for reconstructing a survivorship-bias-free historical universe. An
  external point-in-time constituent/status dataset (e.g., a
  point-in-time index-membership history, or a commercial point-in-time
  security-master data product) is likely required to responsibly build
  this input. This is flagged as an **open gap requiring a decision**, not
  silently worked around.
- **Mandatory or optional:** **Mandatory** for a survivorship-bias-free
  calibration; without it, calibration can still run but must be labeled
  as **survivorship-biased** and treated as directional evidence only, not
  as suf­ficient grounds for final numeric approval (§13, §14).

### 1.4 Sector / GICS classification

- **Why needed:** the sector-cap and correlation-guard parameters (§7.6,
  §7.7) need a sector/industry label per symbol to operate at all, and the
  concentration-metric measurement in calibration (§9) needs it too.
- **Required fields:** symbol → sector (and ideally sub-industry) mapping.
- **Required timeframe:** ideally **historical**, since a company's sector
  classification can change over the calibration window (reclassifications
  happen periodically as index providers update GICS); using only today's
  classification retroactively is itself a look-ahead-bias risk, smaller
  than the price-data case but real.
- **Point-in-time required?** Ideally yes; a reasonable simplification
  (today's classification applied throughout) is a defensible interim
  compromise but must be **explicitly labeled** as such, not silently
  assumed accurate historically.
- **Survivorship bias concern:** Indirect — if the sector mapping source
  itself only covers currently-listed companies, delisted names from §1.3
  would have no sector label and would need to be dropped from
  concentration analysis or handled with an "unknown sector" bucket.
- **Look-ahead bias concern:** Yes, as above (using today's classification
  for historical decisions).
- **Alpaca sufficiency: GAP, confirmed by evidence, not assumed.** Web
  research on Alpaca's Assets API documentation and structure did not
  surface any GICS/sector/industry classification field in the standard
  asset record (the Assets API's documented fields are oriented around
  tradability — `asset_class`, `exchange`, `tradable`, `status` — not
  fundamental/classification data).
  [Get Assets — Alpaca Docs](https://docs.alpaca.markets/us/reference/get-v2-assets-1)
  **An external source is required** — e.g., a GICS data provider, a free
  sector-mapping dataset, or a fundamentals API that includes
  classification (several commercial and some free options exist; specific
  source selection is a future decision, not made here).
- **Mandatory or optional:** Mandatory for §7.6/§7.7 calibration; optional
  for the core strategy-mechanics calibration (§8), which does not depend
  on sector data.

### 1.5 ETF classification (which symbols are ETFs at all)

- **Why needed:** to separate the ETF-inclusion analysis from single-stock
  analysis, and to identify sector/thematic/broad-index sub-categories.
- **Required fields:** symbol → is-ETF flag, and ideally a
  sub-classification (broad-index / sector / thematic / single-country).
- **Alpaca sufficiency:** Alpaca's Assets API includes `asset_class` (e.g.
  `us_equity`) but this does not by itself distinguish an ETF from a
  common stock in a clearly documented, reliable field based on the
  research performed for this document — **flagged as unverified, not
  claimed available.** A supplementary source (e.g. a maintained ETF
  ticker list, or a data vendor's fund-classification field) is likely
  needed and should be explicitly verified before being relied upon,
  rather than assumed present.
- **Mandatory or optional:** Mandatory (leveraged/inverse exclusion, §1.6,
  depends on first knowing which symbols are funds at all).

### 1.6 Leveraged/inverse ETF identification

- **Why needed:** D-0026's structural exclusion of leveraged/inverse ETFs
  (approved principle) needs an explicit, auditable identification
  mechanism — this cannot be inferred from price/volume data alone.
- **Required fields:** symbol → leverage factor / direction flag (e.g.
  "3x long," "−1x short," "not leveraged").
- **Alpaca sufficiency:** Not verified as available from Alpaca's asset
  metadata based on the research performed for this document. This is very
  likely an **external, curated list** (leveraged/inverse ETF issuers are a
  known, enumerable set — ProShares, Direxion, etc. — so a maintained
  exclusion list is a realistic, low-effort external source, but it must
  be sourced and maintained, not assumed to exist in Alpaca).
- **Mandatory or optional:** Mandatory (this is a hard structural exclusion
  already approved in principle — the mechanism to actually apply it needs
  this data).

### 1.7 Spread information or a defensible spread proxy

- **Why needed:** execution-quality hard filter (§7.2) and the fill-quality
  proxy used throughout calibration metrics (§9).
- **Alpaca sufficiency — more nuanced than the prior-pass assumption.**
  **FACT, confirmed by this session's research:** Alpaca documents
  dedicated **historical quotes** and **historical trades** endpoints
  (`GET /v2/stocks/{symbol}/quotes` and `.../trades`), separate from the
  bars endpoint, which would in principle provide actual historical
  bid/ask quote data — a real spread measurement, not merely a proxy. This
  is a **materially better starting point than the second-pass document
  assumed**, which discussed only a high-low-range proxy without checking
  whether true quote history existed.
  [Historical quotes — Alpaca Docs](https://docs.alpaca.markets/us/reference/stockquotes-1);
  [Historical trades — Alpaca Docs](https://docs.alpaca.markets/reference/stocktrades-1)
  **However**, this document does not claim this data is complete, clean,
  or free of the same reliability issues flagged in §1.3 (community
  reports of historical data quality problems — VWAP calculation
  complaints, missing daily-timeframe data — appeared in the same forum
  search, suggesting general historical-data quality should be verified
  empirically, not assumed, before being relied upon for calibration).
  [Historical VWAP data very wrong — Alpaca Community Forum](https://forum.alpaca.markets/t/historical-vwap-data-very-wrong/14272);
  [Missing historic data for daily timeframe — Alpaca Community Forum](https://forum.alpaca.markets/t/missing-historic-data-for-daily-timeframe/14567)
  Additionally, the free **IEX feed only reflects IEX's own order book
  (~2.5% of US equity volume)** — a spread computed from IEX-only
  historical quotes would **not represent the true consolidated market
  spread** most of our actual fills would experience (our own execution
  happens against the broader market, not IEX alone). The full-market
  **SIP feed requires a paid subscription** — a real cost/scope decision,
  not something to assume away.
  [Difference between IEX and SIP in historical data? — Alpaca Community Forum](https://forum.alpaca.markets/t/difference-between-iex-and-sip-in-historical-data/10191)
- **Revised recommendation:** attempt to use **true historical quotes via
  SIP** (subscription cost is a Controller decision, §15) as the primary
  spread source; if SIP access is not authorized, fall back to a **high-low
  range-based proxy computed from daily bars**, explicitly labeled as an
  approximation, per the Controller's explicit instruction not to pretend
  a range proxy equals a real spread (§3 below covers this in detail).
- **Mandatory or optional:** Mandatory in some form (true quotes preferred,
  proxy acceptable as a clearly-labeled fallback).

### 1.8 Broad-market volatility and drawdown / regime information

- **Why needed:** regime classification (stage E of the pipeline;
  parameter §7.4) needs a market-wide reference series, independent of any
  single candidate symbol.
- **Required fields:** a broad-market index price series (e.g. a
  large-cap index ETF as a tradable proxy, since Alpaca's own data covers
  equities/ETFs it can trade) with enough history to compute rolling
  realized volatility and drawdown.
- **Alpaca sufficiency:** likely sufficient — a broad index ETF (e.g. a
  large, liquid market-tracking fund) is itself a tradable US equity ETF
  and should be covered by the same historical-bars capability as §1.1. A
  true VIX-style implied-volatility series is a separate, options-derived
  data product and not confirmed available from Alpaca's equity/ETF bars
  endpoint — **flagged as unverified**, with the realized-volatility-on-an-
  index-ETF approach recommended as the more clearly available substitute
  (consistent with the "percentile-relative to trailing history" form
  already recommended for this parameter in the third-pass design).
- **Mandatory or optional:** Mandatory for regime-parameter calibration
  (§7.4); the core strategy-mechanics calibration (§8) does not strictly
  require it, though regime-segmented reporting (§6, §9) does.

### 1.9 Corporate actions (splits, mergers, symbol changes) — additional dataset identified beyond the Controller's explicit list

- **Why needed:** un-adjusted historical prices around a split or corporate
  action would produce a fabricated "Floor trigger" or "Ladder trigger"
  purely from the corporate action, not from a real market move — this
  would corrupt calibration results in a way that's hard to detect after
  the fact.
- **Required fields:** action type, effective date, and adjustment factor,
  per symbol.
- **Alpaca sufficiency: FACT, confirmed by this session's research.**
  Alpaca documents a dedicated **Corporate Actions API**
  (`CorporateActionsClient`) as part of its platform. This appears to be
  available, though the research performed for this document did not
  verify its exact historical depth or completeness — **flagged for
  verification, not assumed complete.**
  General search context: [historical data reference — Alpaca-py](https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html)
- **Mandatory or optional:** Mandatory. This is a correctness requirement
  for the backtest engine, not an optional enhancement — using
  split-unadjusted prices without correction would silently corrupt every
  downstream metric.

### 1.10 Summary table

| Dataset | Mandatory? | Alpaca sufficient? | Gap / external source needed |
|---|---|---|---|
| Daily OHLCV bars | Mandatory | Yes (confirmed) | None, subject to §1.3's caveat |
| Volume | Mandatory | Yes (bundled with bars) | None |
| Point-in-time tradable-universe history | Mandatory (for unbiased calibration) | **No — gap confirmed** | External point-in-time security-master / index-constituent history |
| Sector/GICS classification | Mandatory (for §7.6/§7.7 only) | **No — not found in Assets API** | External classification source |
| ETF classification | Mandatory | Unverified, likely partial | Supplementary ETF ticker/classification list |
| Leveraged/inverse ETF list | Mandatory | **No** | Curated exclusion list (small, enumerable issuer set) |
| Spread / quotes | Mandatory (proxy acceptable as fallback) | **Partially — true quotes exist but SIP (paid) needed for full-market coverage; IEX-only is a known-incomplete view; general data-quality complaints exist in the community and must be verified empirically** | SIP subscription (cost decision) or accept a labeled range-proxy fallback |
| Broad-market vol/drawdown | Mandatory (for regime calibration) | Likely yes, via an index ETF's own bars | VIX-style implied vol not confirmed available |
| Corporate actions | Mandatory | Likely yes (dedicated API exists) | Verify historical depth/completeness before relying on it |

---

## 2. Data source decision

### Is "daily OHLCV bars ≥3 years from Alpaca" (the prior-pass framing) actually enough?

**No — confirmed insufficient as a complete description, evidenced above.**
Daily OHLCV bars alone would support the core strategy-mechanics
calibration (§8) reasonably well, but the full parameter catalog (§7)
needs at least four additional dataset categories (§1.3, §1.4, §1.6, and
ideally §1.7's true-quote form) that Alpaca's bars endpoint does not
provide, and at least two of those (§1.3 point-in-time universe history,
§1.4 sector classification) are **not available from Alpaca at all** based
on the research performed for this document.

### What the repository already has

Nothing (§ "Repository inspection performed before writing this document,"
above). This is unchanged from the prior two analysis passes and is
reconfirmed here.

### What Alpaca can provide (confirmed by evidence, cited above)

- Daily (and finer) OHLCV bars, ~7 years SIP / ~5 years IEX.
- Historical quotes and trades endpoints (true bid/ask and executed-trade
  history) — IEX free / SIP paid, with IEX covering only ~2.5% of volume.
- A Corporate Actions API (depth/completeness not independently verified
  here).
- A current (not historical point-in-time) Assets list for tradability.

### What Alpaca likely cannot provide, or was not confirmed to provide (gaps)

- Point-in-time historical universe/tradability status changes (only a
  current snapshot is confirmed; community reports independently confirm
  practical unreliability for delisted/renamed symbols).
- Sector/GICS classification.
- Explicit leveraged/inverse ETF flags.
- A confirmed options-derived volatility index series.

### What can only be approximated

- Spread, if SIP access is not authorized — falls back to a labeled
  high-low-range proxy (§3).
- Historical sector classification, if only a *current* mapping is
  sourced externally — falls back to a labeled "today's classification
  applied retroactively" simplification (§1.4).

### Bottom line

Alpaca is a **necessary but not sufficient** data source. At least one, and
likely two, external data sources are required (point-in-time universe
history; sector classification) before a genuinely unbiased calibration can
run. This is a concrete decision point for the Controller (§15, §16), not
something to work around silently.

---

## 3. Spread proxy — detailed analysis

The Controller specifically flagged this as important. Full analysis:

### Is the high-low-range proxy acceptable for calibration?

**Only as a labeled, relative, lower-confidence approximation — never as a
stand-in for real spread.** A daily high-low range measures **intraday
price dispersion**, which correlates with, but is not the same thing as,
the **bid-ask spread at any specific moment of execution**. A stock can
have a wide high-low range purely from legitimate price discovery across
the day while having a perfectly tight spread at the specific moment our
strategy would execute (our checks are at 08:30–14:30 CT, not at
open-to-close extremes) — or the reverse, a narrow daily range with a
temporarily wide spread during a specific illiquid moment the range-based
measure would never capture.

### What information does the proxy lose?

- **Timing precision** — our order executes at a specific check time
  (hourly, 08:30–14:30 CT); the daily range says nothing about spread
  *specifically at that hour*.
- **True two-sided quote structure** — high-low range reflects trade
  prices, not the bid/ask quote itself; a stock can trade across a wide
  range on light volume with a much tighter quoted spread than the range
  implies, or vice versa.
- **Depth-at-price** — neither the range proxy nor a simple spread number
  captures whether our specific 10-40 share order could actually execute
  at the quoted spread without moving the price (a full order-book depth
  question, out of scope for either the proxy or a simple average-spread
  measurement).

### How could it bias results?

- Systematically underestimates true execution cost for stocks whose
  volatility is concentrated at the open/close (common) versus the mid-
  session times we actually execute at, which tend to be calmer — the
  proxy could make some names look worse (or better) than they'd actually
  behave at our check times.
- Could favor names with erratic single-tick spikes (which widen the daily
  range without reflecting typical trading conditions) over names with
  consistently moderate but persistent spreads.

### Should it be used only as a relative execution-quality proxy?

**Yes — this is the responsible framing.** It should be used to **rank**
symbols against each other on relative execution difficulty within the
same calibration run (consistent with the already-proposed "percentile-
relative within today's eligible pool" form for the spread parameter), not
to claim an absolute dollar or basis-point spread estimate that gets
compared against, e.g., a literal ±0.5% band as if it were the real
quoted spread.

### Do we need a better historical source?

**Ideally yes — and one may already exist (§1.7's finding).** The
discovery that Alpaca documents true historical quotes/trades endpoints
means the "we only have a proxy" premise from the second-pass document was
**incomplete**. The path forward should be:

1. Verify whether the historical quotes endpoint, under whichever feed tier
   is authorized (IEX free vs. SIP paid), returns usable, sufficiently
   complete bid/ask data for our specific candidate symbols and check
   times.
2. If yes (especially under SIP), use it as the **primary** spread
   measurement and drop the range proxy to a fallback role only.
3. If SIP is not authorized and IEX-only quotes prove too sparse/
   unrepresentative (IEX is only ~2.5% of volume), fall back to the
   high-low range proxy, explicitly labeled, per the framing above.

### Can the D-0007 ±0.5% price band meaningfully constrain it?

**Yes, as a sanity check, not as a validation.** The reasoning from the
second-pass document still holds: whatever spread measurement is used
(true quotes or the range proxy) can be expressed as "what fraction of the
D-0007 ±0.5% tolerance does this spread consume" — this is a useful,
strategy-specific way to interpret an otherwise abstract number, and
applies regardless of which underlying spread-data source is ultimately
used. It doesn't make the range proxy more accurate; it makes the *proxy's
output more interpretable* in the context of our specific approval
mechanics.

---

## 4. Point-in-time / survivorship bias — design

### The core risk, restated concretely

If we build "the historical universe" by taking **today's** Alpaca asset
list and pretending it existed unchanged for the last 3+ years, every
company that was delisted, went bankrupt, was acquired, or was renamed
during that window is invisibly erased from the historical simulation.
This would make the calibration's "candidate pool" artificially clean —
survivors only — and any resulting parameter would be calibrated against a
too-easy dataset that doesn't resemble what live trading will actually
face (where some fraction of candidates genuinely will go bad).

### Design to avoid it

1. **Do not derive the historical candidate universe from today's asset
   list.** Require a point-in-time record (§1.3) of what was tradable on
   each historical decision date, including symbols that no longer exist
   today.
2. **Explicitly include delisted/failed names in the in-sample and
   out-of-sample periods**, not just successful survivors — this is
   precisely the population the "wasted-slot rate" and "no-warning-Floor
   rate" metrics (§9) are partly designed to catch.
3. **Historical sector classifications**: apply the point-in-time mapping
   where available (§1.4); where only current classification is sourced,
   label results explicitly as using a simplification, and treat
   concentration-metric findings from that period with correspondingly
   lower confidence.
4. **Historical tradability**: a symbol that was halted, restricted, or
   otherwise not genuinely tradable on a historical date must be excluded
   from that date's simulated candidate pool — using only price-continuity
   as a filter is not sufficient (a halted stock can still show
   interpolated or stale price data in some datasets).
5. **Corporate actions (splits, symbol changes)**: apply split-adjustment
   consistently (§1.9) so a 2-for-1 split doesn't fabricate a −50% "Floor
   trigger" that never actually happened to a real position.
6. **ETFs that changed characteristics**: some ETFs change their tracked
   index, leverage profile, or even direction over their lifetime (fund
   closures/relaunches, strategy changes) — where this is material and
   detectable from prospectus/classification history, it should be
   reflected at the correct historical date, not assumed constant.
   Flagged as a lower-priority, harder-to-source item — reasonable to
   accept as a known limitation if a dedicated fund-history dataset isn't
   available, provided it's disclosed as such.
7. **Data availability at the exact historical decision time**: the
   calibration engine must only use data that would have been genuinely
   knowable as of each simulated decision — e.g., a bar for day D must not
   be used to make a decision that (in the live system) would actually
   fire against an *intraday* Last Trade during day D's session, before
   day D's bar is even complete. This interacts directly with §8's
   strategy-mechanics fidelity requirement.

### What level of historical fidelity is realistically required?

**A pragmatic, tiered answer, not an all-or-nothing one:**

- **Tier 1 (mandatory before any numeric approval):** point-in-time
  tradable-universe history (§1.3) and split/corporate-action adjustment
  (§1.9). Without these two, calibration results are not just
  approximate — they can be actively misleading (invisible survivors;
  fabricated triggers from unadjusted splits).
- **Tier 2 (materially improves confidence, pursue if reasonably
  available):** historical sector classification, true historical
  quotes/spread via SIP.
- **Tier 3 (acceptable to approximate with clear labeling):** ETF
  characteristic-change history, exact intraday depth-at-price data.

A calibration that has Tier 1 but not Tier 2 can still produce useful,
labeled, lower-confidence directional evidence. A calibration missing Tier
1 should **not** be used to justify moving a parameter to APPROVED (§13,
§14).

---

## 5. Backtest data split design

### Rejecting the "just use the last 3 years" default

A fixed "last 3 years" window is exactly the kind of convention-based
choice the Controller has repeatedly asked this project to avoid without
justification. The right question is: **does the chosen window actually
contain the regime diversity the calibration needs to test?** — not "how
many years is customary."

### Recommended structure: multi-regime blocks with walk-forward validation, not one static 3-way split

**Why walk-forward over a single static in-sample/out-of-sample split:**
A single static split risks the in-sample period accidentally being
dominated by one regime and the out-of-sample period by another,
confounding "does this parameter generalize" with "did the regime happen
to match." Walk-forward validation instead repeatedly slides the
in-sample/out-of-sample boundary forward through time, producing multiple
independent out-of-sample evaluations across different regime
combinations — a materially stronger test of generalization, and directly
addresses the Controller's explicit preference for testing "multiple
market regimes," not just one arbitrary split.

**Design:**

1. **Identify the full available historical window** once data is
   acquired (bounded above by data-source depth — recall Alpaca's own
   ceiling is ~7 years SIP / ~5 years IEX, so "multi-year" here is itself
   bounded by data availability, not arbitrarily chosen).
2. **Partition into rolling windows**, e.g. (illustrative structure, not a
   proposed final value): a training window of length T, followed by a
   validation window of length V, then step forward by some interval and
   repeat — producing several overlapping-but-distinct train/validate
   pairs across the available history.
3. **Reserve a final, untouched holdout block** at the *end* of the full
   available window — this block is never used in any walk-forward
   iteration, never seen during parameter selection, and is evaluated
   exactly once, after a parameter set has already been chosen from the
   walk-forward process. This is the true out-of-sample test (§12
   discusses why this separation matters for overfitting protection).
4. **Ensure the training portion of at least one walk-forward window, and
   ideally the reserved holdout block, contains a genuine high-volatility
   or drawdown episode** — not by hand-picking a specific crash to include
   (that would itself be a form of cherry-picking, addressed in §12), but
   by choosing the overall window length and step size such that, given
   how history actually unfolded, at least one such episode falls
   somewhere in the walk-forward sequence. If the available data (bounded
   by the ~5-7 year Alpaca ceiling) does not naturally contain a
   suf­ficiently severe drawdown episode, this is a **real limitation to
   disclose**, not a reason to synthesize one artificially.

### Exact period lengths — not specified here

Consistent with the Controller's instruction not to force numeric
decisions prematurely: the exact T (training length), V (validation
length), and step size are themselves calibratable choices, not free —
they should be set with reference to (a) how much history is actually
available (a hard ceiling from the data source), (b) ensuring each window
is long enough to contain a statistically meaningful number of Ladder/
Floor trigger events for the strategy simulation to produce non-noisy
metrics, and (c) leaving a genuinely untouched final holdout block. These
are engineering/statistical judgment calls to make once real data
characteristics are known, not something to freeze in a planning document
before any data exists.

---

## 6. Regime segmentation

### Why not fixed thresholds yet

Per Controller instruction, no fixed numeric regime boundary (e.g. "VIX
above X") is proposed here. Instead: the *method* for defining regime
boundaries.

### Candidate methods, compared

- **Percentile-based** (e.g., "today's realized volatility is in the top
  decile of the trailing N-year history"): self-adjusts as long-run market
  volatility drifts over multi-year horizons; doesn't require picking an
  absolute level that might become stale. This was already the
  third-pass design's recommendation for the regime parameter's *form*
  (§3.4 of `universe-selection-analysis.md`) and is reaffirmed here as the
  leading candidate.
- **Volatility-based** (realized volatility over a rolling window on a
  broad-market proxy): straightforward to compute from the data in §1.8;
  a natural complement to, not a replacement for, the percentile framing
  above (i.e., "percentile of realized volatility," not volatility in
  isolation as an absolute number).
- **Drawdown-based** (peak-to-trough decline on a broad-market proxy over
  a rolling window): captures a different dimension than volatility — a
  slow, low-volatility grind downward can be a meaningfully different
  regime from a sharp, high-volatility whipsaw, even at similar
  volatility readings; drawdown-based classification catches the former,
  which pure volatility might miss.
- **Composite** (combining volatility percentile and drawdown percentile
  into a small number of regime buckets): most defensible **if** it can be
  validated to actually separate meaningfully different execution/
  volatility environments (see below) rather than just adding complexity
  for its own sake — this is the currently favored direction, but its
  exact combination rule is left open pending the validation step, not
  frozen here.

### How to validate the regime definition without circularity

**The trap:** if we define "HIGH VOL" as "days where our chosen HIGH VOL
threshold correctly predicted worse execution/strategy outcomes," we've
used the outcome to define the regime and then "confirmed" the regime
predicts the outcome — pure circularity.

**The way to avoid it:**

1. Define the regime-classification rule using **only** the broad-market
   reference series (§1.8) — never using our own strategy's simulated
   outcomes, our own candidate-symbol data, or anything downstream of the
   universe-selection pipeline.
2. **Independently** verify that days classified into different regime
   buckets show measurably different *execution-relevant characteristics*
   (e.g., materially different median spreads, materially different
   single-session price-range magnitudes) using data that is not the
   strategy-outcome data itself. This checks that the regime label is
   picking up something real about market conditions, not something
   invented to match our own strategy's behavior.
3. Only **after** the regime definition is validated this way should it be
   used to segment the strategy-mechanics calibration results (§8, §9) —
   at that point, using it to explain *why* outcomes differ by regime is
   legitimate, because the regime label was fixed independently beforehand.

---

## 7. Calibrating each parameter

For each: (A) data needed, (B) hypothesis under test, (C) candidate forms
to test, (D) metrics measured, (E) failure mode prevented, (F) evidence
that would justify approval, (G) evidence that would cause rejection. **No
final numeric value is proposed for any parameter below.**

### 7.1 Liquidity floor

- **A.** Daily volume/dollar-volume history (§1.1); our own fixed order
  sizes (10/10/20 shares) as a reference, not a variable.
- **B.** *Hypothesis:* below some liquidity level (expressed relative to
  our own order size, not an absolute convention), fill quality and
  price-impact risk degrade materially for a 10-40 share order.
- **C.** Candidate forms: an absolute ADV$ constant (the rejected,
  convention-based baseline, kept only as a comparison reference, not a
  serious candidate); a liquidity-adjusted form expressed as our
  worst-case order value divided by a recent representative volume figure
  (the recommended direction from the third pass).
- **D.** Execution-quality proxy at simulated fill times; wasted-slot rate;
  candidate-count sensitivity to the floor's level.
- **E.** Prevents: market-impact-driven fill degradation; corrupting the
  frozen entry reference (D-0001) via a bad initial fill on a thin name.
- **F.** Evidence for approval: stable execution-quality-proxy outcomes
  across the liquidity-adjusted floor's plausible range, validated
  out-of-sample and regime-segmented, per §13's checklist.
- **G.** Evidence for rejection: execution-quality proxy remains poor even
  well above a candidate floor level (suggesting liquidity isn't actually
  the binding constraint, and the parameter's form itself is wrong); or
  results are unstable/contradictory across regimes.

### 7.2 Spread cap

- **A.** Historical quotes (preferred, §1.7) or the labeled range proxy;
  cross-referenced against the D-0007 ±0.5% band.
- **B.** *Hypothesis:* symbols whose typical spread consumes a large
  fraction of the D-0007 tolerance produce measurably worse simulated
  fill outcomes (i.e., the frozen entry reference ends up materially off
  from what a tighter-spread symbol would have produced).
- **C.** Candidate forms: flat bps constant (comparison baseline only);
  percentile-relative within the eligible pool, cross-checked against the
  D-0007-band-consumption framing (recommended direction).
- **D.** Execution-quality proxy; slippage proxy (§9); distribution of how
  much of the D-0007 band a given spread level actually consumed in
  simulation.
- **E.** Prevents: a materially mispriced frozen entry reference that
  silently shifts every Ladder/Floor level away from what was approved.
- **F.** Evidence for approval: same structure as §7.1 — stable,
  regime-segmented, out-of-sample-validated outcomes.
- **G.** Evidence for rejection: spread level shows no measurable
  relationship to simulated fill-quality outcomes (suggesting the proxy
  itself, or the whole filter, isn't capturing the intended risk); or true
  quotes vs. range-proxy give materially different conclusions (a sign the
  proxy is unreliable and shouldn't be relied on for final approval, per
  the Controller's explicit instruction not to pretend the two are
  equivalent).

### 7.3 ATR% band (both bounds)

- **A.** Daily bars for ATR computation; the frozen strategy's own
  Ladder1/Ladder2/Floor levels (0%, −5%, −8%, −10%) as fixed reference
  points, never varied.
- **B.** *Hypothesis (lower bound):* below some ATR% level, Ladder 1
  essentially never fires within a trade's plausible holding period.
  *Hypothesis (upper bound):* above some ATR% level (geometrically related
  to the fixed 2-point Ladder2-to-Floor gap), Floor fires without Ladder1
  or Ladder2 having had a realistic chance to fire first, in a materially
  elevated fraction of simulated trades.
- **C.** Candidate forms: a flat percentage range (comparison baseline);
  a form derived from the Ladder2-to-Floor gap as a multiple/fraction
  (recommended direction, per the third-pass geometric reasoning).
- **D.** Signal-type distribution (Ladder1-only / Ladder1+2 / Floor-hit);
  wasted-slot rate (lower-bound-related); no-warning-Floor rate
  (upper-bound-related, this is the primary metric for this parameter).
- **E.** Prevents: wasted candidate slots (too-low ATR%); trades that skip
  the Controller's intended step-by-step ladder review entirely
  (too-high ATR%).
- **F.** Evidence for approval: a clearly identifiable band where both the
  wasted-slot rate and the no-warning-Floor rate are simultaneously low,
  stable across regimes and out-of-sample.
- **G.** Evidence for rejection: no band exists where both rates are
  acceptably low simultaneously (suggesting the fixed 5/8/10 spacing
  itself may not suit a meaningful fraction of the real symbol
  population — an important finding to report to the Controller even
  though changing the spacing itself is out of scope for this
  calibration, per §8's constraint).

### 7.4 Regime classification thresholds and per-regime adjustments

- **A.** Broad-market volatility/drawdown series (§1.8); the independent
  validation method from §6.
- **B.** *Hypothesis:* days classified into a higher-stress regime bucket
  show measurably worse execution-quality and higher-ATR conditions for
  candidate symbols, justifying tighter thresholds in that bucket.
- **C.** Candidate forms: percentile-based, drawdown-based,
  volatility-based, composite (§6) — the classification method itself is
  a parameter under test here, not just its numeric cutoff.
- **D.** Regime-segmented versions of every metric in §9; specifically
  whether tightened-regime thresholds actually reduce no-warning-Floor
  and poor-execution-proxy rates during high-stress periods without
  needlessly emptying the universe (directly testing the "CRASH tightens,
  doesn't empty" principle's practical effect).
- **E.** Prevents: admitting candidates in stress conditions whose
  execution/volatility profile would have failed under calmer-regime
  thresholds; also prevents the previously-rejected failure mode of
  needlessly excluding legitimate opportunities during drawdowns.
- **F.** Evidence for approval: the regime classification is independently
  validated (§6) AND regime-adjusted thresholds demonstrably improve the
  relevant metrics relative to using flat, non-regime-adjusted thresholds
  throughout, across multiple walk-forward windows.
- **G.** Evidence for rejection: regime-adjusted thresholds show no
  measurable improvement over flat thresholds (suggesting the added
  complexity of regime-adaptation isn't earning its keep), or the regime
  classification itself fails the independent-validation check in §6.

### 7.5 Ranking / secondary-score composition

- **A.** Daily bars for computing liquidity-beyond-floor, RVOL, and trend
  context, among filter-survivors only.
- **B.** *Hypothesis:* among symbols that already pass the hard filters
  (§7.1–7.3), higher-ranked symbols (by the secondary score) produce
  measurably better realized simulated outcomes than lower-ranked ones.
- **C.** Candidate forms: percentile-based composite among survivors
  (recommended direction from the third pass); alternative — dropping
  trend entirely and using only liquidity-beyond-floor + RVOL, per the
  second-pass finding that a hard trend gate is in tension with the
  strategy's own drawdown-buying purpose (worth testing whether trend adds
  value even as a *soft* signal, or should be dropped altogether).
- **D.** Correlation between rank position and realized simulated outcome
  quality (a form of "does the ranking actually rank well," not just "does
  it produce *a* ranking").
- **E.** Prevents: consistently picking materially weaker candidates over
  stronger ones purely due to ranking order when survivor count exceeds N.
- **F.** Evidence for approval: a measurable, stable, out-of-sample-
  validated positive relationship between rank and realized outcome
  quality.
- **G.** Evidence for rejection: no measurable relationship (ranking is
  effectively noise among filter-survivors, in which case a simpler
  tie-break rule, e.g. pure liquidity, might be preferable to a composite
  score that adds complexity without benefit).

### 7.6 Sector cap

- **A.** Sector classification (§1.4); simulated concurrent-position
  scenarios across the calibration window.
- **B.** *Hypothesis:* uncapped sector concentration produces materially
  worse aggregate-portfolio drawdown scenarios (simulated) than a capped
  alternative, during correlated sector-wide adverse moves.
- **C.** Candidate forms: this parameter is explicitly flagged (third pass,
  reaffirmed here) as **structurally downstream of the still-TBD
  portfolio-level risk limits** — candidate forms should be expressed
  relative to whatever the eventual portfolio risk limit specifies (e.g.
  "max same-sector simultaneous exposure as a fraction of the portfolio
  limit"), not chosen independently.
- **D.** Simulated aggregate drawdown under sector-concentrated vs.
  sector-capped candidate admission, across regime-segmented windows.
- **E.** Prevents: correlated, sector-wide drawdown risk across
  simultaneously open trades compounding beyond what any single trade's
  own Ladder/Floor math was designed to bound.
- **F.** Evidence for approval: **should wait until the portfolio-level
  risk limit decision exists** (§ Task 2.7 of the second-pass document,
  reaffirmed) — evidence alone from this calibration cannot fully justify
  a value without that reference point.
- **G.** Evidence for rejection: N/A until the sequencing decision is made.

### 7.7 Correlation threshold

- **A.** Historical return series for pairwise correlation computation
  (derived from §1.1); same portfolio-risk-limit dependency as §7.6.
- **B.** *Hypothesis:* admitting highly-correlated symbols simultaneously
  produces concentration risk that sector classification alone misses
  (theme-driven clustering across different sectors).
- **C.** Candidate forms: percentile-relative within the day's candidate
  pool (recommended direction, third pass) vs. a flat correlation
  coefficient (comparison baseline).
- **D.** Same simulated-concentration-outcome metrics as §7.6, specifically
  isolating cases where sector caps alone would have passed a
  problematically-correlated pair.
- **E.** Prevents: theme-driven clustering risk not caught by sector
  classification alone.
- **F. / G.** Same sequencing caveat as §7.6 — full approval evidence
  should wait on the portfolio-risk-limit decision; calibration can still
  measure whether correlation-based admission adds value *beyond* sector
  capping alone, which is useful evidence to have ready once that
  sequencing decision is made.

### 7.8 Top-N

- **A.** Distribution of how many symbols survive stages A–G on a typical
  day across the calibration window (a market-data question); separately,
  **operational data on Controller review throughput** under the existing
  D-0007 5-minute window (not a market-data question — see below).
- **B.** *Hypothesis:* there exists a count above which simultaneous
  triggers across too many concurrently-eligible symbols would exceed
  realistic human review capacity within the D-0007 window.
- **C.** Candidate forms: a flat constant (comparison baseline); a
  regime-dependent ceiling (recommended direction, tighter during
  higher-trigger-frequency regimes).
- **D.** Distribution of simultaneous-trigger counts per historical day,
  segmented by regime; this is measurable from the market-data
  calibration alone, but the "how many can a human safely review" half of
  this parameter is **not** answerable from historical market data at
  all.
- **E.** Prevents: approval fatigue / alert overload (too large); strategy
  under-utilization (too small).
- **F.** Evidence for approval: **market-data evidence alone is
  insufficient for this parameter** — it must be combined with actual
  observed Controller review throughput once the system is running in
  some capacity (even a shadow/dry-run mode, per §15's phase boundaries),
  which does not exist yet. This is explicitly flagged as a parameter that
  **cannot be fully calibrated from historical market data alone**,
  unlike the others in this catalog.
- **G.** N/A pending the operational data described above.

### 7.9 Warm-up period and staleness threshold

- **A.** Statistical behavior of rolling ATR/ADV$/SMA calculations as a
  function of how many bars of history feed them (a computational/
  statistical question, not primarily a strategy-outcome question).
- **B.** *Hypothesis:* below some minimum history length, these rolling
  calculations are too noisy/unstable to be trusted as eligibility inputs.
- **C.** Candidate forms: likely a fixed engineering constant (as flagged
  in the third pass) rather than something needing the full walk-forward
  backtest process.
- **D.** Statistical stability of the rolling calculations (e.g.,
  variance of the computed ATR%/ADV$ as a function of window length,
  independent of strategy outcomes).
- **E.** Prevents: admitting a symbol on statistically unreliable,
  insufficient history.
- **F.** Evidence for approval: a statistical-stability analysis (simpler
  and faster than the full §5–§13 process) showing the rolling
  calculations stabilize by a given history length — this can reasonably
  proceed somewhat independently of the full multi-regime backtest,
  consistent with the third pass's flag that this parameter category may
  not need the complete process.
- **G.** Evidence for rejection: no stable window length identified within
  a practically reasonable range (would suggest the underlying rolling
  calculations themselves need reconsideration, a more significant
  finding).

---

## 8. Strategy-mechanics calibration — preserving the approved strategy exactly

**Explicit constraint, restated and honored throughout this document:** the
Ladder, Floor, trigger logic, frozen entry reference, fixed 5/8/10 spacing,
D-0011 debounce state machine, and D-0007 approval/re-check are **frozen
inputs** to this calibration. Nothing in this document proposes, implies,
or tests any change to that logic. The calibration engine's job is to
**simulate this exact, unmodified logic** against different candidate
universes and measure the results — never to redesign the logic itself to
produce better-looking numbers.

### How universe parameters should be evaluated against the frozen mechanics

For each candidate universe-selection configuration, run the **identical,
unmodified** strategy simulation forward from each historical decision day,
and specifically measure, per the Controller's explicit list:

- **How often candidates produce usable Ladder opportunities** — the
  fraction of admitted candidates whose subsequent price path actually
  reaches Ladder 1 (−5%) at some point during a plausible holding window;
  this directly tests the ATR% lower bound (§7.3).
- **How often price movement jumps across intended levels** — specifically,
  cases where a single day's move crosses from above Ladder 1 to below
  Ladder 2, or from above Ladder 2 to below Floor, in one session — this
  measures whether the D-0011 debounce and D-0007 approval workflow are
  actually getting a realistic chance to operate as designed, or whether
  the candidate's volatility profile is structurally too fast for the
  human-in-the-loop process the approved policy requires.
- **How often Floor conditions occur without adequate warning** — the
  no-warning-Floor rate already defined in the third-pass document,
  restated here as a first-class strategy-mechanics metric, not just a
  universe-quality one.
- **Whether ATR regimes make the fixed ladder spacing unsuitable for
  certain candidates** — this is explicitly a **finding to surface to the
  Controller**, not a trigger for redesigning the spacing within this
  calibration process. If a meaningful fraction of otherwise-liquid,
  otherwise-reasonable candidates are structurally unsuited to the fixed
  5/8/10 spacing (too fast or too slow relative to it), that is valuable
  evidence for a **future, separate, Controller-approved decision** about
  whether the spacing itself should ever be revisited — never something
  this calibration process decides on its own.
- **Whether spread materially damages expected execution** — measured via
  the slippage proxy (§9), applied specifically at the simulated Ladder
  fill points (initial entry, Ladder 1, Ladder 2), since those are the
  moments D-0001's frozen-reference risk is most acute.
- **Whether liquidity is adequate relative to our actual order sizes** —
  measured by comparing our fixed 10/10/20 share order sizes against each
  candidate's simulated volume at the exact historical fill point, not
  against a generic daily average taken out of context.

### The critical discipline this section exists to state explicitly

**Do not change the strategy to make the universe selector look better.**
If calibration results show the fixed strategy performs poorly even with
an excellent universe-selection configuration, the correct conclusion is
either "the universe-selection configuration needs further work" or "this
is a genuine, disclosed limitation of the current approved strategy applied
to real market conditions" — never "let's quietly adjust the Ladder/Floor
spacing or the D-0007 tolerance to produce better-looking calibration
numbers." Any such change would require its own separate, explicit
Controller decision and its own change-control process (CLAUDE.md §3,
§9) — it is categorically out of scope for a universe-calibration
exercise.

---

## 9. Metrics — full catalog, categorized

### Universe-quality metrics (does the pipeline pick good candidates, independent of strategy outcome)

- Candidate count (pre-ranking, post-filter) — daily distribution
- Eligible-symbol count — how many symbols pass stage A/B before any
  strategy-specific filtering
- Universe churn / turnover — day-over-day set difference
- Stability of selected symbols — overlap across consecutive days/weeks
- Concentration — sector distribution over time
- Correlation concentration — pairwise correlation distribution among
  admitted symbols over time

### Strategy-outcome metrics (what happens when the frozen strategy runs against the selected candidates)

- Signal-type distribution (Ladder1-only / Ladder1+2 / Floor-hit /
  Trailing-activation) — see §8
- Wasted-slot rate — candidates admitted that never triggered anything
- No-warning-Floor rate — see §8
- Number of proposals generated (simulated)
- Number of opportunities missed — candidates excluded by a configuration
  that, in hindsight, would have produced a clean, favorable outcome under
  the frozen strategy

### Execution-quality metrics (how good would the actual fills have been)

- Execution-quality proxy — from §1.7's spread/quotes data (or the labeled
  range-proxy fallback)
- Spread impact proxy — spread as a fraction of the D-0007 ±0.5% band,
  measured specifically at simulated fill points
- Slippage proxy — difference between the simulated intended fill
  reference and what the historical quote/trade data suggests was
  realistically achievable

### Risk metrics

- Concentration risk (aggregated view of the sector/correlation metrics
  above, specifically during simulated multi-position scenarios)
- Regime-specific performance — every metric above, reported separately
  per regime bucket (§6), never only in aggregate
- Opportunity coverage — the fraction of hindsight-identifiable good
  outcomes (under the frozen strategy) that a given configuration actually
  admitted into its candidate pool, vs. a broader reference set (§10)

### Explicit distinction maintained throughout

A configuration that scores well on universe-quality metrics but poorly on
strategy-outcome metrics is a different (and more important) finding than
one that scores well on both — the strategy-outcome and risk metrics are
what actually matter for the trading system; universe-quality metrics are
diagnostic/explanatory, not the target being optimized.

---

## 10. Control experiment — baseline/control universe design

### Requirement, restated

The baseline must **not** be TSLA. TSLA is test-only and must never be a
production or research fallback universe, including as a calibration
baseline — using it here would be exactly the kind of quiet reintroduction
the Controller has repeatedly and explicitly prohibited.

### What a valid baseline/control should be

**Recommended: an unfiltered-or-minimally-filtered broad reference
universe**, distinct from the dynamically-selected D-0026 pipeline output,
constructed from the *same* point-in-time-correct data (§4) so the
comparison is apples-to-apples on data quality. Candidate control designs,
to be chosen from (not decided here):

1. **The full stage-A/B survivor set (tradability + data-quality only, no
   execution/strategy/regime/ranking filtering applied)** — this isolates
   exactly what stages C through H are contributing, since the control and
   the treatment (full pipeline) share the same starting population and
   differ only in the filtering/ranking stages under test.
2. **A broad, static reference index (e.g. a large representative
   market-cap-weighted universe) held constant across the whole
   calibration window** — this tests whether *any* dynamic selection beats
   a simple, unchanging broad basket, a different and complementary
   question from option 1.
3. **A random-N-symbols-per-day control**, redrawn daily from the
   stage-A/B survivor set — this isolates whether the *ranking/
   concentration* logic (stages F/G/H specifically) adds value beyond
   simply picking *some* eligible names at random each day.

**Recommendation: use options 1 and 3 together** as complementary
controls — option 1 tests the marginal value of the execution/strategy/
regime filtering stages (C-E) as a group; option 3 tests the marginal
value of the ranking/concentration/selection stages (F-H) specifically, on
top of whatever option 1 already filtered. Option 2 is a reasonable
addition if a suitable static broad-reference index is readily available,
but is not treated as strictly necessary given 1 and 3 already isolate the
two most important architectural questions.

### Purpose, restated

To determine, with an actual comparison rather than an assumption, whether
the dynamic, multi-stage D-0026 mechanism **measurably improves** candidate
quality and strategy outcomes relative to simpler alternatives — including
the possibility that it does not, in which case that finding itself is
valuable and should be reported honestly rather than the calibration
process being treated as a foregone conclusion that dynamic selection must
help.

---

## 11. Parameter sensitivity testing

### General design

For every parameter in §7, test at minimum three points: a value/form
below the candidate baseline, the candidate baseline itself, and a value/
form above it — using whatever neighboring-configuration concept is
appropriate to that parameter's actual form (a numeric constant, a
percentile cutoff, a formula constant, or — for the structural/categorical
choices like ranking composition or regime-classification method — a
reasonable alternative configuration rather than a literal "higher/lower"
neighbor).

### Per-parameter sensitivity design

- **Liquidity floor, spread cap, ATR% band:** test the candidate
  threshold, and two neighboring thresholds (tighter and looser), on the
  *same* walk-forward windows; a parameter is robust only if the key
  outcome metrics (§9) change gradually and predictably across these three
  points, not abruptly or non-monotonically.
- **Regime-classification thresholds:** test neighboring percentile
  cutoffs (e.g., a slightly more or less inclusive definition of "HIGH
  VOL") and confirm the regime-segmented findings in §7.4 remain
  qualitatively stable.
- **Ranking composition:** test the candidate secondary-score composition
  against at least one simplified alternative (e.g., dropping one signal)
  and one alternative emphasis — a ranking approach is robust if its
  relative ordering of realistic candidates doesn't change dramatically
  under small compositional changes.
- **Sector cap / correlation threshold:** test neighboring cap
  levels/correlation cutoffs once the portfolio-risk-limit sequencing
  issue (§7.6, §7.7) is resolved and a real candidate value exists to test
  around.
- **Top-N:** test neighboring counts against both the market-data-derived
  simultaneous-trigger distribution (§7.8) and, once available, the
  operational Controller-throughput data.
- **Warm-up/staleness:** test neighboring window lengths against the
  statistical-stability metric from §7.9.

### Robustness criterion

A parameter is considered **robust** only if reasonable neighboring
configurations produce **reasonably stable** outcomes on the key metrics —
not identical, but not qualitatively different conclusions. A parameter
that only "works" at one narrow, precisely-tuned value is treated as
**evidence against** approving that specific value, and as a signal to
look for a more robust form (possibly a different formula structure
entirely, not just a different constant) rather than freezing a fragile
number.

---

## 12. Overfitting protection

### Explicit safeguards

- **Reserved, untouched final holdout block** (§5) — never used in any
  walk-forward iteration, never inspected during parameter selection,
  evaluated exactly once after a parameter set is chosen.
- **Walk-forward validation across multiple windows** (§5), not a single
  static split — reduces the chance a parameter merely "got lucky" fitting
  one specific historical episode.
- **Regime-segmented reporting** (§6, §9) — prevents a parameter's poor
  performance in one regime from being hidden inside a favorable aggregate
  number.
- **Sensitivity testing** (§11) — a parameter that only works at one
  precise value is itself flagged as likely overfit, independent of
  whether it "passes" on the primary metrics.
- **Prefer fewer, structurally-motivated parameters over many freely-fit
  constants** (carried from the second pass) — a formula tied to strategy
  geometry (e.g., the ATR% upper bound as a function of the Ladder2-to-
  Floor gap) has an economic rationale independent of the specific
  historical sample, which is inherently more overfitting-resistant than
  an arbitrarily-fit standalone constant.

### Guarding against data snooping and multiple-testing problems

- **Pre-register the metric set** (§9) and the acceptance checklist (§13)
  **before** running calibration on any given walk-forward window — do not
  invent new metrics after seeing results that happen to favor a
  particular configuration.
- **Limit the number of candidate configurations tested per parameter** to
  a small, pre-specified set (e.g., the "below/baseline/above" structure
  from §11) rather than an open-ended search across many possible values —
  an unconstrained search across many configurations, evaluated against
  the same data, is itself a multiple-testing problem that inflates the
  chance of finding a spuriously good-looking configuration by chance
  alone.
- **Do not cherry-pick regimes**: report results for **every** regime
  bucket present in the data, not only the ones where a candidate
  configuration happens to look good.
- **Do not cherry-pick symbols**: the candidate universe for calibration
  must be the full point-in-time-correct eligible population (§4), not a
  hand-selected subset chosen because it's familiar or because early
  inspection suggested it would produce favorable results.
- **Do not choose parameters after seeing out-of-sample/holdout results**:
  this is the single most important rule in this section. The holdout
  block from §5 is evaluated **exactly once**, after every parameter
  choice has already been locked in from the walk-forward process alone.
  If holdout results are unfavorable, the correct response is to report
  that honestly (§14) and revisit the walk-forward methodology or the
  parameter's form — **never** to peek at the holdout, adjust the
  parameter to fit it better, and re-run. Doing so would convert the
  holdout into just another in-sample period, defeating its entire
  purpose.

### Which dataset may influence parameter selection, and which must remain untouched

- **May influence selection:** all walk-forward training/validation
  windows (§5).
- **Must remain untouched until final evaluation:** the single reserved
  holdout block (§5) — no parameter, form, or configuration decision may
  be made with reference to this block's results, at any point before the
  final, single evaluation pass.

---

## 13. Acceptance criteria — PROPOSED → APPROVED

### Expanded rigorous checklist (building on the five-point structure already recorded in the prior pass)

A specific parameter value or formula (not the general mechanism — that's
§7A/§7B territory) may be proposed for Controller approval only when **all**
of the following hold:

1. **Calibration actually run** — using real, acquired historical data
   (§1–§4), not estimated or assumed results. No fabricated numbers, per
   the Controller's explicit and repeated instruction.
2. **Point-in-time / survivorship-bias tier satisfied** — at minimum Tier 1
   from §4's tiered fidelity structure (point-in-time universe history +
   corporate-action adjustment). A calibration missing Tier 1 cannot
   support a final APPROVED status, only a labeled, lower-confidence
   directional finding.
3. **Walk-forward results reported across every available regime bucket**
   (§6, §9) — not aggregate-only.
4. **Final holdout evaluated exactly once**, after parameter selection was
   already locked in from walk-forward results alone (§12) — and the
   holdout result is reported regardless of whether it's favorable.
5. **Sensitivity-stability confirmed** (§11) — the parameter sits on a
   reasonably stable region of the outcome metrics under neighboring
   configurations, not a narrow, fragile optimum.
6. **Control-experiment comparison included** (§10) — evidence that the
   parameter's configuration measurably outperforms, or is at minimum
   defensibly comparable to, the non-TSLA baseline controls, not just an
   isolated "this number looks fine" analysis.
7. **Strategy-mechanics constraint honored** (§8) — the strategy logic
   used in every step of the calibration is confirmed identical, unmodified
   Ladder/Floor/Trigger/D-0007/D-0011 logic; any finding that the fixed
   spacing seems unsuitable for some candidates is reported as a
   *separate* future decision item, not resolved by tweaking the strategy
   within this process.
8. **Evidence presented for review, not just a conclusion** — per CLAUDE.md
   §4, the actual metrics, regime breakdowns, sensitivity results, and
   holdout outcome are shown to the Controller, with FACT / ASSUMPTION /
   HYPOTHESIS / RECOMMENDATION clearly labeled, so the Controller can
   independently judge the evidence rather than take a summary conclusion
   on faith.

### Where a universal numeric threshold can't be defined for a metric

Some metrics in §9 (e.g. "opportunity coverage," "wasted-slot rate") don't
have an obvious universal "good" cutoff independent of context — a
different domain would reasonably expect a different acceptable range.
**For these, evaluation should be relative and comparative** (per §10's
control experiment and §11's sensitivity testing), not against an
arbitrary invented target: does the candidate configuration perform
**measurably and stably better than the non-TSLA baseline controls**,
across regimes and out-of-sample? That comparative, evidence-based
standard is used in place of an invented absolute target, consistent with
the Controller's explicit instruction not to invent arbitrary performance
targets without a defensible reason.

---

## 14. Failure / insufficient-data policy

**The answer is never "just choose a reasonable number."** Per parameter
category, the specific fallback:

| Failure condition | Policy |
|---|---|
| Historical data incomplete (gaps in bars/volume) | Exclude affected symbol-days from calibration; if gaps are widespread enough to bias results, the affected metric/period is reported as **insufficient evidence**, not patched with an assumed value. |
| Spread data unavailable (no SIP access, IEX too sparse) | Fall back to the explicitly-labeled range proxy (§3); any resulting parameter approval is capped at the confidence level appropriate to a proxy-based measurement, and should be flagged as pending re-validation once true spread data becomes available. |
| Sector history incomplete | Concentration-parameter findings (§7.6, §7.7) are labeled as using a simplification (today's classification applied retroactively); such findings alone are **not sufficient** to move those two parameters to APPROVED — combined with the portfolio-risk-limit sequencing issue, they remain PROPOSED regardless. |
| Survivorship-free data unavailable | The entire calibration is labeled **survivorship-biased**; results are usable only as directional, lower-confidence evidence (§4's Tier 1 requirement) — **no parameter may move to APPROVED status on survivorship-biased evidence alone.** |
| Sample too small (too few historical trigger events for a given parameter/regime combination) | Report the metric as statistically inconclusive for that regime; do not extrapolate from a small sample to a confident conclusion; widen the data-collection window or wait, rather than approve on thin evidence. |
| A regime has too few observations | That regime's segment of the results is reported as **insufficient evidence** specifically for that regime, while other, well-observed regimes may still support conclusions — a parameter is only approved for the specific conditions the evidence actually covers, not extrapolated to under-observed regimes. |
| Results are contradictory (e.g., walk-forward windows disagree materially with each other, or with the holdout) | The parameter remains PROPOSED; the contradiction itself is reported to the Controller as a finding, along with hypotheses for why (regime-dependence not yet captured; parameter form itself may be wrong) — not resolved by picking whichever result looks more favorable. |
| Parameters are unstable under sensitivity testing (§11) | Remains PROPOSED; treated as evidence the candidate form itself may need rethinking, not just re-tuning the constant. |

**In every one of these cases, the system remains PROPOSED/TBD.** This
table exists specifically so that "insufficient evidence" has a defined,
consistent, pre-committed response, rather than being resolved ad hoc under
pressure to produce a number.

---

## 15. Implementation boundary — three phases

### PHASE 1 — Planning / design only

**This document, and everything produced in the three D-0026 analysis
passes before it, is entirely within Phase 1.** Phase 1 includes:
- Design documents (this one, and the prior three passes).
- Specifying data requirements, methodology, metrics, acceptance criteria.
- Repository/documentation-only inspection (already performed, confirmed
  no data/code exists).
- **No** data acquisition. **No** code. **No** dependencies. **No** live
  routine, scheduler, or cron changes.

**We remain in Phase 1 now**, per explicit Controller instruction.

### PHASE 2 — Historical-data collection + offline calibration implementation

Would include:
- Actually acquiring the datasets specified in §1 (Alpaca API calls for
  bars/quotes/corporate-actions; sourcing the external point-in-time
  universe-history and sector-classification datasets; resolving the SIP
  subscription cost decision for true spread data).
- Building the offline backtest/simulation engine that runs the frozen
  strategy logic (§8) against historical data — this is **code**, but
  explicitly **offline, not connected to any live account, live order
  submission, or live scheduler.**
- Running the walk-forward calibration process (§5, §6) and producing the
  regime-segmented, sensitivity-tested, control-compared evidence required
  by §13.
- **Not included in Phase 2:** anything touching the live Alpaca paper
  trading account for order submission, the live scheduler/routines, or
  production credentials beyond what's needed for read-only historical
  data pulls.

**Requires explicit Controller approval to begin** — specifically:
approval to (a) acquire the specific external datasets identified as gaps
in §1/§2 (including any associated cost, e.g. a SIP subscription), and
(b) begin writing the offline calibration codebase. Per CLAUDE.md §3's
Research-first / Approval phase, this is presented as a plan, not started
unilaterally.

### PHASE 3 — Live D-0026 implementation

Would include:
- Wiring the (by-then Phase-2-validated) universe-selection pipeline into
  the live Python scheduler (D-0023) to actually produce daily
  `ApprovedUniverseSnapshot` objects for the live strategy engine.
- Connecting this to the live (paper) Alpaca account and the existing
  approval workflow (D-0025).
- Any live routine, cron, or scheduler changes this requires.

**Requires explicit Controller approval to begin, separately from Phase
2** — specifically: approval of the **specific calibrated parameter
values/formulas** that emerged from Phase 2's evidence (per §13's
acceptance checklist), and an explicit "APPLY THE CHANGES" instruction
consistent with how every other live-system change in this project has
been gated so far.

### What requires Controller approval before moving between phases

- **Phase 1 → Phase 2:** approval to acquire specific external datasets
  (with any associated cost disclosed, e.g. SIP subscription) and to begin
  writing offline calibration code.
- **Phase 2 → Phase 3:** approval of specific calibrated parameter values/
  formulas, following the full §13 acceptance checklist, plus the
  project's standing "APPLY THE CHANGES" gate for any live-system change.

**We remain in Phase 1.** Nothing in this document authorizes moving to
Phase 2.

---

## 16. Final recommendation

### 1. Is the current D-0026 architecture ready to approve?

**The principles and pipeline shape (§7A/§7B of
`universe-selection-analysis.md`) — yes, as direction.** They are
reasoning-based, strategy-derived, and don't depend on data that doesn't
exist yet. This document does not change that assessment; if anything, the
data-source research performed here (confirming real gaps in Alpaca's
sector/point-in-time coverage, and a better-than-assumed spread-data
picture) reinforces that the architectural separation (universe subsystem
↔ `ApprovedUniverseSnapshot` ↔ symbol-agnostic engine) is sound
independent of exactly how the parameters inside it end up calibrated.

### 2. Are any numeric parameters ready to approve?

**No.** None. This was true before this document and remains true after
it — if anything, more clearly justified now that the specific data gaps
(§1.3, §1.4) and the true-quotes-vs-proxy nuance (§1.7, §3) are documented
with evidence rather than assumed.

### 3. Should we authorize historical-data collection?

**This is a real decision for the Controller, not decided here.** If
authorized, it is a **Phase 1 → Phase 2 transition** (§15) and should
specifically address: which external datasets to source for §1.3 (universe
history) and §1.4 (sector classification), and whether to authorize the
SIP data subscription cost for true spread data (§1.7) or accept the
range-proxy fallback from the start.

### 4. Should we authorize building the offline calibration engine?

**Also a Controller decision, bundled with #3 as part of the Phase 1 →
Phase 2 transition** (§15) — building the engine without the data to run it
against would be premature, so these two are naturally linked, though the
Controller could authorize data acquisition first and defer the engine
decision, or authorize both together.

### 5. What exact information/evidence must be produced before numeric approval?

The full §13 checklist: calibration actually run on real data; Tier-1
point-in-time/survivorship fidelity satisfied; regime-segmented walk-forward
results; a single untouched holdout evaluated once; sensitivity-stability
confirmed; a non-TSLA control-experiment comparison; strategy-mechanics
logic confirmed unmodified throughout; and the actual evidence (not a
summary conclusion) presented for Controller review with FACT/ASSUMPTION/
HYPOTHESIS/RECOMMENDATION labels.

### 6. What decisions still require explicit Controller approval?

- Whether to authorize Phase 2 (data acquisition + offline calibration
  engine) at all, and its scope (§15).
- Whether to authorize the SIP data subscription (cost decision, §1.7/§2).
- How to source the external point-in-time universe-history and sector-
  classification datasets (§1.3, §1.4) — specific vendor/source selection
  was not made in this document.
- The sequencing question for sector cap / correlation threshold relative
  to the still-TBD portfolio-level risk limits (carried from the prior
  pass, reaffirmed in §7.6/§7.7).
- Each individual parameter's eventual value/formula, once §13's evidence
  bar is met — explicitly not before.
- Eventually, the Phase 2 → Phase 3 transition itself, gated by the
  project's standing "APPLY THE CHANGES" rule.

---

## Decision table

| Decision | Current status | What evidence is needed | My approval required? |
|---|---|---|---|
| D-0026 principles (§7A of the third-pass mechanism doc) | Presented as safe to approve as direction | None beyond the reasoning already documented | Yes — direction-level approval, no numbers involved |
| D-0026 pipeline architecture (§7B, 9-stage design + `ApprovedUniverseSnapshot`) | Presented as safe to approve as direction | None beyond the reasoning already documented | Yes — direction-level approval, no numbers involved |
| Any specific numeric parameter (§7.1–7.9 of this document) | PROPOSED, form only, no value | Full §13 checklist, per-parameter | Yes — explicitly deferred until evidence exists |
| Sector cap / correlation threshold specifically | PROPOSED, structurally blocked | Portfolio-level risk-limit decision, THEN §13 evidence | Yes — two-step: sequencing decision first, then calibration |
| Top-N specifically | PROPOSED, partially blocked | Market-data evidence (§7.8) AND operational Controller-throughput data (doesn't exist yet) | Yes — cannot be fully resolved by historical data alone |
| Authorize historical-data collection (Phase 1→2) | Not authorized | N/A — this itself is the decision | Yes |
| Authorize SIP data subscription (cost) | Not authorized | N/A — this itself is the decision | Yes |
| Authorize offline calibration-engine build (Phase 1→2) | Not authorized | N/A — this itself is the decision | Yes |
| Source selection for point-in-time universe history (§1.3) | Gap identified, no source chosen | N/A — this itself is the decision | Yes |
| Source selection for sector classification (§1.4) | Gap identified, no source chosen | N/A — this itself is the decision | Yes |
| Phase 2 → Phase 3 transition (live implementation) | Not reached | Full §13 evidence for every parameter in scope, plus standing APPLY gate | Yes |

### What I recommend approving now vs. keeping TBD

**Recommend approving now (direction only, zero numbers):** the D-0026
principles and pipeline architecture (§7A/§7B of
`universe-selection-analysis.md`), reaffirmed and not altered by this
document.

**Recommend keeping TBD, explicitly, until evidence exists:** every single
numeric parameter, with no exceptions — including the ones this document's
research made *more* confident about directionally (e.g., the liquidity
floor being far lower than generic convention suggests) — because
direction-of-travel confidence is not the same as calibration evidence,
and the Controller has been consistent and correct in refusing to conflate
the two.

**Recommend as the next decision to make (not a numeric one):** whether to
authorize Phase 2 — historical-data collection and the offline calibration
engine — since without that authorization, no parameter can ever move past
PROPOSED, regardless of how much further design work is done in Phase 1.

---

No historical data was collected while producing this document beyond web
research to verify Alpaca's documented API capabilities (cited above). No
code was written. No live routine, scheduler, cron, or dependency was
touched. No approved trading-policy semantics were changed. D-0026 remains
PROPOSED / NOT APPROVED. TSLA was not used, referenced, or implied as any
form of fallback or baseline anywhere in this document.
