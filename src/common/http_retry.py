"""Generic HTTP retry helper (B26).

Wraps any transport callable with retry-on-transient-failure logic.
Signature-agnostic: the wrapped transport can take any positional /
keyword arguments; only the RESULT is inspected. The result must be
either:
  - an object with a `status: int` attribute, or
  - a `(status: int, body: bytes)` tuple.

Retryable statuses: 429 (rate limit) and 5xx (server). All other
statuses (including 4xx auth / validation errors) are returned
immediately -- retrying a 401 or a 400 will not help.

Never mutates the caller's arguments. Deterministic: given the same
inner transport (or a mock returning a fixed sequence), the wrapper
produces the same call count and the same final response.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Tuple


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential-backoff retry policy.

    Defaults are tuned for public REST APIs:
      max_attempts       = 3   (1 original + 2 retries)
      base_backoff_seconds = 1.0
      max_backoff_seconds  = 30.0
    Backoff on attempt N is min(max, base * 2^(N-1)):
        attempt 1 completes -> sleep 1s -> attempt 2
        attempt 2 completes -> sleep 2s -> attempt 3
    """

    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_backoff_seconds < 0:
            raise ValueError("base_backoff_seconds must be >= 0")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise ValueError(
                "max_backoff_seconds must be >= base_backoff_seconds"
            )


def is_retryable_status(status: int) -> bool:
    """429 (rate limit) or any 5xx server error."""
    return status == 429 or 500 <= status < 600


def compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    """Exponential backoff, 1-indexed attempt. `attempt=1` returns
    `base`; each next attempt doubles up to `max`. Deterministic:
    always returns the same value for the same inputs."""

    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    raw = policy.base_backoff_seconds * (2 ** (attempt - 1))
    return min(policy.max_backoff_seconds, raw)


def _extract_status(response: Any) -> int:
    if hasattr(response, "status"):
        return int(response.status)
    if isinstance(response, tuple) and response:
        return int(response[0])
    raise TypeError(
        f"cannot extract status from transport response of type "
        f"{type(response).__name__}"
    )


def with_retry(
    inner_transport: Callable[..., Any],
    *,
    policy: RetryPolicy,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Callable[..., Any]:
    """Returns a new callable that invokes `inner_transport` with the
    same positional / keyword arguments, and retries on retryable
    statuses up to `policy.max_attempts` total attempts.

    Never catches exceptions -- a raising inner transport propagates
    unchanged. Retry logic operates on HTTP response STATUS only, not
    on transport-level errors. That matches the D-0002 fail-closed
    posture: an unknown transport failure must not be silently
    retried and turned into an ambiguous submission.

    `sleep_fn` is injectable for tests (default: time.sleep).
    """

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        last_response: Any = None
        for attempt in range(1, policy.max_attempts + 1):
            last_response = inner_transport(*args, **kwargs)
            status = _extract_status(last_response)
            if not is_retryable_status(status):
                return last_response
            if attempt < policy.max_attempts:
                sleep_fn(compute_backoff(attempt, policy))
        return last_response

    return wrapped
