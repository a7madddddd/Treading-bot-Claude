# D-0026 — Root Data Source Recommendation (Phase 2) — REJECTED / SUPERSEDED

**⚠️ REJECTED by the Controller: no paid data provider or subscription of
any kind will be used.** The Norgate Data recommendation in this document
is **rejected outright**, along with every other paid option evaluated
here (Databento, Polygon/Massive, Tiingo). **Superseded by
`docs/trading/free-root-data-source-recommendation.md`**, which
recommends a free-only architecture (Stooq + SEC EDGAR + Nasdaq Trader +
Yahoo Finance + FRED). This document is retained for its provider-
comparison research value and its still-applicable canonical-dataset and
symbol-identity design patterns, but its **recommendation (§3, §14, §16
"Confirm the Norgate choice")** must not be acted on.

**Status: PROPOSED / NOT APPROVED.** Phase 2 (historical data collection +
offline calibration engine) was approved by the Controller for **research
and design work**. This document is a research/recommendation deliverable
within Phase 2 — it does not download data, write code, add dependencies,
or touch any live system. Phase 3 (live implementation) remains explicitly
NOT approved.

This document answers the Controller's explicit requirement: **one durable
root data foundation**, not symbol-by-symbol discovery, so a new symbol
appearing tomorrow is automatically covered by the existing data
architecture rather than triggering a new source search.

---

## 1. Defining the root data requirement

"Root data resource" means: a single primary provider whose coverage is
**broad enough by default** (the whole relevant US equity + ETF universe,
not a hand-picked symbol list) that adding a new symbol to our future
dynamic universe never requires evaluating a new data source — the symbol
either already exists in the root provider's universe (the common case) or
it doesn't (a rare, explicit exception to handle, not the default
workflow).

### What the root source should ideally provide

Restating the Controller's list, organized by how essential each item is to
that definition (not all 16 items carry equal weight):

**Core, non-negotiable for a root source:**
- Historical OHLCV + volume, broad universe, bulk-accessible
- Delisted/inactive securities (survivorship-bias mitigation — this is the
  single most differentiating requirement among candidate providers, per
  §2)
- Sufficient history for multi-regime calibration
- Programmatic/bulk access (not manual symbol-by-symbol retrieval)
- Documentation and demonstrated reliability

**Important, but reasonably delegable to a secondary source without
breaking the "one root" principle:**
- Corporate actions
- Sector/GICS (or equivalent) classification
- ETF classification and leveraged/inverse identification
- Point-in-time tradability/universe information
- Stable instrument identifiers

**Lower priority for the *root* source specifically (better suited to our
live broker, not a historical research provider):**
- Historical trades/quotes (bid-ask) — genuinely useful, but as
  established in the prior document, true consolidated-market spread data
  is a specialized, often separately-priced product even among providers
  that are otherwise strong on OHLCV; it is reasonable to treat this as a
  secondary concern rather than a root-source disqualifier.

### Confirming the premise: no single provider satisfies everything

**FACT, established by the research in §2:** none of the providers
researched combine (a) survivorship-bias-free delisted coverage, (b)
sector/GICS classification, (c) point-in-time index/universe membership,
(d) broad bulk OHLCV, and (e) tick-level historical quotes/trades, all in
one product at reasonable cost. A two-tier architecture (root + secondary)
is therefore not a compromise — it reflects how this market's data
products are actually structured, confirmed by the provider-by-provider
findings below, not assumed in advance.

---

## 2. Provider research (current, evidence-based, not assumed)

Each provider evaluated against the Controller's checklist, using current
provider documentation, official pricing pages, and (where official docs
were inconclusive) independent third-party reviews — flagged as such where
used, per the Controller's instruction not to rely only on blogs for
critical capability claims.

### 2.1 Alpaca (already partially researched in the prior document; reconfirmed here for comparison)

| Requirement | Finding |
|---|---|
| Universe coverage | Broad US equities/ETFs (our own execution venue's tradable list) |
| Historical depth | ~7yr SIP / ~5yr IEX |
| OHLCV | Yes, daily and finer |
| Quotes/trades | Yes, dedicated endpoints (IEX free / SIP paid) |
| Corporate actions | Yes, dedicated Corporate Actions API (depth not independently verified) |
| Delisted symbols | **No — confirmed gap.** Community reports (cited in the prior document) show historical data for `status=inactive` symbols returning empty even for periods when the symbol was actively trading. |
| Point-in-time universe history | **No — only a current snapshot via Assets API.** |
| Sector/GICS | **No — not found in Assets API fields.** |
| ETF metadata / leveraged flag | Not confirmed present. |
| API/bulk access | Yes, REST API; not a flat-file bulk product. |
| Cost | Free (IEX) / paid subscription (SIP, "Algo Trader Plus") |
| Survivorship-bias handling | **Poor — this is the decisive disqualifier for the root-source role.** |

**Verdict:** confirmed **not suitable as the root historical source**,
primarily due to the survivorship-bias gap — but remains highly relevant
as our **live execution venue** and a **secondary source for live/recent
market data** (see §10).

### 2.2 Polygon.io (rebranded Massive in early 2026)

| Requirement | Finding |
|---|---|
| Universe coverage | Broad — aggregates, trades, NBBO quotes, ticker reference across all US equities |
| Historical depth | Not explicitly quantified in the sources found; broad intraday/daily aggregate coverage confirmed |
| OHLCV | Yes — minute/hour/day aggregate bars |
| Quotes/trades | Yes — NBBO quotes and trades |
| Corporate actions | Documented as covered (splits, dividends), but **data quality is independently reported as unreliable** — missing dividend data even for SPY, missing split data reported by users |
| Delisted symbols | **"Spotty at best" per independent review** — often missing company name, trading dates, or industry code; explicitly "not recommended if you need data on delisted tickers" |
| Sector/industry | SIC code available via `get_ticker_details` (a real, if coarser-grained, classification than GICS) |
| ETF metadata | `type` field distinguishes common stock vs. ETF in the tickers endpoint |
| API/bulk access | REST + WebSocket + **S3-style flat files** — genuinely bulk-capable |
| Cost | Tiered retail plans; Business/redistribution contracts for exchange-licensed data |
| Survivorship-bias handling | **Weak — confirmed by independent review, not merely absent documentation.** |

**Verdict:** strong on breadth, bulk access, and SIC-level sector data;
**disqualified from the root role by the same survivorship-bias weakness
as Alpaca**, now independently corroborated by a third-party review rather
than only Alpaca's own community forum. Worth keeping in mind as a
possible secondary source for sector/SIC classification specifically, with
its own data-quality caveats noted.

### 2.3 Tiingo

| Requirement | Finding |
|---|---|
| Universe coverage | Broad US equities/ETFs |
| Historical depth | Daily (and weekly/monthly/annual) EOD history; deep coverage claimed |
| OHLCV | Yes |
| Quotes/trades | Not confirmed as a strong offering — Tiingo's core product is EOD prices, not tick-level quotes |
| Corporate actions | Not confirmed in the sources found |
| Delisted symbols | **Confirmed present** — ticker metadata endpoint explicitly returns info for delisted companies including their ending date |
| Sector/industry | **Not confirmed** in the sources found for this document — flagged as unverified, not assumed absent or present |
| API/bulk access | REST API; **free tier explicitly rate-limited to 50 symbols/hour**, which is materially restrictive for full-universe bulk ingestion (thousands of symbols) without a paid tier |
| Cost | Free tier (rate-limited) + paid tiers (pricing page exists; specific bulk-tier cost not itemized in the sources found) |
| Survivorship-bias handling | **Better than Alpaca/Polygon** — delisted coverage confirmed — but sector/GICS and true point-in-time index-constituent tracking not confirmed. |

**Verdict:** a meaningful improvement over Alpaca/Polygon on delisted
coverage, but **materially weaker than the purpose-built option in §2.4**
on the specific combination of survivorship-bias handling, sector
classification, and point-in-time universe reconstruction our calibration
plan requires. A candidate for a secondary EOD-data cross-check, not the
root source.

### 2.4 Norgate Data

| Requirement | Finding |
|---|---|
| Universe coverage | **Entire US stock market**, both currently-listed and delisted, per official product description |
| Historical depth | **30+ years** (with a purchase option starting as far back as 1950 for some data) — far exceeds our multi-year regime-diversity requirement |
| OHLCV | Yes, daily |
| Quotes/trades | **Not a tick/quote product** — this is a daily-bar-and-below (not intraday tick) systematic-trading data product; not a substitute for true bid-ask history |
| Corporate actions | Implied as part of the maintained historical series (splits/adjustments handled as part of the product's core purpose — systematic backtesting) |
| Delisted symbols | **Yes, explicitly the product's core differentiator** — "maintains a complete archive of delisted U.S. equities alongside the active ones"; delisted symbols are appended with their delisting year/month |
| Sector/industry | **Yes — explicitly included**: "enriched with sector classifications, company names, and index membership flags" |
| Point-in-time universe/index history | **Yes — this is a named, documented feature**: point-in-time index constituency tables reconstructing exactly which stocks belonged to an index (S&P 500, Nasdaq 100, Russell 3000) on any historical date, with entry/exit/re-entry tracking |
| ETF metadata | Covered as part of "other listed security types" per the product description; leveraged/inverse-specific flagging not explicitly confirmed — flagged for verification |
| API/bulk access | **Python API (`norgatedata` package)** plus platform integrations (AmiBroker, Zipline, RightEdge, Wealth-Lab); access is via a **locally-synced proprietary database**, not a cloud REST API — see §7 and §11 for the operational implications |
| Cost | **US equities "Platinum" package: $630/year** — a defined, moderate, non-institutional-scale cost; 3-week free trial available |
| Survivorship-bias handling | **This is the product's stated core purpose.** No other researched provider makes this an explicit, named design goal to this degree. |

**Verdict:** the closest match to our actual requirement — survivorship-
bias-free calibration — of any provider researched. This is not a generic
data vendor that happens to include delisted symbols; it is purpose-built
for exactly the systematic-backtesting integrity problem our calibration
plan (Tier 1 requirement, prior document) identified as mandatory.

### 2.5 Databento

| Requirement | Finding |
|---|---|
| Universe coverage | All 15 US exchanges, consolidated |
| Historical depth | Schema-dependent (L0-L3 tiers); specific year-depth not itemized in the sources found |
| OHLCV | Yes — consolidated EOD and intraday |
| Quotes/trades | **Yes — this is a genuine strength**, full-market tick/quote data across all exchanges, no separate "SIP paid tier" gate the way Alpaca structures it |
| Corporate actions | Not confirmed in the sources found |
| Delisted symbols | Not confirmed in the sources found — flagged as unverified, not assumed absent |
| Sector/industry | Not confirmed in the sources found |
| API/bulk access | **Yes — explicit batch/flat-file bulk download product**, designed for large historical pulls (>5GB jobs recommended via batch) |
| Cost | Standard plan $199/month; usage-based components; $125 free credit for evaluation |
| Survivorship-bias handling | Not established either way by the research performed — cannot claim strength here without more evidence, so treated as unconfirmed rather than assumed adequate |

**Verdict:** the strongest researched option for **true tick-level
quotes/trades** specifically, and a serious bulk-access product — but
without confirmed delisted-symbol and sector coverage, it is not a
complete root-source candidate on its own. A strong candidate for the
**spread/quote secondary source** (§1.7 of the prior document's open
question — "do we need a better historical source than a range proxy" —
this is real evidence that one exists and is accessible).

### 2.6 Nasdaq Data Link and Alpha Vantage

- **Nasdaq Data Link:** per the research performed, best suited to macro
  and alternative datasets, not primary equities OHLCV + corporate actions
  + delisted-symbol coverage. **Not a serious root-source candidate** for
  this use case; not evaluated further.
- **Alpha Vantage:** NASDAQ's officially licensed data provider, 15+ years
  of history, but **explicitly and confirmedly has no delisted-symbol
  coverage** ("equity universes carry survivorship bias," stated plainly
  in the source found). This directly disqualifies it from the root role
  under our Tier-1 requirement, regardless of its other strengths (broad
  20+ exchange coverage, official NASDAQ licensing). Pricing $49.99–
  $249.99/month depending on tier.

### 2.7 Summary comparison table

| Provider | Delisted coverage | Sector/GICS | Point-in-time universe | Bulk access | Cost | Root-source viable? |
|---|---|---|---|---|---|---|
| Alpaca | No (confirmed gap) | No | No | REST only | Free/paid tiers | No |
| Polygon/Massive | Weak ("spotty," independently confirmed) | SIC code (coarser than GICS) | Not confirmed | Yes (S3 flat files) | Tiered, some paid | No |
| Tiingo | Yes (confirmed) | Not confirmed | Not confirmed | REST, rate-limited on free tier | Free (limited) / paid | Weak candidate |
| **Norgate Data** | **Yes — core purpose** | **Yes — included** | **Yes — named feature** | **Yes — local bulk-synced database** | **$630/yr** | **Yes — strongest fit** |
| Databento | Not confirmed | Not confirmed | Not confirmed | Yes — batch/flat-file | $199/mo+ | Strong for quotes only, not root |
| Nasdaq Data Link | N/A (not evaluated for this purpose) | N/A | N/A | N/A | N/A | No |
| Alpha Vantage | **No — confirmed absent** | Not confirmed | Not confirmed | REST | $49.99-$249.99/mo | No |

Sources for this section:
[Polygon.io / Massive GitHub overview](https://github.com/api-evangelist/polygon-io);
[A Complete Review of the Polygon.io API](https://medium.com/@yolotrading/a-complete-review-of-the-polygon-io-api-everything-you-wanted-to-know-c79e992a74ff)
(independent review — delisted-ticker and dividend-data-quality findings);
[What does Massive use for Sector?](https://polygon.io/knowledge-base/article/what-does-polygon-use-for-sector);
[Tiingo End-of-Day Stock Price Data API](https://www.tiingo.com/products/end-of-day-stock-price-data);
[Using Tiingo and Pandas DataReader](https://amysillman.medium.com/using-tiingo-and-pandas-datareader-to-access-financial-data-ad72d2fc098c)
(delisted-symbol metadata finding);
[Norgate Data — Overview](https://norgatedata.com/);
[Norgate Data - Stock Market Packages](https://norgatedata.com/stockmarketpackages.php);
[How to Construct a Survivorship bias-free Database in Norgate using Python](https://concretumgroup.com/how-to-construct-a-survivorship-bias-free-database-in-norgate-using-python/);
[Historical Constituents of an Equity Index in Python (Norgate Data)](https://concretumgroup.com/historical-constituents-of-an-equity-index-in-python-norgate-data/);
[Databento US Equities](https://databento.com/catalog/us-equities);
[Databento pricing](https://databento.com/pricing);
[Alpha Vantage API: The Complete 2026 Guide](https://alphalog.ai/blog/alphavantage-api-complete-guide);
[Nasdaq Data Link Review & Pricing 2026](https://tradingdatacompare.com/providers/nasdaq-data-link/)

---

## 3. Recommended root source

**Use Norgate Data as the root historical-data source for D-0026
calibration**, because it is the only researched provider whose core
product purpose — survivorship-bias-free systematic backtesting — matches
our Tier-1 mandatory requirement (established in
`historical-data-calibration-plan.md §4`) directly, rather than as an
incidental feature. Specifically, it satisfies:

- **Delisted/inactive securities** — the confirmed decisive gap in every
  broker-style provider researched (Alpaca, Polygon, Alpha Vantage) is
  Norgate's stated core purpose.
- **Point-in-time universe/index membership** — a named, documented
  product feature, directly solving the hardest confirmed gap from the
  prior document (§1.3 there: "Alpaca alone is likely NOT sufficient").
- **Sector classification** — included, closing the second confirmed gap
  (§1.4 of the prior document).
- **Broad bulk coverage of the entire US equity market** (not a curated
  subset) — matching the Controller's explicit "don't rediscover for each
  new symbol" requirement (§7 below expands on this).
- **Sufficient history for multi-regime calibration** — 30+ years, far
  exceeding what any of the broker-style APIs offer (5-7 years) and giving
  real room to select a walk-forward window (per the prior document's §5)
  that reliably contains multiple genuine market regimes rather than being
  bounded by a short data ceiling.
- **Defined, moderate, predictable cost** ($630/year) — appropriate for a
  calibration research phase, versus Databento's higher recurring cost or
  Polygon's business-tier licensing complexity for redistribution-grade
  needs we don't actually have.

---

## 4. Gaps in the recommended provider

Being honest and specific, per the Controller's explicit instruction:

- **No tick-level historical quotes/trades (true bid-ask spread).**
  Norgate's product is daily-bar-oriented for systematic trading research,
  not a tick-data replacement. **This is expected and acceptable** — our
  own approved strategy checks hourly, at daily-bar-appropriate
  granularity (confirmed in the prior document's Task 1 finding), so this
  gap does not block the core strategy-mechanics calibration. It does
  mean the spread-cap parameter (§7.2 of the prior document) still needs a
  secondary source.
- **No confirmed leveraged/inverse ETF-specific flag.** Norgate covers
  "other listed security types" broadly but a leverage-factor field was
  not confirmed in the research performed. **A small, curated exclusion
  list remains necessary regardless of root provider** — this was already
  anticipated in the prior document (§1.6) as likely needing a maintained
  list of known leveraged/inverse issuers (ProShares, Direxion, etc.),
  which is a small, stable, low-maintenance dataset independent of which
  root provider is chosen.
- **Windows-native local-database access model**, not a cloud REST API —
  this is an operational, not a data-completeness, gap, but it materially
  affects the ingestion design (§7, §11).
- **Stable instrument identifiers not independently confirmed as a
  CRSP-PERMNO-style persistent numeric ID.** Norgate does track historical
  ticker changes as part of its point-in-time design (this is implied by
  its point-in-time index-constituency feature, which requires tracking
  entities across ticker changes to work at all), but the exact identifier
  scheme was not independently confirmed in the sources found for this
  document. **Flagged as a verification item for early Phase 2 execution
  (§11, step 6)**, not assumed to work a specific way.

### Is the secondary source needed only during Phase 2, or also in the future production system?

- **Tick-level quotes (secondary source, e.g. Databento or Alpaca SIP):**
  needed during Phase 2 for spread-parameter calibration. In the eventual
  production system (Phase 3, not approved), **live** trigger-price
  evaluation already uses Alpaca's Last Trade (D-0012, unchanged) — so
  this secondary need is **primarily a Phase 2 (historical calibration)
  concern**, not a new production dependency.
- **Leveraged/inverse ETF exclusion list:** needed in **both** Phase 2
  (to correctly exclude these from calibration) and the eventual
  production system (to enforce the already-approved structural
  exclusion, D-0026 principle §7A item 8) — this is a small, low-
  maintenance list either way, not a major secondary integration.
- **Sector classification, if sourced separately from Norgate:** not
  currently expected to be needed, since Norgate includes this — flagged
  only as a fallback if Norgate's sector data proves insufficient during
  Phase 2 validation (§11, step 5).

### Revised architecture

```
PRIMARY ROOT SOURCE: Norgate Data
  (broad US equity/ETF universe, 30+ yrs, delisted-inclusive,
   sector-classified, point-in-time index membership)
        ↓
Secondary enrichment:
  - Tick-level quotes/spread (Databento, or Alpaca SIP if already
    authorized for other reasons) — Phase 2 calibration only
  - Curated leveraged/inverse ETF exclusion list — Phase 2 AND
    eventual production
        ↓
Ingestion + Validation + Normalization  (§5)
        ↓
Canonical Historical Dataset  (§5)
        ↓
Calibration Engine  (per historical-data-calibration-plan.md)
```

This replaces the earlier "symbol A → search source, symbol B → search
another source" anti-pattern the Controller explicitly warned against: the
**default** path for any US equity/ETF symbol is Norgate's already-bulk-
covered universe; the secondary sources are narrow, well-defined
enrichments applied uniformly across that universe, not per-symbol
lookups.

---

## 5. Normalized local dataset design

### Principle

Once ingested, **the calibration engine must not care which external
provider supplied the data.** All providers' data is normalized into one
canonical internal representation before the calibration engine (per
`historical-data-calibration-plan.md`) ever touches it.

```
Norgate Data (root)  +  secondary sources (quotes, ETF-leverage list)
        ↓
Ingestion (provider-specific readers)
        ↓
Validation (schema checks, gap detection, outlier flags)
        ↓
Normalization (canonical field names/types/identifiers)
        ↓
Canonical Historical Dataset
        ↓
Calibration Engine (provider-agnostic from this point on)
```

### Major tables/entities (design only — no schema implementation yet)

- **`Instrument`** — one row per canonical instrument (see §6 for identity
  design), with current/latest known attributes (name, exchange, asset
  class).
- **`InstrumentHistory`** — time-versioned attributes of an instrument
  (ticker-at-date, name-at-date, exchange-at-date, active/delisted status
  and date) — this is what makes the dataset point-in-time-correct rather
  than a single static snapshot.
- **`DailyBar`** — OHLCV + VWAP (where available) per instrument per date,
  split-adjusted with the adjustment factor also recorded (so both raw and
  adjusted views are reconstructable).
- **`QuoteSpreadHistory`** — from the secondary tick-data source, where
  available; explicitly nullable/sparse, since coverage will not match
  `DailyBar`'s completeness — the calibration engine must handle its
  absence gracefully (falling back to the labeled range-proxy per the
  prior document's §3), not assume it's always present.
- **`CorporateAction`** — split/dividend/merger/symbol-change events with
  effective dates, used to drive both `DailyBar` adjustment and
  `InstrumentHistory` identity transitions (§6).
- **`SectorClassification`** — instrument → sector/industry mapping,
  ideally time-versioned (an `InstrumentHistory`-style table) if Norgate's
  data supports it; a simplification (current classification applied
  throughout) is an acceptable, explicitly labeled fallback if not (per
  the prior document's guidance on this exact issue).
- **`ETFClassification`** — is-ETF flag, and where available,
  sub-classification (broad-index/sector/thematic); joined against the
  separately-maintained leveraged/inverse exclusion list for the
  structural exclusion (this list is best modeled as its own small,
  manually-curated table rather than sourced from Norgate).
- **`TradabilityHistory`** — derived from `InstrumentHistory`'s
  active/delisted status, but modeled separately because "tradable through
  our specific broker at this date" is conceptually a narrower question
  than "existed and was listed somewhere" — even though for Phase 2
  calibration purposes, the two will likely be treated as equivalent
  unless evidence suggests otherwise.
- **`MarketRegimeData`** — the broad-market volatility/drawdown reference
  series (from `DailyBar` on a chosen index-tracking instrument), kept as
  its own derived table since it's computed, not directly ingested.

### What this document does NOT do

No schema (column types, constraints, file formats, storage engine) is
specified here — that is an implementation decision for later in Phase 2
(§11, steps 3-4), not a Phase-1-adjacent research deliverable. This
section defines the **entities and their relationships**, which is the
appropriate level of design for the current approval scope (research/
design work within Phase 2, per the Controller's explicit boundary).

---

## 6. Symbol identity design

### Why ticker strings alone are insufficient

A ticker string is not a stable identity across time: companies change
tickers (mergers, rebranding), the same ticker can be reused for a
different company years after the original delists, and a naive
ticker-keyed dataset would silently conflate these into one "instrument."

### How Norgate's data supports this, and what remains to verify

Norgate's headline feature — reconstructing point-in-time index
membership with entry/exit/re-entry tracking across a 30+-year history —
**could not work at all** unless the underlying data model tracks
instruments across ticker/name changes rather than keying purely on the
current ticker string. This gives reasonable confidence that some form of
persistent internal identity exists in Norgate's data. **However**, the
exact mechanism (a numeric permanent ID akin to CRSP's PERMNO, versus a
ticker-plus-date-range composite key, versus something else) was **not
independently confirmed** in the research performed for this document.

### Recommended canonical identity design

Regardless of exactly what Norgate exposes, our own canonical
`Instrument` table (§5) should use an **internally-generated, stable
surrogate key** (e.g., a UUID or auto-incrementing ID assigned once per
distinct company/security we ingest), **not the ticker string**, as the
primary identifier — with `InstrumentHistory` recording every
ticker/name/exchange the surrogate key has ever been associated with, and
their effective date ranges. This makes our canonical dataset robust to
whatever Norgate's own internal identity scheme turns out to be: we map
Norgate's provided identity (however it's structured) onto our own
surrogate key during ingestion, once, and the calibration engine only ever
sees the stable surrogate key.

### Handling the specific cases the Controller listed

- **Ticker changes:** new row in `InstrumentHistory` for the same
  surrogate key, new ticker, effective date = change date.
- **Company name changes:** same pattern, on the name field.
- **Mergers:** the acquired company's `InstrumentHistory` gets an end
  date; whether the acquiring company's instrument absorbs any
  continuity is a data-availability question to resolve during ingestion
  (§11 step 6), not decided here.
- **Spin-offs:** modeled as a new `Instrument` (new surrogate key) with a
  `CorporateAction` link back to the parent, rather than treated as a
  continuation of the parent's identity.
- **Delistings:** `InstrumentHistory` end date set; `TradabilityHistory`
  correspondingly closed; the instrument and its full prior `DailyBar`
  history remain in the canonical dataset (this is the entire point of
  using a survivorship-bias-free root source).
- **Relistings:** if the same legal entity relists (rare, but possible
  post-bankruptcy reorganization under the same or a new ticker), treated
  as a judgment call during ingestion — likely a **new** surrogate key
  given the legal/economic discontinuity, flagged for explicit handling
  rather than assumed.

### The goal, restated and confirmed achievable with this design

"A symbol appearing tomorrow should map into the same canonical data model
without requiring us to manually rediscover its history" — **achievable**,
because (a) Norgate's bulk, whole-market coverage means a new listing is
already present in the next scheduled Norgate update (§8) without any
manual per-symbol action, and (b) our own ingestion process assigns it a
surrogate key and `InstrumentHistory` row automatically as part of the
routine (not per-symbol-special-cased) ingestion pipeline.

---

## 7. Full-universe ingestion — realism check

### Is bulk, non-per-symbol ingestion realistic with Norgate?

**Yes, and this is a genuine strength, not an assumption.** Norgate's
product is explicitly structured around **watchlists covering the entire
market** (e.g., "All US Stocks," a delisted-securities watchlist, index-
membership watchlists) — the documented Python workflow pattern is to
query one of these broad watchlists and receive the *whole* relevant
symbol set, not to specify symbols individually. This directly matches the
Controller's required pattern:

```
Norgate's "All US Stocks" (+ delisted) watchlist
        ↓
bulk historical ingestion (one pass, whole universe)
        ↓
local canonical dataset (§5)
        ↓
new symbols automatically recognized on the next scheduled Norgate
update, requiring no manual per-symbol configuration
```

### The one operational caveat, stated plainly

Norgate's access model is a **locally-synced proprietary database**,
maintained by a Windows application ("Norgate Data Updater"), with
programmatic access via a Python package that reads from that local sync
— **not** a cloud REST API our Python calibration engine (which per
D-0022 is not tied to a specific OS) could query directly from any host.
This means the realistic ingestion pattern is:

1. Run the Norgate Updater (on a Windows machine or Windows VM) to
   establish and maintain the local database.
2. Use Norgate's Python package, run on that same Windows environment, to
   bulk-export the whole-universe data (OHLCV, sector, delisted list,
   point-in-time index membership) into a **portable format** (e.g.
   Parquet/CSV files) as a one-time or periodically-repeated export step.
3. The portable export files become the actual input to our
   provider-agnostic ingestion/normalization pipeline (§5), which can then
   run on whatever host our calibration engine actually uses.

This is a real but manageable operational detail — it does not undermine
the "bulk, not per-symbol" requirement, but it does mean Phase 2's
execution plan (§11) must include provisioning a Windows environment (a
VM is sufficient) as an explicit early step, not something to discover
later.

---

## 8. Data refresh strategy (design only — not implemented)

```
Historical backfill (Norgate, one-time bulk pull of full available history)
        ↓
Daily incremental update (Norgate Updater's normal operation —
    the product is designed for exactly this ongoing-maintenance pattern)
        ↓
Validation (schema/gap/outlier checks, per §5)
        ↓
Canonical dataset (re-normalized incrementally, not fully rebuilt each time)
```

- **Full re-downloads:** needed once, for initial backfill; not needed
  routinely afterward given Norgate's incremental-update design.
- **Incremental updates:** yes — this is Norgate's normal operating mode
  (the Updater application is explicitly built for ongoing maintenance,
  not one-time snapshotting).
- **Corporate-action updates:** handled as part of the same incremental
  update cycle, since Norgate's adjusted-price series already accounts
  for splits/dividends as part of its core product.
- **Metadata refresh (sector, index membership):** part of the same
  incremental cycle — Norgate's product description indicates continuous
  maintenance of these fields, not a separate process.
- **Delisted-symbol updates:** automatic — when a currently-active
  instrument in our dataset gets delisted, the next incremental Norgate
  update reflects that, and our `InstrumentHistory`/`TradabilityHistory`
  tables are updated accordingly during normalization, not through any
  manual "add this delisted symbol" step.
- **New-symbol discovery:** automatic, per §7 — new listings appear in
  Norgate's whole-market watchlists on their own update cycle, requiring
  no per-symbol action on our part.

**This section describes the design, not an implementation.** No
ingestion code, scheduler entry, or live process is created by this
document.

---

## 9. Cost analysis

### Categorized, current, realistic costs (not assumed)

**Root source:**
- **Norgate Data, US equities "Platinum" package: $630/year** —
  confirmed from Norgate's own pricing page. This is a defined,
  recurring, moderate cost, not a one-time fee — an ongoing Phase 2 (and,
  if Phase 3 is ever approved, ongoing production) line item to budget for.
- 3-week free trial available for initial evaluation before committing to
  the paid subscription (useful for the "representative data sample"
  validation step in §11).

**Secondary source (tick-level quotes/spread), if pursued:**
- **Databento:** Standard plan $199/month, or usage-based via the
  $125 free-credit evaluation tier first. This is a **separate, optional**
  cost — only needed if the true-spread calibration (rather than the
  labeled range-proxy fallback) is judged worth the expense. **Decision
  deferred to the Controller** (§15, item C) — not assumed necessary.
- **Alternative: Alpaca SIP subscription** ("Algo Trader Plus") — cost not
  itemized in the research performed for this document; would need its
  own pricing confirmation if pursued instead of Databento. Using Alpaca
  SIP has the advantage of being the same data our live execution
  eventually sees (§10), at the cost of narrower quote-history depth than
  a dedicated historical-data vendor might offer.

**Free/no-cost components:**
- Leveraged/inverse ETF exclusion list — a small, manually-curated list
  (known issuers: ProShares, Direxion, etc.) requires research time, not
  a paid data subscription.
- Norgate's included sector classification and point-in-time index data —
  bundled into the $630/year Platinum package, no separate line item.

**Storage/bandwidth:**
- Norgate's local-database model means storage is primarily local disk on
  whatever host runs the Windows sync (§7) plus whatever portable export
  files are produced for the actual calibration pipeline — not a
  cloud-storage cost category distinct from normal compute/disk costs.
  Not itemized further here; an implementation-phase estimation exercise
  once actual data volumes are known (§11, step 7).

### Licensing — explicitly not assumed to mean unlimited use

**"API access" does not automatically mean unrestricted permanent
storage/redistribution rights.** Norgate's licensing terms (subscription
FAQ exists on their site but was not fully reviewed line-by-line in this
research pass) should be **explicitly checked during Phase 2 step 2 (§11)**
for: whether locally-stored historical data may be retained after a
subscription lapses, whether the data may be used to derive and store
downstream calibration artifacts indefinitely (our actual use case), and
any restrictions on the specific "systematic backtesting for a personal/
internal trading system" use case versus redistribution or commercial
resale (which is not our use case, but the license terms should be
confirmed rather than assumed permissive). This is flagged as a concrete,
early Phase 2 action item, not resolved in this document.

---

## 10. Alpaca's role — explicit recommendation

**Answer: C — both, with Norgate serving as the historical calibration
root, and Alpaca remaining our secondary/live execution-market-data
source.** Neither A (Alpaca as root) nor B (Alpaca purely secondary,
implying it could be dropped) is correct.

Reasoning, addressing each item the Controller specifically flagged:

- **SIP vs. IEX:** irrelevant to Alpaca's *root-source* candidacy (already
  disqualified on survivorship-bias grounds), but still relevant to
  Alpaca's *live execution* role — D-0012's approved use of Alpaca's Last
  Trade for live ladder triggers is **completely unaffected** by this
  document; that decision remains unchanged.
- **Historical quotes/trades:** a candidate secondary source for the
  spread-calibration gap (§4), but Databento is evaluated as the stronger
  option specifically for this narrow purpose (§2.5) given its more
  clearly documented full-market tick coverage without the free-tier-vs-
  paid-tier IEX/SIP split Alpaca imposes.
- **Corporate actions:** Alpaca has this, but Norgate's adjusted-price
  series already handles it as part of the root dataset — redundant to
  pull from both for the same purpose.
- **Delisted/inactive symbols:** this is precisely where Alpaca is
  disqualified as root and Norgate is the clear answer (§2.1 vs §2.4).
- **Point-in-time universe history:** same — Alpaca's confirmed gap is
  Norgate's confirmed strength.
- **Sector/GICS gap:** same pattern.
- **Survivorship bias:** the decisive, repeated reason Alpaca cannot be
  the root, restated here as the cross-cutting theme of this whole
  section.

### What Alpaca remains essential for, unchanged by this document

- **Live execution** — our actual broker, unaffected.
- **Live Last Trade price for D-0012 ladder triggers** — unchanged,
  unaffected by this document, which concerns only historical calibration
  data.
- **A candidate live/recent market-data cross-check** once the eventual
  (not-yet-approved) Phase 3 production universe subsystem exists, since
  by definition Alpaca's own tradability is the actual execution
  constraint, regardless of what a historical research database says.

---

## 11. Phase 2 execution plan (research/design sequencing — Phase 3 not authorized)

1. **Select root data provider** — this document recommends Norgate Data;
   Controller confirmation of this specific choice is the immediate next
   decision point (§15).
2. **Confirm licensing/access** — review Norgate's actual subscription
   terms for retention/storage/derived-use rights (§9's flagged item);
   confirm the 3-week free trial's scope is sufficient for step 4 below
   before committing to the $630/year subscription.
3. **Define canonical data model** — take §5's entity design from
   conceptual to an actual schema (field names, types, file format
   decision) — this is design work, not yet data acquisition.
4. **Acquire a representative data sample** — using the free trial (step
   2), pull a small, deliberately-chosen subset (e.g., a handful of
   currently-active names, a handful of confirmed-delisted names, one
   or two known corporate-action events) to validate the actual data
   shape before committing to a full-universe pull.
5. **Validate data quality** — check the sample against known facts (e.g.,
   a delisted company's actual delisting date, a known stock split's
   actual ratio and date) to build empirical confidence rather than
   trusting vendor claims alone — directly addressing the Controller's
   standing instruction not to assume data quality.
6. **Validate symbol identity/history** — specifically confirm how
   Norgate represents ticker changes/delistings internally (the open
   question flagged in §6), so our surrogate-key mapping (§6) is designed
   against Norgate's actual behavior, not an assumption about it.
7. **Acquire full historical dataset** — the full-universe bulk pull
   (§7), once steps 4-6 have de-risked the approach.
8. **Add required secondary metadata** — the leveraged/inverse exclusion
   list (manual curation) and, if the Controller authorizes the
   associated cost (§9), the tick-quote secondary source (Databento or
   Alpaca SIP) for spread calibration.
9. **Build validation checks** — systematic, repeatable data-quality
   checks (gap detection, outlier flags, cross-source consistency where
   secondary sources overlap with Norgate) as part of the ingestion
   pipeline (§5), not one-off manual review.
10. **Build offline calibration engine** — implementing the frozen-
    strategy simulation and the walk-forward/regime-segmentation
    methodology already specified in `historical-data-calibration-plan.md`.
    **This is the first step in this sequence that constitutes "building
    the offline calibration engine" the Controller's Phase 2 approval
    explicitly covers** — still entirely offline, no live connection.
11. **Run calibration** — execute the walk-forward process across the
    canonical dataset, per the prior document's §5-§9.
12. **Produce evidence** — the regime-segmented, sensitivity-tested,
    control-compared evidence package per the prior document's §13
    acceptance checklist.
13. **Review parameters** — presented to the Controller for the eventual
    PROPOSED → APPROVED decision on each parameter, per that same
    checklist. **This step produces a recommendation, not an automatic
    approval** — per the Controller's explicit instruction, no numeric
    threshold is approved automatically as an output of this process.

**Explicitly not part of this sequence:** anything connecting to live
execution, the live scheduler, live routines, or Phase 3 in any form.

---

## 12. Phase 2 exit criteria

Phase 2 is complete, and the question of numeric parameter approval can be
brought to the Controller, only when **all** of the following are true:

1. **Root source selected and confirmed** — this document's recommendation
   (Norgate Data) accepted or revised by the Controller.
2. **Licensing confirmed** — the specific retention/derived-use questions
   flagged in §9 resolved, not assumed.
3. **Required datasets available** — OHLCV, delisted coverage, sector
   classification, point-in-time universe history all present in the
   canonical dataset, either from Norgate directly or the confirmed
   secondary sources (§4).
4. **Historical coverage verified empirically** — not just claimed by
   vendor documentation, per step 5 of §11.
5. **Point-in-time concerns addressed** — the canonical dataset's
   `InstrumentHistory`/`TradabilityHistory` design (§5, §6) actually
   implemented and validated against known historical facts, not merely
   designed on paper.
6. **Canonical dataset validated** — the normalization pipeline (§5)
   producing consistent, schema-correct output across a full-universe
   pull, not just the small representative sample.
7. **Calibration engine validated** — the offline simulation engine
   confirmed to run the frozen approved strategy logic (Ladder/Floor/
   Trailing/D-0007/D-0011) exactly, unmodified, per the strategy-mechanics
   preservation requirement (`historical-data-calibration-plan.md §8`).
8. **Walk-forward methodology implemented** — per that document's §5, not
   simplified to a single static split without justification.
9. **Out-of-sample holdout preserved** — the single, untouched final block
   genuinely never inspected before final evaluation, per that document's
   §12 overfitting safeguards.
10. **Sensitivity testing completed** — per that document's §11, for every
    parameter with a numeric or formula-based candidate value.
11. **Evidence package produced** — per that document's §13's eight-point
    acceptance checklist, presented for Controller review with FACT/
    ASSUMPTION/HYPOTHESIS/RECOMMENDATION labeling (CLAUDE.md §4).

**Only after all eleven conditions are met** does numeric parameter
approval become a live question for the Controller to decide — and even
then, per the Controller's standing instruction, each parameter is
evaluated individually against this evidence, never approved automatically
as a batch or as a side effect of Phase 2 "finishing."

---

## 13. Safety boundary — reaffirmed, unchanged by this document

Nothing in this document modifies, or authorizes modifying:

- Live trading code, scheduler, or routines
- Cron jobs
- Live dependencies (no `requirements.txt`/`pyproject.toml` created)
- Any connection between the calibration engine (not yet built) and live
  execution
- Live universe selection
- Strategy mechanics (Ladder, Floor, Trailing, D-0007, D-0011 — all
  unchanged, all treated as frozen inputs per the prior document's §8)
- Any numeric threshold's status (all remain PROPOSED/TBD)
- Order placement of any kind

**Phase 2, as approved, is offline research/calibration only.** This
document is itself still a research/design deliverable within Phase 2 —
no data has actually been downloaded, no Norgate subscription has been
purchased, no code has been written, as part of producing this document.

---

## 14. Final output

### A. Recommended root data source

**Norgate Data** (US equities Platinum package).

### B. Secondary source(s), if unavoidable

- **Tick-level quotes/spread:** Databento (preferred, per §2.5's stronger
  documented tick-coverage) or Alpaca SIP (alternative, same data our live
  execution eventually sees) — **cost-gated, Controller decision pending**
  (§15).
- **Leveraged/inverse ETF exclusion list:** a small, manually-curated
  list of known issuers — negligible cost, needed regardless of root
  provider.

### C. Why this combination is sufficient

Norgate closes the two most consequential gaps identified in the prior
calibration-methodology document (point-in-time universe history, sector
classification) and the survivorship-bias gap common to every broker-style
API researched, at a moderate, defined cost, with genuinely bulk
(whole-market, not per-symbol) access matching the Controller's explicit
architectural requirement. The two secondary items (tick quotes,
leveraged-ETF list) are narrow, well-understood, and don't reintroduce the
"search per new symbol" anti-pattern — they're small, stable enrichments
applied uniformly, not per-symbol lookups.

### D. Estimated cost

- Norgate Data (root): **$630/year**, confirmed from official pricing.
- Databento (optional secondary, if authorized): **$199/month** (~$2,388/
  year) or usage-based via free credit — a separate Controller decision.
- Leveraged/inverse ETF list: negligible (research time, not a paid
  subscription).
- Storage/bandwidth: not separately itemized; a normal compute/disk cost
  once actual data volumes are known (§11 step 7).

### E. Data coverage

Whole US equity + ETF market, 30+ years, including delisted securities,
with sector classification and point-in-time index/universe membership —
the specific combination our calibration methodology requires, confirmed
by evidence rather than assumed.

### F. Main risks/gaps

- No tick-level quotes from Norgate itself (addressed via secondary
  source, cost-gated).
- No confirmed leveraged/inverse ETF flag (addressed via a small curated
  list).
- Windows-native local-database access model — an operational, not
  data-quality, consideration requiring a Windows VM in the ingestion
  pipeline (§7, §11 step 1-ish/implicit in step 3-4 provisioning).
- Exact stable-identifier mechanism not independently confirmed —
  flagged for explicit verification early in Phase 2 (§11 step 6).
- Licensing/retention terms not fully reviewed in this research pass —
  flagged as an explicit early Phase 2 action item (§9, §11 step 2).

### G. Proposed canonical dataset architecture

`Instrument`, `InstrumentHistory`, `DailyBar`, `QuoteSpreadHistory`,
`CorporateAction`, `SectorClassification`, `ETFClassification`,
`TradabilityHistory`, `MarketRegimeData` — entity design only, per §5; no
schema/implementation yet.

### H. Phase 2 implementation sequence

Thirteen steps, per §11 — provider selection and licensing confirmation
through parameter review, explicitly stopping short of Phase 3.

### I. Phase 2 exit criteria

Eleven conditions, per §12 — all required before numeric parameter
approval becomes a live question.

### J. Remaining decisions requiring Controller approval

1. **Confirm (or revise) the root-source recommendation: Norgate Data.**
2. **Authorize the $630/year Norgate subscription cost.**
3. **Decide on the tick-quote secondary source** (Databento vs. Alpaca SIP
   vs. defer entirely and rely on the labeled range-proxy fallback from
   the prior document) — including authorizing its cost if pursued.
4. **Confirm provisioning a Windows environment/VM** for the Norgate
   Updater sync step (§7, §11).
5. **Review of Norgate's actual licensing terms** once obtained (§9, §11
   step 2) — a follow-up confirmation, not a new open-ended decision, but
   flagged as needing to come back to the Controller if anything material
   is found.
6. Everything already listed as pending in the prior document's decision
   table (per-parameter approvals, portfolio-risk-limit sequencing,
   Phase 2→3 transition) — **unchanged and still pending**, not resolved
   by this document.

---

No data was downloaded, no subscription purchased, no code written, no
dependency added, and no live system touched while producing this
document. TSLA was not used, referenced, or implied as any form of
fallback, baseline, or default anywhere in this document. D-0026 remains
PROPOSED / NOT APPROVED. Phase 2 remains research/design + (pending this
document's approval) data-acquisition scope only. Phase 3 remains NOT
approved.
