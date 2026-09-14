# D-0026 — GitHub-Native Data Sources (Working, Verified Replacement Path)

**Status: PROPOSED / NOT APPROVED.** Still Phase 2. Phase 3 remains NOT
approved. This document reorients the data-acquisition plan around
sources this environment can **actually reach**, per the Controller's
explicit instruction to stop attempting blocked financial-data domains
and search only GitHub/public-repository-reachable options.

**Everything in this document was empirically tested, not assumed.**
Real files were fetched, a real repository was cloned (read-only, via
this environment's sanctioned `add_repo` mechanism — not a workaround of
the network policy), real archives were extracted, and a real delisted-
security test was run against actual data. Nothing here is inferred from
search-result summaries alone where a direct check was possible.

---

## 0. What changed and why

`data-acquisition-pilot.md` established that this environment's network
egress policy blocks `stooq.com`, `sec.gov`/`data.sec.gov`,
`nasdaqtrader.com`, `stlouisfed.org`, and `finance.yahoo.com` outright —
confirmed via direct `curl` against real endpoints, not just terms pages.
**`raw.githubusercontent.com` and anonymous `git clone` of public GitHub
repositories, however, both work cleanly.** This document searches
specifically within that reachable space, per the Controller's redirect.

---

## 1. Confirmed working, verified candidates

### 1.1 Market-regime data — SOLVED via `datasets/finance-vix`

- **Repository:** [`github.com/datasets/finance-vix`](https://github.com/datasets/finance-vix)
- **Verified directly:** fetched `data/vix-daily.csv` via
  `raw.githubusercontent.com`. Real content confirmed: **9,272 rows,
  daily OHLC for the CBOE VIX from 1990-01-02 through 2026-09-11** —
  current as of this research, not stale.
- **License, verified directly from `datapackage.json`:** **Open Data
  Commons Public Domain Dedication and License v1.0 (PDDL)** — genuinely
  public domain, no "personal use only" ambiguity, no attribution
  requirement even. This is **cleaner** than every option evaluated in
  the prior documents (Stooq unresolved, Yahoo explicitly restrictive).
- **Maintenance:** part of the `datasets` GitHub organization (a
  long-established, widely-used open-data collection), with visible
  GitHub Actions automation for updates.
- **This fully replaces the FRED VIXCLS dependency** from
  `free-root-data-source-recommendation.md §13` — same underlying series,
  reachable, current, unambiguously licensed.

### 1.2 Reference/identity data — SOLVED (current-snapshot only) via `datasets` org

- **`datasets/s-and-p-500-companies`** — verified directly: real CSV with
  `Symbol, Security, GICS Sector, GICS Sub-Industry, Headquarters
  Location, Date added, CIK, Founded`. **Includes CIK directly** —
  materially useful for the identity-anchoring design in
  `data-acquisition-pilot.md §6`. License: PDDL (data), MIT/BSD (code).
- **`datasets/nasdaq-listings`** — verified reachable (HTTP 200 via
  `raw.githubusercontent.com`). License: PDDL.
- **Limitation, stated plainly:** both are **current-snapshot**
  reference lists (S&P 500 membership today, Nasdaq listings today), not
  historical point-in-time membership — this does **not** close the
  point-in-time-universe gap identified in `data-acquisition-pilot.md
  §7`; it only provides a clean, free, well-licensed **current** identity
  layer.
- **Not independently verified this pass, flagged as a candidate for
  future checking, not claimed working:** `JerBouma/FinanceDatabase`
  (reported in search results as 300,000+ symbols across equities, ETFs,
  funds, indices) and `zyhe16/top-us-stock-tickers` (reported as
  including S&P 500 constituents and industry-grouped lists) — both
  surfaced in research but not fetched/tested directly in this pass.

### 1.3 Bulk historical OHLCV — PARTIALLY SOLVED via `eliangcs/pystock-data`

This is the most consequential finding of this document, and the one
requiring the most careful, honest characterization.

- **Repository:** [`github.com/eliangcs/pystock-data`](https://github.com/eliangcs/pystock-data)
- **Verified directly:** cloned (read-only, shallow) via this
  environment's sanctioned `add_repo` mechanism — **517 MB of real,
  committed data**, not a notebook or a reference to an external
  service (contrast with §2 below).
- **Coverage, per the repository's own README (cross-checked against
  actual file contents, not taken on faith):** daily US stock prices
  from **2009-01-01 through 2017-03-31**, after which the project was
  explicitly marked unmaintained and frozen. **This is a hard ceiling —
  there is no coverage of 2017-04 onward, meaning no 2018 volatility
  spike, no 2020 COVID crash, no 2022 bear market, and nothing current.**
  This must be disclosed prominently in any calibration evidence built
  on this source, not glossed over.
- **Structure, verified directly:** an initial bulk historical batch
  (`0001_initial.tar.gz` through `0003_initial.tar.gz`, covering
  2009-01-01 to 2015-03-20) plus one small daily-incremental archive per
  trading day from 2015-03-23 through 2017-03-31 (each daily archive
  contains roughly two trading days' worth of price rows per symbol, per
  the README's own explanation — intended for split-detection, not as a
  full-history file). **Assembling a symbol's complete multi-year series
  requires concatenating the initial batch with every subsequent daily
  increment** — a real, non-trivial but entirely mechanical ingestion
  step for whenever full acquisition is authorized.
- **License, verified directly from the repository's `LICENSE` file:**
  **Creative Commons Attribution-ShareAlike 4.0 International (CC
  BY-SA 4.0).** For strictly internal, non-redistributed research use
  (our actual use case), this imposes essentially no practical
  restriction. If any derived dataset were ever published or
  redistributed, CC BY-SA would require attribution and require the
  redistributed work to carry the same license — a real constraint,
  but not one that affects internal calibration use.
- **Data provenance, per the README:** tickers from NASDAQ.com, prices
  from Yahoo Finance, financial-statement data from SEC EDGAR — all
  crawled **daily and contemporaneously between 2009 and 2017**, not
  reconstructed retrospectively. This detail matters directly for the
  survivorship-bias question (§2 below).
- **Bonus, not originally sought but genuinely present:** `reports.csv`
  in each archive contains real SEC 10-Q/10-K-derived fundamentals
  (revenue, operating income, net income, EPS, balance-sheet items, cash
  flow) — useful, unrequested, additional evidence for future work, not
  something this document recommends acting on now.

---

## 2. The critical delisted-security test — run empirically, not assumed

Per the standing discipline in this project ("do not generalize from one
successful example"), **four** real, well-known, unambiguous
2009–2017-window corporate delistings were tested by extracting the
initial 2009–2015 batch archive and grepping for each symbol's actual
price rows:

| Symbol | Company | Known outcome | Result in this dataset |
|---|---|---|---|
| **FDO** | Family Dollar Stores | Acquired/delisted mid-2015 (Dollar Tree merger) | **✅ FOUND** — 1,564 price rows, extending through 2015-03-20 (the archive's own cutoff date), consistent with FDO trading right up to shortly before its actual delisting |
| RSH | RadioShack | Bankruptcy/delisted Feb 2015 | ❌ Not found — zero rows, not even listed in `symbols.txt` |
| BBI | Blockbuster | Delisted 2010 | ❌ Not found — zero rows |
| DELL | Dell Inc. | Went private, delisted 2013 | ❌ Not found — zero rows |
| HNZ | H.J. Heinz | Acquired/delisted 2013 | ❌ Not found — zero rows |

**Honest interpretation, not overstated in either direction:**

- **This is real, positive evidence that the dataset can and does
  capture at least some delisted names' pre-delisting price history** —
  FDO is not a cherry-picked success; it was one of five names tested
  without knowing the outcome in advance.
- **It is equally real evidence that coverage is inconsistent, not
  systematic** — four of five names tested (including some of the
  best-known delistings of that era) returned nothing.
- **A plausible, disclosed (not confirmed) explanation:** the total
  price-bearing symbol universe in the initial batch was **1,980 unique
  symbols** (out of 6,121 symbols listed in `symbols.txt` — meaning even
  many *listed* symbols had zero price rows in this specific batch, a
  further data-quality nuance). This suggests the crawler's effective
  coverage was closer to a large-cap/mid-cap-oriented universe (plausibly
  S&P 1500-scale) than the full US equity market including small-caps
  and micro-caps — RSH, BBI, DELL, and HNZ may simply have fallen outside
  whatever list the crawler was working from at various points, for
  reasons not determinable from the data alone.
- **Quantified, not rounded up or down:** in this small, deliberately
  diverse 5-symbol test, **1 of 5 (20%) delisted-security price
  histories were successfully captured.** This is a real number from a
  real test, not a projection — and per the standing project discipline,
  it should **not** be extrapolated to "the dataset has ~20% delisted
  coverage overall" without a much larger sample. It is reported here as
  exactly what it is: one honest data point from a small, real
  experiment.
- **Symbol-universe growth check:** by 2017-03-31, the daily file showed
  **5,981 unique symbols** with price data that day — roughly 3× the
  initial batch's count. This suggests the crawler's effective universe
  grew substantially over the project's lifetime, meaning **later years
  (2016–2017) plausibly have broader coverage than the earliest years**
  — another honest, disclosed nuance rather than treating the dataset as
  uniform across its whole span.

---

## 3. What this means for the D-0026 calibration plan

### Directly replaces, with improvements

- **Market-regime data (FRED VIXCLS dependency):** fully replaced by
  `datasets/finance-vix` — same series, better license, verified
  current.

### Partially replaces, with a new, disclosed ceiling

- **Bulk historical OHLCV (the Stooq root-source role):**
  `eliangcs/pystock-data` can serve this role for the **2009–2017
  window only**. This is a **materially different scope** than the
  Controller-approved architecture assumed (Stooq, if its terms had
  cleared, would have offered current, ongoing coverage). Using this
  GitHub-native source instead means:
  - **Gain:** verified reachable right now, verified real data, clean
    unambiguous license, and — per §2 — genuine (if partial and
    unquantified-at-scale) delisted-security coverage, which Stooq's
    coverage for delisted names was never actually confirmed either way
    (`free-data-verification-pass.md §3D`).
  - **Loss:** **no data after March 2017.** Any calibration built on this
    source alone cannot speak to current market microstructure, recent
    regimes (2018 vol spike, 2020 COVID crash, 2022 bear market), or
    today's liquidity/spread characteristics. This must be disclosed as
    a first-class limitation in any evidence package, not a footnote.

### Still not solved by anything found in this pass

- **True point-in-time universe membership** (exact tradable set on a
  specific historical date) — unchanged, still not available from any
  source checked across all research passes.
- **True historical bid-ask spread/quotes** — `eliangcs/pystock-data`
  provides OHLCV only, same as Stooq would have; the labeled range-proxy
  remains the only spread signal.
- **Current/ongoing bulk OHLCV** (2017-04-onward) — this is a **new**
  gap introduced by pivoting away from live-source access, not present
  in the original (blocked) Stooq-based plan. Flagged explicitly as a
  tradeoff of this pivot, not hidden.
- **Comprehensive, quantified delisted-security coverage rate** — §2's
  20% (1-of-5) figure is a real but statistically small sample; the
  `DataCoverageLog` mechanism (`free-data-verification-pass.md §4,
  Control D`) should still be run at full scale once full acquisition is
  authorized, using this source's actual complete symbol universe, not
  assumed from this 5-symbol spot check.

---

## 4. Recommended path forward

```
FREE ROOT (historical OHLCV, 2009-2017 window): eliangcs/pystock-data
  - CC BY-SA 4.0, internal use unaffected by the share-alike clause
  - Verified real, 517MB, reachable via anonymous git clone
  - Requires concatenating 3 initial batches + ~500 daily increments
    per symbol to assemble full multi-year series (mechanical, not
    yet implemented)

FREE MARKET-REGIME SOURCE: datasets/finance-vix
  - PDDL (public domain), verified current through 2026-09-11
  - Directly replaces the FRED dependency

FREE REFERENCE/IDENTITY (current-snapshot only): datasets/s-and-p-500-companies,
  datasets/nasdaq-listings
  - PDDL, includes CIK directly
  - Does not solve point-in-time universe membership

STILL UNRESOLVED: true point-in-time universe membership; true spread/
  quote data; any OHLCV coverage after March 2017; full-scale delisted-
  coverage measurement (only a 5-symbol spot check has been run)
```

**This document does not authorize full acquisition.** Per the
Controller's staged-approval pattern across this entire D-0026 thread,
the next step is the Controller's decision on:

1. Whether the March-2017 coverage ceiling is an acceptable tradeoff for
   a GitHub-reachable, cleanly-licensed, already-verified source, versus
   continuing to seek a way to reach Stooq/SEC/FRED directly (e.g., via
   the Controller's own machine, as discussed in the prior pilot
   document) for current, ongoing coverage.
2. Whether to authorize extracting and canonicalizing
   `eliangcs/pystock-data`'s full contents (all initial batches + all
   daily increments, not just the small samples tested in this
   document) into the local canonical dataset design
   (`data-acquisition-pilot.md §6`).
3. Whether `JerBouma/FinanceDatabase` and `zyhe16/top-us-stock-tickers`
   (mentioned in §1.2, not yet independently verified) are worth
   checking as supplementary identity sources before finalizing the
   architecture.

**No numeric D-0026 parameter is proposed for approval by this
document.** No full-market acquisition has occurred — only a small,
targeted verification (one repository fully cloned and spot-checked; one
CSV file fetched and read) sufficient to answer the Controller's
redirect with real evidence rather than more search-result paraphrase.
The cloned repositories were removed from local disk after inspection
(`rm -rf`, confirmed) — no bulk data has been retained or committed to
this project's repository.

---

## 5. Final GitHub-native research pass (2026-09-14) — recency search

The Controller explicitly rejected `eliangcs/pystock-data` as the sole
ROOT source because its 2009–2017 ceiling is too old for modern-regime
calibration, and requested **one final pass** focused specifically on
recency (2018–2026, ideally 2010–2026+), with every candidate verified
against actual files — not search-result descriptions — including a
multi-symbol delisted test where applicable.

### 5.1 Candidates evaluated and eliminated on fit (description-level check, no clone needed)

- **`PCnslt/stock-market-data`** — a pre-market/news snapshot pipeline,
  not multi-year per-symbol OHLCV. Wrong shape for calibration. Rejected.
- **`SteelCerberus/us-market-data`** — a single aggregate S&P/SPY-proxy
  index series, and the repository's own README states "do not trust the
  data in this repo." Not per-symbol, and self-disclosed as low
  confidence. Rejected.

### 5.2 `irachex/open-stock-data` — verified via `add_repo` clone + empirical reachability test

This was the most promising candidate on paper: MIT license, an active
GitHub Actions pipeline (`update-symbols`, `update-bars-us` — daily,
UTC 22:00 Mon–Fri), symbols from the NASDAQ Screener API, bars from
AKShare, and a README describing US equity daily bars published as
Parquet assets attached to GitHub Releases (`data-us-bars` tag →
`us_bars.parquet`).

**Empirical findings, not assumed:**

- Cloned read-only via the sanctioned `add_repo` mechanism. The git tree
  itself is only 1.6 MB and contains no committed price data — only
  `symbols/*.csv` reference snapshots and Python scripts. This is
  consistent with the README's claim that bars are distributed via
  Releases, not the git tree.
- **Direct test of the documented Release-asset URL**
  (`github.com/irachex/open-stock-data/releases/download/data-us-bars/us_bars.parquet`)
  returned **HTTP 404 ("Not Found")** — a genuine GitHub response, not
  this environment's access-gate message (that gate returns a distinct
  JSON 403 body, seen separately below).
- **Direct test via `git ls-remote --tags origin` and `git ls-remote
  origin`** (both run against the already-cloned, already-authorized
  repository, so this is not a network-policy artifact): the repository
  has **zero tags** — `ls-remote` returns only `HEAD` and
  `refs/heads/main`. GitHub Releases are always backed by a tag ref. **No
  tag means no Release has ever actually been published for this
  repository**, regardless of what the README describes as the intended
  pipeline.
- The repository is otherwise genuinely active (latest commit
  2026-09-11, "data: update symbols 2026-09-11," three days before this
  research pass), and the `update-bars-us.yml` workflow file exists in
  `.github/workflows/`. So the *symbols* pipeline appears to run; there
  is no direct evidence the *bars* pipeline has ever successfully
  produced and published a retrievable artifact.
- **Conclusion for this candidate: DISQUALIFIED — not on license or data-
  quality grounds, but on data-existence grounds.** This is exactly the
  "do not overvalue GitHub hosting" risk the Controller warned against: a
  well-documented pipeline design with an MIT license is not the same
  thing as retrievable data. No delisted test was run because there is no
  reachable price data to test.
- Clone removed from local disk after inspection (`rm -rf
  /home/user/irachex`, confirmed).

### 5.3 `hanurd25/stock-data-collector` — verified via direct file fetch

- README describes a GitHub Actions job using `yfinance` to fetch
  **minute-level** prices for a small, manually-configured ticker
  dictionary (a handful of European tickers plus `AAPL`; `TSLA` appears
  only as a "how to add a ticker" example, not a confirmed-present
  symbol).
- **Direct fetch of `data/AAPL.csv`** (HTTP 200, 20.6 MB) confirms real
  data exists for at least that one symbol. Inspecting the file directly:
  columns are `Price, Close, High, Low, Open, Volume, ticker, company,
  exchange, currency, fetched_at_utc`; **earliest row is 2026-07-02
  09:30:00-04:00, latest row is 2026-09-14 14:46:00-04:00** — roughly
  **10 weeks of 1-minute bars**, starting from whenever the workflow
  first began running, not backfilled.
  - `data/TSLA.csv` returned HTTP 404 — confirms TSLA is not actually
    in the collected set, consistent with it being a documentation
    example only. (Noted only to confirm the README's own list, not
    used as a calibration input — TSLA remains test-only per D-0026 §5.)
- **Conclusion: DISQUALIFIED.** No historical depth at all (~10 weeks,
  entirely inside 2026), and only a small, ad hoc, non-broad-market
  ticker list. Cannot support any modern-regime requirement, let alone
  2018–2026. No delisted test is meaningful against a 10-week window.

### 5.4 `blumenty/stock-data-automation` — verified via README, no clone needed

- README states explicitly: generated files hold **"last 50 days"** of
  data (`data/Shazam-Stock-Info-SP500.csv` — "S&P 500 stock data
  (Polygon.io, last 50 days)"). This is an explicit **rolling 50-day
  window**, not an accumulating historical archive — each run's output
  overwrites/rolls forward, it does not build depth over time the way
  `hanurd25`'s does.
- S&P 500 data is sourced from **Polygon.io**, a paid provider — with a
  live-looking API key hardcoded in the README itself (redacted here
  deliberately; not reproduced in this document). This is a third
  party's exposed credential, not ours; it is not used, tested, or acted
  on here — noted only because a paid-provider dependency is itself
  disqualifying per the Controller's standing "no paid data provider"
  instruction (`docs/trading/free-root-data-source-recommendation.md`),
  independent of the credential-hygiene problem.
- **Conclusion: DISQUALIFIED** — not historical (50-day rolling window
  only) and dependent on a paid provider. No clone or delisted test
  warranted.

### 5.5 Comparison table

| | pystock-data | irachex/open-stock-data | hanurd25/stock-data-collector | blumenty/stock-data-automation |
|---|---|---|---|---|
| Coverage start | 2009-01-01 | N/A (no data retrievable) | 2026-07-02 | N/A (rolling 50-day window) |
| Coverage end | 2017-03-31 (frozen) | N/A | 2026-09-14 (current) | N/A |
| Modern-regime coverage (2018–2026) | **None** | None (no data) | None (only 10 weeks, all 2026) | None (50-day rolling) |
| Symbol count | 1,980 (2009) → 5,981 (2017), priced | 0 retrievable | ~6 tickers, ad hoc | ~500 (S&P 500) but not retained historically |
| ETF coverage | Not verified this pass | N/A | No (equities only, per config) | No |
| Delisted coverage | 1 of 5 tested (FDO found; RSH/BBI/DELL/HNZ absent) | Not testable (no data) | Not testable (10-week window) | Not testable (rolling window) |
| OHLCV | Yes, daily | Designed for, not delivered | Yes, 1-minute intraday | Yes, daily, but not retained |
| License | CC BY-SA 4.0 | MIT (moot — no data to license) | Not stated | Not stated; Polygon.io ToS applies to the S&P 500 half |
| Bulk availability | Yes, git-tracked, verified | No — Release pipeline undocumented as non-functional (0 tags) | Yes, git-tracked, but shallow | Yes, git-tracked, but rolling/shallow |
| Update frequency | None (frozen 2017) | Designed daily, unproven | Actually running, ~daily accumulation | Actually running, but window-limited |
| Data quality | Verified real; partial delisted coverage; universe grew 3x over life | Unknown — no data to assess | Real but trivial depth | Real but non-cumulative |
| Survivorship concerns | Partial (some delisted names present) | N/A | Cannot assess (too short) | Cannot assess (rolling) |
| Suitability for D-0026 | Historical-control only, pre-2018 | **Disqualified** — no retrievable data | **Disqualified** — no historical depth | **Disqualified** — not historical, paid-provider dependency |

### 5.6 Conclusion

**C. NO FREE GITHUB DATASET IS GOOD ENOUGH.**

None of the four new candidates investigated in this pass — the two
description-level rejects (§5.1) and the two empirically-tested
candidates that looked most promising on paper (§5.2–5.4) — provide
genuinely free, GitHub-hosted, bulk US equity/ETF OHLCV data reaching
into 2018–2026 at broad-market scale. The single candidate with an
actual designed pipeline for exactly this (`irachex/open-stock-data`)
turns out, on direct empirical test, to have never actually published
the data its README describes (zero Release tags). The two "currently
running" candidates are real but structurally unable to provide
historical depth: one only started accumulating ~10 weeks ago
(`hanurd25`), and the other explicitly discards everything older than 50
days by design (`blumenty`). `pystock-data` remains the only candidate
across all research passes in this thread with **verified, real, bulk,
multi-year, git-tracked, cleanly-licensed** US equity OHLCV — but its
2009–2017 ceiling is exactly the limitation the Controller correctly
identified as disqualifying it as a **sole** ROOT for modern-regime
calibration. It is not superseded by anything found in this pass; it
remains valid only as a **pre-2018 historical control**, not as evidence
covering the COVID crash, the 2022 bear market, or any current regime.

**The exact missing capability:** a free, GitHub-hosted, broad-market
(hundreds to thousands of symbols), multi-year (ideally 2018 or earlier
through today), point-in-time-complete (including delisted names)
US equity/ETF daily-OHLCV archive that is **both actually populated and
currently maintained**. Every candidate found either has the "actually
populated" property (pystock-data) without recency, or is actively
running without depth (hanurd25, blumenty), or has neither in practice
despite documentation implying otherwise (irachex). No candidate found
in any research pass in this thread has both properties at once, free of
charge, within this environment's reachable surface (`raw.githubusercontent.com`
+ anonymous git clone).

### 5.7 Required final output block

```
BEST FREE ROOT: eliangcs/pystock-data (unchanged from prior pass — no
  better candidate found; retained only as a pre-2018 historical control,
  NOT as a modern-regime-capable sole root)
DATE RANGE: 2009-01-01 to 2017-03-31 (frozen)
MODERN REGIME COVERAGE: NONE — no candidate found in this pass or any
  prior pass provides free, GitHub-hosted, broad-market OHLCV for
  2018-2026
SYMBOL COVERAGE: 1,980 (2009) growing to 5,981 (2017) priced symbols in
  pystock-data; no viable alternative candidate has broad-market symbol
  coverage at all
DELISTED COVERAGE: 1 of 5 tested in pystock-data (20%, small-sample, not
  extrapolated); not testable for any other candidate (no usable data)
LICENSE: CC BY-SA 4.0 (pystock-data) — fine for internal, non-redistributed
  use
MAJOR LIMITATION: no free, GitHub-hosted dataset found anywhere in this
  thread combines real historical depth with 2018-2026 recency; the
  recency gap is not solved
D-0026 CALIBRATION SUITABILITY: pystock-data can support a pre-2018
  historical-control analysis only; it cannot alone support calibration
  across pre-COVID, COVID-crash, post-COVID, and 2022-bear-market regimes,
  because it does not cover any of those periods except a partial
  pre-2018 slice

Can this dataset reasonably support our historical calibration through
modern market regimes? NO — not on its own. pystock-data can serve only
as a disclosed pre-2018 historical control alongside whatever the
Controller decides to pursue next for 2018-2026 coverage (e.g. direct
Stooq/SEC/FRED access from a non-blocked network, a paid provider under
a future explicit Controller decision, or continued search for a
GitHub-native source not yet found). This document does not recommend
either of those paths — it only reports that the free-GitHub-only search
space has been exhausted against the candidates found, without success,
for the recency requirement specifically.
```

No production code, dependency, scheduler change, live routine change,
live universe selection, or order was created while producing this
document. No strategy mechanics were changed. TSLA was not used,
referenced, or implied as any calibration baseline or fallback, except to
note (§5.3) that a candidate's own README example ticker was confirmed
absent from its actual collected data — not used as a data point.
D-0026 remains PROPOSED / NOT APPROVED. Phase 3 remains NOT approved. The
`irachex/open-stock-data` clone was removed from local disk after
inspection (`rm -rf /home/user/irachex`, confirmed). No full acquisition,
canonical dataset construction, calibration code, or numeric D-0026
parameter was authorized or created by this pass.
