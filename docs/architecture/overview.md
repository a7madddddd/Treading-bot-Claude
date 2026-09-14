# Architecture Overview

This file is design guidance. It does not encode any approved trading
behavior — that lives in `../trading/strategy.md` and `../trading/execution.md`.

The concrete language / framework / package manager for this project is
**not yet chosen**. Before any implementation begins, Claude must present a
Master Plan (see `../../CLAUDE.md` §3) and get Controller approval.

---

## 1. Decision pipeline (conceptual)

```
Market Data
    ↓
Research (AI)
    ↓
AI Analysis (advisory)
    ↓
Signal
    ↓
Risk Engine (deterministic)
    ↓
Strategy Rules (approved policy)
    ↓
Controller Approval  ← for entries and ladders
    ↓
Execution Engine (idempotent)
    ↓
Alpaca Paper Trading
    ↓
Position Monitoring / Reconciliation
    ↓
Logs & Notifications
    ↓
Performance Evaluation → Research Feedback
```

- Deterministic risk and execution rules are separate from AI reasoning.
- AI never bypasses the risk engine or the active protective floor.

## 2. Skills architecture

One skill per capability. Small and composable. Initial set:

- **research** — Perplexity Agent API research. Returns market research,
  signals, watchlist, sources, confidence.
- **trade** — Alpaca paper-order placement. Enforces guardrails, checks
  positions via API before acting, idempotent.
- **journal** — Records reasoning and decisions.
- **benchmark** — Records portfolio value and benchmark performance.
- **report** — End-of-day report generation.

Do not copy an example architecture blindly; adapt to the project's actual
conventions once chosen.

## 3. Perplexity integration

- Use `PERPLEXITY_API_KEY` from env.
- Wrap in named research operations, not scattered API calls:
  `researchMarket`, `researchStock`, `researchNews`, `researchStrategy`,
  `researchRisk`, `researchTradingTechnology`, `researchBacktestingMethod`.
- Prefer lightweight configs for simple queries; use deeper presets only
  when justified.
- Research responses should include: question, summary, findings,
  evidence, sources, risks, confidence, recommendation, suggested next
  experiment.
- Research is advisory. It never becomes a trade directly. It becomes an
  experiment or a Controller proposal.

## 4. Alpaca integration

- Paper endpoint only. Assert `ALPACA_BASE_URL` at startup.
- Never assume fills; always query and reconcile (see `execution.md` §6).
- One authoritative active protective exit per position.
- Idempotent submission; duplicate prevention.

## 5. Routines (planning only — no cron yet)

Approved constraints (see `../trading/decisions.md`):

- **Timezone:** America/Chicago (D-0005). Scheduler must be TZ-aware
  so CST ↔ CDT DST transitions are handled without manual edits.
- **Calendar:** US market workdays only (D-0006). Weekends and US
  market holidays / early-close days are skipped.



**Do not create or modify cron/schedules yet.** Claude must first propose:

- routine-to-cron mapping
- exact schedule per routine
- timezone
- prompt / behavior per routine
- dependencies between routines
- shared-state / concurrency protection

Then wait for Controller approval.

Conceptual routines (times are examples, subject to Controller approval):

- **Pre-market (7:00 CT)** — market/news research, watchlist, candidate setups.
- **Market open (8:30 CT)** — evaluate approved strategy conditions,
  analyze candidates, prepare or (if approved) execute per policy, record.
- **Midday (11:00 CT)** — reassess positions, risk, ladder conditions,
  trailing-floor updates.
- **End of day** — review positions/risk, run approved exit checks
  (never auto-close just because it's EOD), update benchmark, journal,
  report, notify.

Constraints:

- Separate routines so they cannot corrupt shared state.
- Prefer at least 30 minutes between major routines unless technically
  required otherwise.
- Use the project's chosen scheduling mechanism — do not add a new
  scheduler without cause.

## 6. Notifications

Preferred first provider: **Telegram** (mobile push, simple bot, low
complexity). Credentials via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Levels:

- **CRITICAL** — order submitted / filled / rejected, ladder triggered,
  floor triggered, trailing activated, risk limit reached, unexpected
  position, API failure, system failure.
- **IMPORTANT** — pre-market report, watchlist generated, major research
  update, strategy recommendation, end-of-day report.
- **OPTIONAL** — detailed logs, debugging info (usually stay in logs, not
  notifications).

Abstraction:

```
INotificationService
      ↓
TelegramNotificationService
```

The trading engine depends on the abstraction, not on Telegram directly,
so another provider can be added later without rewriting the engine.

Notifications are never the source of truth. See `../../CLAUDE.md` §6.

## 7. Logging

Structured logs. Every trading action must include enough context to
reconstruct what happened.

Suggested fields:

`timestamp, event, symbol, price, quantity, averageEntry, positionSize,
orderId, orderStatus, strategyRule, riskState, reason, source,
aiRecommendation, approvedDecision, error`

Never log secrets, API keys, or credentials.

Separate:

- **Application logs** — detailed, searchable, for engineers.
- **User notifications** — concise, actionable, for the Controller.

## 8. Secrets

See `../../CLAUDE.md` §8 and `../../.env.example`.

## 9. Testing strategy

Before any trading behavior ships:

- Unit tests for pure logic (ladder math, trailing math, risk math).
- Property tests for invariants: trailing floor never decreases; active
  floor never falls; Ladder never fires below active floor.
- Integration tests against Alpaca paper endpoint (mocked and live-paper).
- Reconciliation tests: partial fills, rejects, cancels, network errors,
  duplicate-submit prevention.
- Backtests per `../trading/backtesting.md`.
- Smoke tests before each deploy.

## 10. Development phases (proposal)

1. Bootstrap: language/framework choice, secrets, env, logging, telegram
   pinger, alpaca "hello paper" client, no strategy yet.
2. Deterministic strategy engine (frozen policy from `strategy.md`),
   simulation only.
3. Reconciled Alpaca paper execution + protective-order management +
   idempotency.
4. Approval workflow (proposals + Telegram approve/reject + expiration).
5. Perplexity research skill + research log wiring.
6. Routines + scheduler (Controller-approved schedule).
7. Backtesting harness.
8. Benchmark + EOD report.
9. Experiment framework.

Each phase requires a Controller-approved plan.
