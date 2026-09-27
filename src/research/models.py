"""Dataclasses for the D-0019/D-0046 research subsystem.

All classes are frozen. All fields are typed. All timestamps are
timezone-aware UTC. No class defined here reaches the trading path;
they are advisory records only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple


class FindingLabel(str, Enum):
    """Per `docs/architecture/research-sources.md §2` and CLAUDE.md §4."""

    FACT = "FACT"
    SOURCE = "SOURCE"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    RECOMMENDATION = "RECOMMENDATION"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ResearchOperation(str, Enum):
    """The six named operations documented in D-0019 / research-sources.md §1.A."""

    RESEARCH_MARKET = "researchMarket"
    RESEARCH_STOCK = "researchStock"
    RESEARCH_NEWS = "researchNews"
    RESEARCH_STRATEGY = "researchStrategy"
    RESEARCH_RISK = "researchRisk"
    RESEARCH_BACKTESTING_METHOD = "researchBacktestingMethod"


@dataclass(frozen=True)
class EvidenceSource:
    url: str
    title: str
    retrieval_timestamp: datetime

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("EvidenceSource.url must be non-empty")
        if self.retrieval_timestamp.tzinfo is None:
            raise ValueError("EvidenceSource.retrieval_timestamp must be tz-aware")


@dataclass(frozen=True)
class Finding:
    label: FindingLabel
    text: str
    supporting_sources: Tuple[EvidenceSource, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Finding.text must be non-empty")


@dataclass(frozen=True)
class ResearchReport:
    """Structured output of one Perplexity research operation."""

    report_id: str
    operation: ResearchOperation
    question: str
    summary: str
    findings: Tuple[Finding, ...]
    sources: Tuple[EvidenceSource, ...]
    risks: Tuple[str, ...]
    confidence: Confidence
    recommendation: Optional[str]
    suggested_experiment: Optional[str]
    model_served: str
    generated_at: datetime
    subject_symbol: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.report_id:
            raise ValueError("ResearchReport.report_id required")
        if not self.question.strip():
            raise ValueError("ResearchReport.question required")
        if self.generated_at.tzinfo is None:
            raise ValueError("ResearchReport.generated_at must be tz-aware")


@dataclass(frozen=True)
class CapitolTradesRecord:
    """One row scraped from capitoltrades.com. All fields deterministic;
    no LLM anywhere in the extraction path."""

    record_id: str
    politician: str
    security_name: str
    ticker: Optional[str]
    transaction_type: str
    disclosed_trade_date: Optional[datetime]
    publication_date: datetime
    transaction_size: Optional[str]
    source_url: str
    extraction_timestamp: datetime
    parse_confidence: Confidence
    validation_status: str

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("CapitolTradesRecord.record_id required")
        if self.publication_date.tzinfo is None:
            raise ValueError("publication_date must be tz-aware")
        if self.extraction_timestamp.tzinfo is None:
            raise ValueError("extraction_timestamp must be tz-aware")


@dataclass(frozen=True)
class ComparisonRecord:
    """Per `research-sources.md §3`. Never merges outputs blindly:
    keeps each source separate, then names overlap, contradictions,
    missing questions, and a HYPOTHESIS the Controller can review."""

    comparison_id: str
    ticker: str
    capitol_trades_records: Tuple[CapitolTradesRecord, ...]
    perplexity_reports: Tuple[ResearchReport, ...]
    overlap: Tuple[str, ...]
    contradictions: Tuple[str, ...]
    missing: Tuple[str, ...]
    hypothesis: Optional[str]
    confidence: Confidence
    suggested_experiment: Optional[str]
    synthesized_at: datetime

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("ComparisonRecord.ticker required")
        if self.synthesized_at.tzinfo is None:
            raise ValueError("synthesized_at must be tz-aware")
