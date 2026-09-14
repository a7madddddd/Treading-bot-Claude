# State Management — design directive (D-0018)

The trading engine and routines must maintain deterministic, recoverable
state. A restart must never lose the frozen references, the current
active floor, or in-flight approval state.

The concrete store (SQLite file, JSONL journal, a persistent broker
note, a small key-value store, etc.) is TBD pending language/runtime
choice. This document fixes the **design principles and the required
fields**, not the storage medium.

---

## 1. Required fields (per active trade)

Each active trade record must at minimum carry:

### Entry references (frozen)

- `symbol`
- `trade_id` — internal identifier for the trade lifecycle
- `initial_order_id` — Alpaca order id of the initial entry
- `initial_order_status` — one of {pending, partially_filled, filled, cancelled, expired}
- `initial_order_reconciled` — bool (true once the initial order is fully filled OR fully closed as cancelled/expired)
- `original_initial_entry_fill_price` — **frozen** weighted-average fill of the initial entry order only, set exactly once on `initial_order_reconciled = true`
- `initial_filled_shares` — actual filled qty at the moment of freeze
- `freeze_timestamp`

### Derived original levels (frozen once reference is frozen)

- `ladder1_price` = `original_initial_entry_fill_price × 0.95`
- `ladder2_price` = `original_initial_entry_fill_price × 0.92`
- `original_floor_price` = `original_initial_entry_fill_price × 0.90`

Written once. Never recomputed. Never overwritten by later fills.

### Ladder trigger / execution state

- `ladder1_triggered_at` (nullable timestamp)
- `ladder1_proposal_id` (nullable)
- `ladder1_filled` (bool)
- `ladder1_fill_order_id`, `ladder1_fill_price`, `ladder1_fill_qty`
- Same fields for `ladder2_*`

### Live risk metrics (computed, not frozen)

- `total_shares` — total live position
- `weighted_avg_entry_price` — recomputed after every fill; used for
  trailing activation and reporting, never for original levels

### Trailing floor state

- `trailing_activated` (bool)
- `trailing_current_threshold` — the last threshold crossed
  (`weighted_avg_entry × 1.10` on activation; multiplied by 1.05
  compounded on each subsequent crossing)
- `trailing_floor_price` = `trailing_current_threshold × 0.95`
- `trailing_last_updated_at`
- **Invariants:**
  - `trailing_floor_price` monotonically non-decreasing over time
  - `trailing_current_threshold` monotonically non-decreasing

### Active protective floor

- `active_floor_price` = `max(original_floor_price, trailing_floor_price if trailing_activated else -Inf)`
- **Invariant:** `active_floor_price` is monotonically non-decreasing.
- `active_floor_source` = "original" or "trailing"

### Protective order state

- `protective_order_id` — the current authoritative sell stop
- `protective_order_stop_price`
- `protective_order_status`
- Cancellation-replacement lineage (previous ids, for audit)

### Approval workflow state (per pending proposal)

- `proposal_id`
- `ladder_number` (1 or 2)
- `trigger_price`
- `current_price_at_proposal`
- `weighted_avg_entry_at_proposal`
- `original_initial_entry_fill_price`
- `active_floor_at_proposal`
- `requested_qty`
- `estimated_dollar_risk_at_floor`
- `reason`
- `proposal_created_at`
- `approval_state` — one of {pending, approved, rejected, expired}
- `approval_received_at`
- `approval_expires_at` = `approval_received_at + 5 minutes`
- `submitted_order_id` (nullable)
- `submit_decision` — one of {submitted, blocked_price, blocked_expired, blocked_floor_priority}

### Reconciliation

- `last_broker_poll_at`
- `last_reconciled_at`
- `last_error` (nullable)

## 2. Invariants (must hold across every write)

1. `original_initial_entry_fill_price` is written exactly once. Second-write attempts must fail.
2. `original_floor_price`, `ladder1_price`, `ladder2_price` are written exactly once.
3. `active_floor_price(t+1) ≥ active_floor_price(t)`.
4. `trailing_floor_price(t+1) ≥ trailing_floor_price(t)`.
5. Ladder N submission requires: proposal approved AND now − approval_received_at ≤ 5 min AND |current − trigger|/trigger ≤ 0.005 AND trigger > active_floor_price.
6. A Ladder is never submitted if its trigger price ≤ active_floor_price.
7. Protective order is never cancelled without a successor being ready to place (single-window minimization).

## 3. Recovery contract

On routine start:

1. Load persisted state for the symbol.
2. Reconcile against broker: `/v2/orders/{initial_order_id}`, `/v2/positions/{symbol}`, `/v2/orders?status=open&symbols={symbol}`.
3. If broker state contradicts persisted state, prefer broker as source of truth for order and position identity; recompute derived state; log the discrepancy.
4. Refuse to submit new orders until reconciliation succeeds.

## 3a. Runtime environment guard (cross-reference)

The runtime that hosts this engine must assert, at startup, the
paper-endpoint guard specified in `docs/trading/execution.md §8`:

- `ALPACA_BASE_URL` points at the paper endpoint.
- Live-trading URLs are refused.
- A missing or malformed env fails closed — no orders, no state
  mutation.

State management does not enforce this itself; it depends on the
runtime having done so before the first write.

## 4. What we are NOT deciding here

- Storage medium (SQLite, JSON, DB, broker note, etc.). Deferred to
  the language/runtime decision.
- Multi-trade / multi-symbol scaling. Design supports it (records
  are keyed by `trade_id` / `symbol`); implementation deferred.
- Cross-routine locking. Deferred; the design assumes one writer at a
  time per symbol.
