# Research Tools Integration Plan (D-0050 — PROPOSED)

**Status:** PROPOSED / NOT YET IMPLEMENTED
**Author:** Claude (as per Controller directive 2026-10-01)
**Scope:** Integrate 5 dormant API keys (FRED, Perplexity, Polygon,
Finnhub, Tiingo, Alpha Vantage) into the trading system to improve
research quality and proposal context. Paper trading only.

---

## 1. Current state — what the live engine uses today

| Role | Source | Code |
|------|--------|------|
| Market data (quotes + bars) | Alpaca IEX feed | `AlpacaMarketDataSource`, `AlpacaFeatureEnricher` |
| Universe assets list | Alpaca `/v2/assets` | `AlpacaAssetsProvider` |
| Trading execution | Alpaca paper broker | `AlpacaBrokerClient` |
| Notifications + decisions | Telegram | `TelegramNotificationService`, `TelegramDecisionSource` |
| Regime | **hardcoded placeholder** `vix_percentile=0.5` | `scripts/run_universe_selection.py:135` |
| News / research | **Not wired to live engine** | `src/research/perplexity_agent.py` exists but only `scripts/run_research_cycle.py` uses it |

Dormant keys (set in env, zero references in `src/`):
`FINNHUB_API_KEY`, `FRED_API_KEY`, `TIINGO_API_KEY`,
`POLYGON_API_KEY` (+ S3), `ALPHA_VANTAGE_API_KEY`.

Perplexity key is used but ONLY in offline research cycle, not in
the proposal/trading loop.

## 2. Architectural fit — the integration pattern

The system is deliberately layered. Each integration must respect:

- **Deterministic risk engine owns final decisions** (CLAUDE.md §5).
  Research outputs are advisory ONLY — never auto-execute.
- **Fail-open on external APIs.** A down Perplexity/FRED/Polygon must
  not crash the engine or block a proposal. The system falls back to
  existing Alpaca data.
- **Interface-based substitution.** New data sources implement
  existing abstract interfaces (`MarketDataSource`, `FeatureEnricher`,
  etc.) so the engine's own code doesn't change.
- **Change-control per CLAUDE.md §9.** Each integration is a separate
  commit with tests. Trading-behavior changes are optional, flagged
  default-off, and only promoted after Controller approval.

## 3. Target architecture — where each source plugs in

```
                               ┌─────────────────────────────────┐
                               │  Engine (src/engine/engine.py) │
                               │  — unchanged by this plan —    │
                               └──────┬──────────────────────────┘
                                      │
                     ┌────────────────┼────────────────────┐
                     ▼                ▼                    ▼
            MarketDataSource    FeatureEnricher     ProposalContext
            ─────────────────   ─────────────────   ─────────────────
            default: Alpaca      Alpaca (bars)       NEW: Perplexity
            ADD opt: Polygon     + Finnhub (fund)    research summary
                                 + Alpha Vantage     attached to the
                                 (RSI/MACD)          Telegram message

                                      ▲
                                      │ consumes
                     ┌────────────────┴────────────────────┐
                     ▼                                     ▼
               RegimeState                        UniverseSelectionConfig
               ─────────────                      ──────────────────────
               NEW: FRED-derived                  OFFLINE: Tiingo-driven
               vix_percentile +                   calibration (D-0026
               rate + yield curve                  Phase 2 unlock)
```

## 4. Integration order — priority ranked

Priority formula: `(value × safety) / effort`.

| # | Source | What it adds | Risk | Effort | Priority |
|---|--------|--------------|------|--------|----------|
| 1 | FRED | Real regime detection (VIX, DFF, T10Y2Y) | LOW (advisory stage, fail-open) | 2 h | **CRITICAL** |
| 2 | Perplexity in Telegram | News summary in proposals | LOW (fail-open, advisory) | 2 h | **HIGH** |
| 3 | Finnhub | Fundamentals + sentiment on enriched features | LOW (new enricher, chain-of-responsibility) | 3 h | HIGH |
| 4 | Alpha Vantage | RSI/MACD/Bollinger on enriched features | LOW (same pattern as #3) | 3 h | MEDIUM |
| 5 | Polygon | SIP market data (replaces IEX) | MEDIUM (critical path change) | 4 h | MEDIUM |
| 6 | Tiingo | Offline historical calibration of D-0026 cuts | LOW (offline only) | 5 h | DEFERRED |

Items 1-4 ship together on the same evening. Item 5 ships separately
after validation. Item 6 is a weekend research task.

## 5. Per-integration specification

### 5.1 FRED Regime (Priority 1)

**New file:** `src/marketdata/fred_source.py`
- Public: `class FredSource` with `get_series(series_id, start_date,
  end_date) -> List[(date, value)]`.
- Series needed:
  - `VIXCLS` — VIX close (daily)
  - `DFF` — Federal Funds effective rate (daily)
  - `T10Y2Y` — 10Y-2Y Treasury spread (daily)
- Endpoint: `https://api.stlouisfed.org/fred/series/observations`
- Rate limit: 120 req/min. Retry via existing `common.http_retry`.
- Transport injectable (same pattern as `AlpacaFeatureEnricher`).
- Fail-open: any error returns `[]` and caller falls back to
  placeholder `vix_percentile=0.5`.

**New file:** `src/d0026/regime_classifier.py`
- Public: `classify_regime(fred_source, as_of_date) -> RegimeState`
- Logic:
  - Fetch last 252 trading days of VIX
  - `vix_percentile = percentileofscore(history, today_vix) / 100`
  - If error, return the placeholder RegimeState (fail-open).
- Reference values become real: `("vix_percentile", 0.63)`,
  `("vix_level", 18.4)`, `("fed_funds_rate", 4.33)`, `("yield_curve_spread", -0.21)`.
- Label changes from `UNCLASSIFIED_PENDING_CALIBRATION` → derived
  (`RISK_ON` if vix_pct ≤ 0.33, `RISK_OFF` if ≥ 0.66, else `NEUTRAL`).

**Model change:** `src/d0026/models.py` — extend `RegimeLabel` enum
with `RISK_ON`, `RISK_OFF`, `NEUTRAL` (adds enum values, existing
code paths unaffected).

**Wire change:** `scripts/run_universe_selection.py`
- Replace the hardcoded regime block with
  `regime = classify_regime(FredSource.from_env(), effective)`
- Env var `FRED_API_KEY` already set.

**Testing:**
- `tests/marketdata/test_fred_source.py` — 5 tests (success, 429,
  schema error, timeout, empty series).
- `tests/d0026/test_regime_classifier.py` — 6 tests (high/mid/low
  VIX, insufficient history, API error fallback, cutoffs).

### 5.2 Perplexity in Telegram (Priority 2)

**Use existing:** `src/research/perplexity_agent.py` (unchanged).

**New file:** `src/engine/proposal_enricher.py`
- Public: `enrich_proposal_message(symbol, perplexity_agent) -> str`
- Returns `""` on failure (so the base message is still sent).
- Short Perplexity query: `"Latest news (last 24h) on {symbol}:
  catalysts, sentiment, risks. 3 sentence summary."`
- Timeout 10s. One retry.

**Wire change:** `src/engine/engine.py`
- Add constructor arg `proposal_enricher: Optional[Callable[[str],
  str]] = None`.
- In `_start_new_trade` and `_create_ladder_proposal`, if enricher is
  set, call it and append its return to the notification `message`.
- Default `None` — engine behavior unchanged when enricher not wired.

**Wire change:** `scripts/run_paper_session.py`
- Build enricher from Perplexity, pass to Engine.

**Testing:**
- `tests/engine/test_proposal_enricher.py` — 5 tests (success append,
  empty on error, timeout fail-open, no-enricher default, unchanged
  message when None).

### 5.3 Finnhub Fundamentals (Priority 3)

**New file:** `src/d0026/finnhub_enricher.py`
- Mirrors `AlpacaFeatureEnricher` interface: callable
  `(candidate, as_of_date, regime_state) -> UniverseCandidate`
- Adds fields (via extending `DailySecurityFeatures` or an adjacent
  "FundamentalFeatures" dataclass):
  - `pe_ratio`, `eps_ttm`, `revenue_growth_yoy`, `news_sentiment_7d`
- Endpoints: `/stock/metric?metric=all`, `/news-sentiment`
- Rate limit: 60 req/min. Chunked with existing retry policy.
- Composed with Alpaca enricher via chain-of-responsibility.

**Model change:** Add optional fields to `DailySecurityFeatures`
(backward-compatible — default None).

**Testing:**
- 6 tests covering metric fetch, sentiment fetch, both missing,
  rate-limit retry, bad JSON, enricher chain composition.

### 5.4 Alpha Vantage Technicals (Priority 4)

**New file:** `src/d0026/alpha_vantage_enricher.py`
- Fetches `RSI`, `MACD`, `BBANDS` for a symbol.
- Rate limit: 5 req/min on free tier → caching aggressive.
- Only runs once/day per symbol (same as D-0026 pipeline cadence).

**Testing:** 5 tests, same pattern as Finnhub.

### 5.5 Polygon Market Data (Priority 5 — ships separately)

**New file:** `src/marketdata/polygon_source.py`
- Implements `MarketDataSource` interface.
- `get_last_trade(symbol) -> float` → `/v2/last/trade/{symbol}`
- `get_price_context(symbol) -> dict` → `/v2/aggs/ticker/{symbol}/prev`
- Rate limit: free tier 5 req/min; paid tier handles ~100.
- Fail-open: if Polygon returns error, raise `MarketDataUnavailableError`
  (engine's existing fallback path).

**Wire change:** `scripts/run_paper_session.py`
- New arg `--market-data-source {alpaca,polygon}`, default `alpaca`.

**Testing:** 7 tests + 1 interface-conformance test matching
`AlpacaMarketDataSource`.

### 5.6 Tiingo Calibration (Priority 6 — deferred)

Pure offline. New standalone script `scripts/calibrate_universe.py`
reads 5 years of Tiingo daily bars for ~3000 symbols, computes
percentile cutoffs that today sit as hardcoded defaults in
`UniverseSelectionConfig`. Writes a proposed config to
`docs/trading/calibration-results.md` for Controller review BEFORE
promotion. Does NOT change `UniverseSelectionConfig` automatically.

## 6. Testing matrix — zero-bug guarantees

Every new module ships with:
- Minimum 5 unit tests covering success + 3 failure modes + rate-limit.
- Interface-conformance test if replacing an existing abstraction.
- `fail_open` test — API down → caller receives a safe default,
  engine keeps running.

Each integration PR must pass:
- `pytest -q` — full suite green (current: 1116; target per PR +5-10).
- Smoke test: run `scripts/run_paper_session.py --max-hours 0.02`
  with the new source enabled, confirm engine starts, heartbeat
  updates, no new CRITICAL notifications.
- Dry-run of the universe selection with real APIs (not mocks) to
  confirm the external services respond as the mocks assumed.

After all 4 priority-1-to-4 integrations ship, run a **full regression
sweep** (the explicit Controller requirement) using the same 6-lens
audit protocol from the 2026-10-01 Phase 3 deep audit:

1. Path exercise — trace each Controller-visible action end-to-end
2. Config-vs-wiring — grep every D-00XX contract against production
3. State-invariant — find paths that set one half of a required pair
4. Race/timing — ordering assumptions that break under restart
5. Boundary / edge-case — 0 shares, 0.01 price, exact :30 boundary
6. xhigh code-review as a supplement only

## 7. Rollback plan per integration

Each integration is OFF by default. Enabling requires:
- Env var or `--flag` present
- Successful smoke test on paper account

If a bug surfaces live:
- Set the opt-out env var / drop the `--flag` on next engine restart
- Code path reverts to prior Alpaca-only behavior
- No DB migration involved → zero data risk

## 8. Deliverables (in commit order, same evening)

1. `src/marketdata/fred_source.py` + tests
2. `src/d0026/regime_classifier.py` + tests + model enum extension
3. `scripts/run_universe_selection.py` wire change
4. `src/engine/proposal_enricher.py` + tests
5. `src/engine/engine.py` enricher wiring (backward-compat default-off)
6. `scripts/run_paper_session.py` wire enricher
7. `src/d0026/finnhub_enricher.py` + tests
8. `src/d0026/alpha_vantage_enricher.py` + tests
9. Deep audit + fix (6-lens protocol)
10. Telegram notification: "Integration complete"

Polygon (Priority 5) + Tiingo (Priority 6) are a separate deliverable
after items 1-9 are stable in production for 24 hours.

## 9. Safety guardrails

- Paper trading only (CLAUDE.md §6). No live order path touched.
- Trading strategy itself (D-0004/D-0008) unchanged. Only INPUTS to
  the deterministic risk engine improve.
- Research outputs stay advisory. Perplexity summaries go in the
  Telegram message for Controller review, NOT into an auto-approval
  path.
- Rate limits respected per source (documented per section).
- Secrets stay in env vars; nothing committed.

---

## Appendix A — Why NOT to merge research sources

Per `docs/architecture/research-sources.md §3`: "Do NOT blindly merge
outputs from different sources." Each new enricher produces fields on
`DailySecurityFeatures` in its OWN namespace. The synthesis layer
reads them, finds overlap/contradictions, and surfaces the labeled
disagreement to the Controller via Telegram — never silently averaged.
