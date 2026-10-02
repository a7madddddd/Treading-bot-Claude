"""Tests for regime_classifier (D-0050 Phase 1)."""

import unittest
from datetime import date

from d0026.models import RegimeLabel
from d0026.regime_classifier import classify_regime


class _StubFred:
    def __init__(self, series_map, *, raise_on=None):
        self._series = series_map
        self._raise_on = raise_on or set()
        self.calls = []

    def get_series(self, series_id, start_date, end_date):
        self.calls.append(series_id)
        if series_id in self._raise_on:
            raise RuntimeError("boom")
        return list(self._series.get(series_id, []))


def _history(values):
    """Build (date, value) pairs with sequential dates ending today."""
    base = date(2026, 1, 1)
    return [(date.fromordinal(base.toordinal() + i), float(v))
            for i, v in enumerate(values)]


class TestClassifyRegime(unittest.TestCase):
    def test_none_source_returns_placeholder(self):
        got = classify_regime(None, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION)
        self.assertEqual(got.classification_method_version, "stub-v1")

    def test_empty_series_returns_placeholder(self):
        fred = _StubFred({"VIXCLS": []})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION)

    def test_fred_raises_returns_placeholder(self):
        fred = _StubFred({}, raise_on={"VIXCLS"})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION)

    def test_too_little_history_returns_placeholder(self):
        fred = _StubFred({"VIXCLS": _history([15.0] * 10)})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION)

    def test_low_vix_classified_risk_on(self):
        # today's value (10.0) is the lowest -- percentile = 0.0
        hist = _history([20.0] * 100 + [10.0])
        fred = _StubFred({"VIXCLS": hist})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.RISK_ON)
        d = dict(got.reference_series_values)
        self.assertAlmostEqual(d["vix_percentile"], 0.0)
        self.assertAlmostEqual(d["vix_level"], 10.0)

    def test_high_vix_classified_risk_off(self):
        # today = 40.0, everyone else is 20.0 -> percentile ~ 1.0
        hist = _history([20.0] * 100 + [40.0])
        fred = _StubFred({"VIXCLS": hist})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.RISK_OFF)

    def test_mid_vix_classified_neutral(self):
        # 100 values 0..99, today = 50 -> 50/100 = 0.5 -> NEUTRAL
        hist = _history(list(range(100)) + [50])
        fred = _StubFred({"VIXCLS": hist})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.NEUTRAL)

    def test_optional_series_failure_does_not_downgrade(self):
        hist = _history(list(range(100)) + [50])
        fred = _StubFred({"VIXCLS": hist}, raise_on={"DFF", "T10Y2Y"})
        got = classify_regime(fred, date(2026, 10, 2))
        self.assertEqual(got.label, RegimeLabel.NEUTRAL)
        keys = [k for k, _ in got.reference_series_values]
        self.assertIn("vix_percentile", keys)
        self.assertNotIn("fed_funds_rate", keys)

    def test_optional_series_populated_when_available(self):
        hist = _history(list(range(100)) + [50])
        fred = _StubFred({
            "VIXCLS": hist,
            "DFF": _history([5.25, 5.25]),
            "T10Y2Y": _history([-0.5, -0.4]),
        })
        got = classify_regime(fred, date(2026, 10, 2))
        d = dict(got.reference_series_values)
        self.assertAlmostEqual(d["fed_funds_rate"], 5.25)
        self.assertAlmostEqual(d["yield_curve_spread"], -0.4)


if __name__ == "__main__":
    unittest.main()
