"""Concrete UniverseSourceProvider backed by Alpaca `/v2/assets` (B24).

Returns `RawCandidateRef` tuples for tradable, active US equities.
Supports an optional whitelist so a session can start with a
controlled sub-universe (Controller's per-day list) rather than
Alpaca's full ~14K symbols.

Never returns delisted, non-tradable, or non-equity symbols. HTTP
transport is injectable (same pattern as `AlpacaBrokerClient`) so
unit tests never hit the network.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import date
from typing import Callable, Iterable, Mapping, Optional, Tuple

from d0026.models import RawCandidateRef
from d0026.provider import UniverseSourceProvider


_DEFAULT_BASE_URL = "https://paper-api.alpaca.markets"
_DEFAULT_TIMEOUT_SECONDS = 30.0


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
        return HttpResponse(ex.code, ex.read())


class AlpacaAssetsProviderConfigError(Exception):
    pass


class AlpacaAssetsProviderFetchError(RuntimeError):
    pass


class AlpacaAssetsProvider(UniverseSourceProvider):
    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        symbol_whitelist: Optional[Iterable[str]] = None,
        require_fractionable: bool = False,
        transport: HttpTransport = _urllib_transport,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not key_id or not secret_key:
            raise AlpacaAssetsProviderConfigError(
                "key_id and secret_key are required"
            )
        self._key_id = key_id
        self._secret = secret_key
        self._base = base_url.rstrip("/")
        self._whitelist = (
            frozenset(s.strip().upper() for s in symbol_whitelist)
            if symbol_whitelist is not None else None
        )
        self._require_fractionable = require_fractionable
        self._transport = transport
        self._timeout = timeout_seconds

    def get_raw_candidates(
        self, as_of_date: date
    ) -> Tuple[RawCandidateRef, ...]:
        url = self._base + "/v2/assets?status=active&asset_class=us_equity"
        headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
        }
        resp = self._transport(url, headers, self._timeout)
        if resp.status != 200:
            raise AlpacaAssetsProviderFetchError(
                f"Alpaca /v2/assets returned {resp.status}: "
                f"{resp.body[:200]!r}"
            )
        assets = json.loads(resp.body.decode("utf-8"))

        candidates = []
        for a in assets:
            if not a.get("tradable"):
                continue
            if self._require_fractionable and not a.get("fractionable"):
                continue
            symbol = a.get("symbol", "").upper()
            if not symbol:
                continue
            if self._whitelist is not None and symbol not in self._whitelist:
                continue
            candidates.append(
                RawCandidateRef(ticker=symbol, as_of_date=as_of_date)
            )
        return tuple(candidates)
