# Telegram Approval Transport (D-0025)

Delivery mechanism for Ladder 1 / Ladder 2 proposals. Design only —
no implementation. D-0007 enforcement stays inside the trading engine,
not in the bot.

---

## 1. Actors

- **Trading engine** — Python process (D-0022) running under the
  TZ-aware scheduler (D-0023) that writes to and reads from the
  SQLite state store (D-0024).
- **Telegram bot** — Python-based bot process (or in-process handler)
  authenticated with `TELEGRAM_BOT_TOKEN`. Talks to Telegram Bot API,
  reads/writes only its own tables in the state store.
- **Controller** — one or more authorized Telegram user ids
  (`TELEGRAM_ADMIN_USER_IDS`, comma-separated env var).

Any user id not on the authorized list is silently rejected at the
bot layer (button press logged, ignored, no state change).

## 2. Proposal message (bot → Controller)

The bot renders a proposal as one message with an inline keyboard.

Minimum text:

```
🟨 Ladder 1 proposal for TSLA
Trigger:     $95.00 (−5% from initial fill $100.00)
Current:     $94.87 (Alpaca Last Trade, 2026-09-14 14:32:11 CT)
Qty:         +10 shares
Position:    10 shares @ avg $100.00 → would become 20 @ $97.44
Original Floor: $90.00 (unchanged)
Active Floor:   $90.00
Est. loss at Floor: −$194 (5.02% of deployed capital)
Proposal id:  P-2026-09-14-0001
Expires:      approval valid ≤5 min; price band ±0.5%
```

Inline keyboard: `[✅ Approve]` `[❌ Reject]`.

Values come straight from the proposal record in state
(`state-management.md §Approval workflow state`).

## 3. Round-trip flow

```
engine detects Ladder trigger
    │
    ▼
engine writes proposal row  (state.proposals INSERT)
    │
    ▼
engine sends Telegram message via bot
    │        (message_id stored on proposal row)
    ▼
Controller taps Approve or Reject
    │
    ▼
Telegram Bot API webhook → bot handler
    │
    ▼
bot verifies user id in TELEGRAM_ADMIN_USER_IDS
    │
    ▼
bot writes approval row  (state.approvals INSERT)
    │        (references proposal_id, sets approval_state,
    │         approval_received_at, telegram_user_id)
    ▼
engine, on next tick (or via a notify signal),
    reads pending approvals, and for each one calls
    submit_or_block(proposal, approval)
```

## 4. `submit_or_block` — engine-side enforcement of D-0007

```
def submit_or_block(proposal, approval):
    if approval.state != APPROVED:
        return mark_blocked(proposal, "rejected")
    age = now() - approval.received_at
    if age > timedelta(minutes=5):
        return mark_blocked(proposal, "blocked_expired")
    p_last = alpaca.last_trade(proposal.symbol)
    if abs(p_last - proposal.trigger) / proposal.trigger > 0.005:
        return mark_blocked(proposal, "blocked_price")
    if proposal.trigger <= state.active_floor(proposal.symbol):
        return mark_blocked(proposal, "blocked_floor_priority")
    order_id = alpaca.submit_market_buy(
        symbol=proposal.symbol,
        qty=proposal.qty,
        client_order_id=proposal.id,  # idempotency
    )
    persist_submission(proposal, order_id)
```

The three re-check conditions match D-0007 exactly. The
`client_order_id = proposal.id` gives Alpaca idempotency so a retry
never duplicates the order.

## 5. Failure modes and their responses

| Failure | Response |
|---|---|
| Bot cannot send Telegram message | Proposal row gets `notification_state = FAILED`; CRITICAL log; engine does NOT retry the trade; engine may retry the message on the next tick (bounded, e.g. ≤ 3 tries) and then give up. No implicit approval. |
| Controller Approves after > 5 min | Blocked_expired. New Ladder trigger tick creates a fresh proposal only if the price condition still holds. |
| Price moves ±0.5% after Approve | Blocked_price. Same fresh-proposal rule. |
| Trailing floor rises above trigger between Approve and submit | Blocked_floor_priority. Ladder is unreachable this trade. |
| Bot restart while proposal pending | Proposal row persists in SQLite; bot rehydrates open proposals on start; Controller can still tap the same message; approval flow completes. |
| Duplicate button press | State layer's `if_version` / unique constraint on `(proposal_id, approval_state)` prevents duplicate writes. |
| Non-authorized user presses Approve | Logged with user id; nothing written; Controller receives no acknowledgement (avoid oracle for probing). |

## 6. State tables (contract, not schema)

Handled by the D-0024 repository abstraction. Minimum tables:

- `proposals` — one row per proposal (from `state-management.md`).
- `approvals` — one row per Controller decision on a proposal.
- `submissions` — one row per submitted order attempt (idempotent by
  `client_order_id = proposal.id`).
- `notifications` — audit trail of Telegram sends/retries and their
  outcomes.

Concrete schemas are drafted alongside the engine, not here.

## 7. Explicitly not decided here

- Bot deployment target (same process as the engine vs sidecar).
  Same-process is simpler for MVP and is compatible with D-0023.
- Message templates beyond the minimum in §2.
- Rate limits / retry backoff for Telegram sends.
- Whether reject requires a reason field.
- Multi-Controller / multi-signature approvals.

These stay TBD; the engine's D-0007 enforcement does not depend on any of them.
