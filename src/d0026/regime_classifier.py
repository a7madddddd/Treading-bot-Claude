"""Regime classification from live FRED data (D-0050 Phase 1).

Replaces the hardcoded placeholder in scripts/run_universe_selection.py
(which set vix_percentile=0.5 and label=UNCLASSIFIED_PENDING_CALIBRATION
for every day).

Logic (Controller-approved D-0050 PROPOSED, see
docs/architecture/research-tools-integration-plan.md §5.1):

  1. Fetch the trailing 365 days of VIXCLS from FRED.
  2. Keep the last 252 observations (approx. one trading year).
  3. Today's percentile = fraction of the 252 history that is
     strictly below today's close. (Scipy-free: percentileofscore.)
  4. Pick the label from percentile:
       <= 0.33 → RISK_ON
       >= 0.66 → RISK_OFF
       else   → NEUTRAL
  5. Also fetch latest DFF and T10Y2Y for informational storage on
     the RegimeState.reference_series_values tuple (Stage E only
     reads vix_percentile).

Fail-open: on any issue (empty FRED response, missing today's value,
too little history, etc.), returns the placeholder RegimeState with
vix_percentile=0.5 and label=UNCLASSIFIED_PENDING_CALIBRATION. The
engine must never crash on a FRED outage.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple

from d0026.models import RegimeLabel, RegimeState


_HISTORY_WINDOW_DAYS = 365  # calendar days -- yields ~252 trading days
_WINDOW_TRADING_DAYS = 252
_RISK_ON_THRESHOLD = 0.33
_RISK_OFF_THRESHOLD = 0.66

_CLASSIFIER_VERSION = "fred-vix-percentile-v1"


def _percentile_of(value: float, history: list) -> float:
    """Fraction of history strictly below ``value``. Returns 0.5 on
    an empty history (defensive -- callers should not reach this
    with empty data)."""
    if not history:
        return 0.5
    below = sum(1 for h in history if h < value)
    return below / len(history)


def _placeholder_regime(as_of_date: date) -> RegimeState:
    """The pre-D-0050 fallback. Used when FRED cannot be reached, or
    the response is unusable."""
    return RegimeState(
        as_of_date=as_of_date,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("vix_percentile", 0.5),),
        classification_method_version="stub-v1",
    )


def classify_regime(
    fred_source,
    as_of_date: date,
) -> RegimeState:
    """Builds today's RegimeState from FRED.

    Returns the placeholder regime on ANY failure path. Never raises.

    ``fred_source`` may be ``None`` (e.g. ``FredSource.from_env()``
    returned ``None`` because FRED_API_KEY is unset) -- treated as a
    fail-open and the placeholder is returned.
    """
    if fred_source is None:
        return _placeholder_regime(as_of_date)

    start = as_of_date - timedelta(days=_HISTORY_WINDOW_DAYS)
    try:
        vix = fred_source.get_series("VIXCLS", start, as_of_date)
    except Exception:  # noqa: BLE001 -- fail-open
        return _placeholder_regime(as_of_date)

    if not vix:
        return _placeholder_regime(as_of_date)

    history_values = [v for _, v in vix][-_WINDOW_TRADING_DAYS:]
    if len(history_values) < 20:
        return _placeholder_regime(as_of_date)

    today_value = history_values[-1]
    percentile = _percentile_of(today_value, history_values[:-1])

    if percentile <= _RISK_ON_THRESHOLD:
        label = RegimeLabel.RISK_ON
    elif percentile >= _RISK_OFF_THRESHOLD:
        label = RegimeLabel.RISK_OFF
    else:
        label = RegimeLabel.NEUTRAL

    # Optional informational series. Any failure here does NOT down-
    # grade us back to the placeholder -- vix_percentile already made
    # it, and the label is already set. Just carry empty informational
    # tuples where the fetch failed.
    reference: list = [("vix_percentile", round(percentile, 4)),
                       ("vix_level", round(today_value, 4))]
    try:
        dff = fred_source.get_series("DFF", start, as_of_date)
        if dff:
            reference.append(("fed_funds_rate", round(dff[-1][1], 4)))
    except Exception:  # noqa: BLE001
        pass
    try:
        t10y2y = fred_source.get_series("T10Y2Y", start, as_of_date)
        if t10y2y:
            reference.append(("yield_curve_spread", round(t10y2y[-1][1], 4)))
    except Exception:  # noqa: BLE001
        pass

    return RegimeState(
        as_of_date=as_of_date,
        label=label,
        reference_series_values=tuple(reference),
        classification_method_version=_CLASSIFIER_VERSION,
    )
