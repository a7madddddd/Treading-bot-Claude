"""Tests for common.http_retry (B26)."""

import unittest

from common.http_retry import (
    RetryPolicy, compute_backoff, is_retryable_status, with_retry,
)


class _StubResp:
    def __init__(self, status: int) -> None:
        self.status = status


class TestPolicy(unittest.TestCase):
    def test_defaults(self):
        p = RetryPolicy()
        self.assertEqual(p.max_attempts, 3)
        self.assertEqual(p.base_backoff_seconds, 1.0)
        self.assertEqual(p.max_backoff_seconds, 30.0)

    def test_bad_max_attempts(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_bad_backoff(self):
        with self.assertRaises(ValueError):
            RetryPolicy(base_backoff_seconds=-1)
        with self.assertRaises(ValueError):
            RetryPolicy(base_backoff_seconds=10.0, max_backoff_seconds=5.0)


class TestRetryableStatus(unittest.TestCase):
    def test_429_retryable(self):
        self.assertTrue(is_retryable_status(429))

    def test_5xx_retryable(self):
        self.assertTrue(is_retryable_status(500))
        self.assertTrue(is_retryable_status(503))
        self.assertTrue(is_retryable_status(599))

    def test_4xx_non_retryable(self):
        for s in (400, 401, 403, 404, 422):
            self.assertFalse(is_retryable_status(s))

    def test_2xx_non_retryable(self):
        for s in (200, 201, 204):
            self.assertFalse(is_retryable_status(s))


class TestBackoff(unittest.TestCase):
    def test_exponential(self):
        p = RetryPolicy(base_backoff_seconds=1.0,
                        max_backoff_seconds=100.0)
        self.assertEqual(compute_backoff(1, p), 1.0)
        self.assertEqual(compute_backoff(2, p), 2.0)
        self.assertEqual(compute_backoff(3, p), 4.0)
        self.assertEqual(compute_backoff(4, p), 8.0)

    def test_capped(self):
        p = RetryPolicy(base_backoff_seconds=1.0,
                        max_backoff_seconds=5.0)
        self.assertEqual(compute_backoff(10, p), 5.0)

    def test_zero_attempt_rejected(self):
        with self.assertRaises(ValueError):
            compute_backoff(0, RetryPolicy())


class TestWithRetry(unittest.TestCase):
    def test_no_retry_on_success(self):
        calls = []
        def inner(x):
            calls.append(x)
            return _StubResp(200)
        wrapped = with_retry(inner, policy=RetryPolicy(),
                             sleep_fn=lambda s: None)
        r = wrapped(42)
        self.assertEqual(r.status, 200)
        self.assertEqual(calls, [42])

    def test_retry_on_429_until_success(self):
        responses = iter([_StubResp(429), _StubResp(429), _StubResp(200)])
        sleeps = []
        wrapped = with_retry(lambda: next(responses),
                             policy=RetryPolicy(max_attempts=3,
                                               base_backoff_seconds=1.0,
                                               max_backoff_seconds=10.0),
                             sleep_fn=sleeps.append)
        r = wrapped()
        self.assertEqual(r.status, 200)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_retry_on_500(self):
        responses = iter([_StubResp(500), _StubResp(200)])
        wrapped = with_retry(lambda: next(responses),
                             policy=RetryPolicy(max_attempts=3),
                             sleep_fn=lambda s: None)
        self.assertEqual(wrapped().status, 200)

    def test_max_attempts_reached_returns_last(self):
        responses = iter([_StubResp(500), _StubResp(500), _StubResp(500)])
        wrapped = with_retry(lambda: next(responses),
                             policy=RetryPolicy(max_attempts=3),
                             sleep_fn=lambda s: None)
        r = wrapped()
        self.assertEqual(r.status, 500)

    def test_400_returned_immediately(self):
        calls = [0]
        def inner():
            calls[0] += 1
            return _StubResp(400)
        wrapped = with_retry(inner, policy=RetryPolicy(),
                             sleep_fn=lambda s: None)
        r = wrapped()
        self.assertEqual(r.status, 400)
        self.assertEqual(calls[0], 1)  # no retries on 4xx

    def test_tuple_response_shape(self):
        responses = iter([(429, b""), (200, b"ok")])
        wrapped = with_retry(lambda: next(responses),
                             policy=RetryPolicy(max_attempts=3),
                             sleep_fn=lambda s: None)
        status, body = wrapped()
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ok")

    def test_exception_propagates(self):
        def inner():
            raise ConnectionError("net down")
        wrapped = with_retry(inner, policy=RetryPolicy(),
                             sleep_fn=lambda s: None)
        with self.assertRaises(ConnectionError):
            wrapped()

    def test_passes_kwargs(self):
        seen = []
        def inner(*args, **kwargs):
            seen.append((args, kwargs))
            return _StubResp(200)
        wrapped = with_retry(inner, policy=RetryPolicy(),
                             sleep_fn=lambda s: None)
        wrapped("url", headers={"a": "b"}, timeout=5.0)
        self.assertEqual(seen[0],
                         (("url",), {"headers": {"a": "b"}, "timeout": 5.0}))


if __name__ == "__main__":
    unittest.main()
