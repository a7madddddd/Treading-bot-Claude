"""SQLite persistence for the research subsystem (D-0046, migration 0005).

Advisory-only tables. Trading path never reads these rows.
Serialization is JSON for the multi-value fields to keep the schema
narrow; parsing is one-shot per record.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from persistence.db import transaction
from research.models import (
    CapitolTradesRecord,
    ComparisonRecord,
    Confidence,
    EvidenceSource,
    Finding,
    FindingLabel,
    ResearchOperation,
    ResearchReport,
)


class ResearchRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- reports -----------------------------------------------------

    def save_report(self, report: ResearchReport, *,
                    status: str = "advisory") -> None:
        with transaction(self._conn) as tconn:
            tconn.execute(
                "INSERT OR REPLACE INTO research_reports ("
                " report_id, operation, question, summary, findings_json, "
                " sources_json, risks_json, confidence, recommendation, "
                " suggested_experiment, model_served, generated_at, "
                " subject_symbol, status"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report.report_id,
                    report.operation.value,
                    report.question,
                    report.summary,
                    json.dumps([_finding_to_dict(f) for f in report.findings]),
                    json.dumps([_source_to_dict(s) for s in report.sources]),
                    json.dumps(list(report.risks)),
                    report.confidence.value,
                    report.recommendation,
                    report.suggested_experiment,
                    report.model_served,
                    report.generated_at.isoformat(),
                    report.subject_symbol,
                    status,
                ),
            )

    def list_reports(self, *, subject_symbol: Optional[str] = None,
                     limit: int = 50) -> Tuple[ResearchReport, ...]:
        cur = self._conn.cursor()
        if subject_symbol is not None:
            cur.execute(
                "SELECT * FROM research_reports WHERE subject_symbol = ? "
                "ORDER BY generated_at DESC LIMIT ?",
                (subject_symbol.upper(), limit),
            )
        else:
            cur.execute(
                "SELECT * FROM research_reports "
                "ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return tuple(_report_from_row(r) for r in rows)

    # ---- capitol trades -----------------------------------------------

    def save_capitol_trades(self,
                            records: Sequence[CapitolTradesRecord]) -> int:
        if not records:
            return 0
        inserted = 0
        with transaction(self._conn) as tconn:
            for r in records:
                tconn.execute(
                    "INSERT OR REPLACE INTO capitol_trades_records ("
                    " record_id, politician, security_name, ticker, "
                    " transaction_type, disclosed_trade_date, "
                    " publication_date, transaction_size, source_url, "
                    " extraction_timestamp, parse_confidence, "
                    " validation_status"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        r.record_id, r.politician, r.security_name, r.ticker,
                        r.transaction_type,
                        r.disclosed_trade_date.isoformat()
                            if r.disclosed_trade_date else None,
                        r.publication_date.isoformat(),
                        r.transaction_size, r.source_url,
                        r.extraction_timestamp.isoformat(),
                        r.parse_confidence.value, r.validation_status,
                    ),
                )
                inserted += 1
        return inserted

    def list_capitol_trades(self, *, ticker: Optional[str] = None,
                            limit: int = 100
                            ) -> Tuple[CapitolTradesRecord, ...]:
        cur = self._conn.cursor()
        if ticker is not None:
            cur.execute(
                "SELECT * FROM capitol_trades_records WHERE ticker = ? "
                "ORDER BY publication_date DESC LIMIT ?",
                (ticker.upper(), limit),
            )
        else:
            cur.execute(
                "SELECT * FROM capitol_trades_records "
                "ORDER BY publication_date DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return tuple(_capitol_trade_from_row(r) for r in rows)

    # ---- comparisons --------------------------------------------------

    def save_comparison(self, comparison: ComparisonRecord) -> None:
        with transaction(self._conn) as tconn:
            tconn.execute(
                "INSERT OR REPLACE INTO research_comparisons ("
                " comparison_id, ticker, perplexity_ids_json, "
                " capitol_trades_ids_json, overlap_json, "
                " contradictions_json, missing_json, hypothesis, "
                " confidence, suggested_experiment, synthesized_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    comparison.comparison_id,
                    comparison.ticker,
                    json.dumps([r.report_id
                                for r in comparison.perplexity_reports]),
                    json.dumps([r.record_id
                                for r in comparison.capitol_trades_records]),
                    json.dumps(list(comparison.overlap)),
                    json.dumps(list(comparison.contradictions)),
                    json.dumps(list(comparison.missing)),
                    comparison.hypothesis,
                    comparison.confidence.value,
                    comparison.suggested_experiment,
                    comparison.synthesized_at.isoformat(),
                ),
            )


# ---- row -> model ------------------------------------------------------

def _finding_to_dict(f: Finding) -> dict:
    return {
        "label": f.label.value,
        "text": f.text,
        "sources": [_source_to_dict(s) for s in f.supporting_sources],
    }


def _source_to_dict(s: EvidenceSource) -> dict:
    return {"url": s.url, "title": s.title,
            "retrieval_timestamp": s.retrieval_timestamp.isoformat()}


def _source_from_dict(d: dict) -> EvidenceSource:
    return EvidenceSource(
        url=d["url"], title=d.get("title", d["url"]),
        retrieval_timestamp=datetime.fromisoformat(d["retrieval_timestamp"]),
    )


def _finding_from_dict(d: dict) -> Finding:
    return Finding(
        label=FindingLabel(d["label"]),
        text=d["text"],
        supporting_sources=tuple(_source_from_dict(s)
                                 for s in d.get("sources", [])),
    )


def _report_from_row(row: dict) -> ResearchReport:
    findings = tuple(_finding_from_dict(f)
                     for f in json.loads(row["findings_json"]))
    sources = tuple(_source_from_dict(s)
                    for s in json.loads(row["sources_json"]))
    risks = tuple(json.loads(row["risks_json"]))
    return ResearchReport(
        report_id=row["report_id"],
        operation=ResearchOperation(row["operation"]),
        question=row["question"],
        summary=row["summary"],
        findings=findings,
        sources=sources,
        risks=risks,
        confidence=Confidence(row["confidence"]),
        recommendation=row["recommendation"],
        suggested_experiment=row["suggested_experiment"],
        model_served=row["model_served"],
        generated_at=datetime.fromisoformat(row["generated_at"]),
        subject_symbol=row["subject_symbol"],
    )


def _capitol_trade_from_row(row: dict) -> CapitolTradesRecord:
    return CapitolTradesRecord(
        record_id=row["record_id"],
        politician=row["politician"],
        security_name=row["security_name"],
        ticker=row["ticker"],
        transaction_type=row["transaction_type"],
        disclosed_trade_date=datetime.fromisoformat(row["disclosed_trade_date"])
            if row["disclosed_trade_date"] else None,
        publication_date=datetime.fromisoformat(row["publication_date"]),
        transaction_size=row["transaction_size"],
        source_url=row["source_url"],
        extraction_timestamp=datetime.fromisoformat(row["extraction_timestamp"]),
        parse_confidence=Confidence(row["parse_confidence"]),
        validation_status=row["validation_status"],
    )
