#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    KPI_SPECS,
    WEIGHT_SUM,
    color_band,
    combine_ccss,
    expanding_percentile,
    momentum_regime,
    recession_signal,
    score_series,
    status_label,
)


def qindex(n=40, start="2000-01-01"):
    return pd.date_range(start, periods=n, freq="QS")


class TestCatalog(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertAlmostEqual(WEIGHT_SUM, 1.0)
        self.assertEqual(len(KPI_SPECS), 10)
        self.assertEqual(sum(s.weight for s in KPI_SPECS), 1.0)

    def test_layers_present(self):
        layers = {s.layer for s in KPI_SPECS}
        self.assertEqual(layers, {"borrower", "serious", "loss", "supply"})

    def test_sloos_is_credit_card_not_ci(self):
        sloos = next(s for s in KPI_SPECS if s.key == "sloos_cc_tighten")
        self.assertEqual(sloos.series_id, "DRTSCLCC")
        self.assertNotIn("DRTSCIS", sloos.series_id)


class TestPercentile(unittest.TestCase):
    def test_needs_min_history(self):
        self.assertIsNone(expanding_percentile(np.arange(10), 5, False))

    def test_high_value_high_rank(self):
        hist = np.arange(1, 31)
        self.assertGreater(expanding_percentile(hist, 30, False), 95)
        self.assertLess(expanding_percentile(hist, 1, False), 10)

    def test_invert_for_credit_growth(self):
        hist = np.arange(1, 31)
        self.assertLess(expanding_percentile(hist, 30, True), 10)
        self.assertGreater(expanding_percentile(hist, 1, True), 90)


class TestScoreSeries(unittest.TestCase):
    def test_rising_series_ends_high(self):
        spec = next(s for s in KPI_SPECS if s.key == "cc_early_flow")
        s = pd.Series(np.linspace(2, 12, 48), index=qindex(48))
        df = score_series(spec, s)
        self.assertGreater(df["score"].iloc[-1], 70)
        self.assertEqual(df["value"].iloc[-1], s.iloc[-1])
        self.assertAlmostEqual(df["change"].iloc[-1], s.iloc[-1] - s.iloc[-2])

    def test_inverted_credit_growth(self):
        spec = next(s for s in KPI_SPECS if s.key == "revol_credit_yoy")
        s = pd.Series(np.linspace(10, -4, 60), index=pd.date_range("2000-01-01", periods=60, freq="MS"))
        df = score_series(spec, s)
        self.assertGreater(df["score"].iloc[-1], 60)

    def test_does_not_invent_values(self):
        spec = next(s for s in KPI_SPECS if s.key == "cc_early_flow")
        s = pd.Series([1.0, np.nan, 2.0], index=qindex(3))
        df = score_series(spec, s)
        self.assertEqual(len(df), 2)


class TestCCSSCombine(unittest.TestCase):
    def _scored_all(self, n=40, high=False):
        scored = {}
        for spec in KPI_SPECS:
            if high:
                vals = np.linspace(1, 20, n)
            else:
                vals = np.full(n, 5.0)
            freq = "MS" if spec.frequency == "monthly" else "QS"
            periods = n * 3 if spec.frequency == "monthly" else n
            if spec.frequency == "monthly":
                vals = np.linspace(1, 20, periods) if high else np.full(periods, 5.0)
            s = pd.Series(vals, index=pd.date_range("2000-01-01", periods=periods, freq=freq))
            scored[spec.key] = score_series(spec, s)
        return scored

    def test_missing_kpis_block_ccss(self):
        scored = self._scored_all()
        for key in list(scored)[:5]:
            del scored[key]
        hist = combine_ccss(scored)
        self.assertTrue(hist["ccss"].isna().all())
        self.assertEqual(hist["confidence"].iloc[-1], "LOW")

    def test_full_panel_computes(self):
        hist = combine_ccss(self._scored_all(high=True))
        self.assertFalse(hist["ccss"].isna().iloc[-1])
        self.assertGreater(hist["ccss"].iloc[-1], 50)
        self.assertEqual(hist["n_kpis"].iloc[-1], 10)
        self.assertEqual(hist["confidence"].iloc[-1], "HIGH")


class TestLabels(unittest.TestCase):
    def test_status_and_color(self):
        self.assertEqual(status_label(15), "VERY HEALTHY")
        self.assertEqual(status_label(35), "NORMAL")
        self.assertEqual(status_label(50), "WATCH")
        self.assertEqual(status_label(70), "CREDIT STRESS")
        self.assertEqual(status_label(80), "RECESSION / CREDIT EVENT RISK")
        self.assertEqual(status_label(95), "CREDIT CRISIS")
        self.assertEqual(color_band(20), "green")
        self.assertEqual(color_band(50), "yellow")
        self.assertEqual(color_band(70), "orange")
        self.assertEqual(color_band(80), "red")
        self.assertEqual(status_label(None), "UNAVAILABLE")


class TestMomentumAndSignal(unittest.TestCase):
    def test_early_warning_regime(self):
        s = pd.Series(np.linspace(20, 50, 12), index=qindex(12))
        out = momentum_regime(s)
        self.assertEqual(out["level_bucket"], "LOW")
        self.assertEqual(out["direction"], "DETERIORATING")
        self.assertEqual(out["regime"], "Early Warning")

    def test_signal_requires_three(self):
        idx = qindex(16)
        up = pd.Series(np.linspace(1, 8, 16), index=idx)
        flat = pd.Series(np.full(16, 3.0), index=idx)
        yoy = pd.Series(np.linspace(8, -1, 16), index=idx)
        sloos = pd.Series(np.linspace(-10, 20, 16), index=idx)
        out = recession_signal(up, up, up, up, up, sloos, yoy)
        self.assertGreaterEqual(out["n_on"], 3)
        self.assertEqual(out["flag"], "ON")
        quiet = recession_signal(flat, flat, flat, flat, flat, flat, pd.Series(np.full(16, 8.0), index=idx))
        self.assertEqual(quiet["flag"], "OFF")


if __name__ == "__main__":
    unittest.main()
