"""Sector-data source (B30).

Feeds the D-0026 Concentration stage (G) with a `sector=<name>`
fragment on each candidate's `features.source_reference`. Without a
provider, that stage treats every candidate as sector="unknown" and
passes through -- documented and safe (fail-open on missing data),
but it means the sector-cap rule (D-0048 max_sector_fraction) is
never actually enforced.

Contract:
  SectorProvider.sector_of(ticker) -> Optional[str]
      Returns a normalized sector identifier (lower_snake_case),
      or None if the ticker is unknown to this provider. `None` is
      passed through unchanged so stage G's fail-open behavior is
      preserved for unlisted symbols.

Concrete `StaticSectorProvider`:
  In-memory ticker -> sector mapping backed by a JSON file at
  `data/sectors.json`. Publicly available reference data (GICS-
  simplified 11-sector taxonomy). Not survivorship-adjusted;
  historical sector changes (spin-offs, restructures) are NOT
  tracked -- if the backtest reaches a date where a ticker's sector
  differed from today's mapping, this file will report today's
  value. That is a known limitation, documented here rather than
  silently papered over.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol


class SectorProvider(Protocol):
    def sector_of(self, ticker: str) -> Optional[str]: ...


@dataclass(frozen=True)
class StaticSectorProvider:
    """Immutable in-memory sector mapping.

    Tickers are normalized to upper-case for lookup so the caller
    does not need to worry about "aapl" vs "AAPL"."""

    mapping: Mapping[str, str]

    @classmethod
    def from_json_file(cls, path: str) -> "StaticSectorProvider":
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        sectors = payload.get("sectors")
        if not isinstance(sectors, dict):
            raise ValueError(
                f"sector data file {path!r} missing 'sectors' object"
            )
        # Normalize keys to upper-case; reject non-string values.
        normalized = {}
        for k, v in sectors.items():
            if not isinstance(v, str) or not v:
                raise ValueError(
                    f"sector value for {k!r} must be a non-empty "
                    f"string, got {v!r}"
                )
            normalized[str(k).upper()] = v
        return cls(mapping=dict(normalized))

    def sector_of(self, ticker: str) -> Optional[str]:
        return self.mapping.get(ticker.upper())


def default_sectors_path() -> str:
    """Path to the shipped sectors.json (repo-root/data/sectors.json)."""

    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(
        os.path.join(here, "..", "..", "data", "sectors.json")
    )


def load_default_sector_provider() -> StaticSectorProvider:
    """Loads the shipped default mapping. Raises if the file is
    missing -- explicit failure is better than silently returning
    an empty provider that leaves stage G a no-op."""

    return StaticSectorProvider.from_json_file(default_sectors_path())


def sector_fragment(sector: Optional[str]) -> str:
    """Formats a sector as a `source_reference` fragment. Returns
    empty string when sector is None so callers can concatenate
    unconditionally."""

    if sector is None or not sector:
        return ""
    return f"sector={sector}"
