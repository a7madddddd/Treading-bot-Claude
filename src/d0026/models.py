"""Conceptual domain models for the D-0026 universe-selection pipeline.

All objects here are immutable (frozen dataclasses) and carry an explicit
identity reference (``security_id``) rather than a bare ticker, per
identity.py's rule. None of these models defines, defaults, or implies
any numeric D-0026 parameter (no liquidity floor, spread cap, ATR band,
momentum threshold, regime threshold, ranking weight, sector cap,
correlation threshold, Top-N, warm-up period, or staleness period) — they
are typed containers for values a future, calibrated implementation will
populate, not decisions about what those values should be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional, Tuple

from .identity import IdentityResolution


class AdjustmentConvention(Enum):
    """Explicit tag for a price bar's adjustment convention. A bar must
    never be untagged — see the raw-vs-adjusted discipline in
    docs/trading/historical-data-calibration-plan.md §19 (still TBD which
    convention D-0026 ultimately standardizes on; this enum only ensures
    every bar states which one it is)."""

    RAW = "raw"
    SPLIT_ADJUSTED = "split_adjusted"
    DIVIDEND_ADJUSTED = "dividend_adjusted"
    SPLIT_AND_DIVIDEND_ADJUSTED = "split_and_dividend_adjusted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarketDataBar:
    """One trading day's OHLCV for one security. Immutable; a correction
    is a new, separately versioned bar, never an in-place overwrite."""

    security_id: str
    ticker_as_of_date: str
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjustment: AdjustmentConvention
    source_reference: str

    def __post_init__(self) -> None:
        if not self.security_id:
            raise ValueError("security_id must be non-empty")
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("open/close must fall within [low, high]")


@dataclass(frozen=True)
class DailySecurityFeatures:
    """Derived per-security, per-date features. Fields are typed slots
    for a future calibrated feature-construction step to populate — no
    field here carries a default numeric value, and no comparison/
    threshold logic exists anywhere in this class.

    ``warm_up_sufficient`` is populated by whatever future warm-up-period
    decision D-0026 calibrates (still TBD); it is explicit here so a
    pipeline stage can act on insufficient warm-up without this class
    having to know what "sufficient" numerically means.
    """

    security_id: str
    feature_date: date
    liquidity_measure: Optional[float]
    atr_measure: Optional[float]
    momentum_measure: Optional[float]
    execution_quality_proxy: Optional[float]
    execution_quality_proxy_is_true_quote: bool
    warm_up_sufficient: bool
    source_reference: str


@dataclass(frozen=True)
class HistoricalMembership:
    """A point-in-time index/universe membership record, used only for
    validation/control purposes (docs/trading/historical-data-calibration-plan.md
    §10) — never as the production universe source. See
    docs/architecture/universe.md and the 2026-09-15 implementation-plan
    discussion for why this must never be wired in as
    ``UniverseSourceProvider``."""

    security_id: str
    ticker_as_of_date: str
    universe_label: str  # e.g. "sp500", "sp400" — a source label, not a rule
    effective_start: date
    effective_end: Optional[date]
    source_reference: str


class RegimeLabel(Enum):
    """Placeholder categorical regime labels. The classification *method*
    (percentile / volatility / drawdown / composite) and its cutoffs are
    still TBD per docs/trading/historical-data-calibration-plan.md §6 —
    this enum exists only so RegimeState has a typed slot to populate,
    not to assert a chosen method or threshold."""

    UNKNOWN = "unknown"
    UNCLASSIFIED_PENDING_CALIBRATION = "unclassified_pending_calibration"


@dataclass(frozen=True)
class RegimeState:
    """Market-wide state for one date, independent of any security. The
    Strategy Engine must never branch on this value — it is informational
    /audit-only on the eventual ApprovedUniverseSnapshot (unchanged from
    docs/architecture/universe.md §2)."""

    as_of_date: date
    label: RegimeLabel
    reference_series_values: Tuple[Tuple[str, float], ...]
    classification_method_version: str

    def __post_init__(self) -> None:
        if self.label is RegimeLabel.UNKNOWN and self.reference_series_values:
            raise ValueError(
                "an UNKNOWN regime must not carry reference series values as "
                "if they were used to classify it"
            )


@dataclass(frozen=True)
class RawCandidateRef:
    """The minimal output of a UniverseSourceProvider before identity
    resolution has run: a bare ticker on a date. Deliberately does not
    carry a security_id — that would imply the ticker has already been
    trusted as a permanent identity, which this whole subsystem exists
    to prevent."""

    ticker: str
    as_of_date: date


@dataclass(frozen=True)
class UniverseCandidate:
    """A single security under evaluation on a single date, after
    identity resolution has run. Immutable — one record per
    (security-or-provisional-ticker, date) pair; a re-evaluation on a
    later date is a new record."""

    raw: RawCandidateRef
    identity_resolution: IdentityResolution
    bar: Optional[MarketDataBar]
    features: Optional[DailySecurityFeatures]

    def __post_init__(self) -> None:
        if self.raw.as_of_date != self.identity_resolution.as_of_date:
            raise ValueError("candidate date must match identity resolution date")


class PipelineStage(Enum):
    """The nine stages of the frozen D-0026 pipeline
    (docs/architecture/universe.md §1). Order matters; RANKING through
    PERSIST_SNAPSHOT only run over what survived the earlier stages."""

    TRADABILITY = "A_tradability"
    DATA_QUALITY = "B_data_quality"
    EXECUTION_QUALITY = "C_execution_quality"
    STRATEGY_MECHANICS_FIT = "D_strategy_mechanics_fit"
    REGIME_ADAPTATION = "E_regime_adaptation"
    RANKING = "F_ranking"
    CONCENTRATION = "G_concentration"
    TOP_N = "H_top_n"
    PERSIST_SNAPSHOT = "I_persist_snapshot"


ORDERED_CANDIDATE_STAGES: Tuple[PipelineStage, ...] = (
    PipelineStage.TRADABILITY,
    PipelineStage.DATA_QUALITY,
    PipelineStage.EXECUTION_QUALITY,
    PipelineStage.STRATEGY_MECHANICS_FIT,
    PipelineStage.REGIME_ADAPTATION,
    PipelineStage.RANKING,
    PipelineStage.CONCENTRATION,
    PipelineStage.TOP_N,
)
"""Every stage except PERSIST_SNAPSHOT, which is not a candidate filter —
it is the act of building and saving the ApprovedUniverseSnapshot."""


class RejectionReasonCategory(Enum):
    """A categorical taxonomy for why a candidate did not survive a
    stage — queryable/auditable, never free text alone. Categories only;
    no numeric threshold values are encoded here."""

    MISSING_MARKET_DATA = "missing_market_data"
    INSUFFICIENT_WARM_UP = "insufficient_warm_up"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
    FAILED_TRADABILITY = "failed_tradability"
    FAILED_DATA_QUALITY = "failed_data_quality"
    FAILED_EXECUTION_QUALITY = "failed_execution_quality"
    FAILED_STRATEGY_MECHANICS_FIT = "failed_strategy_mechanics_fit"
    FAILED_REGIME_ADAPTATION = "failed_regime_adaptation"
    FAILED_CONCENTRATION = "failed_concentration"
    NOT_IN_TOP_N = "not_in_top_n"


@dataclass(frozen=True)
class UniverseSelectionRejection:
    """One candidate's rejection at one stage, with enough detail to
    reconstruct why after the fact (docs on observability, §G of the
    2026-09-15 implementation-plan discussion)."""

    candidate: UniverseCandidate
    stage: PipelineStage
    category: RejectionReasonCategory
    detail: str


@dataclass(frozen=True)
class SelectedCandidateEntry:
    """A candidate that survived every stage, carrying its rank and score
    summary. ``score_summary`` is a tuple of (metric_name, value) pairs —
    a generic container; this module does not assert which metrics exist
    or how they are weighted (ranking composition remains an open design
    question per docs/trading/universe-selection-analysis.md §7.5)."""

    candidate: UniverseCandidate
    rank: int
    score_summary: Tuple[Tuple[str, float], ...]


@dataclass(frozen=True)
class UniverseSelectionResult:
    """The pipeline's internal, pre-approval result for one evaluation
    date. Distinct from ApprovedUniverseSnapshot (snapshot.py), which is
    the published, externally-consumed artifact built from this."""

    as_of_date: date
    regime_state: RegimeState
    selected: Tuple[SelectedCandidateEntry, ...]
    rejections: Tuple[UniverseSelectionRejection, ...]
    is_empty: bool
    empty_reason: Optional[str]
    produced_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if self.is_empty and self.selected:
            raise ValueError("is_empty=True must not carry any selected candidates")
        if self.is_empty and not self.empty_reason:
            raise ValueError("is_empty=True must carry an empty_reason")
        if not self.is_empty and self.empty_reason:
            raise ValueError("empty_reason must be empty when is_empty=False")
