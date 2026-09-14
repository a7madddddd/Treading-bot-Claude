# Pre-APPLY Checklist

Single source of truth for what must happen before the Controller
writes "APPLY THE CHANGES". Updated when a blocker moves.

**Current gate status:** NOT ready for APPLY. Design decisions largely
resolved; several implementation items remain, and no code has been
written.

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
| B1 | Rotate Alpaca paper API keys | ❌ BLOCKER | Controller | Old keys leaked; live routines still hold them. |
| B2 | Rotate Perplexity API key | ❌ BLOCKER | Controller | Pasted in chat; treat as compromised. |
| B3 | Rotate Telegram bot token | ❌ BLOCKER | Controller | Pasted in chat; treat as compromised. |
| B4 | Populate `.env` with new secrets locally | ❌ BLOCKER | Controller | `.env` gitignored; never committed. |
| B5 | Deployment host chosen for the Python process | ❌ BLOCKER | Controller + design | Any host that gives us a stable process + `tzdata`. Options: user's machine, small VM, container-as-a-service. |
| B6 | `TELEGRAM_ADMIN_USER_IDS` env var populated with Controller's Telegram user id | ❌ BLOCKER | Controller | Determines who can Approve/Reject. |
| B7 | Repository abstraction for SQLite drafted (interface only, no impl) | 🟨 IN DESIGN | Claude | Design will be added under `docs/architecture/state-repository.md` when Controller says to. |
| B8 | Engine skeleton drafted (interface only, no impl) | 🟨 IN DESIGN | Claude | Symbol-agnostic per D-0026; awaiting Controller cue. |
| B9 | Verification-plan §1 (static prompt review) executed | 🟨 IN DESIGN | Controller | Line-by-line review of `prompt-proposed.md` files. |
| B10 | Verification-plan §2 (deterministic engine math) executed | ❌ Requires code | Claude on Controller cue | Simulator tests before any broker call. |
| B11 | Verification-plan §3 (broker dry-run) executed | ❌ Requires B1–B5 done | Claude on Controller cue | `no_submit` flag; only reads. |
| B12 | Verification-plan §4 (approval-loop dry run) executed | ❌ Requires B3, B6 done | Claude on Controller cue | Approve / Reject / expired / price-drift / floor-priority. |
| B13 | Verification-plan §6 (timezone tests) executed | ❌ Requires code | Claude on Controller cue | Spring-forward and fall-back scenarios. |
| B14 | Existing account-level Routines disabled or reduced to no-op before Python engine goes live | ❌ BLOCKER | Controller | Prevents dual-writer conflicts. |
| B15 | Universe subsystem design + Controller approval | 🟨 IN DESIGN | Controller + Claude | Design proposal drafted in `docs/trading/universe-selection-analysis.md` (PROPOSED / NOT APPROVED). TSLA is TEST-ONLY (D-0026 §5). No interim `["TSLA"]` fallback. Production engine will not trade until the subsystem can return a valid universe. |

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
