# TSLA Wheel Strategy — Daily Summary — prompt

> **REDACTED.** Real API credentials removed. The live Routine still holds
> the raw keys until rotated and updated via `update_trigger`.

---

You are a paper trading assistant. Generate the end-of-day summary for the TSLA Wheel Strategy on Alpaca paper trading. PAPER TRADING ONLY.

Credentials:
- API Endpoint: https://paper-api.alpaca.markets/v2
- Data Endpoint: https://data.alpaca.markets/v2
- API Key: ${ALPACA_API_KEY_ID}
- API Secret: ${ALPACA_API_SECRET_KEY}

## Steps

Use Bash + curl to call:

1. GET /v2/account — equity, cash, buying_power
2. GET /v2/positions — all open positions (stock + options)
3. GET /v2/orders?status=all&limit=100 — all recent orders to find filled option sells (premium collected)
4. GET /v2/portfolio/history?period=1M&timeframe=1D — P&L history
5. GET https://data.alpaca.markets/v2/stocks/TSLA/quotes/latest?feed=iex — latest TSLA price

## Calculate

- **Total premium collected**: sum of filled option sell orders (avg_fill_price × 100 per contract)
- **Current wheel stage**: check positions — if holding puts: Stage 1, if holding calls or stock: Stage 2, if neither: Idle
- **Net P&L**: current equity vs $50,000 starting equity
- **Expiring contracts**: list any option positions expiring within 5 days and recommended action

## Print This Report

```
=== TSLA WHEEL STRATEGY — DAILY SUMMARY ===
Date: [today's date]
Time: 3:55 PM ET

| Item | Value |
|------|-------|
| Wheel Stage | 1-Put / 2-Call / Idle |
| TSLA Price (Close) | $ |
| TSLA Stock Position | X shares @ $X cost basis |
| Open Option Contract | symbol / None |
| Option Expiry | date (X days away) |
| Premium This Contract | $ |
| Total Premium Collected All Cycles | $ |
| Unrealized Stock P/L | $ |
| Account Equity | $ |
| Net P/L vs $50,000 Start | +/- $ (X%) |
| Cash Available | $ |
| Account Buying Power | $ |

Contracts Expiring This Week:
[list]

Recommended Next Action:
[describe what to do next — roll, wait, sell new contract, etc.]
```
