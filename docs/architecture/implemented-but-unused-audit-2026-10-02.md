# Implemented-but-unused audit — 2026-10-02

Status: COMPLETE
Methodology: static grep across `src/`, `scripts/`, `tests/`,
`docs/`, `routines/`. "DORMANT in production" means the module /
feature is NOT imported from `scripts/run_paper_session.py` or
`scripts/run_universe_selection.py` (the two live engine entry
points), even if tests exercise it.

This audit was explicitly requested by the Controller on 2026-10-01:
*"I was implement so many things in the old session before I opened
this session. I think you didn't think everything you have done. So
for that, after we finish our update, I need you to research again
for the things we already implemented and didn't use it."*

---

## 1. Fully implemented, WIRED, in production

These are the components the live engine actually touches on every
paper session. Confirmed by import graph from the two production
entry points.

| Component | Where | Role |
|---|---|---|
| `src/engine/engine.py` | `src/engine/` | Main engine loop, trigger + reconciliation ticks |
| `src/engine/lock.py` | `src/engine/` | EngineLock heartbeat (split into engine_lock.sqlite) |
| `src/engine/watchlist.py` | `src/engine/` | Static + snapshot universe source |
| `src/engine/snapshot_watchlist.py` | `src/engine/` | Reads D-0048 universe snapshot |
| `src/engine/decision_source.py` | `src/engine/` | Telegram decision polling |
| `src/engine/schedule.py` | `src/engine/` | D-0021 schedule check |
| `src/execution/service.py` | `src/execution/` | ExecutionService |
| `src/execution/alpaca_broker_client.py` | `src/execution/` | Alpaca broker + SEC Rule 612 formatting |
| `src/execution/sqlite_repository.py` | `src/execution/` | Order executions repo |
| `src/marketdata/alpaca_source.py` | `src/marketdata/` | Alpaca market data |
| `src/notifications/telegram.py` | `src/notifications/` | Telegram notification service |
| `src/notifications/telegram_decision.py` | `src/notifications/` | Telegram approval transport |
| `src/orchestration/trade_proposal_service.py` | `src/orchestration/` | Proposal creation + validation |
| `src/persistence/db.py` | `src/persistence/` | SQLite bootstrap + lock-only schema |
| `src/proposals/` (all) | `src/proposals/` | Trade proposals, D-0007 revalidation |
| `src/risk/enforcer.py` | `src/risk/` | D-0047 portfolio risk gating |
| `src/risk/portfolio_snapshot.py` | `src/risk/` | Live portfolio snapshot builder |
| `src/trade/` (all) | `src/trade/` | Trade state model, serialization |
| `src/d0026/alpaca_assets_provider.py` | `src/d0026/` | Universe asset list |
| `src/d0026/alpaca_enricher.py` | `src/d0026/` | 4-feature daily enricher (B30 sector wired, Bug #2 fix) |
| `src/d0026/sector_provider.py` | `src/d0026/` | Static sector lookup |
| `src/d0026/pipeline.py` | `src/d0026/` | 8-stage universe pipeline |
| `src/d0026/stages/*.py` | `src/d0026/stages/` | All 8 stages (including regime_adaptation) |
| `src/d0026/sqlite_repository.py` | `src/d0026/` | Snapshot repo |
| `src/common/http_retry.py` | `src/common/` | Shared retry policy |

Env vars USED: `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`,
`ALPACA_BASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`TELEGRAM_ADMIN_USER_IDS`.

---

## 2. Fully implemented but NOT wired to the live engine

This is the Controller's main target — valuable prior work sitting
idle. All of these are production-grade code with tests, but no live
caller.

### 2.1 Backtesting module — `src/backtesting/` ENTIRE DIRECTORY

| File | What it does | Prod imports |
|---|---|---|
| `src/backtesting/models.py` | Backtest result dataclasses | 0 |
| `src/backtesting/simulator.py` | Single-symbol event-driven simulator | 0 |
| `src/backtesting/portfolio_simulator.py` | Full portfolio simulator with D-0047 limits | 0 |
| `src/backtesting/walk_forward.py` | Walk-forward optimizer | 0 |
| `src/backtesting/metrics.py` | Sharpe/Sortino/DD metrics | 0 |

- 27 test imports, 0 production imports.
- Entry scripts exist (`scripts/run_backtest.py`,
  `scripts/run_portfolio_backtest.py`) but neither is referenced by
  any trigger, routine, or launch command.
- **Why dormant:** no scheduled nightly backtest; Controller runs
  these ad-hoc only.
- **Effort to wire:** 2 h — add a nightly Routine that invokes
  `scripts/run_portfolio_backtest.py` over the last 30 trading days
  and posts a Telegram summary.
- **Risk:** LOW — read-only on historical bars, no live orders.
- **Recommendation:** wire a weekly Routine (Saturday) that generates
  a backtest report over the current week's universe snapshots.

### 2.2 Research module — `src/research/` (Perplexity + Capitol Trades)

| File | What it does | Prod imports |
|---|---|---|
| `src/research/perplexity_agent.py` | HTTP wrapper for Perplexity Agent API | 0 |
| `src/research/capitol_trades_scraper.py` | Scrapes CapitolTrades.com for Ro Khanna disclosures | 0 |
| `src/research/synthesis.py` | Comparison + overlap/contradiction detection | 0 |
| `src/research/sqlite_repository.py` | Research reports / capitol trades persistence | 0 |
| `src/research/log_writer.py` | Append to `docs/trading/research-log.md` | 0 |

- Fully functional offline via `scripts/run_research_cycle.py` (4
  external refs — probably docs + tests).
- Zero connection to the live engine's proposal path.
- **Why dormant:** Controller approved research as *advisory only*
  (CLAUDE.md §5); it was never agreed that the live engine would
  consult research during proposal creation.
- **Effort to wire (into Telegram proposal messages):** 3 h —
  implemented as Phase 2 of D-0050 in this same session.
- **Risk:** LOW with the default-off flag (`--enable-perplexity`).
- **Recommendation:** wire via D-0050 Phase 2 (this evening).

### 2.3 Scheduler module — `src/scheduler/`

| File | What it does | Prod imports |
|---|---|---|
| `src/scheduler/dstaware_cron.py` | TZ-aware cron scheduler (DST-safe) | 0 |

- 5 test imports, 0 production imports.
- `scripts/run_scheduler.py` exists but no trigger invokes it.
- **Why dormant:** D-0021 schedule check is inline in
  `src/engine/schedule.py`; the scheduler module was built for a
  more general cron-replacement that was superseded by the current
  Routines-based wake architecture.
- **Effort to wire:** N/A — current Routines model has replaced the
  need. Candidate for REMOVAL.
- **Risk:** LOW to remove (test-only coverage).
- **Recommendation:** DEFER. Keep code for reference; mark as
  deprecated in a follow-up decision.

### 2.4 Verification scripts — `scripts/verification/b16_use_cases.py`

Tests connectivity to all 5 "dormant" API keys:
- POLYGON_API_KEY (REST + delisted tickers)
- FRED_API_KEY (series endpoints)
- ALPHA_VANTAGE_API_KEY (free tier limits)
- TIINGO_API_KEY (daily bars)
- FINNHUB_API_KEY (fundamentals + news)

- Zero external refs (orphaned — never invoked by any trigger).
- **Why dormant:** built during D-0042 free-data calibration
  research as a one-off connectivity probe; never integrated into
  a scheduled health check.
- **Effort to wire (as a daily morning health check):** 1 h.
- **Risk:** LOW — the script is explicitly read-only, never prints
  key values.
- **Recommendation:** add a daily 08:00 ET Routine that runs this
  script and posts PASS/FAIL counts to Telegram. Catches credential
  drift early.

### 2.5 Session reporting scripts

| Script | What it does | Prod invokes |
|---|---|---|
| `scripts/session_status.py` | Read-only snapshot of current session state | 3 external refs (docs) |
| `scripts/session_summary.py` | End-of-session P&L + proposal aggregates (CSV option) | 2 external refs (docs) |

- Documented but not scheduled.
- **Why dormant:** usable by hand but not wired into any Routine
  or notification cycle.
- **Effort to wire:** 30 min each.
- **Risk:** LOW (read-only).
- **Recommendation:** add an EOD Routine (16:05 ET weekdays) that
  runs `session_summary.py --csv`, archives the CSV to Git, and
  posts the P&L line to Telegram.

### 2.6 Broker dry-run — `scripts/verification/broker_dry_run.py`

- 2 external refs (docs).
- Validates Alpaca API connectivity + account state without placing
  any order.
- **Why dormant:** developer convenience script; preflight already
  covers the same checks inside the engine.
- **Recommendation:** DEFER or REMOVE. Low value given the engine's
  preflight.

### 2.7 Dormant env vars (keys set but no code reads them in production)

| Env var | Referenced in | Live use |
|---|---|---|
| FINNHUB_API_KEY | scripts/verification/b16_use_cases.py only | NONE |
| FRED_API_KEY | scripts/verification/b16_use_cases.py only | NONE |
| TIINGO_API_KEY | scripts/verification/b16_use_cases.py only | NONE |
| POLYGON_API_KEY | scripts/verification/b16_use_cases.py only | NONE |
| ALPHA_VANTAGE_API_KEY | scripts/verification/b16_use_cases.py only | NONE |
| POLYGON_S3_ACCESS_KEY_ID / POLYGON_S3_SECRET_KEY / POLYGON_S3_ENDPOINT | nowhere | NONE |
| PERPLEXITY_API_KEY | `src/research/perplexity_agent.py` + `scripts/run_research_cycle.py` | OFFLINE ONLY |

- **Why dormant:** The Controller mentioned yesterday that these
  were meant for Phase 2 D-0026 calibration + enrichment. The
  Phase 2 calibration was documented but never run (data not
  authorized — `docs/trading/data-acquisition-pilot.md`).
- **Recommendation:** D-0050 (this session's Phase B) wires FRED,
  Perplexity, Finnhub, Alpha Vantage. Polygon stays deferred until
  the Controller decides whether to replace Alpaca IEX. Polygon S3
  stays deferred (bulk historical only, requires paid tier).

---

## 3. Partially implemented / scaffolding only

### 3.1 `src/research/synthesis.py` compared-record output

- Produces a `ComparisonRecord` with overlap/contradictions, but no
  consumer writes those to Telegram or to the proposal message.
- **Status:** fully coded with tests, but output has no sink.
- **Recommendation:** DEFER — Phase 2 of D-0050 wires only a short
  Perplexity news summary, not the full synthesis path. Full sync
  needs more design.

### 3.2 Capitol Trades data pipeline

- `src/research/capitol_trades_scraper.py` can scrape the live site.
- Persistence table exists (`capitol_trades_records`).
- Synthesis layer can compare Perplexity findings against disclosed
  trades.
- **Status:** every piece exists, no scheduled scraper, no engine
  consumer.
- **Recommendation:** DEFER. The scraping of a third-party website
  is fragile (schema can change), and the signal-to-noise of
  politician trades is debatable. Revisit after Phase B stabilizes.

---

## 4. Scheduled / triggered but not firing

### 4.1 Routines documented in `routines/` directory

The repo contains snapshots of 4 Routines that were live at some
point in a previous session:

| Directory | Live trigger id (snapshot) | Status today |
|---|---|---|
| `tsla-paper-trading-monitor/` | `trig_01NeX4pSm5jEHPXBJzCaNWkW` | **NOT in current account's trigger list** |
| `tsla-wheel-hourly-monitor/` | `trig_01581wxFHJzBnuLXJUUuQtDS` | **NOT in current account** |
| `tsla-wheel-daily-summary/` | `trig_01TxPK91QKfqtsHVgexhTxVB` | **NOT in current account** |
| `capitol-trades-copy-ro-khanna/` | `trig_01UzNbZZgcGj8Jkj9SZHJwJR` | **NOT in current account** |

- Current account only has the 4 heartbeat Routines bound to the
  new session, plus whatever D-0050 trigger is live tonight.
- The 4 historical Routines were superseded by the current engine
  architecture (watchlist + D-0021).
- **Recommendation:** mark `routines/README.md` as historical and
  remove the live-trigger-id column, OR delete the snapshot
  directories if they are confirmed obsolete.

### 4.2 Policy-alignment doc

`docs/trading/routine-policy-alignment.md` compares each historical
routine against approved strategy. Since the routines are dead,
this doc is reference-only.

---

## 5. Recommendations summary — what to wire tonight (D-0050) vs defer

**Wire tonight (Phase B of this same session):**
- ✅ FRED regime detection → `src/marketdata/fred_source.py` +
  `src/d0026/regime_classifier.py`
- ✅ Perplexity news in Telegram → `src/engine/proposal_enricher.py`
- ✅ Finnhub fundamentals → `src/d0026/finnhub_enricher.py`
- ✅ Alpha Vantage technicals → `src/d0026/alpha_vantage_enricher.py`

**Wire this weekend (follow-up Routines, no new code):**
- 📅 Daily credential health check via b16_use_cases.py (08:00 ET)
- 📅 End-of-session summary via session_summary.py (16:05 ET weekdays)
- 📅 Nightly backtest via run_portfolio_backtest.py (Saturday 09:00 UTC)

**DEFER (needs Controller approval before building):**
- 🤔 Polygon market data (replaces Alpaca IEX, higher cost)
- 🤔 Tiingo historical calibration (needs auth to acquire data)
- 🤔 Capitol Trades scheduled scraper (signal quality questionable)
- 🤔 Full research synthesis in Telegram (needs design)

**REMOVE candidates (if Controller agrees):**
- 🗑 `src/scheduler/` module (superseded by Routines)
- 🗑 `scripts/verification/broker_dry_run.py` (preflight covers it)
- 🗑 `routines/` snapshot directories (if confirmed obsolete)
