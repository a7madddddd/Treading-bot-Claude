"""Instrument eligibility -- excludes leveraged and inverse products
from the Universe before any pipeline stage sees them (P-021,
Controller-approved 2026-10-05).

WHY THIS EXISTS
---------------
`AlpacaAssetsProvider` requests `asset_class=us_equity`. Alpaca files
ETFs, leveraged ETFs and inverse ETFs under that same asset class --
there is no separate class for them -- so the Universe happily selected
`DXD` (ProShares UltraShort Dow30, a -2x LEVERAGED INVERSE fund) into
the live snapshot of 2026-10-03.

That is incompatible with the approved ladder (D-0004), which BUYS MORE
as price falls:

  - An inverse fund falls when the market RISES. So during an ordinary
    rally the engine would average down into a leveraged bet against
    that rally.
  - A -2x fund moves twice the index, so a 5% index gain walks the
    position through Ladder 1 (-5%), Ladder 2 (-8%) and the Floor
    (-10%) within days -- collapsing three separate decision points
    into one.
  - Daily-rebalanced leveraged funds decay over multi-day holds even
    when the index ends flat, so time works against the position on
    top of direction.

SCOPE -- deliberately narrow
----------------------------
This module excludes ONLY leveraged and inverse products. It does NOT
decide whether ordinary (non-leveraged, non-inverse) funds belong in
the Universe -- that is a separate, deliberately deferred Controller
decision tracked as P-024. `MAGS`, `QQQI`, `ILF`, `CGGR` and `BCI`
therefore still pass this filter.

DETECTION
---------
Alpaca's `/v2/assets` payload carries the asset's `name`, and leveraged
and inverse funds are named with near-universal conventions. Detection
is therefore name-based, in three independent rules:

  1. An explicit multiplier token: `2X`, `3X`, `-1X`, `1.5X`, ...
  2. An explicit leverage/inverse word: ULTRA, ULTRASHORT, ULTRAPRO,
     LEVERAGED, INVERSE, BEAR.
  3. The word SHORT used in the "inverse fund" sense.

Rule 3 carries the only real false-positive risk, because SHORT also
names a DURATION in ordinary bond funds -- `iShares Short Treasury Bond
ETF` and `Vanguard Short-Term Bond ETF` are not inverse anything. Those
are excluded from rule 3 by an explicit duration-context list, and the
behavior is pinned by tests.

This module is pure: no I/O, no clock, no configuration, no global
state. It is the single place this policy is expressed, so the rule can
be audited and changed in one edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

ELIGIBILITY_POLICY_VERSION = "D0026-INSTR-ELIG-001"
"""Identifies this exact rule set. Any change to the rules below is a
NEW version string recorded as its own dated decisions.md entry --
never a silent edit in place. Mirrors the convention used by
FEATURE_DEFINITION_VERSION in evidence.py."""


_MULTIPLIER_RE = re.compile(r"(?<![A-Z0-9])-?\d+(?:\.\d+)?X(?![A-Z0-9])")
"""Matches a standalone leverage multiplier token: 2X, 3X, -1X, 1.5X.
The lookarounds stop it matching inside an unrelated word or ticker
fragment (e.g. the 'X' in 'XLK' or a name containing 'MAX')."""


_LEVERAGE_WORDS: Tuple[str, ...] = (
    "ULTRASHORT",
    "ULTRAPRO",
    "ULTRA",
    "LEVERAGED",
    "INVERSE",
    "BEAR",
)
"""Words that identify a leveraged or inverse product on their own.

ULTRA is included deliberately and is not a guess: in fund naming
'ProShares Ultra <index>' is the house name for a +2x fund and
'ProShares UltraShort <index>' for a -2x fund. ULTRASHORT and ULTRAPRO
are listed before ULTRA only so the reported reason names the most
specific match.

BULL is deliberately NOT here. 'Direxion Daily ... Bull 3X Shares' is
already caught by the multiplier rule, while 'bull' alone appears in
ordinary fund names and would produce false positives."""


_SHORT_DURATION_CONTEXT: Tuple[str, ...] = (
    "SHORT-TERM",
    "SHORT TERM",
    "SHORT DURATION",
    "SHORT-DURATION",
    "SHORT MATURITY",
    "SHORT-MATURITY",
    "SHORT TREASURY",
    "SHORT-TREASURY",
    "SHORT BOND",
    "SHORT GOVERNMENT",
)
"""In these phrases SHORT names a maturity, not a direction. An
ordinary short-duration bond fund is not an inverse product, so it must
not be excluded by this module -- whether such a fund belongs in the
Universe at all is P-024's question, not this one's."""


_SHORT_RE = re.compile(r"(?<![A-Z])SHORT(?![A-Z])")


@dataclass(frozen=True)
class EligibilityVerdict:
    """The outcome for one instrument. `reason` is non-None exactly when
    `eligible` is False, and names the rule that fired so a rejection is
    explainable after the fact rather than just a count."""

    eligible: bool
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.eligible and self.reason is not None:
            raise ValueError("an eligible verdict must not carry a reason")
        if not self.eligible and not self.reason:
            raise ValueError("an ineligible verdict must carry a reason")


def classify_instrument(
    *, symbol: str, name: Optional[str],
) -> EligibilityVerdict:
    """Decides whether one instrument may enter the Universe.

    `name` is the asset's human-readable name as the broker reports it
    (Alpaca's `name` field). A missing or empty name returns ELIGIBLE:
    this module never rejects on absent data, because "we could not
    tell" is not evidence of leverage, and Stage B's own data-quality
    handling owns missing-data decisions. The practical consequence is
    deliberate and worth stating plainly -- if the broker ever stops
    sending names, this filter silently stops protecting, so the caller
    is expected to surface the exclusion count (see
    `AlpacaAssetsProvider.last_excluded_count`) rather than assume it
    worked.

    `symbol` is accepted for the reason string only. It is NOT used for
    detection: ticker-based blocklists go stale the moment a fund is
    renamed or a ticker is reused, which is exactly the identity trap
    the D-0026 subsystem exists to avoid.
    """

    if not name:
        return EligibilityVerdict(eligible=True)

    upper = name.upper()

    match = _MULTIPLIER_RE.search(upper)
    if match:
        return EligibilityVerdict(
            eligible=False,
            reason=(f"leveraged: name carries multiplier "
                    f"{match.group(0)!r} ({symbol})"),
        )

    for word in _LEVERAGE_WORDS:
        if word in upper:
            return EligibilityVerdict(
                eligible=False,
                reason=f"leveraged/inverse: name contains {word!r} ({symbol})",
            )

    if _SHORT_RE.search(upper):
        if not any(ctx in upper for ctx in _SHORT_DURATION_CONTEXT):
            return EligibilityVerdict(
                eligible=False,
                reason=(f"inverse: name contains 'SHORT' outside a "
                        f"duration context ({symbol})"),
            )

    return EligibilityVerdict(eligible=True)


def is_eligible(*, symbol: str, name: Optional[str]) -> bool:
    """Convenience boolean for callers that do not need the reason."""

    return classify_instrument(symbol=symbol, name=name).eligible
