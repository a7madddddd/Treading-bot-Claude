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
