"""Committee → sector/ticker relevance mapper (D-0050 Phase B.23).

Determines whether a politician's trade falls within their
committee's jurisdiction. A trade with committee relevance = HIGH is
weighted higher than an unrelated trade; this is the signal that
separates "insider knowledge" from "random portfolio move".

Example: Dan Sullivan sits on Senate Energy & Natural Resources.
If he buys XOM → committee-match (HIGH relevance).
If he buys TSLA → no committee-match (LOW relevance).

All mappings here are PUBLIC domain knowledge about committee
jurisdictions. Nothing proprietary.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional

from research.politicians import (
    COMMITTEE_ARMED_SERVICES_HOUSE, COMMITTEE_ARMED_SERVICES_SENATE,
    COMMITTEE_BANKING_SENATE, COMMITTEE_FINANCIAL_SERVICES,
    COMMITTEE_ENERGY_HOUSE, COMMITTEE_ENERGY_SENATE,
    COMMITTEE_HEALTH_HELP, COMMITTEE_HEALTH_HOUSE,
    COMMITTEE_INTELLIGENCE_HOUSE, COMMITTEE_INTELLIGENCE_SENATE,
    COMMITTEE_FOREIGN_AFFAIRS, COMMITTEE_FOREIGN_RELATIONS,
    COMMITTEE_SCIENCE_HOUSE, COMMITTEE_COMMERCE_SENATE,
    COMMITTEE_JUDICIARY_SENATE, COMMITTEE_WAYS_AND_MEANS,
    COMMITTEE_FINANCE_SENATE, COMMITTEE_APPROPRIATIONS_HOUSE,
    COMMITTEE_APPROPRIATIONS_SENATE,
    PoliticianProfile, lookup,
)


# Committee → set of SIC description keywords indicating a relevant
# industry. Match is case-insensitive substring.
_COMMITTEE_SIC_KEYWORDS: Dict[str, FrozenSet[str]] = {
    COMMITTEE_ARMED_SERVICES_HOUSE: frozenset({
        "aircraft", "guided missiles", "defense", "ordnance", "military",
        "ship building", "communications equipment",
    }),
    COMMITTEE_ARMED_SERVICES_SENATE: frozenset({
        "aircraft", "guided missiles", "defense", "ordnance", "military",
        "ship building", "communications equipment",
    }),
    COMMITTEE_FINANCIAL_SERVICES: frozenset({
        "bank", "insurance", "security", "credit", "investment",
        "financial services", "real estate",
    }),
    COMMITTEE_BANKING_SENATE: frozenset({
        "bank", "insurance", "security", "credit", "investment",
        "financial services", "real estate",
    }),
    COMMITTEE_FINANCE_SENATE: frozenset({
        "bank", "insurance", "pharmaceutical", "medical",
        "financial", "credit",
    }),
    COMMITTEE_ENERGY_HOUSE: frozenset({
        "petroleum", "oil", "gas", "coal", "electric", "pipeline",
        "renewable", "nuclear",
    }),
    COMMITTEE_ENERGY_SENATE: frozenset({
        "petroleum", "oil", "gas", "coal", "electric", "pipeline",
        "renewable", "nuclear", "mining",
    }),
    COMMITTEE_HEALTH_HELP: frozenset({
        "pharmaceutical", "medical", "hospital", "biological products",
        "surgical", "health",
    }),
    COMMITTEE_HEALTH_HOUSE: frozenset({
        "pharmaceutical", "medical", "hospital", "biological products",
        "surgical", "health",
    }),
    COMMITTEE_INTELLIGENCE_HOUSE: frozenset({
        "semiconductor", "communications equipment", "computer",
        "software", "cyber", "defense",
    }),
    COMMITTEE_INTELLIGENCE_SENATE: frozenset({
        "semiconductor", "communications equipment", "computer",
        "software", "cyber", "defense",
    }),
    COMMITTEE_SCIENCE_HOUSE: frozenset({
        "semiconductor", "computer", "software", "research",
        "scientific", "aerospace",
    }),
    COMMITTEE_COMMERCE_SENATE: frozenset({
        "communications", "transport", "retail", "telecom",
        "broadcast",
    }),
    COMMITTEE_FOREIGN_AFFAIRS: frozenset({
        "aircraft", "defense", "mining", "petroleum", "pharmaceutical",
    }),
    COMMITTEE_FOREIGN_RELATIONS: frozenset({
        "aircraft", "defense", "mining", "petroleum", "pharmaceutical",
    }),
    COMMITTEE_WAYS_AND_MEANS: frozenset({
        "pharmaceutical", "insurance", "retail",
    }),
    COMMITTEE_APPROPRIATIONS_HOUSE: frozenset(),
    COMMITTEE_APPROPRIATIONS_SENATE: frozenset(),
    COMMITTEE_JUDICIARY_SENATE: frozenset({
        "software", "computer", "pharmaceutical",
    }),
}


def committee_matches_sector(
    politician_name: str,
    sic_description: Optional[str],
) -> bool:
    """True iff ANY committee the politician sits on has a keyword
    found in the stock's sic_description. False on missing data."""
    if not isinstance(sic_description, str) or not sic_description.strip():
        return False
    prof = lookup(politician_name)
    if prof is None:
        return False
    hay = sic_description.lower()
    for cmte in prof.committees:
        kws = _COMMITTEE_SIC_KEYWORDS.get(cmte)
        if not kws:
            continue
        for kw in kws:
            if kw in hay:
                return True
    return False


def committee_match_count(
    trades_by_politician: Dict[str, str],
    sic_description: Optional[str],
) -> int:
    """How many of the politicians who traded this stock have a
    committee-match? ``trades_by_politician`` is {name: action}
    where action is "BUY" or "SELL"; only BUYs are counted here —
    SELLs are tallied separately by the clustering engine."""
    count = 0
    for name, action in trades_by_politician.items():
        if (action or "").upper() == "BUY" and committee_matches_sector(
                name, sic_description):
            count += 1
    return count
