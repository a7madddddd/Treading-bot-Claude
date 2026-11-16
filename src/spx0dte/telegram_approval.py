"""Telegram approval transport for the SPX 0DTE module (D-0095 execution
wiring) -- a SEPARATE bot from the equity-ladder one
(notifications.telegram / notifications.telegram_decision), on purpose.

Why separate, not shared: Telegram's `getUpdates` long-poll offset is
tracked per BOT TOKEN, not per consumer. Running a second independent
poller against the SAME token as the existing
`notifications.telegram_decision.TelegramDecisionSource` would make the
two instances intermittently steal each other's updates -- a real
behavioral effect on the existing stock-ladder approval flow, even
though no line of its code would be touched. A second bot
(`SPX_TELEGRAM_BOT_TOKEN`, created via @BotFather) removes the
conflict entirely: fully independent token, fully independent offset.

Deliberately self-contained: does not import `notifications.telegram`,
`notifications.telegram_decision`, or `engine.decision_source`. It
duplicates their general SHAPE (send with inline keyboard; a
long-polling receiver that only trusts an explicit admin allow-list;
every decision carries who made it) because that shape is sound, not
because this module depends on any of those.

Stdlib only (urllib), per this project's dependency policy.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, List, Mapping, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"
_DEFAULT_LONG_POLL_SECONDS = 25
_DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
_DEFAULT_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_BACKOFF_MAX_SECONDS = 30.0

# Prefixed distinctly from the equity-ladder bot's "approve:"/"reject:"
# callback data -- belt-and-suspenders: even if a future change ever
# put both bots' updates through one handler by mistake, the payloads
# cannot be confused with each other.
_CALLBACK_KINDS = {"spx_approve": "approve", "spx_reject": "reject"}
_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class DecisionKind(Enum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class ControllerDecision:
    proposal_id: str
    kind: DecisionKind
    decided_by: str

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id must be non-empty")
        if not isinstance(self.kind, DecisionKind):
            raise TypeError(f"kind must be a DecisionKind, got {type(self.kind)!r}")
        if not self.decided_by:
            raise ValueError("decided_by must be non-empty -- every decision must be attributable")


class TelegramConfigError(Exception):
    """Raised only at construction time. Never includes the token value."""


def _urllib_get(url: str, timeout: float) -> Tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 -- diagnostic path
            body = b""
        return exc.code, body


def _urllib_post_json(url: str, payload: dict, timeout: float) -> Tuple[int, bytes]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            resp_body = exc.read()
        except Exception:  # noqa: BLE001
            resp_body = b""
        return exc.code, resp_body


def send_proposal_message(
    *, bot_token: str, chat_id: str, text: str, proposal_id: str,
    timeout: float = 10.0,
) -> bool:
    """Sends `text` with an Approve/Reject inline keyboard to `chat_id`.
    Returns True on a 2xx response, False on any failure (never
    raises -- a failed send is a logged, non-fatal event; the proposal
    row persists either way and a caller may retry)."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"spx_approve:{proposal_id}"},
                {"text": "❌ Reject", "callback_data": f"spx_reject:{proposal_id}"},
            ]],
        },
    }
    try:
        status, _ = _urllib_post_json(url, payload, timeout)
    except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as ex:
        logger.warning("SPX Telegram send failed (network error): %s", ex)
        return False
    if not (200 <= status < 300):
        logger.warning("SPX Telegram send failed: HTTP %s", status)
        return False
    return True


class SpxTelegramDecisionSource:
    """Long-polling receiver for the SPX approval bot. Same lifecycle
    shape as notifications.telegram_decision.TelegramDecisionSource
    (start/stop, context manager, poll() drains an in-process queue,
    never blocks) -- a separate implementation, not a subclass, to
    keep this module importable without engine.decision_source."""

    def __init__(
        self,
        *,
        bot_token: str,
        admin_user_ids: Iterable[int],
        http_get: Callable[[str, float], Tuple[int, bytes]] = _urllib_get,
        http_post: Callable[[str, dict, float], Tuple[int, bytes]] = _urllib_post_json,
        long_poll_seconds: int = _DEFAULT_LONG_POLL_SECONDS,
        http_timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        backoff_base_seconds: float = _DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = _DEFAULT_BACKOFF_MAX_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token or not bot_token.strip():
            raise TelegramConfigError("SPX_TELEGRAM_BOT_TOKEN is missing or empty")
        allow: Set[int] = set()
        for uid in admin_user_ids:
            if not isinstance(uid, int) or uid <= 0:
                raise TelegramConfigError("SPX_TELEGRAM_ADMIN_USER_IDS entries must be positive integers")
            allow.add(uid)
        if not allow:
            raise TelegramConfigError("SPX_TELEGRAM_ADMIN_USER_IDS must contain at least one admin id")
        if http_timeout_seconds <= long_poll_seconds:
            raise TelegramConfigError("http_timeout_seconds must exceed long_poll_seconds")

        self._bot_token = bot_token
        self._admin_ids = allow
        self._http_get = http_get
        self._http_post = http_post
        self._long_poll_seconds = long_poll_seconds
        self._http_timeout_seconds = http_timeout_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._sleep_fn = sleep_fn

        self._queue: "queue.Queue[ControllerDecision]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._offset = 0
        self._backoff = backoff_base_seconds

    @classmethod
    def from_env(cls, **kwargs) -> "SpxTelegramDecisionSource":
        bot_token = os.environ.get("SPX_TELEGRAM_BOT_TOKEN", "")
        raw = os.environ.get("SPX_TELEGRAM_ADMIN_USER_IDS", "")
        admin_ids: List[int] = []
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                admin_ids.append(int(piece))
            except ValueError as ex:
                raise TelegramConfigError("SPX_TELEGRAM_ADMIN_USER_IDS contains a non-integer entry") from ex
        return cls(bot_token=bot_token, admin_user_ids=admin_ids, **kwargs)

    def poll(self) -> List[ControllerDecision]:
        drained: List[ControllerDecision] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return drained

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="SpxTelegramDecisionSource-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> "SpxTelegramDecisionSource":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._process_one_batch()
                self._backoff = self._backoff_base_seconds
            except _PermanentError as exc:
                logger.error("SPX Telegram decision source: permanent error, halting (%s)", exc)
                return
            except Exception as exc:  # noqa: BLE001 -- loop must survive any transient failure
                logger.warning(
                    "SPX Telegram decision source: transient error (%s: %s); backing off %.2fs",
                    type(exc).__name__, exc, self._backoff,
                )
                self._sleep_fn(self._backoff)
                self._backoff = min(self._backoff * 2, self._backoff_max_seconds)

    def _process_one_batch(self) -> None:
        url = f"{_TELEGRAM_API_BASE}/bot{self._bot_token}/getUpdates?offset={self._offset}&timeout={self._long_poll_seconds}"
        status, body = self._http_get(url, self._http_timeout_seconds)

        if status in (401, 404):
            raise _PermanentError(f"Telegram getUpdates returned HTTP {status}")
        if not (200 <= status < 300):
            raise _TransientError(f"Telegram getUpdates returned HTTP {status}")

        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            raise _TransientError("Telegram getUpdates payload was not valid JSON") from ex
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            raise _TransientError("Telegram getUpdates returned ok=false or non-object body")
        result = parsed.get("result")
        if not isinstance(result, list):
            raise _TransientError("Telegram getUpdates 'result' was not a list")

        for update in result:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = max(self._offset, update_id + 1)
            decision = self._parse_update(update)
            if decision is not None:
                self._queue.put(decision)

    def _parse_update(self, update: Mapping[str, object]) -> Optional[ControllerDecision]:
        callback = update.get("callback_query")
        if isinstance(callback, Mapping):
            return self._parse_callback(callback)
        return None

    def _parse_callback(self, callback: Mapping[str, object]) -> Optional[ControllerDecision]:
        sender = callback.get("from")
        if not isinstance(sender, Mapping):
            return None
        raw_id = sender.get("id")
        if not isinstance(raw_id, int) or raw_id not in self._admin_ids:
            logger.info("SPX Telegram update dropped: sender not on admin allow-list")
            return None

        data = callback.get("data")
        if not isinstance(data, str) or ":" not in data:
            return None
        kind_str, _, proposal_id = data.partition(":")
        kind_value = _CALLBACK_KINDS.get(kind_str.strip().lower())
        if kind_value is None:
            return None
        proposal_id = proposal_id.strip()
        if not _PROPOSAL_ID_RE.match(proposal_id):
            return None

        self._ack_and_dismiss(callback, label=kind_value)
        return ControllerDecision(
            proposal_id=proposal_id, kind=DecisionKind(kind_value), decided_by=str(raw_id),
        )

    def _ack_and_dismiss(self, callback: Mapping[str, object], *, label: str) -> None:
        callback_id = callback.get("id")
        if isinstance(callback_id, str) and callback_id:
            try:
                url = f"{_TELEGRAM_API_BASE}/bot{self._bot_token}/answerCallbackQuery"
                self._http_post(url, {"callback_query_id": callback_id, "text": f"Recorded: {label}"}, self._http_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 -- best-effort
                logger.info("SPX answerCallbackQuery best-effort failed: %s", exc)

        message = callback.get("message")
        if isinstance(message, Mapping):
            chat = message.get("chat")
            chat_id = chat.get("id") if isinstance(chat, Mapping) else None
            message_id = message.get("message_id")
            if isinstance(chat_id, (int, str)) and isinstance(message_id, int):
                try:
                    url = f"{_TELEGRAM_API_BASE}/bot{self._bot_token}/editMessageReplyMarkup"
                    self._http_post(
                        url,
                        {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
                        self._http_timeout_seconds,
                    )
                except Exception as exc:  # noqa: BLE001 -- best-effort
                    logger.info("SPX editMessageReplyMarkup best-effort failed: %s", exc)


class _TransientError(Exception):
    """Recoverable failure -- retried with backoff."""


class _PermanentError(Exception):
    """Configuration failure -- halts the background loop."""
