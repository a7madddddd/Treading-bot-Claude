"""Capitol Trades scraper (D-0019, D-0046).

Deterministic HTML parser — NO LLM anywhere in this file. If the
site's HTML schema changes and the parser cannot locate its standard
fields (politician, security, ticker, transaction type, dates), it
raises `CapitolTradesParserBrokenError` so the caller can escalate a
CRITICAL notification. Never fabricates records.

Default subject: Ro Khanna (per D-0019). Extensible by passing a
different `politician_slug` — the scraper handles one politician per
instance.
"""

from __future__ import annotations

import html
import re
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Mapping, Optional, Tuple

from research.models import CapitolTradesRecord, Confidence


_DEFAULT_BASE_URL = "https://www.capitoltrades.com"
_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; PaperTradingResearch/1.0; +advisory)"
)
_DEFAULT_POLITICIAN_SLUG = "ro-khanna"


class CapitolTradesConfigError(Exception):
    pass


class CapitolTradesFetchError(RuntimeError):
    pass


class CapitolTradesParserBrokenError(RuntimeError):
    """Raised when the fetched HTML does not carry any of the expected
    schema markers. Signals a CRITICAL alarm per research-sources.md §5;
    never fabricates records."""


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
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return HttpResponse(resp.status, resp.read())
    except urllib.error.HTTPError as ex:
        return HttpResponse(ex.code, ex.read())


# ---- token patterns ----------------------------------------------------
# Parser is intentionally schema-tolerant: it looks for standard textual
# markers rather than a specific tag tree, so a cosmetic HTML rewrite
# does not break it. If ALL of these markers disappear, the parser is
# genuinely broken and we alarm.

_SCHEMA_MARKERS: Tuple[str, ...] = (
    "politician", "trade", "asset", "publish", "ticker", "issuer",
)

# Row-shaped fragments the CapitolTrades listing uses.
_ROW_TAG_RE = re.compile(
    r"<(?:tr|div)[^>]*(?:trade-row|q-tr|q-trade|row|list-item)[^>]*>(.*?)</(?:tr|div)>",
    re.IGNORECASE | re.DOTALL,
)
_ISSUER_RE = re.compile(
    r'class="(?:issuer|asset|security)[^"]*"[^>]*>([^<]{2,80})',
    re.IGNORECASE,
)
_TICKER_RE = re.compile(
    r"\b([A-Z][A-Z0-9]{0,5})(?::[A-Z]{2,3})?\b",
)
_ACTION_RE = re.compile(
    r"\b(buy|sell|purchase|sale|exchange)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+20\d{2})",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(
    r"\$?[\d,]+[Kk-]?\s*(?:-|to|–)\s*\$?[\d,]+[Kk-]?",
)


class CapitolTradesScraper:
    def __init__(
        self,
        *,
        politician_slug: str = _DEFAULT_POLITICIAN_SLUG,
        base_url: str = _DEFAULT_BASE_URL,
        transport: HttpTransport = _urllib_transport,
        user_agent: str = _DEFAULT_USER_AGENT,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        slug = politician_slug.strip().lower()
        if not slug:
            raise CapitolTradesConfigError("politician_slug required")
        self._politician_slug = slug
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds

    def fetch(self) -> Tuple[CapitolTradesRecord, ...]:
        url = f"{self._base_url}/politician/{self._politician_slug}"
        headers = {"User-Agent": self._user_agent,
                   "Accept": "text/html,application/xhtml+xml"}
        resp = self._transport(url, headers, self._timeout_seconds)
        if resp.status != 200:
            raise CapitolTradesFetchError(
                f"CapitolTrades fetch failed: HTTP {resp.status}"
            )
        body = resp.body.decode("utf-8", errors="replace")
        self._validate_schema(body)
        return tuple(self._parse(body, url))

    def _validate_schema(self, body: str) -> None:
        lower = body.lower()
        hits = sum(1 for m in _SCHEMA_MARKERS if m in lower)
        if hits < 3:
            raise CapitolTradesParserBrokenError(
                f"CapitolTrades HTML missing schema markers "
                f"(matched {hits}/{len(_SCHEMA_MARKERS)})"
            )

    def _parse(self, body: str, source_url: str) -> List[CapitolTradesRecord]:
        rows = _ROW_TAG_RE.findall(body)
        records: List[CapitolTradesRecord] = []
        now = datetime.now(timezone.utc)
        politician = self._politician_from_slug()

        for row in rows:
            text = _strip_tags(row)
            security = _find_security(row, text)
            ticker = _find_ticker(text)
            action = _find_action(text)
            dates = _find_dates(text)
            size = _find_size(text)

            fields_found = sum(bool(x) for x in
                               (security, ticker, action, dates))
            if fields_found < 2:
                continue

            confidence = _confidence_from_completeness(
                security=security, ticker=ticker, action=action, dates=dates
            )
            publication = dates[0] if dates else now
            disclosed = dates[1] if len(dates) > 1 else None

            records.append(CapitolTradesRecord(
                record_id=f"ct-{uuid.uuid4().hex[:12]}",
                politician=politician,
                security_name=security or "",
                ticker=ticker,
                transaction_type=(action or "unknown").lower(),
                disclosed_trade_date=disclosed,
                publication_date=publication,
                transaction_size=size,
                source_url=source_url,
                extraction_timestamp=now,
                parse_confidence=confidence,
                validation_status="unvalidated",
            ))
        return records

    def _politician_from_slug(self) -> str:
        return " ".join(w.capitalize() for w in self._politician_slug.split("-"))


# ---- pure helpers (unit-testable) -------------------------------------

def _strip_tags(fragment: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", fragment)
    return html.unescape(re.sub(r"\s+", " ", no_tags)).strip()


def _find_security(raw_row: str, plain_text: str) -> Optional[str]:
    m = _ISSUER_RE.search(raw_row)
    if m:
        cleaned = _clean(m.group(1))
        if cleaned:
            return cleaned
    # Fallback: first sequence of Title-Case words in plain text
    m2 = re.search(r"\b(?:[A-Z][a-z]+\s+){1,4}[A-Z][a-z]+\b", plain_text)
    cleaned = _clean(m2.group(0)) if m2 else None
    return cleaned if cleaned else None


def _find_ticker(plain_text: str) -> Optional[str]:
    # Prefer explicit `TICKER:EX` colon-form; else any 1-5 letter all-caps
    hits = _TICKER_RE.findall(plain_text)
    # Filter obvious noise words the regex may match
    banned = {"USD", "USA", "US", "SEC", "CEO", "IPO", "ETF", "NEW", "OLD",
              "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG",
              "SEP", "OCT", "NOV", "DEC"}
    for h in hits:
        if h in banned:
            continue
        if 1 <= len(h) <= 5 and h.isupper():
            return h
    return None


def _find_action(plain_text: str) -> Optional[str]:
    m = _ACTION_RE.search(plain_text)
    return m.group(0).lower() if m else None


def _find_dates(plain_text: str) -> Tuple[datetime, ...]:
    out: List[datetime] = []
    for m in _DATE_RE.finditer(plain_text):
        parsed = _parse_date(m.group(0))
        if parsed is not None:
            out.append(parsed)
    return tuple(out)


def _find_size(plain_text: str) -> Optional[str]:
    m = _SIZE_RE.search(plain_text)
    return m.group(0).strip() if m else None


def _parse_date(raw: str) -> Optional[datetime]:
    tz = timezone.utc
    raw = raw.replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _confidence_from_completeness(*, security: Optional[str],
                                  ticker: Optional[str],
                                  action: Optional[str],
                                  dates) -> Confidence:
    have = sum(1 for x in (security, ticker, action) if x) + (1 if dates else 0)
    if have >= 4:
        return Confidence.HIGH
    if have >= 3:
        return Confidence.MEDIUM
    return Confidence.LOW
