"""Post-pipeline snapshot symbol construction (B23).

Takes ranked survivors from the pipeline and produces
SnapshotSymbolEntry list via evidence classification. This lives
OUTSIDE `pipeline.py` so the D0026-EV-INV-1 invariant is preserved:
Stage A-H orchestration / decision code (which lives in `pipeline.py`
and `stages/`) must never reference evidence-layer identifiers.

Evidence classification is post-hoc routing metadata, invisible to
ranking, applied strictly after the pipeline has finished picking
survivors. NOT_ELIGIBLE candidates are dropped here (not by any
stage), matching the frozen 2026-09-15 Evidence/Confidence Layer
design.
"""

from __future__ import annotations

from datetime import date
from typing import Tuple

from .evidence import classify_evidence
from .models import (
    RegimeState, SelectedCandidateEntry, UniverseCandidate,
)
from .snapshot import (
    SnapshotSymbolEntry, build_snapshot_symbol_entries,
)


def build_snapshot_symbols(
    ranked_survivors: Tuple[UniverseCandidate, ...],
    *,
    regime_state: RegimeState,
    as_of_date: date,
) -> Tuple[SnapshotSymbolEntry, ...]:
    """Given the pipeline's ranked survivors, produce the published
    SnapshotSymbolEntry tuple. Order-preserving: rank == position in
    the survivor tuple (1-indexed)."""

    selected = tuple(
        SelectedCandidateEntry(
            candidate=c, rank=i + 1,
            # aggregate score not carried on the frozen candidate;
            # order encodes the rank, which is what the snapshot
            # entry stores.
            score_summary=(),
        )
        for i, c in enumerate(ranked_survivors)
    )
    classified = tuple(
        (sel, classify_evidence(
            identity_resolution=sel.candidate.identity_resolution,
            bar=sel.candidate.bar,
            bar_corrupted=False,
            regime_state=regime_state,
            features=sel.candidate.features,
        ))
        for sel in selected
    )
    return build_snapshot_symbol_entries(
        classified, as_of_date=as_of_date,
    )
