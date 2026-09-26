"""Concrete Alpaca implementation of the broker-neutral BrokerClient
contract defined in src/execution/broker_client.py.

Interface neutrality: this file is the ONLY place in the project that
knows about Alpaca URLs, headers, JSON field names, and status
vocabulary. ExecutionService and everything above it stay strictly on
the neutral BrokerClient contract (BrokerOrderState, BrokerClient's
abstract methods, and the four BrokerClient errors); nothing outside
this file should import an Alpaca-specific symbol or read an
Alpaca-specific field name.

Terminal classification is done here, per the abstract's docstring --
only a real broker implementation knows its own status vocabulary. The
raw broker status string is passed through unmodified to
BrokerOrderState.status; consumers read is_terminal to make decisions,
never re-interpret the string themselves.

Transport injection: the concrete client accepts an optional HTTP
transport callable so tests can substitute a fully mocked transport
without any real network. The default transport uses stdlib urllib,
so this file adds no third-party runtime dependency.

Ambiguity vs. definite outcome (submit_order): only a broker-level
error with a well-formed HTTP response is a definite outcome
(accepted, filled immediately, or rejected). A network timeout, a
connection reset, or a torn response body must raise
BrokerSubmissionAmbiguousError; the caller does not know whether the
broker received the order.

Idempotency (submit_order): every submission includes the caller's
client_order_id. If Alpaca has already seen that client_order_id
(returned as HTTP 422 with a duplicate marker), this method looks up
the existing order and returns its current state; it never assumes
the caller's retry means the earlier attempt failed.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from execution.broker_client import (
    BrokerAccountBlockedError,
    BrokerClient,
    BrokerClientError,
    BrokerCommunicationError,
    BrokerOrderState,
    BrokerSubmissionAmbiguousError,
)


# Alpaca order statuses that are guaranteed to be final for the order id
# they attach to. `partially_filled` is intentionally NOT here: a
# partially filled order can still fill more, be cancelled, or expire.
# `replaced` is terminal for THIS client_order_id -- the replacement
# lives under a different broker_order_id with its own lifecycle.
_TERMINAL_ALPACA_STATUSES = frozenset({
    "filled",
    "canceled",
    "expired",
    "rejected",
    "done_for_day",
    "stopped",
    "replaced",
})

# Sides accepted by Alpaca's /v2/orders POST body. Kept as a set so a
# typo like "buy_to_open" fails locally rather than as a broker
# rejection round-trip.
_ACCEPTED_SIDES = frozenset({"buy", "sell"})


# The one and only host the paper-only guardrail (D-0002) accepts by
# default. A future decision authorizing live trading must extend this
# via the `allow_non_paper_url=True` opt-in on the constructor, never
# by silently widening this constant.
_APPROVED_PAPER_HOST = "paper-api.alpaca.markets"


HttpResponse = Tuple[int, bytes]  # (http_status_code, response_body_bytes)
HttpTransport = Callable[..., HttpResponse]  # (method, url, *, headers, body) -> HttpResponse


@dataclass(frozen=True)
class _AlpacaConfig:
    base_url: str        # e.g. "https://paper-api.alpaca.markets" (no trailing /v2)
    key_id: str
    secret_key: str
    timeout_seconds: float


class AlpacaBrokerClient(BrokerClient):
    def __init__(
        self,
        *,
        base_url: str,
        key_id: str,
        secret_key: str,
        timeout_seconds: float = 10.0,
        http_transport: Optional[HttpTransport] = None,
        allow_non_paper_url: bool = False,
    ):
        if not base_url:
            raise ValueError("base_url must be a non-empty URL")
        if not key_id or not secret_key:
            raise ValueError("key_id and secret_key are required")
        # Normalize: strip any trailing slashes and any trailing /v2 so
        # the caller's env value can safely be either
        # "https://paper-api.alpaca.markets" or the same with "/v2" or a
        # trailing slash -- an operational foot-gun observed on this
        # project's cloud environment.
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v2"):
            normalized = normalized[: -len("/v2")]
        # Paper-only guardrail (D-0002; verification-plan §3): refuse to
        # construct against any URL that is not the Alpaca paper endpoint.
        # This is the code-layer enforcement of "paper trading only" and
        # is deliberately uncircumventable except by an explicit,
        # documented `allow_non_paper_url=True` opt-in reserved for a
        # future signed decision that authorizes live trading.
        #
        # The check compares the parsed HOST against the exact approved
        # paper host -- a substring test on the URL is not enough because
        # a URL like "https://api.alpaca.markets/paper-api" would pass a
        # substring test while its host is actually the live account.
        if not allow_non_paper_url:
            host = (urllib.parse.urlparse(normalized).hostname or "").lower()
            if host != _APPROVED_PAPER_HOST:
                raise ValueError(
                    f"Refusing to construct AlpacaBrokerClient against a "
                    f"non-paper host. Paper trading only (D-0002). Expected "
                    f"host {_APPROVED_PAPER_HOST!r}, parsed host {host!r} "
                    f"from URL {normalized!r}. If you have an approved "
                    f"decision authorizing live trading, pass "
                    f"allow_non_paper_url=True explicitly."
                )
        self._config = _AlpacaConfig(
            base_url=normalized,
            key_id=key_id,
            secret_key=secret_key,
            timeout_seconds=timeout_seconds,
        )
        self._http = http_transport or self._default_transport

    # ---- BrokerClient contract -----------------------------------------

    def submit_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
    ) -> BrokerOrderState:
        _validate_client_order_id(client_order_id)
        _validate_symbol(symbol)
        if side not in _ACCEPTED_SIDES:
            raise ValueError(f"side must be one of {sorted(_ACCEPTED_SIDES)}, got {side!r}")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive int")
        if not (isinstance(limit_price, (int, float))) or limit_price <= 0:
            raise ValueError("limit_price must be a positive number")

        body = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side,
            "type": "limit",
            "time_in_force": "day",
            "limit_price": f"{float(limit_price):.4f}",
            "client_order_id": client_order_id,
        }
        try:
            status, payload = self._request("POST", "/v2/orders", body=body)
        except _AmbiguousTransport as ex:
            raise BrokerSubmissionAmbiguousError(str(ex)) from ex

        if 200 <= status < 300:
            return _parse_order_state(payload)

        # Duplicate client_order_id path: Alpaca returns 422 for this
        # case. A retry that raced with an already-accepted submission
        # must return the existing order's true state, never a
        # fabricated one.
        if status == 422 and _is_duplicate_client_order_id(payload):
            existing = self.get_order_by_client_order_id(client_order_id)
            if existing is not None:
                return existing
            # Broker said duplicate but has no record -- treat as
            # ambiguous rather than success or reject.
            raise BrokerSubmissionAmbiguousError(
                f"broker reported duplicate client_order_id={client_order_id!r} "
                "but returned no existing order on lookup"
            )

        # Any other 4xx/5xx is a definite outcome. Alpaca has processed
        # the request enough to reject it; the order will not fill
        # under this client_order_id. Raise BrokerClientError so
        # ExecutionService can classify it as a definite reject.
        raise BrokerClientError(
            f"Alpaca rejected order client_order_id={client_order_id!r} "
            f"with HTTP {status}: {_error_snippet(payload)}"
        )

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[BrokerOrderState]:
        _validate_client_order_id(client_order_id)
        path = "/v2/orders:by_client_order_id?" + urllib.parse.urlencode(
            {"client_order_id": client_order_id}
        )
        try:
            status, payload = self._request("GET", path)
        except _AmbiguousTransport as ex:
            raise BrokerCommunicationError(str(ex)) from ex

        if status == 404:
            return None
        if 200 <= status < 300:
            return _parse_order_state(payload)
        raise BrokerCommunicationError(
            f"Alpaca returned HTTP {status} on order lookup for "
            f"client_order_id={client_order_id!r}: {_error_snippet(payload)}"
        )

    def cancel_order(self, client_order_id: str) -> None:
        _validate_client_order_id(client_order_id)
        existing = self.get_order_by_client_order_id(client_order_id)
        if existing is None:
            # Nothing to cancel -- idempotent no-op per contract.
            return
        if existing.is_terminal:
            # Already terminal -- idempotent no-op per contract.
            return
        try:
            status, payload = self._request("DELETE", f"/v2/orders/{existing.broker_order_id}")
        except _AmbiguousTransport:
            # Cancel is documented as best-effort and asynchronous;
            # ambiguity here is not a failure. Callers must re-poll
            # for the terminal state per the BrokerClient contract.
            return

        # 204 = cancel accepted; 200 = accepted with a body; 404/422 =
        # already terminal or unknown. All are safe outcomes for an
        # idempotent cancel; only a 5xx is worth surfacing.
        if 500 <= status < 600:
            raise BrokerCommunicationError(
                f"Alpaca returned HTTP {status} on cancel for "
                f"broker_order_id={existing.broker_order_id!r}: {_error_snippet(payload)}"
            )

    def get_cash_balance(self) -> float:
        try:
            status, payload = self._request("GET", "/v2/account")
        except _AmbiguousTransport as ex:
            raise BrokerCommunicationError(str(ex)) from ex

        if not (200 <= status < 300):
            raise BrokerCommunicationError(
                f"Alpaca returned HTTP {status} on /v2/account: {_error_snippet(payload)}"
            )

        obj = _load_json(payload, on_bad_json="/v2/account payload was not valid JSON")

        if obj.get("account_blocked") is True or obj.get("trading_blocked") is True:
            raise BrokerAccountBlockedError(
                f"Alpaca account is blocked: "
                f"account_blocked={obj.get('account_blocked')!r}, "
                f"trading_blocked={obj.get('trading_blocked')!r}"
            )

        raw = obj.get("cash")
        if raw is None:
            raise BrokerCommunicationError("/v2/account response is missing 'cash'")
        try:
            return float(raw)
        except (TypeError, ValueError) as ex:
            raise BrokerCommunicationError(f"/v2/account 'cash' was not a number: {raw!r}") from ex

    # ---- transport internals ------------------------------------------

    # Standard "ambiguous" transport-level exceptions -- a caller of
    # _request() does not know whether the broker received or processed
    # the request. Any transport (default or injected) may raise these;
    # _request() translates them into the internal _AmbiguousTransport
    # marker for the calling method to map to the right public error
    # class.
    _AMBIGUOUS_NETWORK_EXCEPTIONS = (
        urllib.error.URLError,
        socket.timeout,
        ConnectionError,
        TimeoutError,
    )

    def _request(self, method: str, path: str, *, body: Optional[dict] = None) -> HttpResponse:
        url = self._config.base_url + path
        headers = {
            "APCA-API-KEY-ID": self._config.key_id,
            "APCA-API-SECRET-KEY": self._config.secret_key,
            "Accept": "application/json",
        }
        raw_body: Optional[bytes] = None
        if body is not None:
            raw_body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            return self._http(
                method=method,
                url=url,
                headers=headers,
                body=raw_body,
                timeout=self._config.timeout_seconds,
            )
        except self._AMBIGUOUS_NETWORK_EXCEPTIONS as ex:
            raise _AmbiguousTransport(str(ex)) from ex

    @staticmethod
    def _default_transport(
        *,
        method: str,
        url: str,
        headers: dict,
        body: Optional[bytes],
        timeout: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url=url, method=method, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.getcode()
                payload = response.read()
                return status, payload
        except urllib.error.HTTPError as http_err:
            # A well-formed HTTP error IS a definite outcome; return
            # the status and body so the caller can classify it. Other
            # network exceptions (URLError, timeouts, connection resets)
            # are re-raised and translated to _AmbiguousTransport inside
            # _request().
            try:
                payload = http_err.read()
            except Exception:  # noqa: BLE001 -- best-effort read of a torn error body
                payload = b""
            return http_err.code, payload


# ---- private helpers --------------------------------------------------


class _AmbiguousTransport(Exception):
    """Internal marker for transport-level ambiguity. Translated by the
    calling method into the correct public BrokerClient error class."""


def _parse_order_state(payload: bytes) -> BrokerOrderState:
    obj = _load_json(payload, on_bad_json="Alpaca order payload was not valid JSON")

    broker_order_id = obj.get("id")
    status = obj.get("status")
    if not isinstance(broker_order_id, str) or not isinstance(status, str):
        raise BrokerClientError(
            f"Alpaca order payload missing required fields 'id' and 'status': "
            f"got id={broker_order_id!r}, status={status!r}"
        )

    filled_qty_raw = obj.get("filled_qty") or "0"
    try:
        # Alpaca returns filled_qty as a decimal string; the strategy
        # only ever submits integer share counts, so the filled quantity
        # is expected to be an integer on the wire.
        filled_qty = int(float(filled_qty_raw))
    except (TypeError, ValueError) as ex:
        raise BrokerClientError(
            f"Alpaca order payload 'filled_qty' was not numeric: {filled_qty_raw!r}"
        ) from ex

    filled_avg_price_raw = obj.get("filled_avg_price")
    filled_avg_price: Optional[float]
    if filled_avg_price_raw in (None, ""):
        filled_avg_price = None
    else:
        try:
            filled_avg_price = float(filled_avg_price_raw)
        except (TypeError, ValueError) as ex:
            raise BrokerClientError(
                f"Alpaca order payload 'filled_avg_price' was not numeric: "
                f"{filled_avg_price_raw!r}"
            ) from ex

    return BrokerOrderState(
        broker_order_id=broker_order_id,
        status=status,  # raw broker string, passed through unmodified
        is_terminal=(status in _TERMINAL_ALPACA_STATUSES),
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
    )


def _is_duplicate_client_order_id(payload: bytes) -> bool:
    # Alpaca's duplicate marker appears in the JSON error body's
    # `message` field. Matching a substring keeps this resilient to
    # small wording changes across Alpaca API versions.
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    message = obj.get("message", "")
    if not isinstance(message, str):
        return False
    lowered = message.lower()
    return "client_order_id" in lowered and ("already exists" in lowered or "duplicate" in lowered)


def _load_json(payload: bytes, *, on_bad_json: str) -> dict:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as ex:
        raise BrokerCommunicationError(on_bad_json) from ex
    if not isinstance(obj, dict):
        raise BrokerCommunicationError(f"{on_bad_json}: top-level value was not a JSON object")
    return obj


def _error_snippet(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 -- diagnostic path must never mask the primary error
        text = "<unreadable body>"
    return text[:200]


def _validate_client_order_id(client_order_id: str) -> None:
    if not isinstance(client_order_id, str) or not client_order_id:
        raise ValueError("client_order_id must be a non-empty string")


def _validate_symbol(symbol: str) -> None:
    if not isinstance(symbol, str) or not symbol or not symbol.isupper():
        raise ValueError(f"symbol must be a non-empty uppercase string, got {symbol!r}")
