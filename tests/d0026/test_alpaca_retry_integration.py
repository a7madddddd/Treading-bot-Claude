"""Regression tests: AlpacaAssetsProvider and AlpacaFeatureEnricher
retry wiring (B28)."""

import json
import unittest
from datetime import date

from common.http_retry import RetryPolicy, with_retry
from d0026.alpaca_provider import AlpacaAssetsProvider, HttpResponse as ProviderResp
from d0026.alpaca_enricher import AlpacaFeatureEnricher, HttpResponse as EnricherResp
from d0026.identity import (
    IdentityConfidence, IdentityResolution, ResolutionOutcome,
    SecurityIdentity, TickerAlias,
)
from d0026.models import (
    RawCandidateRef, RegimeLabel, RegimeState, UniverseCandidate,
)


def _asset(symbol):
    return {"id": "1", "class": "us_equity", "exchange": "NASDAQ",
            "symbol": symbol, "name": symbol, "status": "active",
            "tradable": True, "marginable": True, "fractionable": True}


class TestProviderRetry(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        responses = iter([
            ProviderResp(429, b'{"error":"rate limit"}'),
            ProviderResp(429, b'{"error":"rate limit"}'),
            ProviderResp(200, json.dumps([_asset("AAPL")]).encode()),
        ])
        sleeps = []
        base = lambda url, headers, timeout: next(responses)
        wrapped = with_retry(base, policy=RetryPolicy(max_attempts=3),
                             sleep_fn=sleeps.append)
        p = AlpacaAssetsProvider(key_id="k", secret_key="s",
                                 transport=wrapped)
        r = p.get_raw_candidates(date(2026, 1, 5))
        self.assertEqual([x.ticker for x in r], ["AAPL"])
        self.assertEqual(len(sleeps), 2)

    def test_retry_policy_wired_via_constructor(self):
        # Alternative wiring: pass retry_policy directly.
        responses = iter([
            ProviderResp(500, b'{"error":"boom"}'),
            ProviderResp(200, json.dumps([_asset("AAPL")]).encode()),
        ])
        p = AlpacaAssetsProvider(
            key_id="k", secret_key="s",
            transport=lambda url, headers, timeout: next(responses),
            retry_policy=RetryPolicy(max_attempts=2,
                                     base_backoff_seconds=0.0,
                                     max_backoff_seconds=0.0),
        )
        r = p.get_raw_candidates(date(2026, 1, 5))
        self.assertEqual([x.ticker for x in r], ["AAPL"])


def _cand(ticker="AAPL"):
    identity = SecurityIdentity(
        security_id=f"s-{ticker}", display_name=ticker, cik="0",
        confidence=IdentityConfidence.RESOLVED_CIK,
    )
    alias = TickerAlias(security_id=identity.security_id, ticker=ticker,
                        effective_start=None, effective_end=None)
    resolution = IdentityResolution(
        outcome=ResolutionOutcome.RESOLVED, ticker=ticker,
        as_of_date=date(2026, 1, 5), identity=identity, alias=alias,
    )
    return UniverseCandidate(
        raw=RawCandidateRef(ticker=ticker, as_of_date=date(2026, 1, 5)),
        identity_resolution=resolution, bar=None, features=None,
    )


def _regime():
    return RegimeState(
        as_of_date=date(2026, 1, 5),
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("vix_percentile", 0.5),),
        classification_method_version="t",
    )


def _bars(n):
    return {"bars": [
        {"c": 100+i, "h": 101+i, "l": 99+i, "o": 100+i,
         "v": 1000, "vw": 100+i}
        for i in range(n)
    ]}


class TestEnricherRetry(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        responses = iter([
            EnricherResp(429, b'{"error":"rate limit"}'),
            EnricherResp(200, json.dumps(_bars(35)).encode()),
        ])
        e = AlpacaFeatureEnricher(
            key_id="k", secret_key="s",
            transport=lambda url, headers, timeout: next(responses),
            retry_policy=RetryPolicy(max_attempts=2,
                                     base_backoff_seconds=0.0,
                                     max_backoff_seconds=0.0),
        )
        result = e(_cand(), date(2026, 1, 5), _regime())
        self.assertIsNotNone(result.features)


if __name__ == "__main__":
    unittest.main()
