# Research Sources — Perplexity + Capitol Trades (D-0019)

Perplexity and Capitol Trades are **independent** research sources.
Neither is treated as automatically correct; neither directly submits
trades; both feed the research synthesis layer for Controller review.

---

## 1. Sources

### A. Perplexity Agent API

- Web-grounded LLM research
- Named operations: `researchMarket`, `researchStock`, `researchNews`,
  `researchStrategy`, `researchRisk`, `researchBacktestingMethod`
- Returns structured JSON: question, summary, findings, evidence,
  sources, risks, confidence, recommendation, suggested next
  experiment

### B. Capitol Trades (Ro Khanna, extensible)

- Deterministic scrape of https://www.capitoltrades.com
- No LLM inside this source
- Returns structured records: politician, security, ticker,
  transaction type, disclosed trade date, publication date,
  transaction size/range, source URL, extraction timestamp,
  parse confidence, validation status
- Fragile: HTML on a third-party site can change silently — health
  check must alarm if the schema breaks

## 2. Flow

```
        ┌──────────────┐         ┌──────────────┐
        │  Perplexity  │         │ CapitolTrades│
        └──────┬───────┘         └──────┬───────┘
               │                        │
               ▼                        ▼
        ┌──────────────────────────────────────┐
        │  Findings (typed, timestamped)       │
        │  FACT / SOURCE / INFERENCE /         │
        │  HYPOTHESIS / RECOMMENDATION         │
        └──────────────┬───────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────┐
        │  Research synthesis                  │
        │   - correlate                        │
        │   - flag contradictions              │
        │   - name missing info                │
        │   - propose experiments              │
        └──────────────┬───────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────┐
        │  research-log.md / experiments.md    │
        └──────────────┬───────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────────┐
        │  Controller review                   │
        │   - accept as informational          │
        │   - promote to experiment            │
        │   - promote to policy (rare)         │
        │   - reject                           │
        └──────────────────────────────────────┘
```

## 3. Comparison rules

- Do NOT blindly merge outputs from Perplexity and Capitol Trades.
  Perplexity summarizes text; Capitol Trades reports a filing. They are
  different evidence types.
- When both cover the same ticker, produce a **comparison record**:

  ```
  ticker: XYZ
  capitol_trades: { politician, direction, date_disclosed, size_range, source_url }
  perplexity:    { recent_events, sentiment, notable_risks, sources }
  overlap:       { …facts both sources agree on… }
  contradictions: [ …divergences… ]
  missing:        [ …questions neither source answered… ]
  hypothesis:     "…" (labelled HYPOTHESIS)
  confidence:     LOW / MEDIUM / HIGH (with justification)
  suggested_experiment: "…"
  ```

- Findings labelled RECOMMENDATION are advisory. They flow into
  `experiments.md`, not straight into policy.

## 4. What research MUST NOT do

- Submit any Alpaca order.
- Modify approved policy files (`docs/trading/strategy.md`,
  `execution.md`, `risk-management.md`).
- Silently overwrite prior findings (append to `research-log.md`).
- Fabricate sources or extraction times.

## 5. Health / integrity

- Capitol Trades scraper must record `parse_confidence` per record.
- If the parser can't identify any of the standard fields (politician,
  ticker, direction, dates) in the current HTML, emit a CRITICAL
  notification: "Capitol Trades parser broken" — and stop, do not
  fabricate.
- Perplexity errors (timeouts, refusals) are logged and reported
  IMPORTANT-level; the routine continues without inventing a summary.

## 6. Future extensibility

Additional research sources (news APIs, SEC EDGAR, insider filings,
alt-data) plug in the same way: their output is a typed, timestamped
findings record; the synthesis layer treats them independently.

## 7. Perplexity transport (Agent API)

- **Endpoint:** `POST https://api.perplexity.ai/v1/responses`.
- **Legacy endpoint NOT used:** the older OpenAI-compatible path
  `POST /chat/completions` was deprecated by Perplexity in favor of the
  Agent API. Any code that references `/chat/completions` for this
  project is an error and must be rejected in review.
- **Request shape (minimum):**
  ```json
  {
    "model": "<supported-agent-model>",
    "input": "<prompt string or structured input>"
  }
  ```
  The exact list of supported model names is decided at implementation
  time by reading Perplexity's current Agent API documentation, not by
  guessing. Model choice is recorded in the adapter's config, not
  hard-coded across the codebase.
- **Auth:** `Authorization: Bearer $PERPLEXITY_API_KEY` (env var name
  unchanged; see `.env.example`).
- **Error handling:** any HTTP 4xx/5xx from `/v1/responses` is treated
  the same as any other Perplexity error under §5 above — logged,
  IMPORTANT-level notification, and the routine continues without a
  fabricated summary. A response with `code: agent_api_migration_required`
  is CRITICAL and blocks the routine, because it indicates the endpoint
  itself changed again.
- **No live trading path depends on Perplexity.** Consistent with
  `CLAUDE.md §5–6`, Perplexity output remains advisory and never
  overrides the deterministic risk engine or the active protective floor.

## 8. Historical calibration data sources (D-0042)

Distinct from §1's research sources (Perplexity + Capitol Trades),
which are advisory-only and text-shaped, the calibration data
sources listed here are quantitative and are used to derive numeric
parameters for the D-0026 universe pipeline. Adoption per D-0042.

### 8.A Primary group — calibration workhorses

- **Alpaca historical bars.** `data.alpaca.markets/v2/stocks/{symbol}/bars?feed=sip`
  serves daily OHLCV for currently-listed US equities from 2016-01-04
  to present on the professional SIP feed, at 200 requests/minute,
  using our existing paper keys.
- **`eliangcs/pystock-data` on GitHub (CC BY-SA 4.0).** Daily OHLCV
  2009-01 to 2017-03. Includes a partial slice of names that were
  later delisted, giving the earliest anti-survivorship-bias slice.
- **`fja05680/sp500` on GitHub (MIT).** Point-in-time S&P 500
  constituency 1996-2026. The anti-survivorship-bias overlay for
  any backtest that reconstructs a historical universe.
- **`datasets/finance-vix` on GitHub (PDDL).** Daily VIX 2004-2026.
  Feeds the Stage E regime signal in the D-0026 pipeline.

### 8.B Support group — cross-validation and official records

- **SEC EDGAR.** Official filings including 8-K item 3.01
  delisting notices. Requires a descriptive `User-Agent`
  identifying the caller (probe verified via Apple's CIK 320193).
  Used for authoritative delisting dates and corporate-action
  records; not a price source.
- **Yahoo Finance v8 chart.** `query1.finance.yahoo.com/v8/finance/chart/{symbol}`
  serves daily OHLCV for currently-listed symbols with a browser
  `User-Agent`. Used for cross-validation of Alpaca prices and
  as a fallback during Alpaca outages. Delisted symbols return
  404 (verified for BBI and FDO); Yahoo is not a delisted-price
  source.
- **Nasdaq Trader symbol directory.** Daily active-symbol list
  (pipe-separated CSV, columns Symbol, Security Name, Market
  Category, Test Issue, Financial Status, Round Lot Size, ETF,
  NextShares). Used for the "is this symbol tradable today"
  question and for building a daily active roster.
- **Twelve Data.** Cross-check secondary. The public `demo` key
  returns real recent data; a free registration lifts the limit
  to 8 requests/minute and 800/day. Used only when Alpaca and
  Yahoo disagree on a specific bar.

### 8.C Deferred sources (not part of the design today)

Kept available as future backups without further work:
Alpha Vantage (25 requests/day free), Tiingo (end-of-day only
free), Polygon.io (5 requests/minute free), Finnhub (one year
history free), FRED (macro-only, not equity). Stooq is the single
source genuinely blocked from the current cloud environment (probe
returned an immediate timeout).

### 8.D Residual gap disclosed

Symbols delisted between 2018 and 2026 have no price series
available from any of the free cloud sources above. SEC EDGAR
can supply the delisting date but not the pre-delisting price
series. Every calibration or backtest report produced under this
architecture MUST state this limit; no result is presented as if
the gap were closed.

### 8.E What is NOT allowed here

- No paid provider (D-0028's rejection preserved).
- No source whose reachability from the deployment environment is
  unverified; the probe evidence for each source in §8.A and §8.B
  above lives in D-0042's Context section.
- No adoption of a source that would silently overwrite the
  survivorship-bias disclosure in §8.D.
