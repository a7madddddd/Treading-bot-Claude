"""TelegramDecisionSource -- concrete Telegram-backed implementation of
the broker/UI-agnostic PendingDecisionSource contract defined in
src/engine/decision_source.py.

Long-polling (D-0039 slice 3, Controller-approved): the receive mode
is Telegram's long-polling `getUpdates`. This is the interim-host-safe
choice -- no public port, no TLS certificate, no NAT concerns. A
future move to webhook is deliberately supported by isolating the
transport behind an injectable callable so that path can be added
without touching the Engine.

Every failure path stays non-fatal to the engine loop: transport
errors, malformed payloads, unknown callback data, and messages from
unauthorized senders are logged and silently dropped. The abstract's
guarantee that `poll()` never blocks is honored -- the network
long-poll happens in a background thread; `poll()` only drains an
in-process queue.

Security posture (D-0025-aligned):
- The sender's numeric Telegram user id MUST appear in the configured
  admin allow-list before any decision is enqueued. This is checked
  once per update; no other authorization signal is honored.
- The bot token is never logged, never included in an exception
  message, never returned in any external surface.
- The `decided_by` field of every enqueued decision is filled with
  the raw admin numeric id as a string so every decision remains
  attributable (matches ControllerDecision's docstring intent).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional, Set, Tuple

from engine.decision_source import (
    ControllerDecision,
    DecisionKind,
    PendingDecisionSource,
)

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"
_DEFAULT_LONG_POLL_SECONDS = 25
_DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0  # must exceed long_poll to allow the server hold
_DEFAULT_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_BACKOFF_MAX_SECONDS = 30.0

# Callback-button payloads look like: "approve:P-1", "reject:P-1",
# "confirm_l2:P-1". Text commands look like: "/approve P-1",
# "/reject P-1", "/confirm_l2 P-1", optionally with a trailing "@BotName".
_CALLBACK_KINDS = {
    "approve": DecisionKind.APPROVE,
    "reject": DecisionKind.REJECT,
    "confirm_l2": DecisionKind.CONFIRM_LADDER2_PARTIAL_FILL,
}
_TEXT_COMMAND_RE = re.compile(r"^/(approve|reject|confirm_l2)(?:@\S+)?\s+(\S+)\s*$")
_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes


HttpTransport = Callable[[str, Optional[bytes], Mapping[str, str], float], TransportResponse]


class TelegramDecisionConfigError(Exception):
    """Raised only at construction time (configuration problem), never
    from poll() or from the background loop. Never includes the token
    value itself in its message."""


def _urllib_transport(url: str, data: Optional[bytes], headers: Mapping[str, str], timeout: float) -> TransportResponse:
    method = "POST" if data is not None else "GET"
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return TransportResponse(status_code=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 -- diagnostic path
            body = b""
        return TransportResponse(status_code=exc.code, body=body)


class TelegramDecisionSource(PendingDecisionSource):
    """Concrete Telegram long-polling decision source.

    Usable as a context manager for lifecycle safety:

        with TelegramDecisionSource(...) as src:
            engine.set_decision_source(src)
            engine.run()

    Or with explicit start()/stop() when the process wraps its own
    lifecycle (an OS signal handler, a supervisor loop, etc.). Both
    entry points are idempotent."""

    def __init__(
        self,
        *,
        bot_token: str,
        admin_user_ids: Iterable[int],
        transport: HttpTransport = _urllib_transport,
        long_poll_seconds: int = _DEFAULT_LONG_POLL_SECONDS,
        http_timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        backoff_base_seconds: float = _DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = _DEFAULT_BACKOFF_MAX_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token or not bot_token.strip():
            raise TelegramDecisionConfigError("TELEGRAM_BOT_TOKEN is missing or empty")
        allow: Set[int] = set()
        for uid in admin_user_ids:
            if not isinstance(uid, int) or uid <= 0:
                raise TelegramDecisionConfigError(
                    "TELEGRAM_ADMIN_USER_IDS entries must be positive integers"
                )
            allow.add(uid)
        if not allow:
            raise TelegramDecisionConfigError(
                "TELEGRAM_ADMIN_USER_IDS must contain at least one admin id"
            )
        if long_poll_seconds < 0:
            raise TelegramDecisionConfigError("long_poll_seconds must be >= 0")
        if http_timeout_seconds <= long_poll_seconds:
            raise TelegramDecisionConfigError(
                "http_timeout_seconds must exceed long_poll_seconds"
            )
        self._bot_token = bot_token
        self._admin_ids = allow
        self._transport = transport
        self._long_poll_seconds = long_poll_seconds
        self._http_timeout_seconds = http_timeout_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._sleep_fn = sleep_fn

        self._queue: "queue.Queue[ControllerDecision]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._offset: int = 0
        self._backoff = self._backoff_base_seconds

    # ---- classmethods -------------------------------------------------

    @classmethod
    def from_env(cls, **kwargs) -> "TelegramDecisionSource":
        """Reads TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_USER_IDS from the
        environment. Raises TelegramDecisionConfigError if either is
        missing or malformed. Values are never included in the error
        message."""
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        raw = os.environ.get("TELEGRAM_ADMIN_USER_IDS", "")
        admin_ids: List[int] = []
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                admin_ids.append(int(piece))
            except ValueError as ex:
                raise TelegramDecisionConfigError(
                    "TELEGRAM_ADMIN_USER_IDS contains a non-integer entry"
                ) from ex
        return cls(bot_token=bot_token, admin_user_ids=admin_ids, **kwargs)

    # ---- PendingDecisionSource contract ------------------------------

    def poll(self) -> List[ControllerDecision]:
        drained: List[ControllerDecision] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return drained

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="TelegramDecisionSource-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> "TelegramDecisionSource":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---- background loop ---------------------------------------------

    def _run(self) -> None:
        # Any exception raised by _process_one_batch is swallowed to
        # keep the engine's decision pipeline non-fatal; a persistent
        # failure is logged with bounded exponential backoff before
        # the next attempt so we do not hammer a broken API.
        while not self._stop_event.is_set():
            try:
                self._process_one_batch()
                self._backoff = self._backoff_base_seconds
            except _PermanentTelegramError as exc:
                logger.error(
                    "Telegram decision source: permanent error, halting background poll (%s)",
                    exc,
                )
                return
            except Exception as exc:  # noqa: BLE001 -- loop must survive any transient
                logger.warning(
                    "Telegram decision source: transient error (%s: %s); backing off %.2fs",
                    type(exc).__name__,
                    exc,
                    self._backoff,
                )
                self._sleep_fn(self._backoff)
                self._backoff = min(self._backoff * 2, self._backoff_max_seconds)

    def _process_one_batch(self) -> None:
        """One iteration of the receive loop -- calls getUpdates,
        parses each update, enqueues authorized decisions, and
        advances the update_id offset. Extracted from _run for direct
        testability (no threads needed to test the parsing pipeline)."""
        url = "{base}/bot{token}/getUpdates?offset={offset}&timeout={t}".format(
            base=_TELEGRAM_API_BASE,
            token=self._bot_token,
            offset=self._offset,
            t=self._long_poll_seconds,
        )
        response = self._transport(url, None, {}, self._http_timeout_seconds)

        if response.status_code == 401 or response.status_code == 404:
            # Bad token or unknown endpoint -- these are configuration
            # problems that will not resolve on retry.
            raise _PermanentTelegramError(
                f"Telegram getUpdates returned HTTP {response.status_code}"
            )
        if not (200 <= response.status_code < 300):
            raise _TransientTelegramError(
                f"Telegram getUpdates returned HTTP {response.status_code}"
            )

        try:
            body = json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            raise _TransientTelegramError("Telegram getUpdates payload was not valid JSON") from ex

        if not isinstance(body, dict) or not body.get("ok"):
            raise _TransientTelegramError(
                "Telegram getUpdates returned ok=false or non-object body"
            )

        result = body.get("result")
        if not isinstance(result, list):
            raise _TransientTelegramError("Telegram getUpdates 'result' was not a list")

        for update in result:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                # Always advance the offset even for updates we drop --
                # otherwise the same malformed / unauthorized update
                # would be re-delivered indefinitely.
                self._offset = max(self._offset, update_id + 1)
            decision = self._parse_update(update)
            if decision is not None:
                self._queue.put(decision)

    # ---- parsing -----------------------------------------------------

    def _parse_update(self, update: Mapping[str, object]) -> Optional[ControllerDecision]:
        callback = update.get("callback_query")
        if isinstance(callback, Mapping):
            return self._parse_callback(callback)
        message = update.get("message")
        if isinstance(message, Mapping):
            return self._parse_text_message(message)
        # Everything else (edited messages, inline queries, channel
        # posts) is not part of the approval surface.
        return None

    def _parse_callback(self, callback: Mapping[str, object]) -> Optional[ControllerDecision]:
        sender_id = self._authorized_sender_id(callback.get("from"))
        if sender_id is None:
            return None
        data = callback.get("data")
        if not isinstance(data, str):
            logger.info("Telegram callback dropped: 'data' missing or not a string")
            return None
        parsed = _parse_callback_data(data)
        if parsed is None:
            logger.info("Telegram callback dropped: unrecognized data payload")
            return None
        kind, proposal_id = parsed
        return ControllerDecision(
            proposal_id=proposal_id,
            kind=kind,
            decided_by=str(sender_id),
        )

    def _parse_text_message(self, message: Mapping[str, object]) -> Optional[ControllerDecision]:
        sender_id = self._authorized_sender_id(message.get("from"))
        if sender_id is None:
            return None
        text = message.get("text")
        if not isinstance(text, str):
            return None
        parsed = _parse_text_command(text)
        if parsed is None:
            return None
        kind, proposal_id = parsed
        return ControllerDecision(
            proposal_id=proposal_id,
            kind=kind,
            decided_by=str(sender_id),
        )

    def _authorized_sender_id(self, sender: object) -> Optional[int]:
        if not isinstance(sender, Mapping):
            return None
        raw = sender.get("id")
        if not isinstance(raw, int):
            return None
        if raw not in self._admin_ids:
            logger.info("Telegram update dropped: sender id is not on the admin allow-list")
            return None
        return raw


# ---- module-private helpers ------------------------------------------


class _TransientTelegramError(Exception):
    """Recoverable failure -- retried with backoff."""


class _PermanentTelegramError(Exception):
    """Configuration failure -- halts the background loop."""


def _parse_callback_data(data: str) -> Optional[Tuple[DecisionKind, str]]:
    if ":" not in data:
        return None
    kind_str, _, proposal_id = data.partition(":")
    kind = _CALLBACK_KINDS.get(kind_str.strip().lower())
    if kind is None:
        return None
    proposal_id = proposal_id.strip()
    if not _PROPOSAL_ID_RE.match(proposal_id):
        return None
    return kind, proposal_id


def _parse_text_command(text: str) -> Optional[Tuple[DecisionKind, str]]:
    match = _TEXT_COMMAND_RE.match(text.strip())
    if match is None:
        return None
    kind = _CALLBACK_KINDS[match.group(1)]
    proposal_id = match.group(2)
    if not _PROPOSAL_ID_RE.match(proposal_id):
        return None
    return kind, proposal_id
