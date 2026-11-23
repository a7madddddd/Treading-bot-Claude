"""Live entry pricing for the SPX 0DTE module (D-0095-A follow-up,
2026-11-17) -- wires `greeks.reprice_from_stale_quote` ("Option B")
into one callable that a live/unattended loop can use to get a
LIVE-moment reference price for a candidate, despite Alpaca's free
options-data tier being unconditionally 15+ minutes delayed.

Mechanism, each piece already built and tested separately:
  1. SPY's own price IS available live (`feed=iex`, no delay) --
     `alpaca_client.get_spy_minute_bars`.
  2. The SPY:SPX ratio is measured from real data, not hardcoded --
     `ratio_calibration.RatioProvider`.
  3. A STALE (safely 40+ minutes old, clear of the 15-minute
     restriction) option quote is found the same way
     `scripts/spx0dte_verify_order.py` already does --
     `alpaca_client.find_nearest_traded_strike` / `get_option_bars`.
  4. That stale quote's implied volatility, carried forward to the
     LIVE underlying price and LIVE time-to-expiry --
     `greeks.reprice_from_stale_quote`.
  5. A bias correction from the measured, real validation result
     (not an arbitrary safety margin): `scripts/spx0dte_validate_greeks.py`
     found a median +3.8% overestimate across 128 real samples
     (docs/trading/decisions.md, D-0095-A "VALIDATION RESULT",
     2026-11-17). Dividing the raw estimate by `1 + MEASURED_BIAS_PCT`
     corrects that measured, directional error.

This module produces a REFERENCE price for the caller to use as a
limit order's `limit_price` -- it does not submit anything itself
(see `execution.submit_entry_order` for that) and does not decide
whether to trade (that is `signal`/`entry_filters`' job).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient
from spx0dte.econ_calendar import _is_us_dst
from spx0dte.greeks import OptionType, reprice_from_stale_quote
from spx0dte.ratio_calibration import RatioProvider

RISK_FREE_RATE = 0.045  # same approximation used throughout this module's validation
STALE_QUOTE_OFFSET_MINUTES = 40  # matches spx0dte_verify_order.py's own fix for the same restriction
MARKET_CLOSE_UTC_HOUR_EST = 21  # 4:00pm ET in EST (winter)
MARKET_CLOSE_UTC_HOUR_EDT = 20  # 4:00pm ET in EDT (summer, roughly mid-March to early November)
MEASURED_BIAS_PCT = 0.038
"""Median overestimate measured by scripts/spx0dte_validate_greeks.py
over 128 real samples, 2026-10-01 to 2026-11-13 (see
docs/trading/decisions.md D-0095-A "VALIDATION RESULT"). A
correction derived from a real measurement, not a guessed buffer --
re-measure and update this constant (with a fresh decisions.md entry)
if `spx0dte_validate_greeks.py` is re-run and gives a different
number; never silently drift from what was actually measured."""


@dataclass(frozen=True)
class LiveEntryQuote:
    occ_symbol: str
    strike: int
    raw_estimated_price: float
    """Option B's direct Black-Scholes reprice, before the bias correction."""
    bias_corrected_price: float
    """raw_estimated_price / (1 + MEASURED_BIAS_PCT) -- use this as the
    limit order's reference price."""
    stale_quote_time: datetime
    stale_quote_price: float


class LivePricingUnavailableError(Exception):
    """Raised when no live reference price could be produced -- e.g.
    no SPY data, no traded strike found even in the stale window, or
    the stale quote could not produce a valid implied volatility.
    Callers must treat this as "skip this candidate", never fabricate
    a price."""


def get_live_entry_quote(
    client: AlpacaOptionsClient, ratio_provider: RatioProvider, direction: str,
    *, now: Optional[datetime] = None,
) -> LiveEntryQuote:
    """Full path for a NEW candidate: finds a strike near the live
    SPY-derived level, then prices it. `direction` is "call" or "put",
    matching signal.EntrySignal's own field. Raises
    LivePricingUnavailableError on any failure -- never returns a
    fabricated price."""
    if direction not in ("call", "put"):
        raise ValueError(f"direction must be 'call' or 'put', got {direction!r}")
    call_put = "C" if direction == "call" else "P"

    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    live_S, ratio, _spy_bars = _live_underlying_price(client, ratio_provider, today, now)

    stale_time = now - timedelta(minutes=STALE_QUOTE_OFFSET_MINUTES)
    stale_time_iso = stale_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        found = client.find_nearest_traded_strike(today, live_S, call_put, around_time_iso=stale_time_iso)
    except AlpacaDataError as e:
        raise LivePricingUnavailableError(f"strike search failed: {e}") from e
    if found is None:
        raise LivePricingUnavailableError(f"no traded {call_put} strike found near {live_S:.0f} as of the stale window")
    strike, occ_symbol = found

    return reprice_known_symbol(
        client, ratio_provider, occ_symbol=occ_symbol, strike=strike, direction=direction, now=now,
    )


def reprice_known_symbol(
    client: AlpacaOptionsClient, ratio_provider: RatioProvider, *,
    occ_symbol: str, strike: int, direction: str, now: Optional[datetime] = None,
) -> LiveEntryQuote:
    """Re-prices an ALREADY-KNOWN symbol (the Controller approved this
    specific contract) at the current moment -- used at submission
    time per D-0007's "re-check the price before submitting"
    principle, rather than trusting a proposal's possibly-minutes-old
    `estimated_premium`. Does NOT re-search for a strike -- the
    approved symbol stays exactly what was approved."""
    if direction not in ("call", "put"):
        raise ValueError(f"direction must be 'call' or 'put', got {direction!r}")
    option_type = OptionType.CALL if direction == "call" else OptionType.PUT

    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    # 2026-11-22 fix: was a flat EST-only hour, off by a full hour during
    # EDT (~8 months/year) -- a real distortion since this feeds the
    # time-to-expiry used in the Black-Scholes reprice that becomes the
    # actual submitted limit_price, not just a display value. Reuses
    # econ_calendar's existing (rough, already-acknowledged-approximate)
    # DST window rather than a second, independent approximation.
    close_hour = MARKET_CLOSE_UTC_HOUR_EDT if _is_us_dst(now.month, now.day) else MARKET_CLOSE_UTC_HOUR_EST
    expiry = now.replace(hour=close_hour, minute=0, second=0, microsecond=0)

    live_S, ratio, spy_bars = _live_underlying_price(client, ratio_provider, today, now)
    stale_time = now - timedelta(minutes=STALE_QUOTE_OFFSET_MINUTES)

    try:
        bars = client.get_option_bars(
            occ_symbol,
            (stale_time - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            (stale_time + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            limit=50,
        )
    except AlpacaDataError as e:
        raise LivePricingUnavailableError(f"option bars fetch failed: {e}") from e
    if not bars:
        raise LivePricingUnavailableError(f"found strike {occ_symbol} but no bars in the stale window")

    bars_sorted = sorted(bars, key=lambda b: b["t"])
    stale_bar = min(
        bars_sorted,
        key=lambda b: abs((datetime.fromisoformat(b["t"].replace("Z", "+00:00")) - stale_time).total_seconds()),
    )
    stale_quote_time = datetime.fromisoformat(stale_bar["t"].replace("Z", "+00:00"))
    stale_quote_price = stale_bar["c"]

    # The SPY price AT THE STALE MOMENT (not the live one) -- implied
    # vol must be solved against the underlying price simultaneous
    # with the stale option price, not the live one.
    spy_bar_at_stale_time = min(
        spy_bars,
        key=lambda b: abs((datetime.fromisoformat(b["t"].replace("Z", "+00:00")) - stale_quote_time).total_seconds()),
    )
    stale_S = spy_bar_at_stale_time["c"] * ratio

    stale_T = max((expiry - stale_quote_time).total_seconds(), 1) / 86400 / 365
    live_T = max((expiry - now).total_seconds(), 1) / 86400 / 365

    greeks = reprice_from_stale_quote(
        stale_market_price=stale_quote_price, stale_S=stale_S, stale_T=stale_T,
        live_S=live_S, live_T=live_T, K=strike, r=RISK_FREE_RATE, option_type=option_type,
    )
    if greeks is None:
        raise LivePricingUnavailableError(
            f"stale quote {stale_quote_price} for {occ_symbol} could not produce a valid implied volatility"
        )

    return LiveEntryQuote(
        occ_symbol=occ_symbol, strike=strike,
        raw_estimated_price=greeks.price,
        bias_corrected_price=greeks.price / (1 + MEASURED_BIAS_PCT),
        stale_quote_time=stale_quote_time, stale_quote_price=stale_quote_price,
    )


def _live_underlying_price(
    client: AlpacaOptionsClient, ratio_provider: RatioProvider, today: str, now: datetime,
) -> tuple:
    """Returns (live_S, ratio, spy_bars) -- the live SPY-derived SPX
    estimate, the ratio used, and the SPY bars themselves (so a
    caller that also needs a bar at a specific past moment, e.g.
    `reprice_known_symbol`, doesn't fetch SPY twice). Shared by both
    public entry points so they never compute live_S differently."""
    try:
        spy_bars = client.get_spy_minute_bars(today)
    except AlpacaDataError as e:
        raise LivePricingUnavailableError(f"SPY fetch failed: {e}") from e
    if not spy_bars:
        raise LivePricingUnavailableError("no SPY bars for today")

    live_spy_price = spy_bars[-1]["c"]
    ratio = ratio_provider.get_ratio(now=now)
    return live_spy_price * ratio, ratio, spy_bars
