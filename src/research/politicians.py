"""Politician whitelist with alpha weights + committee memberships
(D-0050 Phase B.19).

The 15 politicians below are tracked across every political-data
source (CapitolTrades, QuiverQuant, Senate/House eDisclosure PDFs).
Their alpha-weight is the multiplier applied to their trades in the
clustering + scoring pipeline. Committee memberships drive the
"committee-relevance" boost — a sector trade by a committee member
with jurisdiction over that sector is weighted higher than the same
trade by an unrelated politician.

Weights are documented reflections of publicly-reported disclosure
outcomes (not forward-looking). The Controller tunes them as new
data arrives; nothing here is a hard-coded truth about any
individual.

All data advisory per CLAUDE.md §5. The engine never auto-executes
on a political signal — it only adds candidate symbols to the
universe, which then flow through the full ranker + evaluator +
portfolio + macro pipeline before any proposal is created.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PoliticianProfile:
    name: str
    chamber: str               # "House" or "Senate"
    party: str                 # "D", "R", or "I"
    committees: Tuple[str, ...]
    alpha_weight: float        # multiplier for their trades in scoring
    notes: str = ""


# Canonical committee codes used for sector-relevance mapping.
# Keep these STABLE — the committee mapper (B.23) joins against them.
COMMITTEE_ARMED_SERVICES_SENATE = "Senate Armed Services"
COMMITTEE_ARMED_SERVICES_HOUSE  = "House Armed Services"
COMMITTEE_BANKING_SENATE        = "Senate Banking"
COMMITTEE_FINANCIAL_SERVICES    = "House Financial Services"
COMMITTEE_ENERGY_SENATE         = "Senate Energy & Natural Resources"
COMMITTEE_ENERGY_HOUSE          = "House Energy & Commerce"
COMMITTEE_HEALTH_HELP           = "Senate HELP"  # Health, Education, Labor, Pensions
COMMITTEE_HEALTH_HOUSE          = "House Energy & Commerce (Health)"
COMMITTEE_INTELLIGENCE_HOUSE    = "House Intelligence"
COMMITTEE_INTELLIGENCE_SENATE   = "Senate Intelligence"
COMMITTEE_FOREIGN_AFFAIRS       = "House Foreign Affairs"
COMMITTEE_FOREIGN_RELATIONS     = "Senate Foreign Relations"
COMMITTEE_SCIENCE_HOUSE         = "House Science, Space, Technology"
COMMITTEE_COMMERCE_SENATE       = "Senate Commerce, Science, Transportation"
COMMITTEE_JUDICIARY_SENATE      = "Senate Judiciary"
COMMITTEE_WAYS_AND_MEANS        = "House Ways and Means"
COMMITTEE_FINANCE_SENATE        = "Senate Finance"
COMMITTEE_APPROPRIATIONS_HOUSE  = "House Appropriations"
COMMITTEE_APPROPRIATIONS_SENATE = "Senate Appropriations"


# The whitelist. alpha_weight is normalized so 1.0 = SPY-equivalent
# alpha. Weights above 1.0 mean the politician has historically
# outperformed; the number itself is a prior, not a hard forecast.
WHITELIST: Tuple[PoliticianProfile, ...] = (
    PoliticianProfile(
        name="Nancy Pelosi", chamber="House", party="D",
        committees=(COMMITTEE_INTELLIGENCE_HOUSE,),
        alpha_weight=1.5,
        notes="Top-performing public tracker (+31% 2023, +65% 2024). "
              "Spouse Paul Pelosi makes many of the trades.",
    ),
    PoliticianProfile(
        name="Dan Crenshaw", chamber="House", party="R",
        committees=(COMMITTEE_ARMED_SERVICES_HOUSE,),
        alpha_weight=1.4,
        notes="Defense sector specialist; +74% 2024.",
    ),
    PoliticianProfile(
        name="Josh Gottheimer", chamber="House", party="D",
        committees=(COMMITTEE_FINANCIAL_SERVICES,),
        alpha_weight=1.2,
        notes="Financials focus; +23% average 2022-2024.",
    ),
    PoliticianProfile(
        name="Michael McCaul", chamber="House", party="R",
        committees=(COMMITTEE_FOREIGN_AFFAIRS,),
        alpha_weight=1.2,
        notes="Foreign-policy insight, heavy semis exposure.",
    ),
    PoliticianProfile(
        name="Ro Khanna", chamber="House", party="D",
        committees=(COMMITTEE_ARMED_SERVICES_HOUSE,
                    COMMITTEE_SCIENCE_HOUSE),
        alpha_weight=1.1,
        notes="Tech & Science cross-over; Silicon Valley district.",
    ),
    PoliticianProfile(
        name="Mark Green", chamber="House", party="R",
        committees=(COMMITTEE_ARMED_SERVICES_HOUSE,),
        alpha_weight=1.1,
        notes="Defense + healthcare dual exposure.",
    ),
    PoliticianProfile(
        name="Markwayne Mullin", chamber="Senate", party="R",
        committees=(COMMITTEE_ARMED_SERVICES_SENATE,
                    COMMITTEE_COMMERCE_SENATE),
        alpha_weight=1.1,
        notes="Diversified; strong 2024 record.",
    ),
    PoliticianProfile(
        name="Dan Sullivan", chamber="Senate", party="R",
        committees=(COMMITTEE_ARMED_SERVICES_SENATE,
                    COMMITTEE_COMMERCE_SENATE,
                    COMMITTEE_ENERGY_SENATE),
        alpha_weight=1.2,
        notes="Energy sector insider.",
    ),
    PoliticianProfile(
        name="Tommy Tuberville", chamber="Senate", party="R",
        committees=(COMMITTEE_ARMED_SERVICES_SENATE,
                    COMMITTEE_HEALTH_HELP),
        alpha_weight=1.1,
        notes="Mixed book; biotech + defense.",
    ),
    PoliticianProfile(
        name="Mitch McConnell", chamber="Senate", party="R",
        committees=(COMMITTEE_APPROPRIATIONS_SENATE,),
        alpha_weight=1.0,
        notes="Broad book, slower cadence.",
    ),
    PoliticianProfile(
        name="Chuck Schumer", chamber="Senate", party="D",
        committees=(COMMITTEE_FINANCE_SENATE,
                    COMMITTEE_JUDICIARY_SENATE),
        alpha_weight=1.0,
        notes="Majority Leader; conservative book.",
    ),
    PoliticianProfile(
        name="Pat Fallon", chamber="House", party="R",
        committees=(COMMITTEE_ARMED_SERVICES_HOUSE,),
        alpha_weight=1.1,
        notes="Active trader, defense focus.",
    ),
    PoliticianProfile(
        name="Shelley Moore Capito", chamber="Senate", party="R",
        committees=(COMMITTEE_APPROPRIATIONS_SENATE,
                    COMMITTEE_COMMERCE_SENATE),
        alpha_weight=1.0,
        notes="Energy state, utilities exposure.",
    ),
    PoliticianProfile(
        name="John Boozman", chamber="Senate", party="R",
        committees=(COMMITTEE_APPROPRIATIONS_SENATE,),
        alpha_weight=1.0,
        notes="Agribusiness focus (COGS inputs).",
    ),
    PoliticianProfile(
        name="Debbie Wasserman Schultz", chamber="House", party="D",
        committees=(COMMITTEE_APPROPRIATIONS_HOUSE,),
        alpha_weight=1.0,
        notes="Mid-cap focus; healthcare + consumer.",
    ),
)


# Fast lookup by lowercase, name-normalized key.
_BY_NAME: Dict[str, PoliticianProfile] = {}


def _normalize_name(name: str) -> str:
    """Case + punctuation-insensitive key for matching feed-provided
    representatives names (which vary: 'Pelosi, Nancy' vs 'Nancy Pelosi')."""
    return "".join(c for c in name.lower() if c.isalnum())


def _build_name_index() -> None:
    global _BY_NAME
    _BY_NAME = {_normalize_name(p.name): p for p in WHITELIST}


_build_name_index()


def lookup(name: str) -> Optional[PoliticianProfile]:
    """Returns the whitelist profile matching this politician name, or
    None if not tracked. Handles 'Last, First', 'First Last', case,
    punctuation, and extra whitespace."""
    if not name or not isinstance(name, str):
        return None
    key = _normalize_name(name)
    hit = _BY_NAME.get(key)
    if hit is not None:
        return hit
    # Try the "Last, First" permutation.
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2:
            swapped = f"{parts[1]} {parts[0]}"
            return _BY_NAME.get(_normalize_name(swapped))
    return None


def is_whitelisted(name: str) -> bool:
    return lookup(name) is not None


def all_profiles() -> List[PoliticianProfile]:
    return list(WHITELIST)
