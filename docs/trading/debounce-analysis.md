# D-0011 Analysis — Ladder Trigger Debounce

Analytical work for D-0011. Presents the problem, four candidate
approaches, a recommendation, and a concrete proposed rule.

**D-0011 remains PROPOSED / TBD** at the end of this document.
Nothing is APPROVED until the Controller explicitly says so.

---

## 1. What problem D-0011 is solving

The engine polls the market on a schedule (D-0023) — currently once per
hour on the half-hour during the session (D-0021). Between two ticks
the Last Trade price (D-0012) may cross a Ladder trigger multiple
times. If the engine simply says "trigger fired whenever `p_last ≤
ladder_price`", then any of these can happen:

- **Duplicate proposals.** The same Ladder level emits multiple
  proposals for the same trade.
- **Duplicate approvals.** The Controller sees two proposals, approves
  both, and the engine attempts to submit twice.
- **Duplicate orders.** Two market buys land on Alpaca for the same
  Ladder level.
- **Approval-noise proposals.** Immediately after a proposal expires or
  is rejected, if the price is still below the trigger, a new proposal
  is created instantly — the Controller feels spammed.

Illustrative price micro-sequence around Ladder 1 = $95.00:

```
tick   p_last
1      95.03   (above)
2      94.98   (crossed — trigger)
3      95.02   (recovered)
4      94.99   (crossed again — trigger)
5      95.05   (recovered)
6      94.96   (crossed again — trigger)
```

Without any debounce, three proposals emit for the same event.

The purpose of D-0011 is to define, deterministically, when a Ladder
level is allowed to emit a new proposal.

### What D-0011 does NOT need to solve

- **Broker-level duplicate submission.** Handled by D-0025's
  `client_order_id = proposal.id`. Alpaca refuses two submits with the
  same client id.
- **Two ticks arriving in the same second.** The scheduler cadence
  (hourly) makes this a non-issue for the run loop. But we still need
  to defend against multiple `submit_or_block` invocations across
  restarts and reconciliations.
- **Portfolio-wide risk limits.** Separate concern (still TBD in
  `risk-management.md`).

## 2. Candidate approaches

For each approach: **exact behavior; advantages; disadvantages;
determinism; partial-fill interaction; approval-expiration interaction;
D-0007 interaction; multi-ladder interaction; restart; reconciliation;
suppression risk; duplicate-order defense; complexity.**

### A. Time-based debounce

**Behavior.** Once a Ladder level fires, ignore further triggers on
that level for N seconds (or minutes).

**Advantages.**
- Trivial to implement.
- Works even without a durable state store (a timestamp is enough).

**Disadvantages.**
- Arbitrary N. No principled value. 30s and 300s are both defensible;
  neither has a market or trading rationale.
- Silently suppresses legitimate re-triggers if the price stays below
  the level for longer than N. Example: proposal is rejected, price
  keeps falling, and 6 minutes later the trigger fires again "for
  real" — but the engine ignored it because it was still within N.
- Weak against session gaps and process restarts (see below).

**Determinism / replayability.** Depends on wall-clock. Replaying a
tick log at a different clock speed produces different suppression
decisions.

**Partial fills.** No interaction; time-based debounce is agnostic to
fills.

**Approval expiration (D-0007).** Poor. If a proposal expires at t=5m
and the debounce window is 10m, the engine cannot re-propose even
though the market is still below trigger. Inverse case is also
possible.

**D-0007 5-min / ±0.5%.** Independent; can coexist but does not use
D-0007's material-move band.

**Multi-ladder.** Applied per level.

**Restart.** Timestamp must persist. If the process restarts and the
last-fire timestamp was in memory only, the debounce evaporates.

**Reconciliation.** No natural coupling to broker state.

**Suppression risk.** HIGH — arbitrary window suppresses real events.

**Duplicate-order defense.** Weak by itself; relies on the N being
"long enough". Combined with `client_order_id` idempotency it's fine
against duplicate submits, but not against duplicate approvals from
duplicate proposals emitted just after N elapsed.

**Complexity.** Very low.

### B. State-based debounce (per-level state machine)

**Behavior.** Each Ladder level has a per-trade state machine. Once a
level enters a non-idle state (`PROPOSAL_PENDING`, `APPROVED`, `EXECUTED`),
it cannot emit a new proposal until it returns to an eligible state.
Terminal states like `EXECUTED` are DEAD for the rest of the trade (a
Ladder can trigger only once per trade — strategy.md §3).

**Advantages.**
- Aligned with the strategy: `strategy.md §3` says each Ladder can
  trigger only once per trade. A state machine implements exactly
  that.
- Deterministic and testable.
- Naturally survives restart (state persisted per D-0018 / D-0024).

**Disadvantages.**
- Does not by itself handle the "price oscillates around trigger after
  a proposal is rejected/expired" case. If the level goes
  `PROPOSAL_PENDING → BLOCKED_EXPIRED → IDLE` and the price is still
  below trigger, the very next tick emits another proposal.

**Determinism.** Full. Replay of a tick log with the same state
produces identical decisions.

**Partial fills.** Native. `EXECUTED` state is reached only after
Alpaca confirms the ladder fill; partial fill remains `AWAITING_FILL`.

**Approval expiration.** Natively integrated: expiration is a state
transition (`PROPOSAL_PENDING → BLOCKED_EXPIRED → IDLE`).

**D-0007.** Compatible. D-0007 checks happen inside `submit_or_block`;
state machine tracks the outcome.

**Multi-ladder.** One state per level.

**Restart.** Fully recovers by reading state.

**Reconciliation.** State updates from broker reconciliation. If the
broker shows a filled Ladder 1 order that state doesn't, transition to
`EXECUTED`.

**Suppression risk.** Very low for legitimate re-triggers *after
terminal states* (nothing suppressed forever except EXECUTED and
BLOCKED_FLOOR_PRIORITY — both of which are meant to be terminal).
Weakness only in the oscillation case above.

**Duplicate-order defense.** Strong: only IDLE emits a proposal; only
PROPOSAL_PENDING transitions to APPROVED; only APPROVED calls
`submit_or_block`. Combined with D-0025's `client_order_id`, duplicate
orders are impossible.

**Complexity.** Moderate. Requires the state machine table and
persistence.

### C. Re-arm / hysteresis

**Behavior.** After a Ladder level fires, the level is "armed off". It
becomes eligible again only after the price moves **away from** the
trigger by some margin (hysteresis). For a downside ladder: the level
re-arms when `p_last ≥ trigger × (1 + h)` for some `h ≥ 0`. Then when
`p_last` next crosses back down to `≤ trigger`, a fresh proposal can be
created.

**Advantages.**
- Handles the oscillation case B is weak against.
- No arbitrary time constant — hysteresis is a price rule, which is
  the natural language of the domain.
- Deterministic and replayable (a function of the tick sequence).

**Disadvantages.**
- Alone, does not model in-flight proposals or approved state. You'd
  still need something to prevent a second proposal while the first
  is PENDING.
- Choosing `h` requires a rationale.

**Determinism.** Full.

**Partial fills.** Neutral. Hysteresis is about the trigger, not fills.

**Approval expiration.** Integrates well: after
`BLOCKED_EXPIRED`, level requires re-arm.

**D-0007.** Excellent synergy: reusing D-0007's 0.5% material-move
band as the hysteresis constant gives an evidence-based (not
invented) value with clear rationale ("meaningful move" is already
defined by D-0007).

**Multi-ladder.** One hysteresis flag per level.

**Restart.** Hysteresis state (re-armed / not-re-armed) must persist.

**Reconciliation.** No natural coupling; sits alongside state
reconciliation.

**Suppression risk.** LOW. If the market truly does not recover to
`trigger × (1 + h)`, the level stays armed off — but if the market
never recovers, it's already crossed and the previous proposal
outcome (EXECUTED / REJECTED / EXPIRED) governs what happens next.
Worst case: after Controller REJECT, the level stays "armed off"
unless the market recovers — desirable, since a rejection is a real
decision.

**Duplicate-order defense.** Weak alone; needs to be paired with
state.

**Complexity.** Low-to-moderate.

### D. State + re-arm (combination)

**Behavior.** Combine B and C. Each level has a state machine as in B.
When the state re-enters IDLE (from any non-terminal outcome), an
additional **armed** flag is required. The armed flag is set to
`False` when the level fires; it is set back to `True` when the Last
Trade price reaches `trigger × (1 + h)`. Only an armed level in IDLE
can emit a new proposal.

Terminal states (`EXECUTED`, `BLOCKED_FLOOR_PRIORITY`, `REJECTED`) are
DEAD for the rest of the trade — no re-arm, no re-trigger. The
strategy already says "each Ladder can trigger only once per trade"
and a Controller REJECT should not be overridable by market noise.

Non-terminal outcomes (`BLOCKED_EXPIRED`, `BLOCKED_PRICE`) return to
IDLE. From IDLE, the level requires re-arm before another proposal.

**Advantages.**
- Handles duplicates (state) AND oscillation (hysteresis).
- Reuses an already-approved number (D-0007's 0.5%) for the
  hysteresis constant — no invented value.
- Deterministic and replayable.
- Fully survives restart and integrates naturally with reconciliation.

**Disadvantages.**
- Slightly more state to persist (armed flag per level).
- Requires the definition of DEAD vs revivable outcomes to be
  explicit — but that's an already-implied design.

**Determinism.** Full.

**Partial fills.** Natural (state machine handles it).

**Approval expiration (D-0007).** Native.

**D-0007.** Reuses the ±0.5% material-move band as the re-arm
threshold for **`BLOCKED_PRICE` only**. `BLOCKED_EXPIRED` does not
require re-arm (it is a human-availability event, not a market
signal — see §3). Under the current hourly cadence, this asymmetry
preserves responsiveness to a live opportunity while silencing
oscillation around the trigger. If cadence ever tightens to
sub-minute intervals, this rule should be revisited.

**Multi-ladder.** Independent per level.

**Restart.** State + armed flag reloaded from SQLite. Behavior
resumes deterministically.

**Reconciliation.** State transitions triggered by broker updates.

**Suppression risk.** LOW. Legitimate re-fire after a material
recovery is preserved. The only case suppressed is: rejection or
oscillation around trigger with no material recovery, which is by
design.

**Duplicate-order defense.** Strong. Only IDLE-armed emits a
proposal; only PROPOSAL_PENDING transitions to APPROVED; only
APPROVED calls `submit_or_block` with `client_order_id`.

**Complexity.** Moderate (state machine + one bool + one comparison).

### E. Others considered and rejected

- **Cool-down at proposal time (bot-side).** Rate-limiting inside the
  Telegram bot. Rejected because it puts safety logic in the delivery
  layer, not the engine, and can be bypassed if the bot restarts.
- **Signature-based dedup (SHA of proposal fields).** Handles exact
  duplicates but not "another proposal for the same level with a
  slightly different price". Insufficient.
- **Only-once-per-session.** Same problem as pure time-based; also
  contradicts the strategy's "once per trade" wording (trades can
  span sessions).

---

## 3. Recommendation

**Recommended: Approach D — state machine + re-arm hysteresis, applied
asymmetrically depending on which block occurred, with the re-arm
threshold reusing D-0007's approved 0.5% material-move band.**

The two block outcomes are semantically different and must be treated
differently:

- `BLOCKED_EXPIRED` is a **human availability** event — the market did
  not move; the Controller did not answer within 5 minutes.
  → State returns to `IDLE` with `armed = True`.
  → The next scheduled check re-proposes if the trigger still holds.
- `BLOCKED_PRICE` is a **market** event — at submit time the price was
  outside D-0007's ±0.5% band.
  → State returns to `IDLE` with `armed = False`.
  → Re-arm requires `p_last ≥ trigger × 1.005` (D-0007's band, reused).

Reasons:
- Aligned with the already-approved strategy ("each Ladder can trigger
  only once per trade" is a state machine).
- Handles all four duplicate risks (proposal, approval, order, spam).
- **Responsive to legitimate opportunities.** Under the current hourly
  cadence (D-0021: 7 checks per session), a proposal that expires
  because the Controller was briefly unavailable would otherwise be
  silently swallowed for the rest of the day — even though the
  strategy engine keeps sampling a valid trigger. Making
  `BLOCKED_EXPIRED` re-arm immediately closes that gap while capping
  worst-case notifications at ≤ 7 per level per day (one per hourly
  check while the trigger still holds — each corresponding to an
  independent market observation, not oscillation flicker within a
  burst).
- **Silent on price oscillation.** `BLOCKED_PRICE` uses the 0.5%
  hysteresis so a market flicker around the trigger does not produce
  a second ask until the price meaningfully recovers.
- No invented constant: the 0.5% comes from D-0007, which is already
  Controller-approved and represents a "material" price move for
  approval purposes; using the same value for "material recovery"
  after a price-block outcome is consistent domain language.
- Deterministic and replayable — behavior is a function of the ordered
  tick sequence and the per-level state.
- Survives restart via SQLite (D-0018 / D-0024).
- Integrates cleanly with reconciliation.

The mechanism is: **primarily a state machine**, augmented by a
re-arm rule that reuses D-0007's ±0.5% band **only for the market-based
block reason**, not for expiration or as a general time window.

### Why asymmetric

`BLOCKED_EXPIRED → armed = True` was reviewed against three
alternatives:

| Alternative | Notifications / day (worst) | Missed-opportunity risk | Noise risk |
|---|---|---|---|
| **A. armed = True** (recommended) | 7 (bounded by hourly cadence) | Zero — any active trigger surfaces on the next check | Low; each notification is an independent market observation |
| **B. Require p_last > trigger before re-arm** | 0 to 7, depending on tick pattern | Modest — long stays below trigger produce no re-ask | Low |
| **C. Require full 0.5% recovery (same as BLOCKED_PRICE)** | 0 to 1 | High — Controller can go a full day without another ask on a live trigger | Very low |

Under the hourly cadence B and C save few notifications but cost
missed opportunities. A is the only option that never suppresses a
legitimate opportunity because of a temporary Controller absence, and
the "7 per day worst case" corresponds to a genuine session-long
trigger — proportionate to the underlying event, not noise.

If cadence ever tightens to sub-minute intervals, the arithmetic
changes and this rule should be revisited in a follow-up D-0011
revision.

## 4. Concrete example — Ladder 1 at $95.00 (initial fill = $100)

`trigger = 95.00`; re-arm level = `trigger × 1.005 = 95.475`.

Initial state per level: `IDLE`, `armed=True`.

### 4a. Happy path (approved)

| tick | p_last | state before | action | state after |
|---|---|---|---|---|
| 1 | 95.03 | IDLE, armed | above trigger; no action | IDLE, armed |
| 2 | 94.98 | IDLE, armed | trigger fires → emit proposal P-1; state → PROPOSAL_PENDING; armed=False | PROPOSAL_PENDING |
| 3 | 95.02 | PROPOSAL_PENDING | no new proposal (state ≠ IDLE); Controller taps Approve | APPROVED |
| 4 | 95.10 | APPROVED | submit_or_block runs D-0007 checks; passes; order submitted; on fill → EXECUTED | EXECUTED (terminal) |

### 4b. Duplicate suppression (state)

| tick | p_last | state | action |
|---|---|---|---|
| 2 | 94.98 | IDLE, armed | trigger → PROPOSAL_PENDING, emit P-1, armed=False |
| 3 | 94.99 | PROPOSAL_PENDING | no new proposal (state ≠ IDLE) |
| 4 | 94.96 | PROPOSAL_PENDING | no new proposal (state ≠ IDLE) |
| 5 | 94.95 | PROPOSAL_PENDING | no new proposal (state ≠ IDLE) |

Duplicates suppressed. Alpaca `client_order_id = proposal.id` gives
final-mile duplicate defense at the broker.

### 4c. Two re-eligibility cases

**Case (i) — BLOCKED_EXPIRED** (Controller didn't answer within 5 min).
State returns to `IDLE` with `armed = True` immediately. Re-ask on
the very next scheduled check if the trigger still holds.

| check (hourly) | p_last | state | action |
|---|---|---|---|
| N   | 94.90 | IDLE, armed=True | trigger fires → emit P-1; armed=False |
| —   | (5 min elapses; no Controller response) | PROPOSAL_PENDING → BLOCKED_EXPIRED → IDLE, armed=True | |
| N+1 | 94.95 | IDLE, armed=True | ≤ trigger → emit P-2; armed=False |
| N+2 | 95.10 | PROPOSAL_PENDING (still, if P-2 unresolved) | no new proposal |

Worst case on hourly cadence: 7 asks per level per day if the trigger
holds all session and the Controller is unreachable — bounded and
proportionate.

**Case (ii) — BLOCKED_PRICE** (Controller approved but the price had
moved outside ±0.5% by submit time). State returns to `IDLE` with
`armed = False`. Re-arm requires material recovery.

| check (hourly) | p_last | state | action |
|---|---|---|---|
| N   | 94.90 | IDLE, armed=True | trigger fires → emit P-1; armed=False |
| N+ε | 94.30 | PROPOSAL_PENDING | Controller approves; submit_or_block: age ok, but 94.30 is 0.74% below trigger → BLOCKED_PRICE → IDLE, armed=False |
| N+1 | 94.70 | IDLE, armed=False | ≤ trigger BUT not armed → no proposal |
| N+2 | 95.10 | IDLE, armed=False | 95.10 < 95.475 → still not re-armed |
| N+3 | 95.55 | IDLE, armed=False | 95.55 ≥ 95.475 → armed=True |
| N+4 | 94.85 | IDLE, armed=True | trigger fires → emit P-2; armed=False |

If the price never rises to 95.475 for the rest of the trade, no
further proposals fire for this Ladder level. That is the intended
suppression on market-driven blocks.

### 4d. REJECT is terminal

Controller taps Reject → state REJECTED (terminal for this trade).
The Ladder level cannot fire again regardless of price movement in
this trade. A Controller rejection is a real decision; market noise
does not override it. To reconsider, the Controller starts a new
trade or explicitly resets the level (an out-of-band operator action
that is out of scope for D-0011).

### 4e. Floor-priority is terminal

If the trailing floor rises above the Ladder 1 price before P-1
executes, `submit_or_block` returns `BLOCKED_FLOOR_PRIORITY` and the
state becomes terminal. The Ladder is unreachable for this trade,
which is consistent with `strategy.md §4`.

## 5. Persisted state (per Ladder level, per trade)

Extends `docs/architecture/state-management.md`:

- `level` — 1 or 2
- `state` — one of `IDLE | PROPOSAL_PENDING | APPROVED | EXECUTED | REJECTED | BLOCKED_EXPIRED | BLOCKED_PRICE | BLOCKED_FLOOR_PRIORITY`
- `armed` — bool
- `last_transition_at` — timestamp
- `armed_flipped_at` — timestamp of last arm/disarm
- `terminal` — bool derived from state, but persisted for query ease

Invariants (extend §2 of state-management.md):

1. A proposal for level L is created only if `state[L] == IDLE AND
   armed[L] == True AND p_last ≤ trigger[L] AND trigger[L] >
   active_floor_price`.
2. `armed[L]` transitions to False when a proposal for L is created.
3. On transition into `BLOCKED_EXPIRED`, the level immediately
   transitions back to `IDLE` with `armed = True` (expiration is a
   human-availability event, not a market signal — see §3).
4. On transition into `BLOCKED_PRICE`, the level immediately transitions
   back to `IDLE` with `armed = False`; `armed` returns to True when
   `p_last ≥ trigger[L] × 1.005` AND `state[L] == IDLE` AND
   `terminal[L] == False`.
5. `state[L] ∈ {EXECUTED, REJECTED, BLOCKED_FLOOR_PRIORITY}` implies
   `terminal[L] == True`. Once terminal, no state transitions and no
   proposals for the rest of the trade.
6. On process restart, `armed[L]` is loaded from persisted values only;
   never inferred from a fresh price fetch. Any pending proposal older
   than 5 minutes at restart is immediately transitioned to
   `BLOCKED_EXPIRED` (and thus to `IDLE` with `armed = True`).

## 6. Behavior on restart and reconciliation

**Restart.** On startup the engine loads per-level state from SQLite.
No re-derivation from current market. If a proposal was PROPOSAL_PENDING
and > 5 min old, it is transitioned to BLOCKED_EXPIRED and the level
returns to IDLE with `armed=False` — a legitimate "material recovery"
must precede the next proposal.

**Reconciliation.** Broker state is authoritative for orders and
positions. If reconciliation shows Ladder N was submitted and filled
during downtime, transition to EXECUTED (terminal) and mark the
proposal accordingly. If a submitted order was rejected by the broker,
transition to BLOCKED_PRICE (or a broker-specific bucket) and require
re-arm before another proposal.

**Idempotency.** All transitions use SQLite `if_version` semantics
(state-management.md §Invariants). Two concurrent transitions on the
same level are safe — the loser retries or aborts.

## 7. Interaction with everything else

| Concern | D-0011 (this design) |
|---|---|
| D-0001 frozen references | Independent; state machine keys off the same triggers. |
| D-0003 approval required | State machine is exactly the gate. |
| D-0004 compounded trailing thresholds | Independent. |
| D-0007 (5 min + ±0.5%) | Enforced in `submit_or_block`; state transitions record the outcome. |
| D-0008 trailing floor threshold-based | Independent. |
| D-0009 partial-fill freeze | State ladder_N transitions to EXECUTED only after full fill; partial → AWAITING_FILL sub-state (implementation detail). |
| D-0010 partial+cancel initial | Ladder state machines are per-trade; if the initial entry ends unfilled and the trade is abandoned, ladder state machines never leave IDLE. |
| D-0012 Alpaca Last Trade as trigger source | The `p_last` in all rules above is the Alpaca Last Trade. |
| D-0025 Telegram approval | The bot writes approvals; the engine consumes them and moves the state machine. |
| D-0026 dynamic universe (TSLA test-only) | Per-symbol, per-trade state; the state store is keyed by `(symbol, trade_id)`. |

## 8. Alternatives NOT chosen and why

- **Pure time-based (Option A).** Rejected — arbitrary constant, weak
  determinism, poor D-0007 fit.
- **State machine alone (Option B).** Rejected — does not handle
  oscillation around trigger after BLOCKED_EXPIRED / BLOCKED_PRICE.
- **Hysteresis alone (Option C).** Rejected — no defense against
  duplicate proposals while one is PROPOSAL_PENDING.

## 9. Proposed policy wording

> **D-0011 — Ladder trigger debounce: per-level state machine with
> post-BLOCKED_PRICE re-arm.**
>
> Each Ladder level (Ladder 1, Ladder 2) has, per trade, a state
> machine over the following states:
> `IDLE`, `PROPOSAL_PENDING`, `APPROVED`, `EXECUTED`, `REJECTED`,
> `BLOCKED_EXPIRED`, `BLOCKED_PRICE`, `BLOCKED_FLOOR_PRIORITY`.
>
> Terminal (for the trade):
> - `EXECUTED`, `REJECTED`, `BLOCKED_FLOOR_PRIORITY` — no further
>   proposals or transitions on that level.
>
> Non-terminal blocks:
> - `BLOCKED_EXPIRED` (the D-0007 5-minute clock ran out with no
>   Controller response) returns the level to `IDLE` with
>   `armed = True`. The next scheduled market check re-asks if the
>   trigger condition still holds. This is intentional: expiration is
>   a human availability event, not a market signal, and on the
>   approved hourly cadence (D-0021) it is bounded to at most one
>   re-ask per check.
> - `BLOCKED_PRICE` (D-0007 re-check found `abs(current − trigger)/trigger > 0.005`
>   at submit time) returns the level to `IDLE` with `armed = False`.
>   Re-arm requires the Alpaca Last Trade price to reach `trigger × 1.005`
>   on some subsequent scheduled check.
>
> Proposal creation gate:
> - A new proposal is created only when the level is in `IDLE` AND
>   `armed == True` AND `p_last ≤ trigger` AND `trigger > active_floor_price`.
> - `armed` transitions to `False` at the moment a proposal is created.
>
> The `0.005` re-arm margin reuses the ±0.5% material-move band
> already approved in D-0007. It is intentionally not a separately
> invented constant.
>
> Alpaca `client_order_id = proposal.id` (D-0025) provides broker-side
> idempotency, so a duplicate submit attempt from any source (retry,
> race, restart) is refused at Alpaca even before D-0011's state
> machine catches it.
>
> All debounce state (per trade, per level) is persisted in SQLite
> via the D-0024 repository abstraction and survives process restart
> and broker reconciliation. On restart, any proposal older than
> 5 minutes with no recorded approval is transitioned to
> `BLOCKED_EXPIRED` before the engine acts.
>
> Future-work note: the asymmetric treatment of `BLOCKED_EXPIRED` vs
> `BLOCKED_PRICE` is calibrated to the current hourly scheduler
> cadence (D-0021). If cadence ever tightens to sub-minute intervals,
> this rule should be revisited as a follow-up D-0011 revision.

## 10. Status

**D-0011 — APPROVED on 2026-09-14.**

The approved policy wording is the version in §9 above; the
authoritative record is `docs/trading/decisions.md` D-0011.
Any change to this rule requires a new dated entry in `decisions.md`
and Controller approval per CLAUDE.md §9.
