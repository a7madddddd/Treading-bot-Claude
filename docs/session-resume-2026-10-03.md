# Session Resume Guide — 2026-10-03

**Session ID:** session_01V8fhdbKrVxUwNAdPYK3teB
**Branch:** `claude/youthful-goodall-4cr0ei`
**Status:** All work pushed. Ready to continue.

---

## ✅ What has been completed in this session

### Phase A — Audit
- `docs/architecture/implemented-but-unused-audit-2026-10-02.md`
- Identified dormant APIs + unused code paths.

### Phase B.1 — FRED regime classifier
- `src/d0026/regime_classifier.py`
- Live VIX percentile → RISK_ON / NEUTRAL / RISK_OFF
- Wired into `scripts/run_universe_selection.py`

### Phase B.2 — Perplexity proposal enrichment
- `src/engine/proposal_enricher.py` (deprecated by B.9)
- `--enable-perplexity` flag (now alias of `--enable-research`)

### Phase B.3+B.4 — Finnhub + Alpha Vantage clients
- `src/marketdata/finnhub_source.py`
- `src/marketdata/alpha_vantage_source.py`
- Standalone HTTP clients, fail-open

### Phase B.5+B.6 — Tiingo + Polygon REST + Polygon S3
- `src/marketdata/tiingo_source.py`
- `src/marketdata/polygon_source.py` (REST + S3Config)
- `src/marketdata/polygon_s3_client.py` (SigV4 stdlib)
- `scripts/download_polygon_bulk.py`

### Phase B.7 — Composite enricher
- Replaced by B.9

### Phase B.8 — Polygon S3 bulk client with SigV4
- `src/marketdata/polygon_s3_client.py`

### Phase B.9 — Deep research composer
- `src/engine/deep_research.py`
- Multi-section Telegram report (Macro/Fund/Tech/Snap/News/Catalysts/Risks/Verdict)
- Parallel API fetches

### Phase B.10 — Central hub + evaluator
- `src/engine/research_hub.py` — SymbolResearchHub + SymbolResearch dataclass
- `src/engine/trade_evaluator.py` — hard filter + soft score 0-100
- `scripts/rank_symbols.py` — CLI ranker

### Phase B.11 — Engine ranker integration
- `src/engine/engine.py` — _check_watchlist runs evaluator, keeps Top-3 ≥ 60
- `_TOP_N_PER_CYCLE = 3`, `_MIN_SCORE = 60.0`

### Phase B.12 — Multi-period returns + relative strength
- Added fields to SymbolResearch: `return_5d/30d/90d_pct`,
  `volatility_30d_pct`, `rel_strength_30d_pct`, `volume_ratio_30d`.
- SPY benchmark cached once per hub batch.
- New score components in TradeEvaluator: `weight_trend=15`,
  `weight_rel_strength=10`.

### Phase B.13 — Backtest harness
- `src/backtest/historical_simulator.py` — zero look-ahead replay
- `scripts/run_ranker_backtest.py` — CLI with Polygon aggregates
- Compares against random baseline (edge calculation)

### Phase B.14 — Portfolio filter
- `src/engine/portfolio_filter.py`
- Gate 1: sector cap (max 1/sector in Top-3)
- Gate 2: correlation cap (ρ < 0.70 of 30-day log returns)

### Phase B.15 — Volatility position sizer
- `src/engine/position_sizer.py`
- `shares = (target_$2000 / price) × (target_vol_25% / actual_vol)`
- **NOT YET WIRED into TradeProposalService.start_trade — stays standalone**

### Phase B.16 — Macro event calendar
- `src/engine/macro_calendar.py`
- Blocks new proposals 24h before FOMC/CPI/NFP
- 15 built-in US events through Q1 2027

### Phase B.17 — News sentiment polarity
- `src/engine/news_sentiment.py`
- Polarity in [-1, +1] from catalysts/risks ratio
- Wired into `_score_news`

### Phase B.18 — Engine integration of macro + portfolio
- Engine constructor + `_check_watchlist` wire both gates
- `scripts/run_paper_session.py` auto-enables when `--enable-research`

---

## 🏗️ Infrastructure (Oracle VM)

- Oracle Cloud Free Tier, UK South (London)
- Ampere A1.Flex, 4 OCPU, 24 GB RAM
- Public IP: `130.162.190.89`
- User: `opc`
- SSH key: `C:\Users\Lenovo\Downloads\ssh-key-2026-10-02.key`
- Project at: `~/Treading-bot-Claude`
- Python 3.11, pytest installed
- `.env` populated with all 15 API keys
- Branch `claude/youthful-goodall-4cr0ei` cloned

**Not yet done on VM:**
- systemd service for 24/7 auto-restart
- Watchdog script
- Auto-deploy (git pull cron)

---

## 📋 Open decisions pending

### Capitol Trades integration (NEXT UP)
Controller approved:
- Multi-politician whitelist: **top 10-15 by alpha** (Pelosi, Crenshaw,
  Gottheimer, McCaul, Khanna, McConnell, Sullivan, Mullin, ...)
- Max **5 symbols/day** added to universe from political signal.
- Approval question still open: single OR double Telegram approval.
- Data source question still open: CapitolTrades-only vs
  CapitolTrades + QuiverQuant vs all three (CapitolTrades +
  QuiverQuant + eDisclosure PDFs).

### Position sizer wiring
- Built in B.15 but NOT wired to `TradeProposalService.start_trade`.
- Controller to decide: wire now (changes quantity semantics) or
  keep standalone until the ranker + portfolio-filter changes are
  battle-tested first.

### Hosting
- systemd + watchdog + auto-deploy still TODO.
- Will be a separate short session after Capitol Trades + backtest
  validation.

---

## 🧪 Backtest status

**Not yet run on real data.** The infrastructure is ready
(`scripts/run_ranker_backtest.py`); needs to be executed on the VM
with POLYGON_API_KEY and the 12-month window.

Command for next session to run on VM:
```bash
cd ~/Treading-bot-Claude && git pull && \
set -a && source .env && set +a && \
PYTHONPATH=src python3.11 scripts/run_ranker_backtest.py \
  --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY \
  --months 12 --picks-per-day 3
```

Output goes to `./backtest_result.json`.

---

## 📊 Test status

- **1339 passed, 0 failed** (as of commit e7a9995)
- 55 new tests added in this session (B.13-B.18)

---

## 🚀 Commits in this session (all pushed)

Most recent first:
```
e7a9995 feat(d0050): Phase B.18 — Engine integration of portfolio + macro gates
         feat(d0050): Phase B.16+B.17 — macro calendar + news sentiment
         feat(d0050): Phase B.15 — volatility position sizing
         feat(d0050): Phase B.14 — portfolio filter
         feat(d0050): Phase B.13 — backtest harness
a0e731b feat(d0050): Phase B.12 — multi-period returns + relative strength
29cd82d feat(d0050): Phase B.12 — SQLite TTL cache for Alpha Vantage
fab0986 perf(d0050): Perplexity 2 queries parallel + hub deadline 60s
e1335c2 fix(d0050): polygon fallback bugs
763cd45 fix(d0050): parser mismatches found by verify_apis.py
2e53146 feat(d0050): verify_apis.py — real-API diagnostic script
3c989de feat(d0050): Phase B.10 — single hub + evaluator
373dfeb feat(d0050): Phase B.11 — ranker gates trigger-check cycle
0316350 fix(d0050): enrich recovery + re-created proposals too
9619c1b feat(d0050): Phase B.9 — deep research report
7ce776a feat(d0050): Phase B.8 — Polygon S3 bulk client with SigV4
b6e4d14 feat(d0050): Phase B.3+B.4 — Finnhub + Alpha Vantage clients
4f91012 feat(d0050): Phase B.2 — optional Perplexity enrichment
560d7d2 feat(d0050): Phase B.1 — FRED regime classifier
840e6f2 docs(d0050): Phase A audit
```

---

## 🎯 Next session should start with:

1. **Confirm Capitol Trades plan** (approval depth + data sources).
2. **Build Phases B.19-B.26** (Capitol Trades integration).
3. **Run backtest on VM** → prove ranker alpha.
4. **If backtest positive** → set up systemd + watchdog + auto-deploy.
5. **Launch 24/7 paper trading.**

To resume, in a new session, say:
> Read `docs/session-resume-2026-10-03.md` and continue from
> Capitol Trades integration (Phases B.19-B.26).
