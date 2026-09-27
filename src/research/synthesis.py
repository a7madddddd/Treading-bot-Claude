"""Research synthesis (D-0019, D-0046).

Per `docs/architecture/research-sources.md §3`, synthesis never
blindly merges outputs. It keeps each source separate, computes:

  - overlap: statements both sources agree on
  - contradictions: divergences
  - missing: questions neither answered
  - hypothesis: a labeled HYPOTHESIS a human can review
  - confidence: LOW/MEDIUM/HIGH with justification
  - suggested_experiment: a concrete next step

No LLM in this file. Pure Python — deterministic, testable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from research.models import (
    CapitolTradesRecord,
    ComparisonRecord,
    Confidence,
    ResearchReport,
)


def synthesize(
    *,
    ticker: str,
    perplexity_reports: Sequence[ResearchReport],
    capitol_trades_records: Sequence[CapitolTradesRecord],
) -> ComparisonRecord:
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")

    ct_relevant = tuple(
        r for r in capitol_trades_records
        if (r.ticker or "").upper() == ticker
    )
    pp_relevant = tuple(
        r for r in perplexity_reports
        if (r.subject_symbol or "").upper() == ticker
        or ticker in (r.question or "").upper()
    )

    overlap = _find_overlap(pp_relevant, ct_relevant)
    contradictions = _find_contradictions(pp_relevant, ct_relevant)
    missing = _find_missing(pp_relevant, ct_relevant)
    hypothesis = _build_hypothesis(ticker, pp_relevant, ct_relevant,
                                   overlap, contradictions)
    confidence = _confidence_of(pp_relevant, ct_relevant, overlap,
                                contradictions)
    experiment = _suggest_experiment(ticker, contradictions, missing)

    return ComparisonRecord(
        comparison_id=f"cmp-{uuid.uuid4().hex[:12]}",
        ticker=ticker,
        capitol_trades_records=ct_relevant,
        perplexity_reports=pp_relevant,
        overlap=overlap,
        contradictions=contradictions,
        missing=missing,
        hypothesis=hypothesis,
        confidence=confidence,
        suggested_experiment=experiment,
        synthesized_at=datetime.now(timezone.utc),
    )


# ---- helpers -----------------------------------------------------------

def _tokens(text: str) -> Set[str]:
    return {t.strip(".,;:()[]").lower() for t in (text or "").split()
            if len(t) > 3}


def _find_overlap(
    pp: Sequence[ResearchReport],
    ct: Sequence[CapitolTradesRecord],
) -> Tuple[str, ...]:
    if not pp or not ct:
        return ()
    ct_dirs = {r.transaction_type.lower() for r in ct}
    hits: List[str] = []
    for report in pp:
        summary_tokens = _tokens(report.summary)
        # Directional overlap: PP summary mentions the transaction type
        # CT actually reports.
        for direction in ct_dirs:
            if direction in summary_tokens and direction not in ("unknown",):
                hits.append(
                    f"perplexity {report.operation.value} summary mentions "
                    f"'{direction}', matching CapitolTrades report"
                )
                break
    return tuple(dict.fromkeys(hits))


def _find_contradictions(
    pp: Sequence[ResearchReport],
    ct: Sequence[CapitolTradesRecord],
) -> Tuple[str, ...]:
    if not pp or not ct:
        return ()
    contradictions: List[str] = []
    for report in pp:
        rec = (report.recommendation or "").lower()
        if not rec:
            continue
        for r in ct:
            action = r.transaction_type.lower()
            if action in ("buy", "purchase") and "sell" in rec:
                contradictions.append(
                    f"PP recommends sell for {r.ticker}, but "
                    f"{r.politician} disclosed a {action}"
                )
            elif action in ("sell", "sale") and "buy" in rec:
                contradictions.append(
                    f"PP recommends buy for {r.ticker}, but "
                    f"{r.politician} disclosed a {action}"
                )
    return tuple(dict.fromkeys(contradictions))


def _find_missing(
    pp: Sequence[ResearchReport],
    ct: Sequence[CapitolTradesRecord],
) -> Tuple[str, ...]:
    missing: List[str] = []
    if not pp:
        missing.append("no Perplexity report available for this ticker")
    if not ct:
        missing.append("no CapitolTrades disclosure available for this ticker")
    if pp and not any(r.risks for r in pp):
        missing.append("Perplexity produced no explicit RISKS section")
    if pp and not any(r.sources for r in pp):
        missing.append("Perplexity produced no citation sources")
    return tuple(missing)


def _build_hypothesis(
    ticker: str,
    pp: Sequence[ResearchReport],
    ct: Sequence[CapitolTradesRecord],
    overlap: Tuple[str, ...],
    contradictions: Tuple[str, ...],
) -> Optional[str]:
    if not pp and not ct:
        return None
    if contradictions:
        return (f"HYPOTHESIS: signal conflict for {ticker} — divergence "
                f"between narrative research and disclosed insider trades.")
    if overlap:
        return (f"HYPOTHESIS: research narrative for {ticker} is consistent "
                f"with disclosed insider trades ({len(overlap)} overlap).")
    if pp and not ct:
        return (f"HYPOTHESIS: {ticker} has narrative coverage but no "
                f"disclosed insider trade in current CapitolTrades window.")
    if ct and not pp:
        return (f"HYPOTHESIS: {ticker} has disclosed insider trades but no "
                f"Perplexity narrative — merits an explicit research query.")
    return None


def _confidence_of(
    pp: Sequence[ResearchReport],
    ct: Sequence[CapitolTradesRecord],
    overlap: Tuple[str, ...],
    contradictions: Tuple[str, ...],
) -> Confidence:
    if not pp and not ct:
        return Confidence.LOW
    if contradictions:
        return Confidence.LOW
    if overlap and pp and ct:
        return Confidence.HIGH
    return Confidence.MEDIUM


def _suggest_experiment(
    ticker: str,
    contradictions: Tuple[str, ...],
    missing: Tuple[str, ...],
) -> Optional[str]:
    if contradictions:
        return (f"Investigate why narrative and insider disclosure diverge "
                f"for {ticker}; run researchNews with a 30-day window.")
    if missing:
        return "Fill the identified gaps: " + "; ".join(missing[:2])
    return None
