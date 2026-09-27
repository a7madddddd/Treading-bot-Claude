"""Append-only writer for `docs/trading/research-log.md`.

Per CLAUDE.md §7 and research-sources.md §4: research findings are
appended, never rewritten. This module owns the append. Callers pass
typed `ResearchReport` / `ComparisonRecord` objects; the writer
formats them into a dated section.

No LLM anywhere. No mutation of prior entries. `write` is idempotent
per (path, report_id) — a second write with the same report_id is a
no-op (deduplication check reads the tail of the file).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from research.models import ComparisonRecord, ResearchReport


_DEFAULT_LOG_PATH = "docs/trading/research-log.md"
_DEDUP_TAIL_BYTES = 65_536  # scan last ~64KB to spot recent report_ids


class ResearchLogWriter:
    def __init__(self, path: str = _DEFAULT_LOG_PATH) -> None:
        self._path = Path(path)

    def append_report(self, report: ResearchReport, *,
                      status: str = "advisory") -> bool:
        """Returns True if a new entry was written, False if the same
        report_id was already logged (dedup)."""

        if self._already_logged(report.report_id):
            return False
        entry = _format_report(report, status=status)
        self._append(entry)
        return True

    def append_comparison(self, comparison: ComparisonRecord) -> bool:
        if self._already_logged(comparison.comparison_id):
            return False
        entry = _format_comparison(comparison)
        self._append(entry)
        return True

    # ---- internals ---------------------------------------------------

    def _already_logged(self, identifier: str) -> bool:
        if not self._path.exists():
            return False
        size = self._path.stat().st_size
        offset = max(0, size - _DEDUP_TAIL_BYTES)
        with self._path.open("rb") as f:
            f.seek(offset)
            tail = f.read()
        return identifier.encode("utf-8") in tail

    def _append(self, text: str) -> None:
        # Ensure the parent directory exists (do not fail if the docs
        # tree was reorganized -- create as needed).
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text)


# ---- pure formatters (unit-testable) ----------------------------------

def _format_report(report: ResearchReport, *, status: str) -> str:
    lines = [
        "",
        f"### {_date_prefix(report.generated_at)} — Perplexity "
        f"{report.operation.value} ({report.report_id})",
        f"- **Status:** {status}",
        f"- **Question:** {report.question}",
        f"- **Model served:** {report.model_served}",
        f"- **Confidence:** {report.confidence.value}",
        f"- **Summary:** {report.summary or '(none)'}",
    ]
    if report.subject_symbol:
        lines.append(f"- **Subject symbol:** {report.subject_symbol}")
    if report.findings:
        lines.append("- **Findings:**")
        for f in report.findings:
            lines.append(f"  - **{f.label.value}:** {f.text}")
    if report.risks:
        lines.append("- **Risks:**")
        for r in report.risks:
            lines.append(f"  - {r}")
    if report.recommendation:
        lines.append(f"- **Recommendation:** {report.recommendation}")
    if report.suggested_experiment:
        lines.append(f"- **Suggested experiment:** {report.suggested_experiment}")
    if report.sources:
        lines.append("- **Sources:**")
        for s in report.sources:
            lines.append(f"  - [{s.title}]({s.url})")
    lines.append("")
    return "\n".join(lines)


def _format_comparison(comparison: ComparisonRecord) -> str:
    lines = [
        "",
        f"### {_date_prefix(comparison.synthesized_at)} — "
        f"Comparison {comparison.ticker} ({comparison.comparison_id})",
        f"- **Confidence:** {comparison.confidence.value}",
        f"- **Perplexity reports considered:** {len(comparison.perplexity_reports)}",
        f"- **CapitolTrades records considered:** {len(comparison.capitol_trades_records)}",
    ]
    if comparison.overlap:
        lines.append("- **Overlap:**")
        for o in comparison.overlap:
            lines.append(f"  - {o}")
    if comparison.contradictions:
        lines.append("- **Contradictions:**")
        for c in comparison.contradictions:
            lines.append(f"  - {c}")
    if comparison.missing:
        lines.append("- **Missing:**")
        for m in comparison.missing:
            lines.append(f"  - {m}")
    if comparison.hypothesis:
        lines.append(f"- {comparison.hypothesis}")
    if comparison.suggested_experiment:
        lines.append(f"- **Suggested experiment:** {comparison.suggested_experiment}")
    lines.append("")
    return "\n".join(lines)


def _date_prefix(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
