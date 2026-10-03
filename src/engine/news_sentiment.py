"""News sentiment polarity scoring (D-0050 Phase 17).

Phase B.9's DeepResearchComposer already pulls Perplexity catalysts
and risks and surfaces them in Telegram. Phase 17 converts those
qualitative blurbs into a single numeric polarity in [-1.0, +1.0]
that the TradeEvaluator can weight directly, instead of merely
COUNTING catalysts.

Rule (deterministic, fail-open):

    catalysts = len(perplexity_catalysts)
    risks     = len(perplexity_risks)
    polarity  = (catalysts - risks) / max(1, catalysts + risks)

That gives:
   +1.0  all signals positive
    0.0  balanced
   -1.0  all signals negative

If BOTH catalysts and risks are empty, polarity is None. Downstream
scorers treat None as "no information available" rather than
"neutral", so an outage-induced empty response does not fake a
neutral verdict.

The function is intentionally rule-based (not an LLM call) so it is
deterministic, free, and testable. The qualitative LLM analysis
already lives upstream in the Perplexity fetches.
"""

from __future__ import annotations

from typing import Optional

from engine.research_hub import SymbolResearch


def compute_news_polarity(research: SymbolResearch) -> Optional[float]:
    """Returns polarity in [-1, +1] or None if no Perplexity data."""
    cats = len(research.perplexity_catalysts or [])
    risks = len(research.perplexity_risks or [])
    total = cats + risks
    if total == 0:
        return None
    return (cats - risks) / total


def polarity_label(polarity: Optional[float]) -> str:
    """Short human-readable label for a polarity value."""
    if polarity is None:
        return "unknown"
    if polarity >= 0.5:
        return "strongly-positive"
    if polarity >= 0.15:
        return "positive"
    if polarity > -0.15:
        return "neutral"
    if polarity > -0.5:
        return "negative"
    return "strongly-negative"
