#!/usr/bin/env python3
"""P-025 measurement tool — how fast do the Universe's symbols actually move?

READ-ONLY. This script cannot change anything:
  - it never opens paper_session.sqlite
  - it never writes a snapshot
  - it never sends Telegram
  - it never touches the engine or any parameter
It fetches daily bars and prints tables. It is safe to run while the
engine is live.

WHY IT EXISTS
-------------
D-0048 approves an ATR band of 1%–5% of price. D-0004 approves fixed
ladder levels at −5% / −8% / −10%. Nothing reconciles the two, so how
fast the ladder walks depends entirely on where in the band a symbol
sits:

    ATR 1%  ->  −5% is ~5 average adverse days, −10% is ~10
    ATR 5%  ->  −5% is ~1 day,  −8% ~1.6,  −10% ~2

At the slow end the ladder effectively never fires and the strategy
degrades into a single plain buy with three quarters of the trade budget
idle. At the fast end all three levels are crossed inside a week, so the
Floor becomes the normal ending rather than the last-resort exit
`strategy.md` §1 calls it.

That is a HYPOTHESIS about pace, not an observed loss. The point of this
tool is to replace it with counts, and above all to answer the one
question that decides whether narrowing the band is safe:

    IF WE NARROW THE BAND, HOW MANY CANDIDATES ARE LEFT?

Narrowing that improves quality but leaves three candidates a day is not
an improvement. The live 2026-10-03 snapshot had only ten survivors out
of 77 candidates, so the pool is already tight.

FAITHFULNESS
------------
It reuses the PRODUCTION objects — `AlpacaAssetsProvider` (including
D-0056's leveraged/inverse exclusion) and `AlpacaFeatureEnricher` — and
computes the ATR fraction exactly as Stage D does:

    atr_fraction = features.atr_measure / bar.close
    (src/d0026/stages/strategy_fit.py)

No parallel implementation of anything. A number printed here is the
number the pipeline would see.

Usage:
    PYTHONPATH=src python3 scripts/measure_atr_distribution.py --max-symbols 60
    PYTHONPATH=src python3 scripts/measure_atr_distribution.py          # whole market
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, ".."))
_src = os.path.join(_repo_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"[FAIL] env var {name} is missing", file=sys.stderr)
        raise SystemExit(1)
    return v


# Bucket edges as FRACTIONS of price. The 1% and 5% edges are D-0048's
# approved band; the inner edges split the band into the tree's three
# branches so each row maps to a behavior, not just a number.
_BUCKETS = (
    (0.000, 0.010, "below band - REJECTED by Stage D"),
    (0.010, 0.020, "slow  - ladder rarely fires"),
    (0.020, 0.030, "MIDDLE - what the ladder wants"),
    (0.030, 0.040, "MIDDLE - what the ladder wants"),
    (0.040, 0.050, "fast  - floor hit in ~2 days"),
    (0.050, 9.999, "above band - REJECTED by Stage D"),
)


def _bucket_for(fraction: float):
    for low, high, label in _BUCKETS:
        if low <= fraction < high:
            return (low, high, label)
    return _BUCKETS[-1]


def _days_to(pct: float, atr_fraction: float) -> str:
    """Average adverse days for a move of `pct` at this daily range.
    Deliberately crude -- it is the same arithmetic used in the tree the
    Controller approved reasoning from, and precision here would imply a
    confidence the model does not have."""
    if atr_fraction <= 0:
        return "n/a"
    return f"{pct / (atr_fraction * 100.0):.1f}"


def main() -> int:
    p = argparse.ArgumentParser(
        description="P-025: measure the ATR distribution of the live Universe "
                    "(read-only; writes nothing)")
    p.add_argument("--max-symbols", type=int, default=0,
                   help="Cap the number of symbols fetched (0 = whole market). "
                        "Start small: each symbol costs one bars request.")
    p.add_argument("--whitelist", default="",
                   help="Comma-separated symbols instead of the whole market.")
    p.add_argument("--effective-date",
                   help="Override the as-of date (YYYY-MM-DD).")
    p.add_argument("--progress-every", type=int, default=25,
                   help="Print a progress line every N symbols (default 25).")
    args = p.parse_args()

    key = _require_env("ALPACA_API_KEY_ID")
    sec = _require_env("ALPACA_API_SECRET_KEY")
    base = _require_env("ALPACA_BASE_URL")

    effective = (date.fromisoformat(args.effective_date)
                 if args.effective_date else date.today())
    whitelist = tuple(s.strip().upper() for s in args.whitelist.split(",")
                      if s.strip())

    from d0026.alpaca_provider import AlpacaAssetsProvider
    from d0026.alpaca_enricher import AlpacaFeatureEnricher
    from d0026.config import UniverseSelectionConfig
    from d0026.identity import (
        IdentityConfidence, IdentityResolution, ResolutionOutcome,
        SecurityIdentity, TickerAlias,
    )
    from d0026.models import RegimeLabel, RegimeState, UniverseCandidate

    cfg = UniverseSelectionConfig()

    print("=" * 68)
    print("P-025 ATR DISTRIBUTION — READ ONLY, writes nothing")
    print("=" * 68)
    print(f"as-of date        : {effective}")
    print(f"approved ATR band : {cfg.min_atr_fraction * 100:.0f}% .. "
          f"{cfg.max_atr_fraction * 100:.0f}% of price")
    print(f"ladder levels     : -5% / -8% / -10% (fixed, D-0004)")
    print()

    provider = AlpacaAssetsProvider(
        key_id=key, secret_key=sec, base_url=base,
        symbol_whitelist=whitelist or None,
    )
    enricher = AlpacaFeatureEnricher(key_id=key, secret_key=sec)

    print("[1/3] fetching tradable symbols from the broker ...", flush=True)
    raw = provider.get_raw_candidates(effective)
    excluded = provider.last_excluded
    print(f"      {len(raw)} tradable symbols")
    print(f"      {len(excluded)} excluded as leveraged/inverse (D-0056)")
    for reason in excluded[:5]:
        print(f"        - {reason}")
    if len(excluded) > 5:
        print(f"        ... and {len(excluded) - 5} more")

    if args.max_symbols and args.max_symbols > 0 and len(raw) > args.max_symbols:
        print(f"      capping to the first {args.max_symbols} per --max-symbols")
        raw = raw[: args.max_symbols]

    regime = RegimeState(
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        as_of_date=effective,
        reference_series_values=(),
        classification_method_version="measure-tool-no-regime",
    )

    print(f"[2/3] fetching daily bars for {len(raw)} symbols "
          f"(one request each) ...", flush=True)

    measured: list = []
    no_data = 0
    for index, ref in enumerate(raw, start=1):
        if args.progress_every and index % args.progress_every == 0:
            print(f"      {index}/{len(raw)} ...", flush=True)
        identity = SecurityIdentity(
            security_id=f"alpaca-{ref.ticker}", display_name=ref.ticker,
            cik=None, confidence=IdentityConfidence.PROVISIONAL,
        )
        resolution = IdentityResolution(
            outcome=ResolutionOutcome.RESOLVED, ticker=ref.ticker,
            as_of_date=ref.as_of_date, identity=identity,
            alias=TickerAlias(security_id=identity.security_id,
                              ticker=ref.ticker, effective_start=None,
                              effective_end=None),
        )
        candidate = UniverseCandidate(
            raw=ref, identity_resolution=resolution, bar=None, features=None,
        )
        try:
            enriched = enricher(candidate, effective, regime)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the run
            print(f"      [warn] {ref.ticker}: {type(exc).__name__}: {exc}")
            no_data += 1
            continue

        if enriched.features is None or enriched.bar is None:
            no_data += 1
            continue
        atr = enriched.features.atr_measure
        close = enriched.bar.close
        if atr is None or close is None or close <= 0:
            no_data += 1
            continue
        # EXACTLY Stage D's formula -- see strategy_fit.py.
        measured.append((ref.ticker, atr / close, close))

    print(f"      {len(measured)} measured, {no_data} without usable data")
    print()

    if not measured:
        print("No symbols produced a usable ATR. Nothing to report.")
        return 1

    print("[3/3] results")
    print()
    total = len(measured)

    print("DISTRIBUTION BY SPEED")
    print("-" * 68)
    print(f"{'band':>14}  {'count':>6}  {'share':>7}   meaning")
    print("-" * 68)
    counts = {}
    for _t, frac, _c in measured:
        counts[_bucket_for(frac)] = counts.get(_bucket_for(frac), 0) + 1
    for bucket in _BUCKETS:
        n = counts.get(bucket, 0)
        low, high, label = bucket
        hi = "inf" if high > 9 else f"{high * 100:.0f}%"
        print(f"{low * 100:>6.0f}% .. {hi:>5}  {n:>6}  "
              f"{(n / total * 100):>6.1f}%   {label}")
    print("-" * 68)
    print(f"{'TOTAL':>14}  {total:>6}")
    print()

    in_band = [m for m in measured
               if cfg.min_atr_fraction <= m[1] < cfg.max_atr_fraction]
    print("THE DECISION — how many candidates survive each choice")
    print("-" * 68)
    print(f"{'option':<22} {'surviving':>10} {'share of all':>14}")
    print("-" * 68)
    print(f"{'keep 1%-5% (today)':<22} {len(in_band):>10} "
          f"{(len(in_band) / total * 100):>13.1f}%")
    for lo, hi in ((0.015, 0.045), (0.020, 0.040), (0.025, 0.035)):
        survivors = [m for m in measured if lo <= m[1] < hi]
        label = f"narrow to {lo * 100:.1f}%-{hi * 100:.1f}%"
        print(f"{label:<22} {len(survivors):>10} "
              f"{(len(survivors) / total * 100):>13.1f}%")
    print("-" * 68)
    print("A narrowing that leaves too few candidates trades quality for")
    print("starvation. Stage A/C/F/G/H still cut further AFTER this.")
    print()

    measured.sort(key=lambda m: m[1])
    print("LADDER PACE AT THE EXTREMES OF WHAT IS CURRENTLY ALLOWED")
    print("-" * 68)
    print(f"{'symbol':<8} {'ATR':>7} {'price':>9}   "
          f"{'days to -5%':>11} {'to -8%':>8} {'to -10%':>8}")
    print("-" * 68)
    sample = [m for m in measured
              if cfg.min_atr_fraction <= m[1] < cfg.max_atr_fraction]
    for ticker, frac, close in (sample[:3] + sample[-3:] if len(sample) >= 6
                                else sample):
        print(f"{ticker:<8} {frac * 100:>6.2f}% {close:>9.2f}   "
              f"{_days_to(5, frac):>11} {_days_to(8, frac):>8} "
              f"{_days_to(10, frac):>8}")
    print("-" * 68)
    print()
    print("Nothing was written. No snapshot, no database, no Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
