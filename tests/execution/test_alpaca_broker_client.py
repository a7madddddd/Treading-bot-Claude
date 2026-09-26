import json
import unittest
from typing import List, Optional, Tuple

from execution.alpaca_broker_client import AlpacaBrokerClient
from execution.broker_client import (
    BrokerAccountBlockedError,
    BrokerClientError,
    BrokerCommunicationError,
    BrokerOrderState,
    BrokerSubmissionAmbiguousError,
)


# ---- test transport -----------------------------------------------------


class _RecordedCall:
    def __init__(self, method: str, url: str, headers: dict, body: Optional[bytes]):
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body


class _StubTransport:
    """Callable that behaves like a real HTTP transport but is fully
    scripted by the test: each call pops the next queued response. A
    queued Exception is raised instead of returning."""

    def __init__(self):
        self._queue: List = []
        self.calls: List[_RecordedCall] = []

    def queue(self, response: Tuple[int, bytes]) -> None:
        self._queue.append(response)

    def queue_exception(self, exc: BaseException) -> None:
        self._queue.append(exc)

    def __call__(self, *, method: str, url: str, headers: dict, body: Optional[bytes], timeout: float):
        self.calls.append(_RecordedCall(method, url, headers, body))
        if not self._queue:
            raise AssertionError(f"unexpected transport call: {method} {url}")
        nxt = self._queue.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def _client(transport: _StubTransport) -> AlpacaBrokerClient:
    return AlpacaBrokerClient(
        base_url="https://paper-api.alpaca.markets",
        key_id="TEST_KEY",
        secret_key="TEST_SECRET",
        http_transport=transport,
    )


def _order_payload(**overrides) -> bytes:
    obj = {
        "id": "BROKER-1",
        "client_order_id": "C-1",
        "status": "accepted",
        "filled_qty": "0",
        "filled_avg_price": None,
    }
    obj.update(overrides)
    return json.dumps(obj).encode("utf-8")


# ---- construction -------------------------------------------------------


class TestConstruction(unittest.TestCase):
    def test_rejects_empty_base_url(self):
        with self.assertRaises(ValueError):
            AlpacaBrokerClient(base_url="", key_id="k", secret_key="s")

    def test_rejects_missing_credentials(self):
        with self.assertRaises(ValueError):
            AlpacaBrokerClient(base_url="https://x", key_id="", secret_key="s")
        with self.assertRaises(ValueError):
            AlpacaBrokerClient(base_url="https://x", key_id="k", secret_key="")

    def test_rejects_non_paper_url_by_default(self):
        # Paper-only guardrail (D-0002; verification-plan §3): the client
        # must refuse construction against Alpaca's live URL.
        with self.assertRaises(ValueError) as ctx:
            AlpacaBrokerClient(
                base_url="https://api.alpaca.markets",
                key_id="k", secret_key="s",
                http_transport=_StubTransport(),
            )
        self.assertIn("paper", str(ctx.exception).lower())

    def test_rejects_arbitrary_non_paper_host(self):
        with self.assertRaises(ValueError):
            AlpacaBrokerClient(
                base_url="https://evil.example.com",
                key_id="k", secret_key="s",
                http_transport=_StubTransport(),
            )

    def test_paper_url_variants_accepted(self):
        # All these contain 'paper-api' and must be accepted.
        for url in (
            "https://paper-api.alpaca.markets",
            "https://paper-api.alpaca.markets/",
            "https://paper-api.alpaca.markets/v2",
        ):
            AlpacaBrokerClient(
                base_url=url, key_id="k", secret_key="s",
                http_transport=_StubTransport(),
            )  # must not raise

    def test_allow_non_paper_url_opt_in_bypasses_guard(self):
        # Reserved for a future signed decision authorizing live trading;
        # today it is not used anywhere, but must exist as the documented
        # opt-in path so the guard is not silently bypassable via config.
        AlpacaBrokerClient(
            base_url="https://api.alpaca.markets",
            key_id="k", secret_key="s",
            http_transport=_StubTransport(),
            allow_non_paper_url=True,
        )  # must not raise

    def test_strips_trailing_slash_and_v2_suffix(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload()))
        for base in (
            "https://paper-api.alpaca.markets",
            "https://paper-api.alpaca.markets/",
            "https://paper-api.alpaca.markets/v2",
            "https://paper-api.alpaca.markets/v2/",
        ):
            transport = _StubTransport()
            transport.queue((200, _order_payload()))
            client = AlpacaBrokerClient(
                base_url=base, key_id="k", secret_key="s", http_transport=transport
            )
            client.submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=10, limit_price=100.0,
            )
            self.assertEqual(
                transport.calls[0].url,
                "https://paper-api.alpaca.markets/v2/orders",
                msg=f"base_url normalization failed for {base!r}",
            )


# ---- submit_order -------------------------------------------------------


class TestSubmitOrder(unittest.TestCase):
    def test_happy_path_accepted(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(status="accepted")))
        state = _client(transport).submit_order(
            client_order_id="C-1", symbol="AAPL", side="buy",
            quantity=10, limit_price=100.0,
        )
        self.assertEqual(state.status, "accepted")
        self.assertFalse(state.is_terminal)
        self.assertEqual(state.filled_qty, 0)
        self.assertIsNone(state.filled_avg_price)

    def test_immediate_fill_marks_terminal(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(
            status="filled", filled_qty="10", filled_avg_price="100.5"
        )))
        state = _client(transport).submit_order(
            client_order_id="C-1", symbol="AAPL", side="buy",
            quantity=10, limit_price=100.0,
        )
        self.assertEqual(state.status, "filled")
        self.assertTrue(state.is_terminal)
        self.assertEqual(state.filled_qty, 10)
        self.assertEqual(state.filled_avg_price, 100.5)

    def test_partial_fill_is_non_terminal(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(
            status="partially_filled", filled_qty="5", filled_avg_price="100.0"
        )))
        state = _client(transport).submit_order(
            client_order_id="C-1", symbol="AAPL", side="buy",
            quantity=10, limit_price=100.0,
        )
        self.assertEqual(state.status, "partially_filled")
        self.assertFalse(state.is_terminal)  # partially_filled is NOT terminal

    def test_rejected_definite_raises_broker_client_error(self):
        transport = _StubTransport()
        transport.queue((400, b'{"message":"insufficient buying power"}'))
        with self.assertRaises(BrokerClientError) as ctx:
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=10, limit_price=100.0,
            )
        # Must NOT be the ambiguous variant -- a well-formed HTTP 400 is a
        # definite rejection.
        self.assertNotIsInstance(ctx.exception, BrokerSubmissionAmbiguousError)

    def test_timeout_raises_ambiguous(self):
        transport = _StubTransport()
        transport.queue_exception(TimeoutError("simulated timeout"))
        with self.assertRaises(BrokerSubmissionAmbiguousError):
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=10, limit_price=100.0,
            )

    def test_connection_reset_raises_ambiguous(self):
        transport = _StubTransport()
        transport.queue_exception(ConnectionError("connection reset by peer"))
        with self.assertRaises(BrokerSubmissionAmbiguousError):
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=10, limit_price=100.0,
            )

    def test_duplicate_client_order_id_returns_existing_state(self):
        transport = _StubTransport()
        # First call: submit returns 422 duplicate.
        transport.queue((422, b'{"message":"client_order_id already exists"}'))
        # Second call: lookup returns the already-accepted order.
        transport.queue((200, _order_payload(status="filled", filled_qty="10",
                                             filled_avg_price="100.0")))
        state = _client(transport).submit_order(
            client_order_id="C-1", symbol="AAPL", side="buy",
            quantity=10, limit_price=100.0,
        )
        self.assertEqual(state.status, "filled")
        self.assertTrue(state.is_terminal)

    def test_duplicate_but_lookup_finds_nothing_is_ambiguous(self):
        transport = _StubTransport()
        transport.queue((422, b'{"message":"client_order_id already exists"}'))
        transport.queue((404, b""))
        with self.assertRaises(BrokerSubmissionAmbiguousError):
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=10, limit_price=100.0,
            )

    def test_rejects_bad_side(self):
        transport = _StubTransport()
        with self.assertRaises(ValueError):
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="short",
                quantity=10, limit_price=100.0,
            )

    def test_rejects_non_positive_quantity(self):
        transport = _StubTransport()
        with self.assertRaises(ValueError):
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=0, limit_price=100.0,
            )

    def test_rejects_non_positive_price(self):
        transport = _StubTransport()
        with self.assertRaises(ValueError):
            _client(transport).submit_order(
                client_order_id="C-1", symbol="AAPL", side="buy",
                quantity=10, limit_price=0.0,
            )

    def test_sends_expected_body(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload()))
        _client(transport).submit_order(
            client_order_id="C-1", symbol="AAPL", side="buy",
            quantity=10, limit_price=101.25,
        )
        call = transport.calls[0]
        self.assertEqual(call.method, "POST")
        self.assertEqual(call.url, "https://paper-api.alpaca.markets/v2/orders")
        self.assertEqual(call.headers["APCA-API-KEY-ID"], "TEST_KEY")
        self.assertEqual(call.headers["APCA-API-SECRET-KEY"], "TEST_SECRET")
        body = json.loads(call.body.decode("utf-8"))
        self.assertEqual(body["symbol"], "AAPL")
        self.assertEqual(body["qty"], "10")
        self.assertEqual(body["side"], "buy")
        self.assertEqual(body["type"], "limit")
        self.assertEqual(body["client_order_id"], "C-1")
        self.assertEqual(body["limit_price"], "101.2500")


# ---- get_order_by_client_order_id --------------------------------------


class TestGetOrderByClientOrderId(unittest.TestCase):
    def test_happy_path_returns_state(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(status="new")))
        state = _client(transport).get_order_by_client_order_id("C-1")
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "new")
        self.assertFalse(state.is_terminal)

    def test_404_returns_none(self):
        transport = _StubTransport()
        transport.queue((404, b""))
        self.assertIsNone(_client(transport).get_order_by_client_order_id("C-1"))

    def test_timeout_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue_exception(TimeoutError("simulated"))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_order_by_client_order_id("C-1")

    def test_5xx_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue((503, b'{"message":"service unavailable"}'))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_order_by_client_order_id("C-1")


# ---- cancel_order -------------------------------------------------------


class TestCancelOrder(unittest.TestCase):
    def test_noop_when_order_not_found(self):
        transport = _StubTransport()
        transport.queue((404, b""))
        _client(transport).cancel_order("C-1")  # must not raise

    def test_noop_when_order_already_terminal(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(status="filled", filled_qty="10")))
        _client(transport).cancel_order("C-1")
        # Only the lookup call happened; no DELETE was issued.
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0].method, "GET")

    def test_deletes_when_order_is_non_terminal(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(status="accepted")))
        transport.queue((204, b""))
        _client(transport).cancel_order("C-1")
        self.assertEqual(transport.calls[1].method, "DELETE")
        self.assertEqual(
            transport.calls[1].url,
            "https://paper-api.alpaca.markets/v2/orders/BROKER-1",
        )

    def test_timeout_on_delete_is_silent(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(status="accepted")))
        transport.queue_exception(TimeoutError("simulated"))
        _client(transport).cancel_order("C-1")  # ambiguous cancel is a no-op

    def test_5xx_on_delete_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue((200, _order_payload(status="accepted")))
        transport.queue((500, b'{"message":"boom"}'))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).cancel_order("C-1")


# ---- get_cash_balance --------------------------------------------------


def _account_payload(**overrides) -> bytes:
    obj = {
        "account_number": "PA-1",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1234.56",
        "trading_blocked": False,
        "account_blocked": False,
    }
    obj.update(overrides)
    return json.dumps(obj).encode("utf-8")


class TestGetCashBalance(unittest.TestCase):
    def test_happy_path_returns_cash(self):
        transport = _StubTransport()
        transport.queue((200, _account_payload(cash="1234.56")))
        self.assertEqual(_client(transport).get_cash_balance(), 1234.56)

    def test_hits_account_endpoint(self):
        transport = _StubTransport()
        transport.queue((200, _account_payload()))
        _client(transport).get_cash_balance()
        self.assertEqual(transport.calls[0].method, "GET")
        self.assertEqual(
            transport.calls[0].url,
            "https://paper-api.alpaca.markets/v2/account",
        )

    def test_trading_blocked_raises_blocked_error(self):
        transport = _StubTransport()
        transport.queue((200, _account_payload(cash="1234.56", trading_blocked=True)))
        with self.assertRaises(BrokerAccountBlockedError):
            _client(transport).get_cash_balance()

    def test_account_blocked_raises_blocked_error(self):
        transport = _StubTransport()
        transport.queue((200, _account_payload(cash="1234.56", account_blocked=True)))
        with self.assertRaises(BrokerAccountBlockedError):
            _client(transport).get_cash_balance()

    def test_timeout_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue_exception(TimeoutError("simulated"))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_cash_balance()

    def test_non_2xx_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue((503, b"unavailable"))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_cash_balance()

    def test_missing_cash_field_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue((200, b'{"status":"ACTIVE"}'))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_cash_balance()

    def test_non_numeric_cash_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue((200, _account_payload(cash="not-a-number")))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_cash_balance()


# ---- terminal-status classification (comprehensive) --------------------


class TestTerminalClassification(unittest.TestCase):
    """Directly exercises the terminal-status set the concrete client
    must enforce, per Alpaca's public documentation and D-0039 §4."""

    NON_TERMINAL = (
        "new", "accepted", "pending_new", "accepted_for_bidding",
        "pending_replace", "pending_cancel", "partially_filled",
        "suspended", "held", "calculated",
    )

    TERMINAL = (
        "filled", "canceled", "expired", "rejected", "done_for_day",
        "stopped", "replaced",
    )

    def test_non_terminal_statuses_are_non_terminal(self):
        for status in self.NON_TERMINAL:
            transport = _StubTransport()
            transport.queue((200, _order_payload(status=status)))
            state = _client(transport).get_order_by_client_order_id("C-1")
            self.assertIsNotNone(state)
            self.assertEqual(state.status, status)
            self.assertFalse(
                state.is_terminal,
                msg=f"status {status!r} must be classified non-terminal",
            )

    def test_terminal_statuses_are_terminal(self):
        for status in self.TERMINAL:
            transport = _StubTransport()
            transport.queue((200, _order_payload(status=status)))
            state = _client(transport).get_order_by_client_order_id("C-1")
            self.assertIsNotNone(state)
            self.assertEqual(state.status, status)
            self.assertTrue(
                state.is_terminal,
                msg=f"status {status!r} must be classified terminal",
            )


# ---- payload robustness ------------------------------------------------


class TestPayloadRobustness(unittest.TestCase):
    def test_missing_id_or_status_raises(self):
        transport = _StubTransport()
        transport.queue((200, b'{"id":"BROKER-1"}'))  # no status
        with self.assertRaises(BrokerClientError):
            _client(transport).get_order_by_client_order_id("C-1")

    def test_bad_json_raises_communication_error(self):
        transport = _StubTransport()
        transport.queue((200, b"not json"))
        with self.assertRaises(BrokerCommunicationError):
            _client(transport).get_order_by_client_order_id("C-1")


if __name__ == "__main__":
    unittest.main()
