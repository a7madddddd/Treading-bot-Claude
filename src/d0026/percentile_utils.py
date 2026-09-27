"""Percentile / rank helpers for the D-0048 percentage-only pipeline.

Small, pure functions. No I/O. Never mutate inputs. Ties are broken
by input order (stable), so results are deterministic given the same
input tuple. Missing values (None) are excluded from percentile
computation and returned as an unranked/absent set the caller then
decides to drop or keep.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")


def top_percentile(
    items: Iterable[T],
    key: Callable[[T], Optional[float]],
    top_fraction: float,
) -> Tuple[Tuple[T, ...], Tuple[T, ...]]:
    """Returns (survivors, rejected). Survivors are the items in the
    top `top_fraction` of the pool ranked by `key(item)`. Items whose
    `key` is None go to `rejected`. Ties are broken by input order,
    stable across calls. Boundary rounding: at least
    `ceil(top_fraction * n_keyed)` survivors, but never more than the
    n_keyed pool.
    """

    if not (0.0 < top_fraction <= 1.0):
        raise ValueError(f"top_fraction must be in (0, 1], got {top_fraction!r}")

    keyed: List[Tuple[float, int, T]] = []
    missing: List[T] = []
    for idx, item in enumerate(items):
        v = key(item)
        if v is None:
            missing.append(item)
        else:
            keyed.append((v, idx, item))
    if not keyed:
        return (), tuple(missing)

    keyed.sort(key=lambda kvi: (-kvi[0], kvi[1]))
    n = len(keyed)
    n_top = max(1, _ceil(top_fraction * n))
    n_top = min(n_top, n)

    survivor_ids = {kvi[1] for kvi in keyed[:n_top]}
    survivors = tuple(kvi[2] for kvi in keyed if kvi[1] in survivor_ids)
    rejected_ranked = tuple(kvi[2] for kvi in keyed if kvi[1] not in survivor_ids)
    return survivors, rejected_ranked + tuple(missing)


def drop_bottom_percentile(
    items: Iterable[T],
    key: Callable[[T], Optional[float]],
    bottom_fraction: float,
) -> Tuple[Tuple[T, ...], Tuple[T, ...]]:
    """Returns (survivors, rejected). Rejects the bottom
    `bottom_fraction` of the pool by `key(item)`. The drop count is
    computed directly: `drop_n = ceil(n * bottom_fraction)`, so a
    small pool with a small fraction can still drop one item rather
    than round to zero. Items whose key is None go to rejected."""

    if not (0.0 <= bottom_fraction < 1.0):
        raise ValueError(
            f"bottom_fraction must be in [0, 1), got {bottom_fraction!r}"
        )

    keyed: List[Tuple[float, int, T]] = []
    missing: List[T] = []
    for idx, item in enumerate(items):
        v = key(item)
        if v is None:
            missing.append(item)
        else:
            keyed.append((v, idx, item))
    if not keyed:
        return (), tuple(missing)
    if bottom_fraction == 0.0:
        return tuple(kvi[2] for kvi in keyed), tuple(missing)

    n = len(keyed)
    # Standard rounding: `n * bottom_fraction` is the fractional
    # number of items to drop; round-half-to-even (Python's `round`)
    # gives the intuitively correct count. For a 1-item pool with
    # bottom_fraction < 0.5, the item is kept (nothing to compare
    # against). For pool >= 5, results align with what a human would
    # expect: drop 1 of 5 (20%), drop 2 of 10 (20%), etc.
    drop_n = min(n, round(n * bottom_fraction)) if bottom_fraction > 0 else 0
    keyed.sort(key=lambda kvi: (kvi[0], kvi[1]))  # ascending
    dropped_ids = {keyed[i][1] for i in range(drop_n)}
    survivors = tuple(kvi[2] for kvi in keyed if kvi[1] not in dropped_ids)
    rejected = tuple(kvi[2] for kvi in keyed if kvi[1] in dropped_ids)
    return survivors, rejected + tuple(missing)


def rank_percentile(
    items: Iterable[T],
    key: Callable[[T], Optional[float]],
) -> Tuple[Tuple[T, float], ...]:
    """Returns each item paired with its rank in [0, 1], where 1.0 is
    the largest value. Items with key=None are excluded from the
    returned tuple. Ties get the same rank (average of tied ranks)."""

    keyed = [(key(it), idx, it) for idx, it in enumerate(items)
             if key(it) is not None]
    if not keyed:
        return ()
    # Sort ascending to compute rank.
    keyed.sort(key=lambda kvi: (kvi[0], kvi[1]))
    n = len(keyed)
    ranks: dict = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and keyed[j + 1][0] == keyed[i][0]:
            j += 1
        # Average rank for the tie group (1-based).
        avg_rank = ((i + 1) + (j + 1)) / 2.0
        norm = (avg_rank - 1) / (n - 1) if n > 1 else 1.0
        for k in range(i, j + 1):
            ranks[keyed[k][1]] = norm
        i = j + 1
    return tuple((it, ranks[idx]) for _, idx, it in keyed)


def historical_percentile(values: Iterable[float], current: float) -> float:
    """Returns the fraction of historical values strictly less than
    `current`. Result in [0, 1]. Empty history → 0.5 (neutral)."""

    vs = [v for v in values if v is not None]
    if not vs:
        return 0.5
    less = sum(1 for v in vs if v < current)
    return less / len(vs)


def _ceil(x: float) -> int:
    n = int(x)
    return n + 1 if x - n > 1e-12 else n
