# Credential Migration Plan

The Alpaca paper API keys currently embedded in every live routine
prompt are compromised. This document specifies the migration.

**No live trigger will be changed** until Controller says APPLY.

---

## 1. Compromised credentials

- Alpaca paper `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` — pasted in
  chat and stored inside four live trigger prompts.
- Perplexity `PERPLEXITY_API_KEY` — pasted in an earlier chat message.
- Telegram bot token `TELEGRAM_BOT_TOKEN` — pasted in an earlier chat
  message.

**All three treated as compromised.**

## 2. Rotation checklist (Controller performs)

- [ ] Alpaca dashboard → Paper Trading → API Keys → **Regenerate**.
- [ ] Perplexity dashboard → API Keys → **Revoke** the old key,
      **create** a new one.
- [ ] Telegram BotFather → `/revoke` the bot token, then `/token` to
      re-issue.
- [ ] Store new values in a local `.env` file (never committed).
- [ ] Confirm `.env` is git-ignored (already configured in
      `.gitignore`).
- [ ] Do NOT paste new keys into chat.

## 3. Live-routine cleanup (after rotation)

For each of the four routines, edit the trigger via the Routines UI or
`update_trigger` so its prompt no longer contains a raw API key.

- If the runtime supports env-var interpolation into the prompt, use
  `${ALPACA_API_KEY_ID}` / `${ALPACA_API_SECRET_KEY}`.
- If it does not, we have two acceptable interim patterns and one
  unacceptable one:
  - ✅ move all order placement out of the prompt and into a small
    program the routine invokes, which reads env vars — the prompt
    only sees non-secret parameters
  - ✅ keep secrets in `environment_variables` on the trigger record
    itself (per-trigger env), never in the prompt text
  - ❌ paste the new raw keys into the prompt text (this reproduces the
    original leak)

Interim safety: if none of the above is feasible before market open on
the next trading day, prefer **disabling** the routine over leaving a
raw-key prompt live with the new keys.

## 4. Repo hygiene

- [ ] Verify no committed file contains the old or new key values.
      A grep sweep is already part of the pre-commit expectation in
      `CLAUDE.md` §6/§8.
- [ ] Recheck any transcripts we export or ship — session logs are
      not source-controlled, but any exported artifact must be
      scrubbed.

## 5. Ongoing rule

Credentials are read exclusively from environment variables at run
time. The names live in `.env.example`. Never in code, never in
prompts, never in docs, never in logs. See `CLAUDE.md` §8.
