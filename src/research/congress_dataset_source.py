"""Congressional-trade source backed by a public dataset (D-0086).

Replaces the paid provider whose key expired and will not be renewed
(P-079). Without it the political component scored 0.0 for EVERY
candidate -- political or not -- so 15 of the 90 weighted points were
unreachable and the D-0058 reserved slot had nothing to reserve.

WHY THIS SOURCE
---------------
Three replacement paths were tested with real calls on 2026-10-07 and
written up in `docs/trading/political-data-source-research-2026-10-06.md`:

  1. The official House/Senate endpoints. The House annual index
     downloads and parses cleanly, but it gives the FILING, not the
     ticker -- the ticker is inside the PDF, and whether that PDF even
     carries a text layer is still unsettled. Days of work, unknown
     depth.
  2. Our own CapitolTrades scraper. Already written, but it handles one
     politician per instance and nothing builds the 15-instance dict
     the aggregator expects. It also returned 403 from the research
     container, so it could not be checked against live HTML.
  3. This dataset, which parses the official filings itself rather than
     reselling an aggregator: 69,546 trades, 2011 to present, refreshed
     daily by a bot, served as static JSON over a host both this
     environment and the VM can reach. 14 of our 15 whitelist
     politicians are present with tickers.

THE JOIN IS THE WHOLE PROBLEM
-----------------------------
`politicians.lookup()` matches a normalized EXACT name, and the dataset
spells people differently: Rohit Khanna, Daniel S Sullivan, Thomas H
Tuberville, A. Mitchell McConnell. Joining on the display name would
silently drop most of the whitelist and leave a source that appears
wired and produces nothing -- the exact failure mode of the eDisclosure
adapter (P-074).

So the join is on the dataset's STABLE filer id, and this module emits
OUR canonical names. Every pair below was read from the live filer
index on 2026-10-07, not guessed from a spelling rule.

LIMITS, STATED PLAINLY
----------------------
- Chuck Schumer is absent from the dataset's 449 filers. He is kept on
  the whitelist and simply never contributes.
- The data is offered "for research and educational purposes"; the MIT
  licence covers the code only. That is a licence question for the
  Controller, recorded in P-087, not a technical one.
- It is a third party's pipeline and can go stale or vanish. This
  module reports a per-filer failure count so that is visible rather
  than silent.
- Disclosure lag is real and large -- a median of 32 to 71 days between
  a trade and its filing. What that means for the 30-day and 14-day
  scoring windows is P-083 and is NOT decided here; this module only
  supplies the trades.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from research.political_aggregator import PoliticalTrade, _normalize_action


_DEFAULT_BASE_URL = (
    "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor"
    "/main/public/data"
)
_DEFAULT_TIMEOUT_SECONDS = 20.0

DATASET_NAME = "congress_dataset"


FILER_ID_TO_WHITELIST_NAME: Mapping[str, str] = {
    # dataset filer id                 our canonical whitelist name
    "house_nancy_pelosi":              "Nancy Pelosi",
    "house_daniel_crenshaw":           "Dan Crenshaw",
    "house_josh_gottheimer":           "Josh Gottheimer",
    "house_michaelt_mccaul":           "Michael McCaul",
    "house_rohit_khanna":              "Ro Khanna",
    "house_markdr_green":              "Mark Green",
    "senate_markwayne_mullin":         "Markwayne Mullin",
    "senate_daniels_sullivan":         "Dan Sullivan",
    "senate_thomash_tuberville":       "Tommy Tuberville",
    "senate_amitchell_mcconnelljr":    "Mitch McConnell",
    "house_patrick_fallon":            "Pat Fallon",
    "senate_shelleym_capito":          "Shelley Moore Capito",
    "senate_john_boozman":             "John Boozman",
    "house_debbie_wassermanschultz":   "Debbie Wasserman Schultz",
    # Chuck Schumer is absent from the dataset's filer index.
}
"""Verified against the live index on 2026-10-07. Note the deliberate
near-misses: `house_markdr_green` is Mark Green, NOT Marjorie Taylor
Greene or Al Green, both of whom are also in the index; and
`senate_markwayne_mullin` is the Senate record, not the separate
executive-branch one that carries zero purchases."""


NON_EQUITY_TICKERS = frozenset({
    "US-TBILL", "US-TNOTE", "US-TBOND", "PENDING", "N/A", "--", "",
})
"""Rows whose 'ticker' is not something the engine could ever trade.
Treasuries dominate several filers' histories and would otherwise
manufacture clusters on an instrument we never buy."""


class HttpResponse:
    __slots__ = ("status", "body")

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


HttpTransport = Callable[[str, Mapping[str, str], float], HttpResponse]


def _urllib_transport(url: str, headers: Mapping[str, str],
                      timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, headers=dict(headers))
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return HttpResponse(r.status, r.read())
    except urllib.error.HTTPError as ex:
        return HttpResponse(ex.code, ex.read() if hasattr(ex, "read") else b"")


def _parse_date(raw) -> Optional[date]:
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


@dataclass
class FetchReport:
    """What actually happened, so a source that quietly stops working is
    visible instead of looking like a quiet month in Congress."""

    filers_attempted: int = 0
    filers_succeeded: int = 0
    trades: int = 0
    failures: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    def healthy(self) -> bool:
        return self.filers_succeeded > 0


class CongressDatasetSource:
    """Pulls each whitelisted politician's trade history and maps it to
    the shape the aggregator already consumes.

    Fail-open per filer, matching every other source in this project: a
    filer that errors is skipped and recorded, the rest still return.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: HttpTransport = _urllib_transport,
        filer_ids: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport
        self._filers = dict(filer_ids or FILER_ID_TO_WHITELIST_NAME)
        self.last_report = FetchReport()

    def fetch(self) -> List[PoliticalTrade]:
        out: List[PoliticalTrade] = []
        failures: List[Tuple[str, str]] = []
        succeeded = 0

        for filer_id, canonical_name in self._filers.items():
            try:
                rows = self._fetch_filer(filer_id)
            except Exception as exc:  # noqa: BLE001 - one filer must not
                failures.append((filer_id, type(exc).__name__))
                continue
            succeeded += 1
            for row in rows:
                trade = self._to_trade(row, canonical_name)
                if trade is not None:
                    out.append(trade)

        self.last_report = FetchReport(
            filers_attempted=len(self._filers),
            filers_succeeded=succeeded,
            trades=len(out),
            failures=tuple(failures),
        )
        return out

    # ---- internals ---------------------------------------------------

    def _fetch_filer(self, filer_id: str) -> List[dict]:
        url = f"{self._base}/filer/{filer_id}.json"
        resp = self._transport(
            url, {"Accept": "application/json"}, self._timeout)
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        payload = json.loads(resp.body.decode("utf-8"))
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
        return []

    @staticmethod
    def _to_trade(row: dict, canonical_name: str) -> Optional[PoliticalTrade]:
        ticker = row.get("ticker")
        if not isinstance(ticker, str):
            return None
        ticker = ticker.strip().upper()
        if ticker in NON_EQUITY_TICKERS:
            return None

        action = _normalize_action(row.get("transaction_type"))
        if action not in ("BUY", "SELL"):
            return None

        trade_date = _parse_date(row.get("transaction_date"))
        if trade_date is None:
            return None

        return PoliticalTrade(
            # OUR name, never the dataset's spelling -- see the module
            # docstring. lookup() matches an exact normalized name, and
            # "Rohit Khanna" is not "Ro Khanna".
            politician_name=canonical_name,
            ticker=ticker,
            action=action,
            trade_date=trade_date,
            report_date=_parse_date(row.get("filing_date")),
            size_range=(row.get("amount_range_label")
                        if isinstance(row.get("amount_range_label"), str)
                        else None),
            chamber=(str(row.get("chamber")).strip()
                     if row.get("chamber") else "Unknown"),
            source=DATASET_NAME,
            source_url=(row.get("doc_url")
                        if isinstance(row.get("doc_url"), str) else None),
        )
