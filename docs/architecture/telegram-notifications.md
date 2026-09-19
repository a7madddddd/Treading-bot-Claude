# Telegram Notification Integration (Phase 1 — implemented)

**Status: IMPLEMENTED, real and tested, NOT wired into any live/production
execution path.** Outbound notifications only. No inbound commands, no
polling, no webhook, no approval buttons, no trading authority of any
kind. Paper trading only remains unaffected — this integration cannot
place, modify, or cancel any order.

Cross-references: `docs/architecture/overview.md` §6 (the original
notification-abstraction design this implements), `docs/architecture/telegram-approval.md`
(D-0025 — the separate, still design-only, future approval-transport
work; not implemented, not affected by this change), `docs/trading/execution.md`
("a failed Telegram send never triggers a retry of the trade and never
causes a duplicate order" — the rule this implementation is built to
satisfy structurally), `docs/trading/decisions.md` D-0032.

---

## 1. What was implemented

- `src/notifications/service.py` — `INotificationService` (ABC),
  `NotificationEvent`, `NotificationLevel` (`CRITICAL` / `IMPORTANT` /
  `OPTIONAL`, matching `overview.md` §6's existing severity model),
  `NotificationResult`.
- `src/notifications/telegram.py` — `TelegramNotificationService`, a
  real Telegram Bot API adapter using `sendMessage` over HTTPS, stdlib
  `urllib.request` only (no third-party dependency — this project has
  none anywhere under `src/`).
- `tests/notifications/` — 26 unit tests, all using an injected fake
  HTTP transport; no real network call and no real bot token is used or
  needed in the test suite.

This is real, working, tested code — not dormant scaffolding — per the
Controller's explicit instruction that Phase 1 not end as scaffolding
only. It is simply **not called from anywhere in the live system yet**,
because (per repository inspection) the live system has no persistent
Python process for it to be called from — see §5.

## 2. Configuration variables

No new variables. Reuses what `.env.example` already declared:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

`TelegramNotificationService.from_env()` reads both from the process
environment and raises `TelegramConfigError` (a construction-time-only
error, never raised from `send()`) if either is missing or empty. The
token/chat id values are never logged and never appear in any exception
message or `NotificationResult`.

## 3. Setup steps (for whoever eventually wires this in)

1. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` (never
   commit `.env`; see `.env.example` and CLAUDE.md §8).
2. Construct `TelegramNotificationService.from_env()` once, at process
   startup, in whatever future component owns notification dispatch.
3. Call `.send(NotificationEvent(...))` for each event; always inspect
   the returned `NotificationResult` for logging purposes only — never
   branch trading logic on it (§4).

## 4. Notification responsibilities and failure isolation

- `send()` **never raises**. Every failure path (network error, 4xx,
  5xx, malformed response) returns `NotificationResult(success=False,
  ...)` instead of propagating an exception.
- Transient failures (network errors, HTTP 429/500/502/503/504) are
  retried with bounded exponential backoff (default: 3 attempts).
- Permanent failures (e.g. HTTP 401 bad token, 400 bad chat id, 404) are
  **not** retried — retrying a configuration/authentication problem
  wastes calls and cannot succeed.
- Nothing in this module can submit, cancel, or modify an order, read
  position/account state, or influence a trading decision — it has no
  such capability by construction. This satisfies `execution.md`'s rule
  structurally, not just by convention.

## 5. Current scope limitations — explicit

- **No inbound Telegram commands are implemented.** No polling
  (`getUpdates`), no webhook, no `/status`, `/positions`, `/orders`, no
  approve/reject buttons, no trading or operational commands. All of
  this remains future scope (D-0025 covers the approval-transport half
  of it, design only).
- **Not wired into any live execution path.** Repository inspection
  (this change) confirmed: the project's only live execution mechanism
  today is a set of Claude Code Routines (`routines/*/prompt.md`) —
  scheduled, prompt-driven agent sessions that issue raw `curl` calls to
  the Alpaca API directly from their prompt text. They have **no runtime
  coupling to this repository's Python code** — nothing under `src/` is
  imported or executed by a live routine. Wiring a real notification
  event into a live routine would require either (a) editing that
  routine's live prompt and deploying it to the actual trigger via
  `update_trigger` (a production operational action on a live
  paper-trading account, out of scope for this change and requiring its
  own separate, explicit Controller authorization), or (b) building a
  new persistent Python process to host the trading engine described in
  `overview.md`'s later development phases, which does not exist yet.
  Neither was undertaken here, per explicit Controller instruction not
  to invent new runtime infrastructure or improvise a larger
  architectural change without stopping to report it first.
- **No production/live credentials were exercised.** This environment
  has no `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` configured (neither
  as environment variables nor a `.env` file) — the real-world send
  verification the Controller requested could not be performed and is
  **not** claimed to have succeeded. See the implementation report for
  the exact, honest finding.

## 6. What this document does NOT authorize

- No change to any live routine or live trigger.
- No inbound Telegram command handling of any kind.
- No trading/operational authority for Telegram.
- No new persistent process/daemon.
- No change to entry, Ladder 1, Ladder 2, Floor, position sizing, or
  risk rules.
- No live trading.
