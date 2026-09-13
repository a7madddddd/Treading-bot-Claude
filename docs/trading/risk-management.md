# Risk Management

The approved strategy is a **laddered** entry, so "−10% Floor" does not mean
"−10% of capital at risk". This document defines how to compute actual
exposure.

---

## 1. Always compute, never assume

Before claiming a risk level, compute:

- Initial capital deployed
- Capital deployed after Ladder 1
- Capital deployed after Ladder 2
- Weighted-average entry after each fill
- Final position size (shares)
- Position market value at Floor
- Dollar loss at Floor
- Percentage loss from weighted-average entry
- Percentage loss from initial capital
- Portfolio-level risk (position risk / account equity)

## 2. Worked example — initial fill $100.00

Assume all ladders fill exactly at their trigger price.

| Step        | Fill $ | Shares added | Total shares | Cash spent | Cumulative spent | Weighted-avg entry |
|-------------|-------:|-------------:|-------------:|-----------:|-----------------:|-------------------:|
| Initial Buy | 100.00 |           10 |           10 |   1,000.00 |         1,000.00 |             100.00 |
| Ladder 1    |  95.00 |           10 |           20 |     950.00 |         1,950.00 |              97.50 |
| Ladder 2    |  92.00 |           20 |           40 |   1,840.00 |         3,790.00 |              94.75 |

Floor triggers at **$90.00** (−10% from initial fill).

- Position at Floor: 40 shares × $90.00 = **$3,600.00**
- Cumulative spent:                       **$3,790.00**
- Dollar loss:                            **−$190.00**
- Loss vs weighted-avg entry:  ($90 − $94.75) / $94.75 = **−5.01%**
- Loss vs initial capital ($1,000): **−19.0%** of initial slug
- Loss vs total capital deployed ($3,790): **−5.01%**

### Takeaways

- The Floor is −10% from the initial fill but only about −5% from the
  weighted-average entry after both ladders — the laddering **improves**
  the average price.
- Absolute dollar risk **triples** vs the initial slug once both ladders
  fill (from $1,000 to $3,790 at risk of drawdown).
- Portfolio risk depends on account equity — always express Floor loss
  as a % of account equity when reporting.

## 3. Trailing floor risk shape

Once the trailing floor becomes the active protective floor and sits above
weighted-average entry, the position has a locked-in floor above break-even.
Report the guaranteed-worst outcome as:

`(trailing_floor − weighted_avg_entry) × total_shares`

This is often the more useful number for the Controller after ratchets have
happened.

## 4. Risk reporting cadence

- On every fill, log the updated risk table.
- On every trailing-floor ratchet, log the updated guaranteed-worst.
- Include the risk snapshot in the end-of-day report.

## 5. Hard limits (placeholders — need Controller approval)

The following limits are **not yet approved**. They are listed as
candidates so the risk engine has something to enforce before the
Controller sets real numbers:

- Max concurrent open trades: **TBD**
- Max % of account equity per single trade at Floor: **TBD**
- Max daily loss stop (halt new entries): **TBD**
- Max drawdown before pause: **TBD**

Do not enforce numbers here as if approved. Ask the Controller.
