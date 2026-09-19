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
- **Current price** — the latest **Alpaca Last Trade** for the symbol
  (D-0012); the trigger evaluation and the proposal display both use
  this source
- Proposed shares
- Current position and weighted-average entry
- Estimated dollar risk at Floor after this fill
- Reason / context

This list is the **minimum** shown to the Controller. The full
persistent proposal + approval record — with every field the engine
must store and reconcile (proposal_id, approval_state, approval
timestamps, active_floor snapshot, etc.) — is defined in
`../architecture/state-management.md §Approval workflow state`.

### Ladder approval expiration — APPROVED (D-0007)

- Approval authorizes execution **only while both conditions hold**:
  1. **Time:** no more than **5 minutes** since Controller approval, AND
  2. **Price:** market price is within **±0.5%** of the trigger price.
- Both conditions must be **re-checked immediately before submitting
  the paper order**, not only at the moment approval is received.
- If either condition fails, the proposal expires and a new Controller
  approval is required. Approval at one price never automatically
  authorizes execution at a materially different price.

## 4. Order type and Ladder execution range (D-0033)

All orders remain **Limit Orders** — Market Orders are never used anywhere
in this system.

- **Initial Entry:** Limit Order at `proposed_entry`. No execution range
  applied.
- **Ladder 1:** D-0007's trigger stays at **−5%**, unchanged. The BUY
  **Limit price** sent to the broker is the upper boundary of a separate,
  fixed **1-percentage-point execution range**: **−4%**.
- **Ladder 2:** D-0007's trigger stays at **−8%**, unchanged. The BUY
  **Limit price** sent to the broker is the upper boundary of the same
  1-percentage-point execution range: **−7%**.
- The execution-range Limit price and D-0007's trigger price are two
  distinct values computed from the same base price. **D-0007 always
  validates against the unchanged trigger price** (§3 above), never
  against the execution-range Limit price.
- No additional price buffer beyond this fixed 1-point range. No change
  to the approved strategy's trigger levels, quantities, or Floor.

Full detail: `decisions.md` D-0033.

## 5. Ladder 2 partial-fill handling (D-0034 — Ladder 2 only)

Ladder 2 requests the full 20 shares on every submission. Real market
liquidity at the Limit price is not predicted in advance — this
mechanism is purely reactive to what the broker actually reports:

1. If the broker reports a live, non-terminal **partial** fill
   (`0 < filled_qty < 20`), the system immediately requests cancellation
   of the remaining unfilled quantity. That observed quantity is never
   treated as final, and the Controller is not notified yet.
2. The system keeps reconciling until the broker reports a genuinely
   **terminal** state. Only the broker's terminal `filled_qty` is
   authoritative.
3. **Final = 20** (including when cancellation loses the race and the
   order fully fills anyway): applied automatically as a normal full
   Ladder 2 fill. No Controller approval required.
4. **Final between 1 and 19:** Trade is **not** updated automatically.
   Controller approval is required before the reduced quantity is
   recorded as the Ladder 2 fill. Once approved, the ladder is marked
   PASSED/COMPLETED using the actual filled quantity. The unfilled
   remainder is **permanently forfeited** — there is no automatic retry
   or top-up.
5. **Final = 0:** no Trade update; no Controller approval needed.
6. A failed or ambiguous cancellation request is retried safely on a
   later reconciliation pass; it never aborts processing of this or any
   other execution.

This mechanism is scoped to **Ladder 2 only**. Ladder 1 partial fills are
not auto-applied to Trade either, but have no confirmation path — they
remain unrepresented pending a separate, future Controller decision.

Full detail: `decisions.md` D-0034.

## 6. Floor SELL execution (D-0035)

Floor (−10% → SELL ALL) and the activated Trailing Floor never go
through the Controller-approval gate (§1/§2) — this section is the
mechanics of that automatic execution.

- **Detection cadence:** the faster, independent reconciliation cadence
  used for order-state polling, **not** the hourly schedule used for
  Ladder/Initial-Entry trigger detection — a protective exit benefits
  from faster detection than a discretionary entry does. This does not
  change the hourly schedule itself, which still governs only
  Ladder/entry detection.
- **Quantity:** always the currently held share count (SELL ALL), read
  fresh at the moment of submission.
- **Order type:** Limit Order, never a Market Order, same as every
  other order in this system. The **execution range is −1% to −0.5%**
  off the current price at the moment Floor fires; the lower (−1%)
  boundary is the actual submitted Limit price — mirrors the same
  execution-range reasoning already used for Ladder 1/Ladder 2 (§4),
  applied symmetrically to a SELL (a sell limit executes at the
  specified price or better).
- **Partial fill:** applied automatically and immediately, **no
  Controller confirmation gate** (unlike Ladder 2, §5) — a protective
  exit must never sit waiting on a human response while shares remain
  unprotected. Any unfilled remainder is automatically re-offered for
  exit on the Engine's next cycle — never forfeited, unlike Ladder 2's
  remainder.
- **Duplicate prevention:** never submitted while a live (non-terminal)
  protective-exit order already exists for the position.
- **Trailing Floor:** the active floor value used for trigger
  comparison already reflects the compounded ratchet (`strategy.md`
  §5) — Floor execution above needs no separate logic for the
  Trailing case; it operates on whichever value is currently active.

Full detail: `decisions.md` D-0035.

## 7. Active protective floor priority

See `strategy.md` §4. Summary for execution:

- Compute the active protective floor before every order decision.
- Block any Ladder whose trigger price is at or below the active floor.
- Never lower the active floor to permit a Ladder.
- Protective exits win over ladders, always.

## 8. Protective order management

Maintain **one** authoritative active protective exit per position.

- Do not create duplicate protective sell orders.
- When the Trailing Floor ratchets up, safely update / replace the active
  protective order using Alpaca's supported order-management behavior
  (replace where possible; otherwise cancel-then-submit with reconciliation).
- Reconcile open orders and actual position state **before** creating or
  replacing protective orders.
- Cancellation/replacement must not leave the position uncovered longer
  than is technically unavoidable.

**Current implementation status (D-0035):** the "replace/cancel-then-
submit a standing protective order" model described above is the
longer-term design direction, not yet built. The current, paper-trading
implementation instead re-evaluates the trigger on each reconciliation
cycle and submits a fresh Limit SELL only when actually triggered (§6)
— no standing resting order is placed or maintained at the broker. This
is sufficient for the current polling cadence; a standing order is a
future latency optimization, deferred, not rejected.

## 9. Alpaca discipline

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

## 10. Source of truth

- Position and order state: broker API + local reconciled state.
- Not notifications. A failed Telegram send never triggers a retry of the
  trade and never causes a duplicate order.

## 11. Environment guard

At startup, assert:

- `ALPACA_BASE_URL` points at the paper endpoint.
- Live-trading URLs are refused.
- A missing or malformed env fails closed (no orders).
