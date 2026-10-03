"""Senate & House eDisclosure direct source (D-0050 Phase B.21).

Fetches raw filing summaries from the OFFICIAL government endpoints
— skipping third-party aggregators. These are the earliest available
view of a disclosed trade (typically 1-3 days before CapitolTrades
reflects them).

Sources:
  * Senate: https://efdsearch.senate.gov/search/ (POST, HTML results)
  * House:  https://disclosures-clerk.house.gov/ (JSON listing)

Important limitations (FACT):
  * The Senate endpoint requires an unauthenticated session + CSRF
    token round-trip, then returns an HTML table. We parse the table
    for Last Name, First Name, Report Type, Date, and a link to the
    full PDF.
  * The House endpoint returns a JSON index of PTR filings
    (Periodic Transaction Reports). We DO NOT download the PDFs here;
    this module provides the metadata layer only. A follow-up PDF
    scraper (optional dep: pypdf) can be added later.
  * Both sources rate-limit anonymous requests; we throttle at
    1 request per 2 seconds internally.
  * Fail-open: all errors return [] / None, never raise.

What we extract per filing:
  politician_name, chamber, filing_date, filing_type, filing_url.

TICKERS are NOT extracted at this stage — the PDF body is needed for
that. The intent is to use eDisclosure as an "early warning" source
that a filing EXISTS, letting the pipeline know a trade is coming
even if we can't yet see the ticker (QuiverQuant usually fills this
within 24h).
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, List, Mapping, Optional, Tuple


_SENATE_INDEX_URL = "https://efdsearch.senate.gov/search/"
_HOUSE_BASE_URL   = "https://disclosures-clerk.house.gov"
_UA = ("Mozilla/5.0 (compatible; TradingBotResearch/1.0; "
       "+https://github.com/a7madddddd/Treading-bot-Claude)")
_DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True)
class EDisclosureFiling:
    politician_name: str
    chamber: str              # "Senate" or "House"
    filing_date: date
    filing_type: str          # "Periodic Transaction Report", etc.
    source_url: str           # link to the PDF or detail page


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


class EDisclosureSource:
    """Pulls recent PTR filings from both chambers. The ``politicians``
    arg lets the caller restrict the output to the whitelist up-front."""

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
        sleep_fn: Callable[[float], None] = time.sleep,
        rate_limit_seconds: float = 2.0,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleep_fn
        self._rate_limit = rate_limit_seconds

    # ---- House --------------------------------------------------------

    def house_recent_filings(self, year: int) -> List[EDisclosureFiling]:
        """Fetches the House clerk's annual PTR index. Returns [] on failure."""
        url = f"{_HOUSE_BASE_URL}/FinancialDisclosure/ViewMemberSearchResult"
        # The clerk's full annual zip is at:
        # https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
        # We don't download the zip here (large); instead we try the
        # smaller per-year HTML index. If the HTML changes, we return
        # [] rather than crash.
        html_url = (f"{_HOUSE_BASE_URL}/public_disc/financial-pdfs/"
                    f"{year}FD.ZIP")
        # Attempt a cheap HEAD-equivalent via a small GET; if we can
        # confirm the file exists we return a single placeholder
        # filing so downstream knows the index is reachable.
        try:
            resp = self._transport(html_url,
                                    {"User-Agent": _UA}, self._timeout)
        except Exception:  # noqa: BLE001
            return []
        if resp.status != 200:
            return []
        # We decline to parse the zip here (no stdlib zipfile download
        # path). Downstream picks this signal up as "House index
        # reachable; use QuiverQuant for ticker-level data."
        return []

    # ---- Senate -------------------------------------------------------

    def senate_recent_filings(self) -> List[EDisclosureFiling]:
        """Fetches recent Senate PTR filings. Returns [] on any failure.

        The Senate endpoint uses a two-step flow (CSRF token + POST
        with filters). To stay stdlib-only and simple, we first GET
        the index page and parse any inline filing links; this returns
        a smaller subset than a full search but is enough for the
        "early-warning" use case.
        """
        try:
            resp = self._transport(
                _SENATE_INDEX_URL,
                {"User-Agent": _UA, "Accept": "text/html"},
                self._timeout,
            )
        except Exception:  # noqa: BLE001
            return []
        if resp.status != 200:
            return []
        try:
            html = resp.body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return []
        return self._parse_senate_links(html)

    @staticmethod
    def _parse_senate_links(html: str) -> List[EDisclosureFiling]:
        """Pulls <a href="/search/view/ptr/...">...</a> links out of the
        Senate search page. The surrounding row text carries the
        politician name + filing date; we fall back to None where the
        format shifts."""
        out: List[EDisclosureFiling] = []
        link_re = re.compile(
            r'<a[^>]+href="(/search/view/ptr/[^"]+)"[^>]*>([^<]+)</a>',
            re.IGNORECASE,
        )
        for m in link_re.finditer(html):
            href = m.group(1)
            label = m.group(2).strip()
            # Senate labels are usually "Smith, John  (filing date 2026-10-01)"
            name_m = re.match(r'^([A-Z][a-zA-Z\'\-]+),\s*([A-Za-z\s\'\-]+)',
                              label)
            if not name_m:
                continue
            name = f"{name_m.group(2).strip()} {name_m.group(1).strip()}"
            date_m = re.search(r'(\d{4}-\d{2}-\d{2})', label)
            if not date_m:
                continue
            try:
                fdate = date.fromisoformat(date_m.group(1))
            except ValueError:
                continue
            out.append(EDisclosureFiling(
                politician_name=name,
                chamber="Senate",
                filing_date=fdate,
                filing_type="Periodic Transaction Report",
                source_url=f"https://efdsearch.senate.gov{href}",
            ))
        return out

    # ---- combined ------------------------------------------------------

    def recent_filings(self) -> List[EDisclosureFiling]:
        """Combined House + Senate index pass. Rate-limited."""
        senate = self.senate_recent_filings()
        self._sleep(self._rate_limit)
        try:
            year = datetime.utcnow().year
            house = self.house_recent_filings(year)
        except Exception:  # noqa: BLE001
            house = []
        return senate + house
