# Pre-APPLY Checklist

Single source of truth for what must happen before the Controller
writes "APPLY THE CHANGES". Updated when a blocker moves.

**Current gate status:** NOT ready for APPLY. Design decisions resolved;
the Trade/Proposal/Execution/Engine implementation is now complete and
tested (706 tests passing, D-0007 through D-0035) — what remains is
almost entirely Controller-owned operational steps (credential
rotation, hosting, disabling live Routines) plus three deferred code
integrations (concrete Alpaca `BrokerClient`, concrete
`MarketDataSource`, Telegram inbound approval). Updated 2026-09-22 to
reflect the actual implemented state — see `decisions.md` D-0035 for
the full implementation trail.

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
| B9 | Verification-plan §1 (static prompt review) executed | 🟨 IN DESIGN | Controller | Line-by-line review of `prompt-proposed.md` files. |
| B10 | Verification-plan §2 (deterministic engine math) executed | ❌ Requires code | Claude on Controller cue | Simulator tests before any broker call. |
| B11 | Verification-plan §3 (broker dry-run) executed | ❌ Requires B1–B5 done | Claude on Controller cue | `no_submit` flag; only reads. |
| B12 | Verification-plan §4 (approval-loop dry run) executed | ❌ Requires B3, B6 done | Claude on Controller cue | Approve / Reject / expired / price-drift / floor-priority. |
| B13 | Verification-plan §6 (timezone tests) executed | ❌ Requires code | Claude on Controller cue | Spring-forward and fall-back scenarios. |
| B14 | Existing account-level Routines disabled or reduced to no-op before Python engine goes live | ✅ DONE (D-0038, 2026-09-20) | Controller | Controller confirmed the legacy Routines that held the old credentials have been deactivated; no dual-writer risk against the Alpaca paper account. |
| B15 | Universe subsystem design + Controller approval | 🟨 IN DESIGN | Controller + Claude | Third-pass configurable design in `docs/trading/universe-selection-analysis.md` (9-stage pipeline, `ApprovedUniverseSnapshot` contract, every threshold a configurable parameter with no value proposed); second-pass evidence trail in `docs/trading/universe-parameter-validation.md`. Both PROPOSED / NOT APPROVED. Principles and architecture (§7A/§7B of the third-pass doc) are presented as safe to approve as direction; no numeric parameter is proposed for approval. TSLA is TEST-ONLY (D-0026 §5). No interim `["TSLA"]` fallback. Production engine will not trade until the subsystem can return a valid universe. Phase A architecture code (identity, snapshot, pipeline contracts, Evidence/Confidence Layer, dormant Stage F ranking scaffolding) implemented and committed (`src/d0026/`, `tests/d0026/`, uncommitted-to-remote — see D-0029). Stage F's architectural *boundary* is separately Controller-approved (D-0029, `docs/trading/stage-f-ranking-architecture.md`) — no ranking metric or numeric parameter is approved, and Stage F is not wired into the pipeline. |
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
