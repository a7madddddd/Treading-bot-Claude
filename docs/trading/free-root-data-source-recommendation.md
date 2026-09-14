# D-0026 — Free Root Data Source Recommendation (Phase 2 revision)

**Status: PROPOSED / NOT APPROVED.** This document **supersedes**
`root-data-source-recommendation.md`'s Norgate recommendation per explicit
Controller instruction: **no paid data provider or subscription of any
kind.** Norgate, Databento, Polygon/Massive, Tiingo (paid tiers), and every
other commercial option are **rejected** — none will be purchased.

Phase 2 remains **research/design only** at this step: no data has been
downloaded, no code written, no dependency added, nothing live touched.
Phase 3 remains explicitly **NOT approved**. This document is committed
only after the full analysis below, per the Controller's instruction.

---

## 1. Executive recommendation

**No single free provider matches Norgate's completeness.** The honest,
evidence-based answer is a **combination architecture**, each piece
selected for what it verifiably does well, with every gap disclosed
rather than glossed over:

- **FREE ROOT SOURCE (bulk historical OHLCV + current whole-market symbol
  universe):** **Stooq**, cross-validated against **Yahoo Finance (via
  `yfinance`)** as a backup/cross-check, both free, both bulk-capable, both
  carrying real licensing caveats disclosed in §18.
- **FREE SECONDARY SOURCES:**
  - **SEC EDGAR** (official, U.S. government, zero-cost, no API key) —
    company identity (CIK), SIC industry code, corporate filings evidence
    of name/ticker changes, mergers, and (via a community-maintained,
    SEC-EDGAR-sourced dataset) delisted-company identification.
  - **Nasdaq Trader Symbol Directory** (official, free, anonymous FTP) —
    current whole-market symbol/reference list including an ETF flag.
  - **FRED (Federal Reserve Economic Data)** — official, free, API-keyed
    but at zero cost — for the market-regime volatility series (VIX).
  - A small, manually-curated, zero-cost list of known leveraged/inverse
    ETF issuers (unavoidable regardless of provider, established in the
    prior document).
- **LOCAL DATA STORE:** a normalized local canonical dataset (§14),
  built once via bulk ingestion, that the offline calibration engine
  consumes — never querying Stooq/Yahoo/SEC/etc. directly at
  calibration time.
- **UNRESOLVED DATA GAP:** **free, systematic, bulk, pre-delisting OHLCV
  price history for delisted securities could not be confirmed available
  from any researched free source.** We can identify *which* securities
  were delisted and *when* (free, via SEC EDGAR-derived data), but not
  confidently retrieve their price history *before* delisting in bulk,
  for free, from a verified source. This is the single most important
  honest finding of this document — see §6, §21.

**Direct answer to the Controller's central question:**

> **Can we perform statistically credible D-0026 historical calibration
> using only free data?**
>
> **YES, WITH LIMITATIONS.**

Explained in full in §22, but in short: free sources can support the core
strategy-mechanics calibration (Ladder/Floor/Trailing behavior against
*currently-tradable* names' full price history) and the market-regime,
sector-approximation, and spread-proxy pieces reasonably well. What free
sources **cannot** confidently support is the **survivorship-bias-free**
tier that the prior calibration methodology (`historical-data-
calibration-plan.md §4`) flagged as **Tier 1 — mandatory before any
numeric parameter reaches APPROVED**. Under a free-only architecture,
calibration evidence should be treated as **labeled, survivorship-biased,
directional evidence** — useful, real, but explicitly **capped below the
confidence level needed for final numeric approval** until this specific
gap is closed (by a future paid subscription, a stronger free source we
haven't yet found, or an explicit Controller decision to accept the
limitation).

---

## 2. All genuinely free candidate sources researched

| # | Source | Category |
|---|---|---|
| 1 | SEC EDGAR (submissions API, company facts API, company_tickers.json, bulk ZIP archives) | Official/government, no auth |
| 2 | Community-maintained SEC-EDGAR-derived delisted-stocks dataset (GitHub) | Open dataset, derived from official source |
| 3 | Nasdaq Trader Symbol Directory (FTP) | Official/exchange, no auth |
| 4 | Stooq | Free bulk CSV, no official API |
| 5 | Yahoo Finance (via `yfinance`) | Free, unofficial endpoint |
| 6 | Alpha Vantage (free tier) | Free API, heavily rate-limited |
| 7 | FRED (Federal Reserve Economic Data) | Official/government, free API key |
| 8 | Kenneth French Data Library | Free, academic |
| 9 | SIC→sector/GICS-approximate public mapping tables (Fintel.io, GitHub gists) | Free reference data |
| 10 | FINRA public data | Investigated, see §2.1 |
| 11 | Other exchange/reference datasets | Investigated where relevant, see notes below |

### 2.1 FINRA public data — investigated, limited relevance

FINRA publishes some public market data (e.g., short-interest data, OTC
transparency data), but nothing found in the research for this document
provides bulk historical OHLCV, delisted-universe reconstruction, or
sector classification comparable to the sources above. **Not incorporated
as a primary or secondary source** — no evidence of a capability gap this
document needs it to fill.

### 2.2 Other free/open datasets investigated

- **AlgoSeek / EODHD / Finaeon-style "survivorship-bias-free" datasets:**
  confirmed to exist and to directly target this exact problem — but **all
  are paid products**, explicitly rejected per the Controller's
  instruction. Mentioned here only to be transparent that purpose-built
  free-of-charge equivalents were searched for and not found.
- **QuantConnect / QuantRocket data libraries:** referenced in search
  results as aggregators of paid datasets (including AlgoSeek) — not
  independently free sources in themselves.

---

## 3. Official source / documentation links

- SEC EDGAR APIs: [SEC.gov | EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces); bulk archives: `https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip` and `https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`; company ticker file: `https://www.sec.gov/files/company_tickers.json`
- Community delisted-stocks dataset (SEC-EDGAR-sourced): [GitHub — BlackFalconData-org/delisted-stocks-list](https://github.com/BlackFalconData-org/delisted-stocks-list)
- Nasdaq Trader Symbol Directory: [Symbol Look-Up/Directory Data Fields & Definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs); files via anonymous FTP at `ftp.nasdaqtrader.com`, `SymbolDirectory/nasdaqlisted.txt` and `otherlisted.txt`
- Stooq: [Stooq.com](https://stooq.com) (CSV download interface; no official API documentation found)
- Yahoo Finance / yfinance: [yfinance documentation](https://ranaroussi.github.io/yfinance/)
- Alpha Vantage: [Alpha Vantage support / rate limits](https://www.alphavantage.co/support/)
- FRED: [St. Louis Fed FRED API docs](https://fred.stlouisfed.org/docs/api/fred/); VIX series: [CBOE Volatility Index: VIX (VIXCLS)](https://fred.stlouisfed.org/series/VIXCLS)
- Kenneth French Data Library: [Dartmouth Tuck — Ken French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)
- SIC codes: [SEC SIC code reference](https://app.edgar.tools/tools/sic-codes) (free, no key)
- SIC→GICS-approximate mapping: [Fintel.io Industry List (Based on SIC Codes)](https://fintel.io/industry); [GitHub Gist — GICS code mapping](https://gist.github.com/uknj/c9bcf66ab379a35fcc8758f9a6c86ceb)
- Licensing-caveat cross-reference for Stooq/Yahoo/Tiingo/Alpha Vantage: [ASSIP 2026 — Free Equity Price APIs Data Card](https://edwardlg.github.io/assip-2026-empirical-finance/textbook/data-cards/free-equity-apis.html)

---

## 4. Cost = $0

Every source recommended in this document's architecture (§20) is free of
direct monetary cost:

- SEC EDGAR: $0, no account required, no API key.
- Nasdaq Trader Symbol Directory: $0, anonymous FTP.
- Stooq: $0, no account required for CSV download.
- Yahoo/yfinance: $0, no account required.
- FRED: $0, free registration for an API key (no cost, no commercial-use
  fee found in the research performed).
- Community delisted-stocks GitHub dataset: $0, open repository.
- Kenneth French Data Library: $0, no registration.
- SIC→sector mapping tables: $0.

**Note, restated from the prior document's licensing discipline:** "$0
cost" is not the same as "unrestricted usage rights" — see §18 for the
specific caveats that come with several of these sources despite being
free of charge.

---

## 5. Coverage matrix

Each candidate against the Controller's A–T checklist. `✅` confirmed
available; `⚠️` partially available / unconfirmed / caveated; `❌`
confirmed unavailable or not found in research.

| Requirement | SEC EDGAR | Nasdaq Trader | Stooq | Yahoo/yfinance | Alpha Vantage (free) | FRED | Ken French |
|---|---|---|---|---|---|---|---|
| A. Historical OHLCV | ❌ (not a price-data source) | ❌ (reference only) | ✅ | ✅ | ⚠️ (rate-limited to unusability at scale) | ❌ (not equity prices) | ❌ (factor returns, not per-symbol OHLCV) |
| B. Historical volume | ❌ | ❌ | ✅ | ✅ | ⚠️ | ❌ | ❌ |
| C. Historical quotes/spread | ❌ | ❌ | ❌ (not confirmed) | ❌ (not confirmed) | ❌ | ❌ | ❌ |
| D. Historical trades | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| E. Corporate actions | ⚠️ (inferable from filings, not a clean feed) | ⚠️ (daily list, current/near-term only) | ⚠️ (prices likely split-adjusted, not separately documented) | ⚠️ (same) | ❌ | ❌ | ❌ |
| F. Delisted securities | ✅ (via filings existing pre-delisting) | ❌ (current list only) | ⚠️ (unconfirmed retention) | ⚠️ (unconfirmed retention) | ❌ | ❌ | ❌ |
| G. Survivorship-bias-free history | ⚠️ (identity yes, price history unconfirmed) | ❌ | ⚠️ (unresolved) | ⚠️ (unresolved) | ❌ | N/A | N/A |
| H. Point-in-time universe membership | ⚠️ (reconstructable from filing dates, labor-intensive, not a ready-made feed) | ❌ (current snapshot) | ❌ | ❌ | ❌ | N/A | N/A |
| I. Symbol/ticker changes | ✅ (former-name history in submissions data) | ⚠️ (daily list, current/near-term) | ❌ (not confirmed) | ❌ (not confirmed) | ❌ | N/A | N/A |
| J. Stable instrument identity | ✅ (**CIK** — genuinely strong, see §8) | ❌ (ticker only) | ❌ (ticker only) | ❌ (ticker only) | ❌ | N/A | N/A |
| K. Sector/industry classification | ✅ (SIC code, not GICS) | ❌ | ❌ | ⚠️ (Yahoo UI shows sector; not confirmed via bulk API) | ❌ | N/A | N/A |
| L. ETF identification | ❌ | ✅ (ETF flag documented in symbol directory field definitions) | ⚠️ (inferable from name/ticker patterns, not a clean flag) | ⚠️ (same) | ❌ | N/A | N/A |
| M. Leveraged/inverse ETF ID | ❌ | ❌ | ❌ | ❌ | ❌ | N/A | N/A |
| N. Historical depth | N/A | N/A | ⚠️ (varies by symbol, not uniformly documented) | ⚠️ (varies) | ⚠️ (claimed 15+ yrs for active names) | ✅ (VIX back to ~1990) | ✅ (decades) |
| O. Whole-market/bulk access | ✅ (bulk ZIP archives) | ✅ (full directory file) | ✅ (CSV per-symbol, scriptable across the whole list) | ⚠️ (per-symbol, scriptable but unofficial/fragile) | ❌ (rate limit prohibits bulk) | ✅ (per-series, small number of series needed) | ✅ (small number of files) |
| P. API/rate limits | ⚠️ (courtesy rate limit, User-Agent required) | ✅ (no documented limit for the static files) | ⚠️ (undocumented, no official API) | ⚠️ (unofficial, throttling observed) | ❌ (25/day — unusable at scale) | ✅ (generous, documented) | ✅ (file downloads, no meaningful limit) |
| Q. Build a local historical DB once | ✅ | ✅ | ✅ | ⚠️ (fragile due to unofficial endpoint) | ❌ (impractical at this rate limit) | ✅ | ✅ |
| R. New symbols handled automatically | ✅ (new CIKs appear in ongoing SEC filings) | ✅ (directory updated daily) | ⚠️ (assumed, not confirmed for very recent IPOs) | ⚠️ (same) | N/A | N/A | N/A |
| S. Licensing/usage restrictions | ✅ (public government data, minimal restriction) | ✅ (public reference data) | ⚠️ (personal/non-commercial use; no official published TOS found) | ⚠️ (personal use only, explicitly stated) | ⚠️ (free-tier TOS applies) | ✅ (public federal data) | ✅ (academic, freely shared) |
| T. Reliability/maintenance | ✅ (official, actively maintained) | ✅ (official, actively maintained) | ⚠️ (community-relied-upon, no official support) | ⚠️ (explicitly described as prone to breakage) | ✅ (officially maintained, just rate-limited) | ✅ (official, well-maintained) | ✅ (academically maintained) |

---

## 6. Survivorship-bias analysis (the hardest requirement — answered honestly)

**Direct question:** can free sources provide "historical point-in-time
US equity/ETF universe including securities that later became delisted,"
with their full pre-delisting price history?

**Answer, decomposed into its two separate parts:**

### Part 1 — "Which securities existed and were later delisted, and
when" — **YES, reasonably well, for free.**

- SEC EDGAR is the authoritative, official, free record of every company
  that has filed with the SEC, including companies no longer active.
- The community-maintained GitHub dataset
  (`BlackFalconData-org/delisted-stocks-list`) explicitly aggregates
  **36,000+ delisted stocks from NYSE, NASDAQ, and OTC**, sourced from
  **daily SEC EDGAR updates since 2002**, including ticker, CIK, filing
  dates, and exchange — this is a free, open, SEC-EDGAR-derived dataset
  that directly targets exactly the "which symbols got delisted and when"
  question.
- This is a genuinely useful, free, and (because it is SEC-EDGAR-sourced
  rather than a private proprietary compilation) reasonably verifiable
  resource.

### Part 2 — "The actual historical OHLCV price series for those
delisted securities, before their delisting date, in bulk, for free" —
**NOT CONFIRMED. This is the honest, disclosed gap.**

- SEC EDGAR itself is a **filings** repository, not a price-data source —
  it does not provide OHLCV bars at all.
- Neither Stooq nor Yahoo Finance has a documented, official policy
  confirming they systematically retain full price history for delisted
  securities. General industry practice (per research into how paid
  providers like EODHD describe their own delisted-data handling) is that
  delisted-name price history is often **selectively** retained by
  free/community sources, not systematically guaranteed — some tickers'
  data disappears from public-facing free tools once delisted, others
  remain accessible, with no documented, verifiable rule governing which.
- **This document does not claim the opposite of what was found.** No
  search result confirmed that Stooq or Yahoo systematically preserves
  delisted-security price history the way Norgate (the rejected paid
  option) explicitly does as a core, documented product feature.

### Conclusion — stated plainly, per the Controller's explicit instruction

**Free sources can identify the delisted-universe membership problem
(Part 1) but cannot be confirmed to solve the delisted-price-history
problem (Part 2) at the same completeness level as the rejected paid
option.** This is not a minor caveat — it is the single most consequential
limitation of an all-free architecture, and it directly determines the
confidence ceiling of any calibration built on it (§22).

### The strongest free approximation, quantified

The best available free approximation is:

1. Build the canonical `Instrument`/`InstrumentHistory` tables (§14) using
   SEC EDGAR + the community delisted-stocks dataset, so **at minimum**
   the calibration process **knows** which candidates existed historically
   and were later delisted (closing the "invisible survivors" blind spot
   at the *identity* level, even if not at the *price-history* level).
2. For each identified delisted symbol, **attempt** to retrieve price
   history from Stooq/Yahoo on a case-by-case basis during ingestion,
   **logging explicitly, per symbol, whether pre-delisting price history
   was actually retrievable or not** — this converts an unknown,
   silently-biased gap into a **measured, disclosed statistic**: e.g.,
   "of the 36,000+ known-delisted symbols, price history was retrievable
   for X% via free sources; the calibration's delisted-name coverage is
   therefore X%, not 100%, and this percentage is reported alongside
   every result."
3. This measured percentage becomes part of the evidence package (per
   `historical-data-calibration-plan.md §13`, item 8 — evidence presented
   for review, not just a conclusion) — the Controller sees the actual
   coverage rate, not an assumed "we solved survivorship bias" claim.

**This is honest, bounded, disclosed partial mitigation — not a solution,
and not pretended to be one.**

---

## 7. Point-in-time universe analysis

Beyond the delisted-symbol question (§6), full point-in-time universe
membership (e.g., "was symbol X actually tradable, actively listed, and
not halted, on date D specifically") is a stronger requirement than
"symbol X existed at some point and was delisted eventually."

- **Nasdaq Trader's Symbol Directory** gives a **current-day snapshot**
  only — it is explicitly described as reflecting "the current trading
  day." It does **not**, by itself, provide a historical archive of what
  the directory looked like on a past date.
- **SEC EDGAR filing dates** can approximate listing/delisting windows
  (a company's filing history has a start and, where applicable, an end),
  but reconstructing exact **tradability** windows (as opposed to
  **SEC-registration** windows, which are related but not identical) from
  filings alone is **labor-intensive and imperfect** — it was not found
  to exist as a ready-made, downloadable feed anywhere in the free sources
  researched.
- **No free source was found that provides a ready-made, point-in-time
  index/universe constituency table** (Norgate's explicitly named
  differentiator, per the rejected paid-option research). This is a
  **second, related but distinct** disclosed gap from §6's price-history
  gap.

**Conclusion:** free sources support a reasonable **approximate**
reconstruction of "was this company an SEC-registered, presumably
tradable US equity around date D" via filing-date ranges, but this is a
**weaker, more labor-intensive, and less precise** substitute than a
purpose-built point-in-time index-constituency product. Flagged
explicitly, not silently accepted as equivalent.

---

## 8. Symbol identity strategy

**Positive finding: SEC EDGAR's CIK (Central Index Key) is a genuinely
strong, free, official, persistent identifier**, and this is a real
strength of the free architecture, not merely a fallback.

- The CIK is assigned once per SEC registrant and **persists across
  ticker changes, name changes, and most corporate restructurings** —
  this is precisely the property a stable canonical identity needs.
- SEC submissions data includes **former names** associated with a CIK,
  giving a free, official record of name-change history.
- **Recommended design (consistent with the canonical-dataset approach
  already established in the rejected-provider document):** use CIK as
  the anchor identity for any US equity/ETF that is an SEC registrant
  (the overwhelming majority of our relevant universe), with our own
  internally-generated surrogate key used **only** as a fallback for the
  rare case of an instrument without a clean CIK mapping (verify during
  ingestion, not assumed never to occur).
- **Caveat, disclosed:** CIK identifies the **legal registrant**, not
  necessarily the **trading ticker** at every point in time — the mapping
  from CIK to ticker-at-date must still be constructed (via submissions
  data plus price-source ticker history), not assumed to be free of
  ambiguity. This mapping-construction step is real work, not a solved
  problem merely because CIK exists.

---

## 9. Corporate-action strategy

- **No dedicated, clean, free corporate-actions feed was confirmed**
  (unlike Alpaca's or Polygon's documented — if imperfect — Corporate
  Actions products).
- **Recommended free approach:** rely on **split-adjusted price series**
  from Stooq/Yahoo (both are widely understood in the quant community to
  provide split-adjusted closes, though this was not independently
  confirmed via an official documentation page in the research performed
  for this document — **flagged for empirical verification in Phase 2's
  representative-sample step**, not assumed).
- **Secondary confirmation source:** SEC 8-K filings (which disclose
  material corporate events including some splits, mergers, and name/
  ticker changes) can serve as an independent cross-check for a sample of
  known corporate actions during data validation (§11's "validate against
  known facts" discipline, carried over from the rejected-provider
  document's Phase 2 execution plan).
- **Explicit gap:** a comprehensive, machine-readable, free corporate-
  actions calendar (dividends specifically) was **not** confirmed to exist
  at the completeness level a paid product offers. Dividend-adjustment
  accuracy in the free architecture should be treated as **unverified
  until spot-checked**, not assumed correct.

---

## 10. Sector/ETF classification strategy

- **Sector:** SEC EDGAR provides the **SIC code** for every registrant —
  free, official, but explicitly **not GICS** (SEC's own data has no
  sector field at all, only SIC — confirmed directly from research, not
  assumed). A free SIC→sector-approximate mapping (e.g., the Fintel.io
  public mapping or the GICS-mapping GitHub gist found in research) can
  translate SIC codes into a coarser sector grouping. **This must be
  labeled an approximation of true GICS, not equivalent to it** — SIC and
  GICS are different classification systems with documented
  inconsistencies between them (this is a well-known limitation in the
  finance-data field generally, not specific to our situation).
- **ETF identification:** Nasdaq Trader's Symbol Directory field
  definitions **document an ETF flag** in the symbol directory files —
  this gives a free, official way to distinguish ETFs from common stocks
  for the currently-listed universe (current-snapshot limitation from §7
  still applies for historical ETF-status reconstruction).
- **Leveraged/inverse ETF identification:** **no free source provides
  this as a queryable field** — unchanged from the rejected-provider
  document's finding. The recommended mitigation is unchanged: a small,
  manually-curated list of known leveraged/inverse issuers and fund
  families (ProShares, Direxion, GraniteShares, etc.), compiled from
  those issuers' own public fund lists at zero cost — this remains true
  regardless of which data architecture (paid or free) is chosen, so it
  is not a *new* cost imposed by rejecting Norgate.

---

## 11. Historical OHLCV strategy

- **Primary attempt:** Stooq, via its free CSV download interface,
  scripted across the whole target symbol list (built from Nasdaq
  Trader's current directory plus the SEC-EDGAR-derived historical/
  delisted symbol list from §6). Stooq is widely relied upon by the
  independent-quant community for exactly this kind of bulk historical
  pull, per multiple third-party sources found in research — but **no
  official Stooq documentation of coverage completeness or update
  reliability was found**, so this reliance is disclosed as
  community-precedent-based, not vendor-guaranteed.
- **Cross-validation / gap-filling:** Yahoo Finance via `yfinance`, used
  to (a) fill gaps where Stooq's data for a given symbol/date-range proves
  incomplete, and (b) spot-check a sample of Stooq's own data for
  consistency, per the same "verify, don't assume" discipline used
  throughout this document.
- **Both sources carry the same core caveat:** neither has a clearly
  documented official policy on depth of history per symbol, and both
  are described as "personal/non-commercial use" in their terms (§18) —
  this must inform how the resulting canonical dataset is used and
  potentially shared, not just how it's built.

---

## 12. Historical spread strategy

**Unchanged from the earlier calibration methodology document's finding,
now confirmed to remain unresolved under an all-free architecture:** no
free source researched provides true historical bid-ask quote data at the
completeness needed to abandon the range-proxy approach.

- **Recommended approach, unchanged in substance:** use the **high-low
  daily range as an explicitly labeled approximation** of execution
  difficulty (per `historical-data-calibration-plan.md §3`'s full
  analysis — that section's reasoning is not repeated here, only
  reaffirmed as still applicable under the free-only constraint).
- **What's different now:** the rejected paid document (§1.7 there) had
  identified Databento/Alpaca-SIP as potential **true-quote** secondary
  sources. Under an all-free architecture, **that option is off the
  table** unless the Controller separately authorizes a paid secondary
  source specifically for spread data in the future — which this document
  does **not** propose, consistent with the "no paid provider" instruction.
  The range-proxy is therefore not a fallback anymore; **it is the
  primary and only spread-quality signal available under this
  architecture**, and calibration results dependent on spread quality
  should be labeled accordingly (lower confidence than they would carry
  with true quote data).

---

## 13. Market-regime data strategy

**Strong, fully free, and arguably an improvement over the rejected
document's proposal here.**

- **FRED's VIXCLS series** (CBOE Volatility Index) is official,
  U.S.-government-hosted, free (API key required but at zero cost, no
  commercial restriction found), well-documented, and has history back to
  approximately 1990 — comfortably covering multiple real market regimes,
  including well-known historical stress periods.
- This is **better** than the rejected document's fallback proposal
  ("realized volatility computed on a broad-market index ETF's own
  bars," used there because a true implied-vol series wasn't confirmed
  available) — VIX **is** the actual implied-volatility benchmark the
  finance industry uses to define "high volatility," and it's free.
- **Recommended design, consistent with the earlier calibration
  methodology's §6 recommendation to validate regime definitions
  independently and non-circularly:** use VIX percentile-relative-to-
  trailing-history as the primary regime signal (the *form* recommended
  in the earlier document), now backed by a confirmed, free, authoritative
  data source rather than a computed proxy.
- **Supplementary:** a broad-market drawdown series, computed from the
  same Stooq/Yahoo OHLCV pull (§11) on a market-index-tracking instrument,
  for the drawdown-based regime dimension the earlier document's §6 also
  recommended alongside pure volatility.

---

## 14. Local canonical dataset architecture

**Unchanged in structure from the rejected-provider document's §5
design** (the entity design there was intentionally provider-agnostic) —
restated here with the free-source-specific identity anchor from §8:

```
SEC EDGAR + Nasdaq Trader + Stooq/Yahoo + FRED + community delisted list
        ↓
Ingestion (source-specific readers, one per free provider)
        ↓
Validation (schema checks, gap detection, cross-source consistency —
    e.g., does Stooq's price series roughly match Yahoo's for the same
    symbol/date, per §11's cross-validation design)
        ↓
Normalization (canonical field names/types; CIK-anchored identity per §8)
        ↓
Canonical Historical Dataset
        ↓
Calibration Engine (provider-agnostic from this point on — never queries
    Stooq/Yahoo/SEC/FRED directly at calibration time, per the Controller's
    explicit architectural requirement)
```

### Entities (same list as the rejected document, with free-source-specific notes)

- **`Instrument`** — keyed by CIK where available (§8), surrogate key
  fallback otherwise.
- **`InstrumentHistory`** — ticker/name-at-date, sourced from SEC
  submissions' former-names data plus observed ticker usage in the
  price-history pull; **explicitly weaker** point-in-time fidelity than
  the rejected paid option, per §7's disclosed limitation.
- **`DailyBar`** — from Stooq (primary) / Yahoo (cross-check + gap-fill),
  with a `source` field recording which provider supplied each row, so
  discrepancies remain traceable rather than silently blended.
- **`QuoteSpreadHistory`** — **not populated from a true source under
  this architecture** (§12); the table exists in the schema for future
  use if a true-quote source is ever authorized, but calibration
  currently relies entirely on the labeled range-proxy computed from
  `DailyBar`.
- **`CorporateAction`** — inferred from split-adjusted price discontinuity
  detection plus SEC 8-K cross-checks (§9); **lower-confidence** than a
  dedicated corporate-actions feed, flagged accordingly.
- **`SectorClassification`** — SIC code (direct from SEC) plus a derived
  approximate-sector field (via the SIC→sector mapping, §10), both stored
  so the approximation is never presented as if it were the primary
  authoritative field.
- **`ETFClassification`** — from Nasdaq Trader's ETF flag (§10, current-
  snapshot limitation noted) joined against the manually-curated
  leveraged/inverse exclusion list.
- **`TradabilityHistory`** — **the weakest table under this architecture**
  (§7) — approximated from SEC filing-date ranges and the delisted-list's
  delisting dates, not a true point-in-time tradability feed. Calibration
  results depending heavily on this table's precision should be flagged
  as lower-confidence.
- **`MarketRegimeData`** — FRED VIXCLS (primary) plus a computed drawdown
  series from `DailyBar` on a market-index instrument (§13) — this table
  is **stronger**, not weaker, than in the rejected-provider design.
- **`DataCoverageLog`** — **new table, specific to this free
  architecture**, recording per-symbol, per-dataset-type, whether data
  was successfully retrieved (e.g., "delisted symbol X: identity known
  via SEC/GitHub list = yes; pre-delisting price history retrieved via
  Stooq = no"). This operationalizes §6's "measure and disclose the
  actual coverage rate" recommendation as a first-class part of the
  canonical dataset, not an afterthought.

**No schema (column types, file formats, storage engine) is specified
here — design only, consistent with the same scope boundary the rejected-
provider document used.**

---

## 15. Bulk ingestion strategy

- **SEC EDGAR:** bulk ZIP archives (`companyfacts.zip`, `submissions.zip`)
  are explicitly designed for exactly this use case — "the most efficient
  means to fetch large amounts of API data," recompiled nightly by SEC
  itself. This is **genuinely bulk**, not per-symbol.
- **Nasdaq Trader Symbol Directory:** the full directory files
  (`nasdaqlisted.txt`, `otherlisted.txt`) are single-file, whole-market
  pulls via anonymous FTP — genuinely bulk.
- **Community delisted-stocks GitHub dataset:** a single repository clone/
  download gives the full 36,000+-symbol list at once — genuinely bulk.
- **Stooq:** **not natively a single bulk endpoint** — per-symbol CSV
  download, but **scriptable across the whole target symbol list** built
  from the sources above, which is a legitimate (if less elegant than a
  true bulk API) bulk-ingestion pattern, and is how the independent-quant
  community is documented to actually use it.
- **Yahoo/yfinance:** same pattern as Stooq — per-symbol, scriptable
  across the whole list, with the added fragility risk noted in §11/§18.
- **FRED:** a handful of named series (VIXCLS, plus whatever supplementary
  series are chosen) — trivially small request volume, not a bulk concern
  at all.

**Overall assessment: bulk, whole-universe ingestion is realistic** for
every piece of this architecture, though Stooq/Yahoo require scripting a
loop over a known symbol list rather than a single bulk file download —
a real but modest difference from SEC EDGAR/Nasdaq Trader's true
single-file bulk pattern, not a blocker.

---

## 16. How new symbols are handled automatically

- A new IPO/listing appears in **Nasdaq Trader's daily-updated symbol
  directory** and in **SEC EDGAR's ongoing filings** (a newly public
  company files a registration statement, generating a new CIK) —
  **both free sources naturally surface new symbols without any manual
  per-symbol search**, directly satisfying requirement R.
  - **Caveat:** Stooq/Yahoo's own coverage-start lag for a brand-new
    listing is unconfirmed (§5, §11) — flagged for empirical verification
    during Phase 2's representative-sample step, not assumed instant.
- A newly-delisted symbol appears in the community GitHub dataset's
  **daily SEC EDGAR updates** — automatic, not manual, consistent with the
  same principle.
- **The architecture as a whole satisfies the Controller's core
  requirement** ("we should not need to search for a new data source
  every time a new symbol appears") **for symbol discovery specifically**
  — the unresolved gap (§6) is about **historical price-data
  completeness** for delisted names, not about needing a *new source* to
  find out that a new symbol exists.

---

## 17. Rate-limit and reliability strategy

- **SEC EDGAR:** free, no API key, but **the SEC enforces a courtesy rate
  limit and requires a descriptive `User-Agent` header** identifying the
  requester — this is a real operational requirement to build into any
  ingestion script (design note for Phase 2 implementation, not
  implemented here), not a blocker.
- **Nasdaq Trader FTP:** no documented rate limit found for the static
  directory files; low request volume needed (a handful of file pulls),
  low risk.
- **Stooq:** no official API, no documented rate limit — **recommended
  practice: conservative, throttled request pacing** during the bulk
  symbol-loop pull, to avoid triggering informal blocking (a real risk
  with any undocumented free service under sustained bulk load, even
  without a published limit).
- **Yahoo/yfinance:** documented as prone to throttling and endpoint
  changes without notice — **recommended as the secondary/cross-check
  source, not the primary**, specifically because of this reliability
  risk (§11's design already reflects this).
- **Alpha Vantage:** 25 requests/day, 5/minute on the free tier — **far
  too restrictive for full-universe bulk ingestion** (thousands of
  symbols would take years at this rate) — **not part of the bulk
  architecture**; retained only as a possible tiny-sample validation
  cross-check (a handful of symbols), consistent with its actual usable
  capacity.
- **FRED:** documented as having generous, well-published rate limits;
  low risk given the small number of series needed here.

---

## 18. Licensing/legal caveats — stated plainly, not glossed over

- **Stooq:** based on the evidence found (an academic data-card resource
  comparing exactly these free APIs), Stooq's terms are understood to
  **permit personal/non-commercial use**, with **no clean official
  redistribution license** confirmed, and explicit guidance from that
  source not to publish/redistribute the raw downloaded bytes as a
  dataset (publish code + pinned pull date instead, if ever shared).
  **No official Stooq terms-of-service page was directly located** in
  this research — this finding rests on a third-party academic summary,
  not a primary-source Stooq document, and should be **independently
  re-verified against Stooq's actual site** during Phase 2 before
  building any ingestion dependent on it.
- **Yahoo Finance / yfinance:** **more clearly documented and more
  restrictive** — Yahoo shut down its official public API in 2017;
  `yfinance` uses **unofficial** endpoints; the data is described as
  **"intended for personal use only,"** and using it for anything beyond
  personal/research use carries **explicit legal and operational risk**
  per multiple independent sources.
- **Our actual use case** (an internal, non-commercial, non-redistributed
  paper-trading calibration research project) is very likely consistent
  with "personal/research use" as commonly understood in these terms —
  **but this document is not a legal opinion**, and the Controller should
  treat this as a disclosed risk to accept knowingly, not a resolved
  non-issue. **Recommendation: do not redistribute, republish, or sell
  any dataset built from Stooq/Yahoo data; keep it strictly internal to
  this research project**, consistent with how the academic source
  describes acceptable use.
- **SEC EDGAR, Nasdaq Trader, FRED, Kenneth French Data Library:** all
  are **public official/government or academic sources** with
  **materially lower licensing risk** — no restrictive "personal use
  only" language found for any of these in the research performed.
- **Alpha Vantage free tier:** standard free-tier API terms apply; not a
  concern at the trivial usage volume this architecture would actually
  use it for (§17).

---

## 19. Known gaps

Consolidated list (each already detailed above, gathered here for
visibility):

1. **Pre-delisting OHLCV price history for delisted securities** — not
   confirmed available in bulk, for free, from any source (§6, the
   central finding of this document).
2. **True point-in-time index/universe constituency** — no free
   equivalent to Norgate's named feature found (§7).
3. **True historical bid-ask quotes/spread** — unresolved, range-proxy
   remains the only signal (§12).
4. **Comprehensive dividend/corporate-actions calendar** — not confirmed
   at paid-product completeness (§9).
5. **Leveraged/inverse ETF flag** — no free source provides this; curated
   list required regardless of architecture (§10, unchanged from the
   rejected-provider finding).
6. **Stooq's official documentation, coverage completeness, and terms of
   service** — not directly located; reliance is on community precedent
   and a third-party academic summary, flagged for independent
   re-verification (§18).
7. **Ticker-history reconstruction precision** — weaker than a
   purpose-built point-in-time product; relies on SEC former-names data
   plus observed price-source ticker usage, which is more
   labor-intensive and less complete (§7, §14).

---

## 20. Recommended FREE architecture

```
FREE ROOT SOURCE:          Stooq (bulk historical OHLCV + volume,
                            whole current + partially-historical universe)

FREE SECONDARY SOURCE(S):  SEC EDGAR (CIK identity, SIC sector, former
                              names, delisted-symbol list via the
                              community SEC-EDGAR-derived GitHub dataset)
                            Nasdaq Trader Symbol Directory (current
                              whole-market symbol/ETF reference)
                            Yahoo Finance / yfinance (cross-validation
                              and gap-fill for Stooq's OHLCV)
                            FRED (VIXCLS market-regime series)
                            Manually-curated leveraged/inverse ETF list
                              (zero-cost, small, maintained list)

LOCAL DATA STORE:          Normalized canonical dataset (§14), CIK-
                            anchored identity, built once via bulk
                            ingestion (§15), consumed exclusively by the
                            offline calibration engine — no direct
                            provider queries at calibration time

UNRESOLVED DATA GAP:       Free, systematic, bulk pre-delisting OHLCV
                            price history for delisted securities is
                            NOT confirmed available. Mitigated, not
                            solved, via a measured, disclosed coverage
                            statistic (the DataCoverageLog table, §14)
                            rather than a silent assumption of
                            completeness.
```

---

## 21. What cannot be solved for free

Stated directly, per the Controller's explicit instruction not to pretend
otherwise:

- **A Norgate-equivalent, fully survivorship-bias-free, price-history-
  complete, point-in-time-accurate historical database cannot be
  confidently assembled from free sources alone**, based on the research
  performed for this document. The identity/existence side of the
  delisted-universe problem (§6 Part 1) is solvable for free; the
  price-history side (§6 Part 2) and the point-in-time-constituency side
  (§7) are not confidently solvable for free.
- **True historical bid-ask spread/quote data** cannot be obtained for
  free at the completeness a dedicated tick-data product offers.
- **A comprehensive, verified corporate-actions calendar** cannot be
  confidently assembled for free at paid-product completeness.

These are not framed as reasons to abandon the free architecture — they
are framed as **explicit, quantifiable confidence limits** that must
travel with any calibration evidence this architecture produces (§22).

---

## 22. What evidence we would need before trusting calibration

Building on `historical-data-calibration-plan.md §13`'s acceptance
checklist (unchanged, still the governing standard), the free-architecture
adds **one additional, mandatory disclosure requirement**:

12. **Delisted-symbol price-history coverage rate must be measured and
    reported** (via the `DataCoverageLog` table, §14) alongside every
    calibration result. A calibration run on, say, 40% delisted-price
    coverage carries a materially different confidence level than one on
    90% coverage, and the Controller must see this number explicitly, not
    infer it.

### Answering the central question directly

> **Can we perform statistically credible D-0026 historical calibration
> using only free data?**

**YES, WITH LIMITATIONS.**

**Why not a clean "YES":** the survivorship-bias mitigation (§6) is
partial and measured, not complete — any calibration evidence produced
under this architecture should be treated, per the earlier methodology
document's own Tier system (`historical-data-calibration-plan.md §4`), as
**below Tier 1** (which required genuine point-in-time universe history)
unless the measured delisted-price-coverage rate (§22, item 12) turns out
to be high enough, empirically, to be judged sufficient — **that judgment
itself requires running the ingestion and measuring the actual rate,
which has not been done, per the "no data downloaded yet" Phase 2
boundary this document respects.**

**Why not a flat "NO":** the free architecture **does** solve the
strategy-mechanics calibration core (`historical-data-calibration-plan.md
§8`) reasonably well for the *currently-tradable* universe (whose full
price history back through multiple regimes is very likely obtainable for
free, per §11), and it **does** solve the market-regime data requirement
**better** than the rejected paid document's own fallback proposal (§13).
The specific parameters most affected by the survivorship-bias limitation
are the ones directly touching universe composition over time (§7.1,
§7.3, §7.4 of `universe-selection-analysis.md`'s parameter catalog); the
core Ladder/Floor/Trailing mechanics testing (§8 of the calibration
methodology) is **less** exposed to this specific gap, because it operates
on whatever candidates the (partially survivorship-biased) universe
contains, not on a claim about the *completeness* of that universe.

**Practical recommendation:** proceed with the free architecture for
Phase 2, **explicitly labeling every resulting piece of evidence with its
measured coverage statistics**, and let the Controller decide — once real
numbers exist, not before — whether the resulting confidence level is
sufficient for numeric parameter approval, or whether closing the
remaining gap eventually requires revisiting the "no paid provider"
constraint for this specific, narrow purpose. **This document does not
make that future recommendation now** — it is explicitly out of scope
given the Controller's current instruction, and would only become a live
question after Phase 2 produces real, measured results.

---

## 23. Phase 2 execution plan (revised for the free architecture)

1. **Confirm this free-architecture recommendation** (or the Controller's
   revision of it) — the immediate next decision point.
2. **Independently re-verify Stooq's and Yahoo's actual current terms of
   use** directly from their own sites (§18's flagged gap) before relying
   on either for ingestion.
3. **Define the canonical data model** — take §14's entity design to an
   actual schema (field names, types, file format) — design work, not
   data acquisition.
4. **Acquire a small representative sample** — a handful of currently-
   active names, a handful of confirmed-delisted names (from the GitHub
   dataset), across SEC EDGAR + Stooq + Yahoo + FRED, to empirically test
   coverage and format before committing to a full pull.
5. **Measure the delisted-price-history coverage rate on the sample**
   (§22, item 12) — this is the single most important early empirical
   step, since it directly determines how much confidence the eventual
   calibration evidence can carry.
6. **Validate corporate-action handling** on the sample against known
   facts (a known split, a known dividend) per §9's cross-check design.
7. **Validate symbol identity** — confirm the CIK-anchoring approach
   (§8) actually works cleanly against the sample, including at least one
   known ticker-change case.
8. **Acquire the full bulk dataset** — SEC EDGAR bulk archives, Nasdaq
   Trader full directory, the full community delisted-stocks list, and
   the scripted Stooq/Yahoo pull across the resulting whole symbol list.
9. **Measure the full-dataset delisted-price-history coverage rate** —
   the sample-level measurement (step 5) repeated at full scale, since a
   small sample may not represent the true rate.
10. **Add FRED market-regime data and the leveraged/inverse ETF exclusion
    list.**
11. **Build validation checks** — systematic gap/outlier detection and
    Stooq-vs-Yahoo cross-consistency checks, as part of the ingestion
    pipeline, not one-off manual review.
12. **Build the offline calibration engine** — implementing the frozen
    strategy simulation and walk-forward/regime methodology already
    specified in `historical-data-calibration-plan.md`.
13. **Run calibration, explicitly reporting coverage statistics alongside
    every result** (§22).
14. **Produce the evidence package** per that document's §13 checklist,
    now extended with the coverage-rate disclosure (§22, item 12).
15. **Review parameters with the Controller** — a recommendation, not an
    automatic approval, unchanged from the rejected-provider document's
    same final step.

**Explicitly not part of this sequence:** any live connection, any Phase
3 activity, any purchase of a paid data source.

---

## 24. Phase 2 exit criteria (revised for the free architecture)

Phase 2 is complete, and numeric-parameter approval becomes a live
question, only when **all** of the following hold:

1. This document's free-architecture recommendation confirmed or revised
   by the Controller.
2. Stooq's and Yahoo's terms of use independently re-verified (§18, §23
   step 2).
3. Canonical dataset schema implemented and validated against the
   representative sample (§23 steps 3-4).
4. **Delisted-price-history coverage rate measured and reported** — not
   assumed, not estimated, **measured** — at both sample and full-dataset
   scale (§23 steps 5, 9).
5. Corporate-action and symbol-identity handling validated against known
   facts (§23 steps 6-7).
6. Full bulk dataset acquired and validated (§23 step 8, 11).
7. Market-regime data (FRED VIXCLS + computed drawdown) integrated.
8. Calibration engine validated against the frozen, unmodified approved
   strategy logic (per `historical-data-calibration-plan.md §8`,
   unchanged).
9. Walk-forward methodology implemented, single untouched holdout
   preserved (per that document's §5, §12, unchanged).
10. Sensitivity testing completed (per that document's §11, unchanged).
11. **Evidence package produced, explicitly including the measured
    coverage-rate disclosure** — this is the one criterion materially
    different from the rejected-provider document's exit criteria,
    reflecting the free architecture's specific, honestly-disclosed
    limitation.

**Only after all eleven conditions are met** does any individual
parameter's PROPOSED → APPROVED question become live — and even then,
each parameter is still evaluated individually against the full evidence,
per the Controller's standing instruction, never approved automatically.

---

## Comparison: free architecture vs. the rejected Norgate solution

| Dimension | Norgate (rejected, paid) | Free architecture (this document) |
|---|---|---|
| Cost | $630/year | $0 |
| Delisted-symbol identity | Complete, core product feature | Reasonably complete (SEC-EDGAR-derived GitHub dataset) |
| Delisted-symbol **price history** | Complete, core product feature | **Unconfirmed / partial — the central gap** |
| Point-in-time index/universe membership | Named, documented feature | Not available; approximated via filing-date ranges, materially weaker |
| Sector classification | Included (proper classification) | SIC code + approximate mapping, not true GICS |
| Historical depth | 30+ years | Unconfirmed uniform depth; likely adequate for current names, uncertain for delisted names |
| Bulk access | Yes, whole-market watchlists | Yes, but Stooq/Yahoo require scripting per-symbol across a known list, not a single bulk file |
| Operational model | Windows-native local database (a real but manageable friction) | Standard REST/FTP/CSV pulls, fewer OS constraints |
| Licensing risk | Commercial data license, clear terms | Mixed — SEC/Nasdaq/FRED low-risk; Stooq/Yahoo "personal use" caveats requiring care |
| Calibration confidence ceiling | Could plausibly reach Tier 1 (full point-in-time fidelity) per the earlier methodology document | **Capped below Tier 1** until the delisted-price-history coverage rate is measured and judged sufficient — an open, unresolved question |

**What we lose by refusing paid data, stated plainly:** primarily,
**confidence in the survivorship-bias mitigation** — the free
architecture can approximate and measure this gap, but not close it with
the same certainty a purpose-built paid product provides. Every other
piece of the architecture (OHLCV depth for currently-active names, market
regime data, sector approximation, symbol identity via CIK) is either
comparable to or, in the market-regime case, arguably **better** than the
rejected paid document's own design.

---

No data was downloaded, no code was written, no dependency was added, and
no live system was touched while producing this document. No paid
provider was purchased or authorized. TSLA was not used, referenced, or
implied as any form of fallback, baseline, or calibration shortcut
anywhere in this document. D-0026 remains PROPOSED / NOT APPROVED. Phase 2
remains research/design scope; this document itself has not yet triggered
any actual data acquisition — that begins only after Controller
confirmation of the architecture recommended in §20. Phase 3 remains NOT
approved.
