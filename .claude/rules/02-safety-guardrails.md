# Safety guardrails — never negotiable

- Paper trading only — no live orders, no real-money execution
- No automatic strategy changes
- No automatic risk-limit changes
- No bypassing Controller approval for entries/ladders
- Never log secrets, API keys, or credentials
- Never hardcode secrets — use env vars (see `.env.example`)
- Never claim profitability without evidence
- Never treat AI confidence as evidence

Notifications are **not** the source of truth for trading state. The
database/state/broker API is. A failed notification never triggers a
retry of the trade or a duplicate order — log it and continue safely.

## AI vs deterministic logic

**Deterministic code** owns: risk limits, position sizing, stop/floor
calculations, ladder thresholds, order validation, duplicate prevention,
account/position checks, execution, safety guardrails.

**AI** owns: research, summarization, idea generation, news analysis,
hypothesis generation, strategy comparison, regime hypothesis, result
explanation, experiment suggestions.

An LLM must **never** override a hard risk limit or the active
protective floor. AI recommendations flow through the Controller and the
deterministic risk engine.

## Secrets and environment

Secrets are read from environment variables. Never commit them.

- `PERPLEXITY_API_KEY` — Perplexity Agent API
- `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` — Alpaca paper trading
- `ALPACA_BASE_URL` — must point at the paper endpoint
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — notifications

`.env` is gitignored. Names go in `.env.example` with no values.

If a secret is pasted into a chat, treat it as compromised and rotate it
before use.
