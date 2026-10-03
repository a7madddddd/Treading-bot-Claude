# Session Resume Guide — Complete Chat Inventory

**Last update:** 2026-10-03 (final — includes Capitol Trades + E2E fix)
**Branch:** `claude/youthful-goodall-4cr0ei`

This document captures EVERYTHING built across the entire chat
(not just the last session). Use it to resume in a fresh session
with full context.

---

## 🏗️ CORE TRADING ENGINE (pre-existing, verified)

- D-0004 Ladder strategy (-5% / -8% buy triggers)
- D-0008 Trailing floor (+10% activation, +5% ratchet)
- D-0007 Approval workflow (±0.5% price band revalidation, 5-min TTL)
- D-0021 Trigger schedule (09:30, 10:30, ..., 15:30 ET weekdays)
- D-0047 Portfolio limits (5 concurrent trades, 3 new proposals/day)
- Alpaca paper trading (`PA38G7QYMDHV`)
- Telegram bot approvals (`@E_TradingControllerBot`)

---

## 🔁 PHASE A — Audit (committed as 840e6f2)

- `docs/architecture/implemented-but-unused-audit-2026-10-02.md`
- Inventoried dormant APIs + unused code paths

---

## 📡 PHASES B.1-B.12 — Research pipeline & live data (committed)

| Phase | Module | Purpose |
|---|---|---|
| B.1 | `src/d0026/regime_classifier.py` | FRED VIX-percentile regime (RISK_ON/NEUTRAL/RISK_OFF) |
| B.2 | `src/engine/proposal_enricher.py` | Perplexity enrichment (superseded by B.9) |
| B.3+B.4 | `src/marketdata/finnhub_source.py`, `alpha_vantage_source.py` | HTTP clients |
| B.5+B.6 | `src/marketdata/tiingo_source.py`, `polygon_source.py` | HTTP clients |
| B.8 | `src/marketdata/polygon_s3_client.py` | SigV4 bulk flat-files (stdlib) |
| B.9 | `src/engine/deep_research.py` | Multi-section Telegram report, parallel fetches |
| B.10 | `src/engine/research_hub.py`, `trade_evaluator.py` | SymbolResearchHub + evaluator (hard filter + soft 0-100) |
| B.11 | `src/engine/engine.py` | `_check_watchlist` runs evaluator, keeps Top-3 ≥ 60 |
| B.12 | hub + evaluator | multi-period returns (5d/30d/90d), volatility, rel_strength vs SPY |

**Scripts added:**
- `scripts/run_universe_selection.py` (D-0026 pipeline runner)
- `scripts/download_polygon_bulk.py` (S3 CLI: list/get/sync)
- `scripts/rank_symbols.py` (manual ranker CLI)
- `scripts/verify_apis.py` (real-API diagnostic — audits every endpoint)

**AV TTL cache:** `src/marketdata/av_cache.py` (SQLite, 6h TTL, dodges 5/min free-tier limit)

---

## 🛡️ PHASES B.13-B.18 — Production hardening (committed)

| Phase | Module | Purpose |
|---|---|---|
| B.13 | `src/backtest/historical_simulator.py` + `scripts/run_ranker_backtest.py` | 12-month zero-look-ahead backtest with random baseline |
| B.14 | `src/engine/portfolio_filter.py` | Sector cap (1/sector) + correlation cap (ρ < 0.70) |
| B.15 | `src/engine/position_sizer.py` | `shares = (target_$ / price) × (target_vol / actual_vol)` — **standalone, NOT yet wired to TradeProposalService** |
| B.16 | `src/engine/macro_calendar.py` | Blocks proposals 24h before FOMC/CPI/NFP (15 built-in events through Q1 2027) |
| B.17 | `src/engine/news_sentiment.py` | Polarity in [-1,+1] from catalysts/risks ratio, wired into `_score_news` |
| B.18 | `src/engine/engine.py` + `scripts/run_paper_session.py` | Engine integration: macro check → portfolio filter → Top-3 |

---

## 🏛️ PHASES B.19-B.28 — Capitol Trades integration (committed)

| Phase | Module | Purpose |
|---|---|---|
| B.19 | `src/research/politicians.py` | 15-politician whitelist with chamber, committees, alpha-weight |
| B.20 | `src/marketdata/quiverquant_source.py` | QuiverQuant REST client (Bearer auth) — needs `QUIVER_QUANT_API_KEY` |
| B.21 | `src/research/edisclosure_source.py` | Senate + House official endpoints (filing early-warning) |
| B.22 | `src/research/political_aggregator.py` | Parallel multi-source fetch, dedup, whitelist filter |
| B.23 | `src/research/committee_mapper.py` | Committee ↔ SIC-sector keyword mapping |
| B.24 | `src/research/political_cluster.py` | Clustering + weighted signal (0-25) + sell-wave detection |
| B.25 | `research_hub.py` | 7 political fields added to `SymbolResearch` |
| B.26 | `trade_evaluator.py` | `_score_political` (weight 15) + sell-wave hard filter |
| B.27 | `src/engine/political_universe_source.py` | Top-5/day cached, no human approval |
| B.28 | `engine.py` | Universe union, enrichment, **re-evaluation after enrichment** |

**Evaluator weight rebalance** (totals unchanged):
```
Before B.26:  fund 20 · tech 25 · mome 15 · trend 15 · rel_str 10 · news 10 = 95
After B.26:   fund 16 · tech 20 · mome 10 · trend 13 · rel_str  9 · news  7 · political 15 = 90
```

---

## 🚨 CRITICAL BUG FIXED (2026-10-03, this commit)

**Found during end-to-end audit:**

In B.28's first wiring, political signals were enriched into
SymbolResearch AFTER `evaluator.rank()` already scored. Result: the
sell-wave hard filter never fired and the political soft-score always
contributed 0. **Not an integration — just a decoration.**

**Fix:** Engine now calls `evaluator.evaluate_research(research)` a
second time AFTER enrichment, then re-sorts the result list.

**Covered by 3 new E2E tests** in `tests/engine/test_engine.py::TestPoliticalSignalEndToEnd`:
- `test_political_signal_actually_boosts_score`
- `test_political_sell_wave_blocks_trade`
- `test_political_source_failure_doesnt_block`

---

## 🧪 TEST STATUS

**1393 passed, 0 failed** (as of this commit)
- B.13-B.18: +52 tests
- B.19-B.28: +58 tests
- E2E political fix: +3 tests

Run full suite:
```bash
PYTHONPATH=src python3.11 -m pytest -q
```

---

## 🏗️ INFRASTRUCTURE (Oracle VM, needs follow-up)

**Set up during chat:**
- Oracle Cloud Free Tier, UK South (London)
- Ampere A1.Flex, 4 OCPU, 24 GB RAM
- Public IP: `130.162.190.89`
- User: `opc`
- SSH key: `C:\Users\Lenovo\Downloads\ssh-key-2026-10-02.key`
- Project at: `~/Treading-bot-Claude`
- Python 3.11, pytest installed
- `.env` populated with 15 API keys
  - All keys VERIFIED via `verify_apis.py` (24 ✅ / 3 ⚠ / 0 ❌)

**Still TODO on VM:**
- `QUIVER_QUANT_API_KEY` not yet added to `.env` (needed for B.20)
- systemd service for 24/7 auto-restart
- Watchdog script
- Auto-deploy (git-pull cron)

---

## 📋 OPEN DECISIONS

- **Position Sizer wiring**: Built in B.15 but NOT wired to
  `TradeProposalService.start_trade`. Controller to decide whether
  to wire now or after ranker + Capitol Trades are battle-tested.

- **Backtest**: Infrastructure ready, not yet run on real data.
  Command for VM:
  ```bash
  cd ~/Treading-bot-Claude && git pull && \
  set -a && source .env && set +a && \
  PYTHONPATH=src python3.11 scripts/run_ranker_backtest.py \
    --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY \
    --months 12 --picks-per-day 3
  ```

- **Hosting (systemd + watchdog)**: Scheduled for the session AFTER
  backtest validates positive edge.

---

## 📜 COMMIT TIMELINE (newest first)

```
<this>    E2E fix + comprehensive resume doc + Capitol Trades E2E tests
2b07497   feat(d0050): Phase B.19-B.28 — Capitol Trades integration
4a2a151   docs: session resume guide 2026-10-03
e7a9995   feat(d0050): Phase B.18 — Engine integration of portfolio + macro
<...>     Phase B.17, B.16, B.15, B.14 (grouped commits)
<...>     Phase B.13 — backtest harness
a0e731b   Phase B.12 — multi-period returns + rel strength
29cd82d   Phase B.12 — AV SQLite TTL cache
fab0986   Perplexity 2-queries parallel
e1335c2   Polygon fallback bugs
763cd45   Parser mismatches found by verify_apis.py
2e53146   verify_apis.py diagnostic
3c989de   Phase B.10 — single hub + evaluator
373dfeb   Phase B.11 — ranker gates trigger-check
0316350   Enrich recovery + re-created proposals
9619c1b   Phase B.9 — deep research report
7ce776a   Phase B.8 — Polygon S3 SigV4
b6e4d14   Phase B.3+B.4 — Finnhub + Alpha Vantage
4f91012   Phase B.2 — Perplexity enrichment
560d7d2   Phase B.1 — FRED regime
840e6f2   Phase A audit
```

---

## 🎯 TO RESUME IN A NEW SESSION

Say to the new session:
> Read `docs/session-resume-2026-10-03.md` end-to-end, then continue
> from "Open decisions". Priority is backtest validation on VM, then
> systemd hosting.

The new session will have ZERO context without this doc. It MUST be
read first before any other work.

---

## 📞 WINDOWS POWERSHELL COMMANDS (local testing without VM)

User reports being on Windows PowerShell, not connected to VM. These
commands let them pull + test the full suite on their Windows machine:

```powershell
# One-time setup (if not already done)
cd C:\path\to\Treading-bot-Claude
python -m pip install --user pytest

# Each time: pull + test
git pull
$env:PYTHONPATH = "src"
python -m pytest -q

# Preview one proposal (uses stubs, no real APIs needed)
python scripts\rank_symbols.py AAPL
```

For real-API tests, the user must also set env vars locally:
```powershell
$env:ALPACA_API_KEY_ID = "<key>"
$env:ALPACA_API_SECRET_KEY = "<secret>"
# etc.
```

Or source from .env file (if present):
```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2])
    }
}
```
