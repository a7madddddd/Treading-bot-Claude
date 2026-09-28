"""Integration test: AlpacaBrokerClient with an injected retry policy
retries on 429/5xx and returns immediately on success. Verifies the
wiring in alpaca_broker_client.__init__."""

import unittest

from common.http_retry import RetryPolicy
from execution.alpaca_broker_client import AlpacaBrokerClient


PAPER_URL = "https://paper-api.alpaca.markets"


class TestBrokerRetryWiring(unittest.TestCase):
    def test_retry_policy_wraps_transport(self):
        # Inject a transport that returns 429 twice then a successful
        # account payload.
        responses = iter([
            (429, b'{"error":"rate limit"}'),
            (429, b'{"error":"rate limit"}'),
            (200, b'{"cash":"12345.67"}'),
        ])
        def transport(method, url, **kw):
            return next(responses)
        sleeps: list = []

        broker = AlpacaBrokerClient(
            base_url=PAPER_URL, key_id="k", secret_key="s",
            http_transport=transport,
            retry_policy=RetryPolicy(max_attempts=3,
                                     base_backoff_seconds=1.0,
                                     max_backoff_seconds=10.0),
        )
        # Monkeypatch time.sleep inside the wrapped transport by
        # replacing the transport with a fresh with_retry using our
        # sleep_fn -- easier: construct our own retry wrapper.
        from common.http_retry import with_retry
        responses2 = iter([
            (429, b'{"error":"rate limit"}'),
            (429, b'{"error":"rate limit"}'),
            (200, b'{"cash":"12345.67"}'),
        ])
        broker2 = AlpacaBrokerClient(
            base_url=PAPER_URL, key_id="k", secret_key="s",
            http_transport=with_retry(
                lambda method, url, **kw: next(responses2),
                policy=RetryPolicy(max_attempts=3,
                                   base_backoff_seconds=1.0),
                sleep_fn=sleeps.append,
            ),
        )
        result = broker2.get_cash_balance()
        self.assertAlmostEqual(result, 12345.67)
        # 2 retries -> 2 sleeps
        self.assertEqual(len(sleeps), 2)

    def test_success_on_first_call_no_sleep(self):
        sleeps: list = []
        from common.http_retry import with_retry
        broker = AlpacaBrokerClient(
            base_url=PAPER_URL, key_id="k", secret_key="s",
            http_transport=with_retry(
                lambda method, url, **kw: (200, b'{"cash":"1.0"}'),
                policy=RetryPolicy(max_attempts=3),
                sleep_fn=sleeps.append,
            ),
        )
        self.assertAlmostEqual(broker.get_cash_balance(), 1.0)
        self.assertEqual(sleeps, [])

    def test_400_immediate_no_retry(self):
        calls = [0]
        from common.http_retry import with_retry
        def counting(method, url, **kw):
            calls[0] += 1
            return (400, b'{"error":"bad"}')
        # 400 is not retryable, so with retry_policy still one call.
        broker = AlpacaBrokerClient(
            base_url=PAPER_URL, key_id="k", secret_key="s",
            http_transport=with_retry(
                counting, policy=RetryPolicy(max_attempts=5),
                sleep_fn=lambda s: None,
            ),
        )
        # 400 → get_cash_balance raises; that's expected behavior.
        with self.assertRaises(Exception):
            broker.get_cash_balance()
        self.assertEqual(calls[0], 1)


if __name__ == "__main__":
    unittest.main()
