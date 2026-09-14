# Execution and Approval Workflow

Paper trading only. Alpaca paper endpoint only. This document defines how
signals become orders.

---

## 1. Execution mode

The system is **semi-automatic**:

- The approved strategy runs automatically for monitoring, calculations,
  and trigger detection.
- The system does **not** submit a new trade unless the required Controller
  approval for that trade has been received.
- Already-approved protective exits (original Floor, activated Trailing
  Floor) may execute automatically in paper trading when their trigger is
  reached.

## 2. Approval required for

- Initial entry
- Ladder 1
- Ladder 2
- Any discretionary or additional entry

Approval is **not** required for the original Floor or the activated
Trailing Floor executing at their approved triggers.

## 3. Ladder approval workflow

```
Ladder trigger detected
        ↓
Prepare proposed order (pending trade proposal)
        ↓
Notify Controller
        ↓
WAIT for approval
        ↓
Approved  → submit paper order
Rejected  → do not submit
Expired   → do not submit; require new approval if trigger re-fires
```

### Proposal contents (minimum)

- Symbol
- Trigger price and rule (e.g. Ladder 1 at −5%)
- Current market price
- Proposed shares
- Current position and average entry
- Estimated dollar risk at Floor after this fill
- Reason / context

### Ladder approval expiration — TBD (D-0007)

- Approval authorizes execution **only while the Ladder remains valid**.
- If price moves materially away from the trigger, the proposal must
  **expire** and require new Controller approval.
- Approval at one price never automatically authorizes execution at a
  materially different price.
- Exact validity band (percentage move, time window, or both) is
  **TBD** per D-0007. Do NOT invent numeric defaults. The architecture
  should support an expiration mechanism; the policy values are set
  later by the Controller.

## 4. Active protective floor priority

See `strategy.md` §4. Summary for execution:

- Compute the active protective floor before every order decision.
- Block any Ladder whose trigger price is at or below the active floor.
- Never lower the active floor to permit a Ladder.
- Protective exits win over ladders, always.

## 5. Protective order management

Maintain **one** authoritative active protective exit per position.

- Do not create duplicate protective sell orders.
- When the Trailing Floor ratchets up, safely update / replace the active
  protective order using Alpaca's supported order-management behavior
  (replace where possible; otherwise cancel-then-submit with reconciliation).
- Reconcile open orders and actual position state **before** creating or
  replacing protective orders.
- Cancellation/replacement must not leave the position uncovered longer
  than is technically unavoidable.

## 6. Alpaca discipline

Never assume an order filled. After every order:

1. Submit order
2. Capture order ID
3. Query order/position
4. Verify status
5. Verify actual fill information (price, quantity, timestamp)
6. Update internal state (source of truth)
7. Log the result (see `../architecture/overview.md` §Logging)
8. Notify the Controller when appropriate

Handle explicitly: rejected orders, partial fills, cancelled orders,
delayed fills, API failures, stale market data, duplicate execution
attempts, network failures.

Execution must be **idempotent** where possible — a retried submission
must never produce a second real order.

## 7. Source of truth

- Position and order state: broker API + local reconciled state.
- Not notifications. A failed Telegram send never triggers a retry of the
  trade and never causes a duplicate order.

## 8. Environment guard

At startup, assert:

- `ALPACA_BASE_URL` points at the paper endpoint.
- Live-trading URLs are refused.
- A missing or malformed env fails closed (no orders).
