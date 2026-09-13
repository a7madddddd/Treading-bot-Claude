# Approved Trading Strategy

**Status:** APPROVED — paper trading only.
**Any change to this document requires explicit Controller approval and a
new dated entry in `decisions.md`.**

---

## 1. Rule table

| Rule     | Trigger | Shares            | Purpose                              |
|----------|---------|-------------------|--------------------------------------|
| Buy      |   0%    | 10                | Initial position                     |
| Ladder 1 |  −5%    | +10               | Small pullback — possibly noise      |
| Ladder 2 |  −8%    | +20               | Larger pullback — real drawdown      |
| Floor    | −10%    | SELL ALL (40)     | Last-resort exit                     |

Maximum planned position size before Floor: **40 shares**.

## 2. Price reference rule

All percentage thresholds for the initial Buy, Ladder 1, Ladder 2, and the
**original** Floor are calculated from the **ORIGINAL INITIAL ENTRY FILL
PRICE**.

- The reference is the weighted-average fill price of the **initial entry
  order only**.
- It is frozen after the initial entry order is fully reconciled.
- Subsequent Ladder fills do **not** modify this reference.
- Levels are **not** recalculated from current market price, later fills,
  weighted-average entry, or the previous ladder price.

### Example

Initial fill = **$100.00**

- Ladder 1  = $95.00 (−5%)
- Ladder 2  = $92.00 (−8%)
- Floor     = $90.00 (−10%)

These levels remain fixed for the entire trade unless the approved strategy
explicitly changes them. The weighted-average entry price is still
recalculated for risk reporting, but it does not move the Ladder or Floor
triggers.

## 3. Execution order

```
ENTRY → BUY 10
   ↓
LADDER 1 at −5% → BUY 10   (position = 20)
   ↓
LADDER 2 at −8% → BUY 20   (position = 40)
   ↓
FLOOR at −10%   → SELL ALL
```

- Each Ladder can trigger **only once per trade**.
- The Floor is always lower than every Ladder level.
- The Floor overrides any Ladder that has not yet triggered.

## 4. Active protective floor priority

There must always be **one** authoritative active protective floor.

- A Ladder order must **never** be submitted if its trigger price is at or
  below the current active protective floor.
- If the trailing floor has moved above an untriggered Ladder level, that
  Ladder is **blocked** and must not execute.
- The system must **never** lower the active protective floor to allow a
  Ladder purchase.
- Protective exits always have priority over Ladder purchases.

## 5. Trailing Floor

The trailing floor is an approved dynamic protective exit that can replace
the original Floor **only when it is higher**.

### Activation

- Activation is based on the **weighted-average filled entry price** of the
  current position.
- Activates when the market price reaches **+10%** above the weighted-average
  entry.
- On activation the trailing floor = **5% below the current market price**.

### Ratchet

Trailing thresholds are +10%, +15%, +20%, +25%, +30%, … above the
weighted-average entry. At each threshold the trailing floor is
recalculated as 5% below the market price.

- The trailing floor **can only move up**. It never moves down.
- A temporary dip below a trailing threshold does not lower or reset it.
- The trailing floor never overrides the original Floor downward.
- Once the trailing floor is higher than the original Floor, it becomes the
  active protective floor.
- The trailing floor is independent from the original Ladder and original
  Floor reference prices. Activating it does not move any of those.

### Example

Weighted-average entry = **$100**

| Price   | Threshold | Trailing floor |
|---------|-----------|----------------|
| $110.00 | +10%      | $104.50        |
| $115.50 | +15%      | $109.73        |
| $121.28 | +20%      | $115.22        |
| $127.63 | +25%      | $121.24        |

If price falls afterward, the floor stays at its highest calculated value.

## 6. Actual risk (not "just 10%")

Do not describe this strategy as "10% max loss". Laddering grows the
position, which grows the dollar risk. Always compute the actual dollar
exposure — see `risk-management.md` for the worked example.

## 7. What must never change without approval

- Ladder trigger percentages (−5%, −8%)
- Ladder share quantities (10, 20)
- Floor percentage (−10%)
- Trailing activation and step (+10% initial, +5% steps, 5% trail distance)
- Any of the reference-price rules above
- Any change to protective-floor priority
