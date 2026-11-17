"""Black-Scholes pricing for the SPX 0DTE module (D-0095-A follow-up,
2026-11-17 -- "Option B" for the live-timing problem).

Why this exists: Alpaca's free options-data tier has an unconditional
15-minute delay (see docs/trading/decisions.md D-0095-A) -- there is
no way to fetch a live options quote for strike selection or pricing.
SPY itself has no such restriction (`feed=iex`). Instead of trading on
stale strike/price data, this module:

  1. Backs an implied volatility out of the LAST AVAILABLE (delayed)
     observed option price via `implied_volatility()`.
  2. Re-prices the option for the CURRENT moment using that implied
     volatility together with a LIVE underlying price (SPY x the
     measured ratio from `ratio_calibration`) and the time remaining
     to the contract's real expiry, via `black_scholes_price()` /
     `black_scholes_greeks()`.

This assumes implied volatility itself changes more slowly than the
raw option price over a ~15-20 minute gap -- a plausible assumption
for ATM 0DTE options, NOT yet an empirically measured one. See
scripts/spx0dte_validate_greeks.py, which must be run against real
historical data before this is trusted for an actual submitted limit
price; this module's own accuracy is deliberately not claimed here.

Standard Black-Scholes (European option; SPX is cash-settled European-
style, so this is the correct model family -- not American-style
early exercise, which SPX options don't have).

Inputs:
  S     = underlying price (SPX index level, or a live SPY-derived estimate)
  K     = strike
  T     = time to expiry, in YEARS (e.g. 50 minutes = minutes_to_years(50))
  r     = risk-free rate (annualized, e.g. 0.045 for 4.5%)
  sigma = implied volatility (annualized, e.g. 0.15 for 15%)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

_IV_LOWER_BOUND = 0.001
_IV_UPPER_BOUND = 5.0
_IV_SOLVER_TOLERANCE = 1e-4
_IV_SOLVER_MAX_ITERATIONS = 100


class OptionType(Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class Greeks:
    price: float   # Black-Scholes theoretical price
    delta: float
    gamma: float
    theta: float   # per calendar day (not per year)
    vega: float    # per 1 percentage point change in IV (not per 1.00)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def minutes_to_years(minutes: float) -> float:
    return minutes / 60.0 / 24.0 / 365.0


def black_scholes_greeks(
    S: float, K: float, T: float, r: float, sigma: float, option_type: OptionType,
) -> Greeks:
    if S <= 0:
        raise ValueError(f"S (underlying price) must be positive, got {S!r}")
    if K <= 0:
        raise ValueError(f"K (strike) must be positive, got {K!r}")
    if T < 0:
        raise ValueError(f"T (time to expiry) must be >= 0, got {T!r}")
    if sigma < 0:
        raise ValueError(f"sigma (implied volatility) must be >= 0, got {sigma!r}")

    if T <= 0 or sigma <= 0:
        # At/past expiry, or degenerate vol -- Greeks are undefined in
        # the usual sense. Return intrinsic-value-based numbers rather
        # than raising, since a 0DTE signal engine legitimately hits
        # this near the close.
        if option_type is OptionType.CALL:
            price = max(S - K, 0.0)
            delta = 1.0 if S > K else 0.0
        else:
            price = max(K - S, 0.0)
            delta = -1.0 if S < K else 0.0
        return Greeks(price=price, delta=delta, gamma=0.0, theta=0.0, vega=0.0)

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT

    gamma = _norm_pdf(d1) / (S * sigma * sqrtT)
    vega_annual = S * _norm_pdf(d1) * sqrtT
    vega_per_pct = vega_annual / 100.0

    if option_type is OptionType.CALL:
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_annual = (
            -(S * _norm_pdf(d1) * sigma) / (2 * sqrtT)
            - r * K * math.exp(-r * T) * _norm_cdf(d2)
        )
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_annual = (
            -(S * _norm_pdf(d1) * sigma) / (2 * sqrtT)
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
        )

    theta_per_day = theta_annual / 365.0
    return Greeks(price=price, delta=delta, gamma=gamma, theta=theta_per_day, vega=vega_per_pct)


def black_scholes_price(S: float, K: float, T: float, r: float, sigma: float, option_type: OptionType) -> float:
    """Convenience wrapper when only the price is needed."""
    return black_scholes_greeks(S, K, T, r, sigma, option_type).price


class ImpliedVolatilityError(Exception):
    """Raised by implied_volatility() when no volatility in
    [_IV_LOWER_BOUND, _IV_UPPER_BOUND] reproduces the observed market
    price within tolerance -- e.g. the price is below intrinsic value
    or otherwise inconsistent with Black-Scholes. Never silently
    returns a guessed volatility."""


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float, option_type: OptionType,
    *, lower: float = _IV_LOWER_BOUND, upper: float = _IV_UPPER_BOUND,
    tolerance: float = _IV_SOLVER_TOLERANCE, max_iterations: int = _IV_SOLVER_MAX_ITERATIONS,
) -> float:
    """Bisection solver (robust, no derivative needed -- Black-Scholes
    price is monotonic in sigma for sigma > 0, so bisection always
    converges if a root exists in [lower, upper]). Raises
    ImpliedVolatilityError if the market price is outside what any
    volatility in range can produce (e.g. below intrinsic value, or a
    market price of 0 with T > 0)."""
    if market_price <= 0:
        raise ImpliedVolatilityError(f"market_price must be positive, got {market_price!r}")
    if T <= 0:
        raise ImpliedVolatilityError("cannot back out an implied volatility at/past expiry (T <= 0)")

    price_at_lower = black_scholes_price(S, K, T, r, lower, option_type)
    price_at_upper = black_scholes_price(S, K, T, r, upper, option_type)
    if not (price_at_lower <= market_price <= price_at_upper):
        raise ImpliedVolatilityError(
            f"market_price {market_price!r} is outside the range producible by "
            f"sigma in [{lower}, {upper}] ([{price_at_lower:.4f}, {price_at_upper:.4f}]) -- "
            "likely below intrinsic value or a data error, not a volatility to solve for"
        )

    lo, hi = lower, upper
    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        price_at_mid = black_scholes_price(S, K, T, r, mid, option_type)
        if abs(price_at_mid - market_price) < tolerance:
            return mid
        if price_at_mid < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def reprice_from_stale_quote(
    *, stale_market_price: float, stale_S: float, stale_T: float,
    live_S: float, live_T: float, K: float, r: float, option_type: OptionType,
) -> Optional[Greeks]:
    """The core of "Option B": backs an implied volatility out of a
    STALE observed price/underlying-price/time-to-expiry triple, then
    re-prices using that same implied volatility with the LIVE
    underlying price and the LIVE time to expiry. Returns None (never
    raises) if the stale quote cannot produce a valid implied
    volatility -- callers must treat None as "cannot reprice, do not
    submit based on this", not as a reason to fall back to a guess."""
    try:
        sigma = implied_volatility(stale_market_price, stale_S, K, stale_T, r, option_type)
    except ImpliedVolatilityError:
        return None
    return black_scholes_greeks(live_S, K, live_T, r, sigma, option_type)
