# Pre-APPLY Checklist

Single source of truth for what must happen before the Controller
writes "APPLY THE CHANGES". Updated when a blocker moves.

**Current gate status:** NOT ready for APPLY. Two open blockers remain:
**B16** (universe calibration data — verdict C, insufficient) and
**B17** (durable always-on host — not yet chosen). Everything else on
this checklist is resolved:

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
| B16 | Historical market data collection for universe calibration | 🟨 DATA-SOURCING PHASE CONCLUDED — verdict C, data not sufficient for D-0026 calibration; further search stopped per explicit Controller instruction; full acquisition still NOT authorized | Controller + Claude | Controller redirected to GitHub-reachable sources only (`docs/trading/github-native-data-sources.md`). Real, verified findings: `datasets/finance-vix` fully solves market-regime data. `eliangcs/pystock-data` partially solves bulk OHLCV for **2009–2017 only** (raw + adjusted prices both present, labeled). `fja05680/sp500` gives real, verified, point-in-time daily S&P 500 constituent membership 1996-2026, MIT — validation/control only, not a substitute for D-0026's dynamic universe. Five single-source recency candidates disqualified (D-0027). Layer D (delisted-securities dataset) disqualified (D-0028 §6.4). **Hugging Face verification (§7):** `huggingface.co` confirmed blocked. `elkassabgi/hfdatalibrary` verified via a reachable GitHub mirror — real, active, 1,391-ticker pipeline, but excludes delisted names before ~2021 (confirmed by direct ticker-list test) and actual bars require registration at a blocked domain — CONDITIONAL. `paperswithbacktest/Stocks-Daily-Price` confirmed **paid** — REJECTED. `mito0o852/OHLCV-1m` — UNVERIFIED, no reachable mirror. **Calibration-data validation pass (2026-09-14, §8):** synthesized whether these components together support defensible D-0026 calibration (not just whether each "looks good"). Findings: DELL ticker-identity is unresolved (has referred to two unrelated companies); Candidate B's own metadata shows a 51.8% average gap rate in its bottom liquidity quintile (uneven, not broad, coverage); **zero actual price rows have ever been inspected from either Hugging Face candidate in this entire thread**; joining `fja05680/sp500` membership to either OHLCV candidate does NOT reduce survivorship bias — it creates historical membership paired with a current/limited OHLCV universe that silently drops most pre-2021 delistings while appearing corrected. Delisted test: FDO/RSH/HNZ remain PARTIAL, BBI remains FAILED. **FINAL VERDICT: C — data is not sufficient for D-0026 calibration**, full or bounded-composite. The only calibration-grade resource with actual, row-level-verified data anywhere in this thread remains `eliangcs/pystock-data` (2009-2017) + `fja05680/sp500` (S&P 500 point-in-time membership) — unchanged from D-0028. Norgate/paid-provider research remains REJECTED/superseded. **Per explicit Controller instruction, no further dataset search was performed or is recommended.** Data-sourcing phase for B16 is concluded. **Pending Controller decision:** Controller-side row-level verification of the Hugging Face candidates from an unblocked network; or proceed to designing the D-0026 calibration strategy bounded by these documented limitations (pre-2018 historical control + S&P-500-only point-in-time validation, no verified modern broad-market OHLCV, no verified broad delisted coverage); or pursue direct Stooq/SEC/FRED access from a non-blocked network; or a future explicit paid-provider decision. None of these is recommended or authorized by this finding. Phase 3 remains NOT approved. |

| B17 | Choose a durable always-on host before live paper session execution starts | 🟨 IN DESIGN | Controller | The current Claude Code cloud session environment (B5's interim choice) is ephemeral: its container is reclaimed after a period of inactivity, so it is NOT guaranteed to remain up through a full US session or across a DST transition. Acceptable for §1/§2/§4 verification and §3 broker dry-run; NOT acceptable for a live paper session that must survive across days. Not a blocker for the verification phase; is a blocker for switching the engine to a live paper session. |

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
