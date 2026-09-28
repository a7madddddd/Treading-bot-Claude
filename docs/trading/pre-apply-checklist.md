# Pre-APPLY Checklist

Single source of truth for what must happen before the Controller
writes "APPLY THE CHANGES". Updated when a blocker moves.

**Current gate status:** APPLY-ready under D-0039 §4's manual-list path,
with two Controller-accepted caveats:
**B16** (universe calibration data — verdict upgraded from C to B by
D-0042; two-group free architecture approved, but no calibration run
yet — the engine trades on the Controller's daily per-symbol manual
list per D-0039 §3–§4 until numeric parameters are approved by a
future decision) and **B17** (durable always-on host — not yet
chosen; the current cloud session is acceptable for verification and
short paper runs but not for a session that spans days or DST
transitions). Everything else on this checklist is resolved:

- Operational steps B1–B6 and B14 closed via D-0038.
- The Trade/Proposal/Execution/Engine implementation and the three
  previously deferred adapter slices (concrete Alpaca `BrokerClient`,
  concrete `MarketDataSource`, Telegram long-polling
  `PendingDecisionSource`) are complete and pushed to the branch
  (D-0035 for the engine; slices in commits `7be9bb7`, `441751d`,
  `9249fce`).
- Universe subsystem structural boundary approved with all numeric
  parameters formally deferred (D-0039); TSLA-specific routine
  retired and replaced by a symbol-agnostic template (D-0040);
  scheduling timezone realigned to America/New_York with a
  per-routine TZ principle (D-0041).
- Every verification-plan section (§1, §2, §3, §4, §6) is executed
  and passing; 802 unit tests plus a 7/7 live paper Alpaca dry run.

Updated 2026-09-26 to reflect the current pushed state — see
`decisions.md` D-0037 through D-0041.

---

## Legend

- ✅ **RESOLVED** — decided and documented; no further work needed here.
- 🟨 **IN DESIGN** — architecture documented; concrete implementation
  step still required before APPLY.
- ❌ **BLOCKER** — must be resolved before APPLY.
- 🕓 **TBD (deliberate)** — explicitly deferred; does not block APPLY
  under the current scope.

## A. Decisions

| Item | Status | Ref |
|---|---|---|
| Roles, approval model, safety | ✅ | CLAUDE.md; D-0001..D-0003 |
| Trailing math (compounded, threshold-based) | ✅ | D-0004, D-0008 |
| Timezone (America/Chicago, DST-aware) | ✅ | D-0005, D-0020 |
| Calendar (US market workdays) | ✅ | D-0006 |
| Ladder approval expiration (5 min + ±0.5%) | ✅ | D-0007 |
| Partial-fill reference freeze | ✅ | D-0009, D-0010 |
| Trigger price = Alpaca Last Trade | ✅ | D-0012 |
| Routine directives (fix / disable / research-only) | ✅ | D-0015, D-0016, D-0017 |
| State-management contract | ✅ | D-0018 |
| Research architecture (Perplexity + Capitol Trades) | ✅ | D-0019 |
| Schedule anchors (08:30 CT open, 07:00 CT research) | ✅ | D-0021 |
| Language: Python | ✅ | D-0022 |
| Scheduler: persistent TZ-aware process | ✅ | D-0023 |
| State store: SQLite behind repository abstraction | ✅ | D-0024 |
| Approval transport: Telegram bot with inline buttons | ✅ | D-0025 |
| Trading universe: dynamic, symbol-agnostic engine | ✅ (principle) | D-0026 |
| Universe **selection mechanism** | 🕓 TBD | D-0013 / D-0026 |
| Ladder trigger **debounce** | ✅ | D-0011 (state machine + asymmetric re-arm; approved 2026-09-14) |
| Portfolio-level hard risk limits | 🕓 TBD | risk-management.md §5 |

## B. Implementation prerequisites (before APPLY)

| # | Item | Status | Owner | Notes |
|---|---|---|---|---|
| B1 | Rotate Alpaca paper API keys | ✅ DONE (D-0038, 2026-09-20) | Controller | Rotated; old leaked keys can now be revoked at Alpaca console. |
| B2 | Rotate Perplexity API key | ✅ DONE (D-0038, 2026-09-20) | Controller | Rotated; old leaked key can now be revoked at Perplexity console. |
| B3 | Rotate Telegram bot token | ✅ DONE (D-0038, 2026-09-20) | Controller | Rotated; old leaked token can now be revoked at BotFather. |
| B4 | Populate `.env` with new secrets locally | ✅ DONE via cloud env vars (D-0038, 2026-09-20) | Controller | Secrets injected via the cloud environment's Environment Variables layer while operating from that host; no local `.env` change needed under B5's current choice. |
| B5 | Deployment host chosen for the Python process | ✅ DONE — interim (D-0038, 2026-09-20) | Controller + design | Interim host: the current Claude Code cloud session environment. Acceptable for verification and dry runs only; NOT approved as the durable host for live paper execution — see B17. |
| B6 | `TELEGRAM_ADMIN_USER_IDS` env var populated with Controller's Telegram user id | ✅ DONE (D-0038, 2026-09-20) | Controller | Controller's Telegram user id set; determines who can Approve/Reject. |
| B7 | Repository abstraction for SQLite | ✅ IMPLEMENTED | Claude | `src/persistence/`, `src/trade/`, `src/proposals/`, `src/execution/` — full SQLite-backed repositories, migrations 0001-0004, 706 tests passing. |
| B8 | Engine skeleton | ✅ IMPLEMENTED | Claude | `src/engine/` — watchlist-driven Trade lifecycle, D-0021-gated Ladder trigger detection, independent reconciliation/Floor cadence, SQLite process lock, startup recovery, in-memory notification dedup. Symbol-agnostic (reads `WatchlistSource`, currently a static Controller-approved TSLA list per D-0026's still-blocked real pipeline). See D-0035. |
| B9 | Verification-plan §1 (static prompt review) executed | ✅ DONE (D-0040, 2026-09-23) | Controller + Claude | TSLA-specific `prompt-proposed.md` reviewed and its drifts recorded (Market orders vs D-0033 Limits, missing D-0034 Ladder 2 partial-fill, missing D-0011 debounce, pre-D-0038 credentials wording). File retired and replaced by `routines/paper-trading-monitor-template/prompt-proposed.md` (symbol-agnostic policy template) via D-0040. Capitol Trades prompt PASS with no drift. `git grep -E 'PKKV|SWeJa|pplx-|8892400776'` returns only doc references, no leaked values. |
| B10 | Verification-plan §2 (deterministic engine math) executed | ✅ DONE (commit `b0dd06a`, 2026-09-23) | Claude | Existing `tests/trade/test_models.py` already covered reference freeze, Original Floor invariance after ladder fills, activation at entry × 1.10, ratchet ×1.05 compounded, floor = threshold × 0.95, ladder-below-floor block, D-0007 5-min / ±0.5% re-check, and partial-fill freeze. Two gaps closed: `test_ratchet_matches_worked_example_table` extended to include the full worked-example table (110 → 115.5 → 121.275 → 127.339 with floors 104.5, 109.725, 115.211, 120.972 within ±0.001), and `test_trailing_floor_monotonic_across_random_walk` added (seed 20260923, 2000 steps). |
| B11 | Verification-plan §3 (broker dry-run) executed | ✅ DONE (commits `b3ec049`, `7fddcac`, `5aa6228`, 2026-09-26) | Claude | Read-only script at `scripts/verification/broker_dry_run.py` runs 7 checks against the paper Alpaca account: env vars present, ALPACA_BASE_URL parses to host `paper-api.alpaca.markets`, `/v2/account` cash reads as float, `/v2/stocks/TSLA/trades/latest` price reads as float, nonexistent order lookup returns None, paper-api guard rejects a non-paper host, and empty-credentials guard rejects empty keys. Last live run: 7/7 PASS with `cash = 45,989.54 USD` and `TSLA last = 372.09 USD`. No orders submitted. The paper-api guard was hardened from a substring test to a strict host-equality test (`7fddcac`) after a review found the substring form would accept the deceptive URL `https://api.alpaca.markets/paper-api`. Reconciliation-mismatch detection (verification-plan §3 fourth item) is covered by unit tests in `tests/execution/test_service.py` (see docstring in the script). |
| B12 | Verification-plan §4 (approval-loop dry run) executed | ✅ DONE (existing coverage confirmed 2026-09-23) | Claude | Full approval loop covered end-to-end: `TelegramDecisionSource` parses inline callback data and text commands, authorizes the sender against `TELEGRAM_ADMIN_USER_IDS`, and enqueues `ControllerDecision`; `PendingDecisionSource.poll()` drains without blocking; `ProposalRepository.record_decision` transitions the proposal to APPROVED or REJECTED; `ExecutionService.submit_approved_proposal` re-checks D-0007 (5-min window, ±0.5% band, active-floor priority) before any broker call. Coverage lives across `tests/notifications/test_telegram_decision.py`, `tests/proposals/test_revalidation.py`, `tests/proposals/test_models_and_repository.py`, and `tests/execution/test_service.TestApprovalAndD0007Boundaries`. |
| B13 | Verification-plan §6 (timezone tests) executed | ✅ DONE (commit `1c06e2d`, D-0041, 2026-09-26) | Claude | `src/engine/schedule.py` timezone realigned from America/Chicago to America/New_York per D-0041 with wall-clock moments unchanged (09:30–15:30 ET replaces the old 08:30–14:30 CT labels). New DST tests in `tests/engine/test_engine.py::TestD0021Schedule`: spring-forward Monday (2027-03-15 09:30 ET), fall-back Monday (2026-11-02 09:30 ET), and a fixed-UTC drift demonstration (same 09:30 ET wall-clock lands on 14:30 UTC winter vs 13:30 UTC summer, which is exactly the drift a fixed-UTC cron would suffer and the reason D-0020/D-0041 forbid it). |
| B14 | Existing account-level Routines disabled or reduced to no-op before Python engine goes live | ✅ DONE (D-0038, 2026-09-20) | Controller | Controller confirmed the legacy Routines that held the old credentials have been deactivated; no dual-writer risk against the Alpaca paper account. |
| B15 | Universe subsystem design + Controller approval | ✅ STRUCTURE APPROVED (D-0039, 2026-09-23); numeric parameters DEFERRED under §2 of D-0039 until B16 is resolved | Controller + Claude | `ApprovedUniverseSnapshot` boundary contract (`docs/architecture/universe.md §2`), the 9-stage A→I pipeline shape (§1), and the "no-universe = no-trade" rule (§6) are all approved as structure. Stage F architectural boundary remains approved under D-0029; no ranking metric or numeric weight is approved. TSLA remains TEST-ONLY (D-0026 §5). D-0039 §3 approves per-symbol daily Controller approval as the operating mode; §4 approves a broker-derived symbolic-capital path where the pipeline reads live cash from Alpaca at each run and derives the candidate share-price ceiling mechanically as `usable_cash / 40`, with the daily universe supplied as a Controller manual list until §2's deferral is lifted. Numeric parameters (liquidity floor, spread cap, ATR band, regime thresholds, ranking weights, sector cap, correlation, Top-N, warm-up) are formally deferred and are unblocked only by a future decision after B16 is resolved. |
| B16 | Historical market data collection for universe calibration | ✅ STAGE 3 EXECUTED, 12/13 PASS, ARCHITECTURE CONFIRMED (D-0043, 2026-09-27); STAGE 4 REGISTRATION VALUE REVIEW COMPLETE (D-0044, 2026-09-27) — three sources removed (Polygon Flat Files, Alpha Vantage, Finnhub), one demoted (Tiingo → support), two kept as specialized (FRED, Polygon REST corporate actions), CBOE VIX promoted to primary | Controller + Claude | D-0042 (2026-09-26) approved the two-group architecture; D-0043 (2026-09-27) records the Stage 3 use-case run against all thirteen probes. 12/13 PASS with real numeric evidence: three independent price sources (Alpaca SIP, Polygon REST, Tiingo) agree exactly on AAPL 2025-01-02..08 close range 242.21–245.00 (triple cross-validation); two independent VIX sources (CBOE 1990-present, GitHub finance-vix 2004-present) agree within a handful of rows; SEC EDGAR delivers 164 8-K filings for delisted Blockbuster (CIK 1085734); Nasdaq Trader delivers 13,289 symbols; fja05680/sp500 delivers 1,262 historical membership rows via raw.githubusercontent.com; FRED UNRATE returns real recent observations; Alpha Vantage IBM daily compact returns 100 days with no error/quota flags; Finnhub returns a real AAPL quote. **One FAIL, actionable:** Polygon delisted probe (FDO 2014) → HTTP 403, confirming that Polygon's free tier does NOT close the D-0042 §4 residual gap for symbols delisted 2018-2026. That gap remains open by design under the free-only policy; closing it would require a paid provider (rejected by D-0028) or a calibration methodology that accepts the survivorship bias with explicit per-report disclosure. **Polygon Flat Files (S3):** three env vars present; live S3 probe still deferred per D-0042 option (c). No calibration run authorized by this update. Every numeric parameter still needs a future decision per D-0039 §2's deferral. See D-0043 for the full Stage 3 evidence trail and `docs/architecture/research-sources.md §8` for the source description. |

| B17 | Choose a durable always-on host before live paper session execution starts | 🟨 IN DESIGN — **DEFERRED BY CONTROLLER (D-0045, 2026-09-27)** — real-case paper testing on the current cloud session comes first; a durable host decision is postponed until those runs teach us what the host actually needs. | Controller | The current Claude Code cloud session environment (B5's interim choice) is ephemeral. Under D-0045 that ephemerality is acceptable for the bounded (`--max-hours`) real-case sessions the Controller wants to run first. Not a blocker for that testing; still a blocker for any session that must span days or a DST transition. Revisit after the staged testing plan under D-0045 completes. |
| B18 | Real-case paper session testing (`scripts/run_paper_session.py`) | 🟨 IN PROGRESS — approved staged rollout (D-0045): session 1 = TSLA alone; session 2 = TSLA + AAPL; session 3 = TSLA + AAPL + SPY. Not a blocker for APPLY; is the exercise that decides B17 and validates the end-to-end cycle on real data. | Controller + Claude | Runner script committed with three D-0045 design gates: `--max-hours` cap (default 6h), pre-flight (`/v2/account` + `/v2/clock` + Telegram `getMe`) with a Controller grace period before Engine start, and clean SIGINT/SIGTERM shutdown that releases `EngineLock` and sends a Telegram close notice. Paper-endpoint enforcement is unchanged (`AlpacaBrokerClient` host-equality guard). No new business logic added by this row. |
| B19 | Research subsystem (Perplexity Agent + CapitolTrades) | ✅ CLOSED (D-0046, 2026-09-27) — `src/research/` implemented with typed models, Perplexity Agent adapter, CapitolTrades scraper, synthesis, Markdown log writer, SQLite persistence (migration 0005, APPROVED_SCHEMA_VERSION 4→5), and `scripts/run_research_cycle.py` runner. Advisory-only per CLAUDE.md §5; never reachable from the trading path. 44 new tests, full suite 846/846 PASS. | Controller + Claude | Perplexity `/v1/responses` verified live before implementation — `preset: "medium"` routes to `openai/gpt-6-luna` today, and an explicit `provider/model` override is supported for reproducibility. `agent_api_migration_required` is CRITICAL (research-sources.md §7). CapitolTrades parser is deterministic HTML (no LLM) with schema-break detection raising `CapitolTradesParserBrokenError`. Runs on its own schedule (twice-daily per Controller decision 1B); does not touch the Engine process. |
| B20 | Portfolio-level hard risk limits | ✅ CLOSED (D-0047, 2026-09-27) — `src/risk/` implements deterministic guardrails: gross exposure ≤60%, single-symbol ≤10%, ≤5 concurrent trades, ≤3 new trades/day, daily-loss kill switch at 3%. Enforced inside `ExecutionService.submit_approved_proposal` AFTER D-0007 and BEFORE any broker call. 30 new tests, full suite 878/878 PASS. | Controller + Claude | Controller-approved numeric values recorded in D-0047. Enforcement location = ExecutionService (Controller decision 6A). Ladder additions do not count against concurrent/new-daily caps but still respect gross, single-symbol, and daily-loss rules. Existing protective Floor / Trailing Floor remains active during a kill switch; only new entries and new ladders are refused. Enforcer wiring in `scripts/run_paper_session.py` is always-on for real paper sessions; unit tests optionally omit the enforcer to keep pre-D-0047 execution tests green. |
| B21 | D-0026 pipeline structure + all 9 stages (percentage-only) | ✅ CLOSED (D-0048, 2026-09-27) — `src/d0026/stages/` implements the eight candidate stages (A Tradability, B Data Quality, C Execution Quality, D Strategy Fit, E Regime, F Ranking, G Concentration, H Top-N) using percentages, ratios, percentile ranks, and domain categories. Zero absolute price/volume/dollar magic numbers. `UniverseSelectionConfig` holds the approved defaults. Full suite 915/915 PASS. | Controller + Claude | D-0039 §2 SUPERSEDED: numeric parameters are no longer deferred; percentage parameters are approved directly in D-0048. `NotCalibratedStageEvaluator` retained as opt-in fallback. Backtesting remains available as an OPTIONAL refinement, no longer a blocker. Engine still uses `StaticWatchlistSource` per D-0039 §4 -- wiring a live selector into the Engine is a follow-up (B22). |
| B22 | Wire live universe snapshot into the Engine | ✅ CLOSED (2026-09-27) — `SnapshotUniverseSource` (`src/engine/snapshot_watchlist.py`) reads today's ApprovedUniverseSnapshot via `SqliteSnapshotRepository` (`src/d0026/sqlite_repository.py`, migration 0006, APPROVED_SCHEMA_VERSION 5→6). `run_paper_session.py` now supports `--universe-mode static\|snapshot` with the static path preserved as the default fallback. Full suite 928/928 PASS. | Controller + Claude | Strict D-0026 §6 (no-universe = no-trade) is honored when `fallback_watchlist=None`; the runner passes the CLI `--symbols` as fallback so a session started before any snapshot exists still behaves like before. The pipeline orchestrator's symbols-population gap remains a separate follow-up (B23) -- with no concrete UniverseSourceProvider + feature enricher wired yet, snapshots produced by the pipeline are still empty; this row closes only the READ path and its wiring. |
| B23 | Pipeline populates snapshot symbols end-to-end | ✅ CLOSED (2026-09-28) — `src/d0026/publish.py` `build_snapshot_symbols` classifies ranked survivors via `classify_evidence` and produces `SnapshotSymbolEntry` rows. `UniversePipeline` calls it after all stages complete, and now accepts an optional `feature_enricher` (post-identity-resolution hook) so a real caller can attach bars + features without changing the pipeline shape. D0026-EV-INV-1 preserved: `pipeline.py` never references evidence-layer identifiers -- the classification lives in a separate module. 5 new tests, full suite 933/933 PASS. | Controller + Claude | Adversarial verification: enricher raises -> CrashOutcome (no silent snapshot); enricher returns None -> CrashOutcome; enricher returns bare candidate (no features) -> SnapshotOutcome empty; no enricher (default) -> unchanged behavior. Feature enricher and concrete UniverseSourceProvider that read live Alpaca data remain follow-up work (do not block APPLY, unblock only "run the pipeline daily against real data"). |
| B24 | Concrete Alpaca UniverseSourceProvider + FeatureEnricher | ✅ CLOSED (2026-09-28) — `src/d0026/alpaca_provider.py` `AlpacaAssetsProvider` reads `/v2/assets` and returns tradable US-equity `RawCandidateRef` tuples (optional symbol whitelist). `src/d0026/alpaca_enricher.py` `AlpacaFeatureEnricher` reads daily IEX bars (65 calendar days ~= 45 trading bars) and computes liquidity (avg dollar volume 20d), ATR (14d), 30-day return, and intraday range/vwap as a spread proxy. `scripts/run_universe_selection.py` runs one pipeline pass end-to-end and persists the snapshot. Live smoke against Alpaca paper: 13 whitelisted candidates enriched with real values, pipeline produced AAPL as Top-1 (validated). 22 new tests, full suite 955/955 PASS. | Controller + Claude | Two implementation bugs found + fixed during live smoke and covered by regression tests: (1) `execution_quality_proxy_is_true_quote=False` (IEX intraday-range proxy is not a real bid/ask spread) now bypasses the `max_spread_fraction` hard cap and relies on the relative percentile filter -- otherwise every real symbol was over the 0.15% cap. (2) `unknown` sector in the concentration stage bypasses the cap -- we cannot enforce "no more than 30% in tech" without sector data. Both fixes are honest fail-open moves that preserve the D-0048 approved numbers when data is actually available. |

## C. Explicitly deferred (does NOT block APPLY under current scope)

- Universe **generation mechanism** (D-0026): principle approved; concrete
  design later. Engine will accept any list via the repository abstraction.
- Portfolio-level hard risk limits: engine will treat them as data;
  concrete values later.
- Second-provider notification transport (email, push): Telegram is the
  first provider; `INotificationService` abstraction preserved.
- Cloud vs self-host beyond MVP choice: any host that satisfies B5 is
  acceptable for MVP.

## D. How to move from here to APPLY

Order matters:

1. Rotate credentials (B1–B4).
2. Pick a host and register the Controller's Telegram user id (B5, B6).
3. Approve the interim universe (B15).
4. On Controller cue: Claude implements the repository, engine, scheduler
   registration, Telegram bot, and Capitol Trades research adapter — one
   verifiable slice at a time.
5. Execute verification plan §1, §2, §4 first (no broker, no orders).
6. Then §3 dry-run and §6 timezone tests.
7. When all six pass and B14 (existing Routines) is confirmed off,
   Controller writes "APPLY THE CHANGES". The engine goes live on paper.

Nothing before step 4 requires code. Steps 1–3 are Controller actions;
step 4 requires an explicit go-ahead.
