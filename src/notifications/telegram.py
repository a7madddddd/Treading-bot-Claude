"""TelegramNotificationService — direct Telegram Bot API over HTTPS.

Stdlib only (`urllib.request`), per this project's demonstrated
dependency discipline (no third-party package anywhere under `src/`).
Outbound `sendMessage` only — no polling, no webhook, no inbound command
handling (explicitly out of scope; see
`docs/architecture/telegram-approval.md` D-0025 for the future,
not-yet-implemented approval-transport design).

Reliability contract:
    - `send()` never raises. Every failure path returns a
      `NotificationResult(success=False, ...)`.
    - Transient failures (network errors, 5xx, 429) are retried with
      bounded exponential backoff.
    - Permanent failures (missing/invalid config, 4xx auth/not-found
      errors other than 429) are NOT retried.
    - The bot token is never logged, never included in an exception
      message, never returned in a `NotificationResult`.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from typing import Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from .service import INotificationService, NotificationEvent, NotificationResult

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_BASE_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class TelegramConfigError(Exception):
    """Raised only at construction time (a startup/configuration
    problem), never from `send()`. Never includes the token or chat id
    value itself in the message."""


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes


# A transport is anything shaped like this. The default is a thin wrapper
# around `urllib.request`; tests inject a fake to avoid any real network
# call and to avoid ever needing a real bot token.
HttpTransport = Callable[[str, bytes, Mapping[str, str], float], TransportResponse]


def _urllib_transport(url: str, data: bytes, headers: Mapping[str, str], timeout: float) -> TransportResponse:
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return TransportResponse(status_code=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        # HTTPError already carries the response body/status for non-2xx
        # replies -- surfaced as a normal TransportResponse so the retry
        # classification logic in TelegramNotificationService handles it
        # uniformly with a successful-transport-but-non-2xx-status case.
        return TransportResponse(status_code=exc.code, body=exc.read())


class TelegramNotificationService(INotificationService):
    """Real, tested Telegram Bot API adapter. Not wired into any
    production/trading path by this change -- see the accompanying
    documentation for the current, explicit scope limitations."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        transport: HttpTransport = _urllib_transport,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        backoff_base_seconds: float = _DEFAULT_BACKOFF_BASE_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token or not bot_token.strip():
            raise TelegramConfigError("TELEGRAM_BOT_TOKEN is missing or empty")
        if not chat_id or not chat_id.strip():
            raise TelegramConfigError("TELEGRAM_CHAT_ID is missing or empty")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._sleep_fn = sleep_fn

    @classmethod
    def from_env(cls, **kwargs: object) -> "TelegramNotificationService":
        """Reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from the
        environment (per `.env.example`). Raises `TelegramConfigError`
        (never logs the values) if either is missing."""

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        return cls(bot_token=bot_token, chat_id=chat_id, **kwargs)  # type: ignore[arg-type]

    TELEGRAM_MAX_TEXT_CHARS = 4096
    """Telegram's hard limit on sendMessage `text`. Longer bodies are
    rejected with HTTP 400 -- and a rejected proposal message is the one
    carrying the inline approve/reject buttons, so the Controller would
    never see the trade at all (P-042)."""

    _TRUNCATION_MARKER = "\n\n[…message shortened to fit Telegram…]"

    @classmethod
    def _fit(cls, text: str) -> str:
        """Truncates to Telegram's limit, keeping the HEAD of the body.

        The head is what matters: the symbol, the prices, the quantity
        and the safeguards all appear before the advisory research
        block. Cutting the tail therefore loses the optional part and
        never the decision-critical part.

        The cap lives HERE, in the transport, and not in each enricher,
        because the limit is Telegram's. An enricher added later must
        not have to know about it to be safe.
        """
        if len(text) <= cls.TELEGRAM_MAX_TEXT_CHARS:
            return text
        keep = cls.TELEGRAM_MAX_TEXT_CHARS - len(cls._TRUNCATION_MARKER)
        return text[:keep].rstrip() + cls._TRUNCATION_MARKER

    _ET = ZoneInfo("America/New_York")

    # D-0071: events whose body already says, in plain words, what the
    # message is. For these the internal event name and the duplicated
    # symbol line are noise -- the Controller asked for a message a
    # non-technical reader can act on.
    _SELF_DESCRIBING_EVENTS = frozenset({
        "proposal_awaiting_approval",
        "political_proposal_awaiting_approval",
    })

    def _format_text(self, event: NotificationEvent) -> str:
        """D-0071 (Controller, 2026-10-05): drop the machine-facing
        parts, keep every number the decision rests on.

        Removed: the `[LEVEL] internal_event_name` header and the
        duplicated `Symbol:` line on self-describing events, and the
        microsecond ISO timestamp everywhere.

        Kept, unchanged: prices, quantities, cost, and all three
        safeguard levels. Those are the decision, not decoration.

        The timestamp becomes `HH:MM ET` because that is the clock the
        Controller trades against; the UTC microsecond form told him
        nothing he could use.
        """
        lines = []
        if event.event not in self._SELF_DESCRIBING_EVENTS:
            lines.append(f"[{event.level.value}] {event.event}")
            if event.symbol:
                lines.append(f"Symbol: {event.symbol}")
        lines.append(event.message)
        for key, value in event.extra:
            lines.append(f"{key}: {value}")
        stamp = event.effective_timestamp()
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        lines.append(stamp.astimezone(self._ET).strftime("%H:%M ET"))
        return self._fit("\n".join(lines))

    def send(self, event: NotificationEvent) -> NotificationResult:
        url = f"{_TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        body: dict = {"chat_id": self._chat_id, "text": self._format_text(event)}
        if event.interactive_actions:
            # Telegram inline keyboard: one row per action pair. Each button
            # carries a callback_data of the form "<action>:<proposal_id>",
            # which `TelegramDecisionSource._parse_callback_data` accepts.
            body["reply_markup"] = {
                "inline_keyboard": [[
                    {"text": label, "callback_data": callback_data}
                    for (label, callback_data) in event.interactive_actions
                ]]
            }
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport(url, payload, headers, self._timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - a transport-layer failure is always transient
                last_error = f"{type(exc).__name__}: transport error"
                logger.warning(
                    "Telegram send attempt %d/%d failed: transport error (%s)",
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                )
                if attempt < self._max_attempts:
                    self._sleep_fn(self._backoff_base_seconds * (2 ** (attempt - 1)))
                    continue
                return NotificationResult(success=False, attempts=attempt, error=last_error)

            last_status = response.status_code
            if 200 <= response.status_code < 300:
                return NotificationResult(success=True, attempts=attempt, status_code=response.status_code)

            if response.status_code not in _RETRYABLE_STATUS_CODES:
                # Permanent failure (e.g. 401 bad token, 400 bad chat id) --
                # do not retry a configuration/authentication problem.
                last_error = f"Telegram API returned HTTP {response.status_code} (not retryable)"
                logger.error(
                    "Telegram send failed with non-retryable status %d on attempt %d",
                    response.status_code,
                    attempt,
                )
                return NotificationResult(
                    success=False, attempts=attempt, status_code=response.status_code, error=last_error
                )

            last_error = f"Telegram API returned HTTP {response.status_code} (retryable)"
            logger.warning(
                "Telegram send attempt %d/%d failed with retryable status %d",
                attempt,
                self._max_attempts,
                response.status_code,
            )
            if attempt < self._max_attempts:
                self._sleep_fn(self._backoff_base_seconds * (2 ** (attempt - 1)))

        return NotificationResult(success=False, attempts=self._max_attempts, status_code=last_status, error=last_error)
