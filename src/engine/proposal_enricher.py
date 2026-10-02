"""Optional Perplexity-driven enrichment for proposal notifications
(D-0050 Phase 2).

Advisory only, per CLAUDE.md §5. The enricher is injected into Engine
and queried once per outgoing proposal notification. On ANY failure
(no client, Perplexity down, timeout, malformed response, exception),
the enricher returns None and the Engine sends the plain proposal
message unchanged.

Never influences trading state. Never gates approval. The output is
a short text blurb (≤ 400 chars) appended to the Telegram message
under a separator so the Controller can see current market context
alongside the proposal.
"""

from __future__ import annotations

from typing import Optional, Protocol


class _ClientLike(Protocol):
    def research_stock(self, ticker: str, question: str): ...


class ProposalEnricher:
    """Wraps a Perplexity client with a strict fail-open contract.

    - never raises
    - never blocks longer than the client's own timeout
    - returns a short, bullet-trimmed string or None
    """

    _QUESTION = (
        "In 3 short bullets, summarize today's most important news or "
        "catalysts for this stock that a swing trader placing a new "
        "order right now would want to know. Keep each bullet under "
        "20 words. If nothing material is happening, say so plainly."
    )
    _MAX_CHARS = 400

    def __init__(self, client: Optional[_ClientLike]) -> None:
        self._client = client

    def enrich(self, symbol: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            report = self._client.research_stock(symbol, self._QUESTION)
        except Exception:  # noqa: BLE001 -- advisory, never blocks a trade
            return None

        text = _extract_text(report)
        if not text:
            return None
        text = text.strip()
        if not text:
            return None
        if len(text) > self._MAX_CHARS:
            text = text[: self._MAX_CHARS - 1].rstrip() + "…"
        return text


def _extract_text(report) -> Optional[str]:
    """Pulls the human-readable summary out of a ResearchReport.

    Perplexity reports carry a ``findings`` tuple; each Finding has a
    ``summary`` field. We join them (one per line) and return. If the
    report shape is unexpected, we return None rather than crash.
    """
    try:
        findings = getattr(report, "findings", None)
        if not findings:
            # Some responses may just have a top-level `summary` field.
            summary = getattr(report, "text", None) or getattr(report, "summary", None)
            if isinstance(summary, str) and summary.strip():
                return summary
            return None
        lines = []
        for f in findings:
            s = getattr(f, "text", None) or getattr(f, "summary", None)
            if isinstance(s, str) and s.strip():
                lines.append("• " + s.strip())
        if not lines:
            return None
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return None


def append_enrichment(message: str, enrichment: Optional[str]) -> str:
    """Pure helper: appends the enrichment block below a blank-line
    separator. The enrichment is expected to supply its own header
    (CompositeEnricher does -- "— Research (advisory):"). Returns the
    message unchanged if enrichment is None/empty."""
    if not enrichment:
        return message
    return message + "\n\n" + enrichment
