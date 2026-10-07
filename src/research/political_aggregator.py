"""Multi-source political-trade aggregator (D-0050 Phase B.22).

ONE point where every political-data source (CapitolTrades,
QuiverQuant, Senate/House eDisclosure) is pulled, normalized, de-
duplicated, and surfaced as a stream of `PoliticalTrade` records.

The aggregator does NOT score — it only COLLECTS. Scoring +
clustering happen downstream in political_cluster.py (B.24).

Fail-open everywhere: if ANY source is unavailable the others
still run; if ALL sources fail, the aggregator returns [] and
callers degrade gracefully (no political signal that cycle, rest
of the pipeline continues unchanged).
"""

from __future__ import annotations

import concurrent.futures as _fut
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from research.politicians import PoliticianProfile, lookup


@dataclass(frozen=True)
class PoliticalTrade:
    """Normalized record used across all downstream consumers."""
    politician_name: str
    ticker: str
    action: str           # "BUY" or "SELL" (uppercase)
    trade_date: date
    report_date: Optional[date]
    size_range: Optional[str]      # e.g. "$1,001 - $15,000"
    chamber: str                   # "Senate" or "House"
    source: str                    # "quiverquant" / "capitoltrades" / "edisclosure"
    source_url: Optional[str] = None

    def dedup_key(self) -> Tuple[str, str, str, date]:
        """Two sources reporting the SAME trade must agree on this
        key so we count it once."""
        return (
            self.politician_name.strip().lower(),
            self.ticker.strip().upper(),
            self.action.strip().upper(),
            self.trade_date,
        )


# ---------------------------------------------------------------------------
# Source adapters — each returns a list of PoliticalTrade, fail-open.
# ---------------------------------------------------------------------------

def _normalize_action(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    r = raw.strip().upper()
    if r in ("P", "PURCHASE", "BUY"):
        return "BUY"
    if r in ("S", "S (PARTIAL)", "SALE", "SELL", "SALE (FULL)",
             "SALE (PARTIAL)"):
        return "SELL"
    if r in ("E", "EXCHANGE"):
        return "EXCHANGE"
    return None


def _parse_iso_date(raw) -> Optional[date]:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def from_quiverquant(client) -> List[PoliticalTrade]:
    """Pulls the live congress-trading feed from QuiverQuant and
    maps each row into our normalized shape. [] on any failure."""
    if client is None:
        return []
    try:
        rows = client.recent_congress_trades()
    except Exception:  # noqa: BLE001
        return []
    out: List[PoliticalTrade] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("Representative")
        ticker = row.get("Ticker")
        action = _normalize_action(row.get("Transaction"))
        tdate = _parse_iso_date(row.get("TransactionDate")
                                 or row.get("Transaction Date"))
        if not (name and ticker and action and tdate):
            continue
        out.append(PoliticalTrade(
            politician_name=str(name).strip(),
            ticker=str(ticker).strip().upper(),
            action=action,
            trade_date=tdate,
            report_date=_parse_iso_date(row.get("ReportDate")
                                         or row.get("Report Date")),
            size_range=(str(row["Range"]).strip()
                        if isinstance(row.get("Range"), str) else None),
            chamber=str(row.get("Chamber", "")).strip() or "Unknown",
            source="quiverquant",
        ))
    return out


def from_capitol_trades(scrapers_by_politician) -> List[PoliticalTrade]:
    """`scrapers_by_politician` is a dict {name: CapitolTradesScraper}.
    One scraper per politician (per the existing module's design).
    """
    if not scrapers_by_politician:
        return []
    out: List[PoliticalTrade] = []
    for name, scraper in scrapers_by_politician.items():
        try:
            records = scraper.fetch()
        except Exception:  # noqa: BLE001
            continue
        for rec in records:
            try:
                ticker = getattr(rec, "ticker", None)
                action = _normalize_action(getattr(rec, "action", None))
                trade_date_dt = getattr(rec, "trade_date", None)
                if isinstance(trade_date_dt, datetime):
                    tdate = trade_date_dt.date()
                else:
                    tdate = _parse_iso_date(trade_date_dt)
                if not (ticker and action and tdate):
                    continue
                out.append(PoliticalTrade(
                    politician_name=name,
                    ticker=str(ticker).strip().upper(),
                    action=action,
                    trade_date=tdate,
                    report_date=None,
                    size_range=getattr(rec, "size", None),
                    chamber="Unknown",
                    source="capitoltrades",
                    source_url=getattr(rec, "source_url", None),
                ))
            except Exception:  # noqa: BLE001
                continue
    return out


def from_congress_dataset(dataset_source) -> List[PoliticalTrade]:
    """D-0086. The dataset source already emits `PoliticalTrade` with
    OUR canonical whitelist names, so there is nothing to normalize
    here -- the name join is its whole job and it owns it."""
    if dataset_source is None:
        return []
    try:
        rows = dataset_source.fetch()
    except Exception:  # noqa: BLE001
        return []
    return [r for r in rows if isinstance(r, PoliticalTrade)]


def from_edisclosure(edisclosure_source) -> List[PoliticalTrade]:
    """eDisclosure gives us FILING metadata, not trade tickers (PDF
    parsing deferred). We emit a 'filing-detected' marker as a
    pseudo-trade with ticker="PENDING" so the aggregator can log
    which politicians have fresh activity, without claiming to know
    the ticker."""
    if edisclosure_source is None:
        return []
    try:
        filings = edisclosure_source.recent_filings()
    except Exception:  # noqa: BLE001
        return []
    out: List[PoliticalTrade] = []
    for f in filings or []:
        if lookup(f.politician_name) is None:
            # Only whitelist politicians generate pending markers.
            continue
        out.append(PoliticalTrade(
            politician_name=f.politician_name,
            ticker="PENDING",
            action="BUY",   # unknown — assume buy as default pending resolution
            trade_date=f.filing_date,
            report_date=f.filing_date,
            size_range=None,
            chamber=f.chamber,
            source="edisclosure",
            source_url=f.source_url,
        ))
    return out


# ---------------------------------------------------------------------------
# Aggregator (fan-out + de-dup)
# ---------------------------------------------------------------------------

class PoliticalAggregator:
    """Runs every source in parallel and returns a de-duplicated,
    whitelist-filtered trade stream."""

    def __init__(
        self,
        *,
        quiverquant=None,
        capitol_scrapers=None,
        edisclosure=None,
        congress_dataset=None,
        max_workers: int = 4,
        deadline_seconds: float = 60.0,
    ) -> None:
        self._qq = quiverquant
        self._ct_scrapers = capitol_scrapers or {}
        self._ed = edisclosure
        self._ds = congress_dataset
        self._max_workers = max_workers
        self._deadline = deadline_seconds

    def fetch_all(self) -> List[PoliticalTrade]:
        trades: List[PoliticalTrade] = []
        with _fut.ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            futs = {}
            if self._qq is not None:
                futs[ex.submit(from_quiverquant, self._qq)] = "quiverquant"
            if self._ct_scrapers:
                futs[ex.submit(from_capitol_trades, self._ct_scrapers)] = "capitoltrades"
            if self._ed is not None:
                futs[ex.submit(from_edisclosure, self._ed)] = "edisclosure"
            if self._ds is not None:
                futs[ex.submit(from_congress_dataset, self._ds)] = "congress_dataset"
            for fut in futs:
                try:
                    rows = fut.result(timeout=self._deadline)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(rows, list):
                    trades.extend(rows)
        return self._dedup_and_filter(trades)

    def _dedup_and_filter(
        self, trades: List[PoliticalTrade],
    ) -> List[PoliticalTrade]:
        seen: Set[tuple] = set()
        out: List[PoliticalTrade] = []
        for t in trades:
            # Only whitelist politicians survive.
            if lookup(t.politician_name) is None:
                continue
            key = t.dedup_key()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out
