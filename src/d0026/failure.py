"""CRASH vs. EMPTY, enforced by the type system rather than by
convention.

Per the 2026-09-15 implementation-plan discussion §F:

- CRASH: an unexpected technical/system failure, a corrupted
  intermediate state, an infrastructure failure, or a programming error.
  No snapshot is published.
- EMPTY: the pipeline completed normally end-to-end and either no
  candidate survived the legitimate filter stages, or the required
  upstream data genuinely produced an empty survivor set. A valid,
  immutable, published ``ApprovedUniverseSnapshot`` exists, with
  ``is_empty=True`` and a specific ``empty_reason``.

``PipelineOutcome`` is a union of exactly these two shapes. There is no
third shape, and neither shape can carry both a crash detail and a
snapshot — this is enforced structurally, not just documented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Union

from .snapshot import ApprovedUniverseSnapshot


class CrashCategory(Enum):
    """A categorized reason a run crashed, for audit purposes. Note that
    a pipeline attempting to run real selection logic before D-0026's
    calibration gate is open is itself treated as a (very loud, very
    early) crash category — CALIBRATION_NOT_READY — rather than being
    allowed to silently produce a plausible-looking result. See
    pipeline.CalibrationRequiredError."""

    UNHANDLED_EXCEPTION = "unhandled_exception"
    DATA_SOURCE_UNREACHABLE = "data_source_unreachable"
    CORRUPTED_STATE = "corrupted_state"
    CALIBRATION_NOT_READY = "calibration_not_ready"
    # P-036 (2026-10-05). A run whose candidate pool is far smaller than
    # a real whole-market fetch did not "legitimately select nothing" --
    # it never ran on the real universe at all, so its result is not
    # evidence about the market and must not become a published
    # snapshot. On 2026-10-05 a 40-candidate test run wrote the day's
    # snapshot with one symbol, and the engine traded that one-symbol
    # universe for a whole session with nothing reporting it.
    INSUFFICIENT_CANDIDATE_POOL = "insufficient_candidate_pool"


@dataclass(frozen=True)
class CrashOutcome:
    """No snapshot is published for this evaluation date."""

    as_of_date: date
    category: CrashCategory
    detail: str


@dataclass(frozen=True)
class SnapshotOutcome:
    """A valid, published ApprovedUniverseSnapshot — either populated or
    ``is_empty=True`` with a reason. Both are legitimate, non-alarming
    outcomes at this type's level; only CrashOutcome represents a
    failure."""

    snapshot: ApprovedUniverseSnapshot


PipelineOutcome = Union[CrashOutcome, SnapshotOutcome]
