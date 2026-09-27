"""Tests for SnapshotUniverseSource (B22)."""

import unittest
from datetime import date, datetime, timezone
from typing import Optional

from d0026.evidence import (
    ConfidenceStatus, DecisionType, EvidenceConditionId, EvidenceQuality,
)
from d0026.identity import IdentityConfidence
from d0026.repository import InMemorySnapshotRepository
from d0026.snapshot import ApprovedUniverseSnapshot, SnapshotSymbolEntry
from engine.snapshot_watchlist import SnapshotUniverseSource


def _entry(ticker: str, security_id: str, rank: int) -> SnapshotSymbolEntry:
    return SnapshotSymbolEntry(
        security_id=security_id, ticker_as_of_date=ticker, rank=rank,
        score_summary=(("aggregate", 0.5),),
        first_seen_by_universe_at=date(2026, 1, 5),
        sector=None,
        identity_confidence=IdentityConfidence.RESOLVED_CIK.value,
        decision_type=DecisionType.STANDARD,
        evidence_quality=EvidenceQuality.HIGH,
        evidence_fired_conditions=(),
        evidence_policy_version="v1",
        feature_definition_version="v1",
    )


def _snap(effective: date, *, empty: bool = False,
          tickers=("AAPL", "TSLA")) -> ApprovedUniverseSnapshot:
    entries = tuple(
        _entry(t, f"sec-{t}", i + 1) for i, t in enumerate(tickers)
    ) if not empty else ()
    return ApprovedUniverseSnapshot.build(
        snapshot_at=datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc),
        effective_trading_date=effective,
        selection_version="test",
        universe_source_version="stub",
        identity_mapping_version="stub",
        regime_label="unknown",
        symbols=entries,
        rejection_summary=(),
        concentration_check_results=(),
        data_quality_summary=(),
        is_empty=empty,
        empty_reason="no-data-yet" if empty else None,
    )


def _now(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


class TestSnapshotUniverseSource(unittest.TestCase):
    def test_returns_symbols_from_todays_snapshot(self):
        repo = InMemorySnapshotRepository()
        repo.save(_snap(date(2026, 1, 5)))
        source = SnapshotUniverseSource(
            repo, now_fn=lambda: _now("2026-01-05T18:00:00"),
        )
        self.assertEqual(source.get_active_symbols(), ("AAPL", "TSLA"))

    def test_returns_empty_when_no_snapshot_and_no_fallback(self):
        repo = InMemorySnapshotRepository()
        source = SnapshotUniverseSource(
            repo, now_fn=lambda: _now("2026-01-05T18:00:00"),
        )
        self.assertEqual(source.get_active_symbols(), ())

    def test_falls_back_when_no_snapshot(self):
        repo = InMemorySnapshotRepository()
        source = SnapshotUniverseSource(
            repo, fallback_watchlist=("TSLA", "AAPL", "SPY"),
            now_fn=lambda: _now("2026-01-05T18:00:00"),
        )
        self.assertEqual(source.get_active_symbols(),
                         ("TSLA", "AAPL", "SPY"))

    def test_returns_fallback_when_snapshot_is_empty(self):
        repo = InMemorySnapshotRepository()
        repo.save(_snap(date(2026, 1, 5), empty=True))
        source = SnapshotUniverseSource(
            repo, fallback_watchlist=("F",),
            now_fn=lambda: _now("2026-01-05T18:00:00"),
        )
        self.assertEqual(source.get_active_symbols(), ("F",))

    def test_stale_prior_day_not_returned(self):
        # Snapshot exists for 2026-01-05, "now" is 2026-01-06 -> no
        # snapshot for TODAY -> empty (strict D-0026 §6).
        repo = InMemorySnapshotRepository()
        repo.save(_snap(date(2026, 1, 5)))
        source = SnapshotUniverseSource(
            repo, now_fn=lambda: _now("2026-01-06T18:00:00"),
        )
        self.assertEqual(source.get_active_symbols(), ())


if __name__ == "__main__":
    unittest.main()
