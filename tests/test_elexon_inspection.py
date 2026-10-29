"""Contract checks for the research audit; no network required."""

from datetime import date, time
import unittest

from scripts.inspect_elexon import audit, report, timestamp
from scripts.benchmark_residuals import metrics, quantile


def forecast(publication="2026-01-01T07:30:00Z", generation=7000):
    return {"dataset": "WINDFOR", "startTime": "2026-01-02T18:00:00Z",
            "publishTime": publication, "generation": generation}


class InspectionTests(unittest.TestCase):
    def test_repeated_boundary_records_are_counted_and_deduplicated(self):
        row = forecast()
        summary, unique = audit([row, dict(row)], "WINDFOR")
        self.assertEqual(summary["repeated_keys"], 1)
        self.assertEqual(unique, [row])

    def test_conflicting_natural_key_fails(self):
        with self.assertRaisesRegex(ValueError, "conflicting values"):
            audit([forecast(), forecast(generation=6000)], "WINDFOR")

    def test_missing_generation_is_rejected_without_becoming_zero(self):
        summary, rows = audit([forecast(generation=None)], "WINDFOR")
        self.assertEqual(summary["rejected_rows"], 1)
        self.assertEqual(rows, [])

    def test_equivalent_timezone_offsets_have_the_same_key(self):
        summary, rows = audit([forecast(), forecast("2026-01-01T08:30:00+01:00")], "WINDFOR")
        self.assertEqual(summary["repeated_keys"], 1)
        self.assertEqual(len(rows), 1)

    def test_naive_timestamp_rejected(self):
        with self.assertRaisesRegex(ValueError, "Naive"):
            timestamp("2026-01-02T18:00:00")

    def test_decision_scope_uses_following_delivery_day(self):
        result = report([], [forecast()], date(2026, 1, 1), date(2026, 1, 1), time(9, 20))
        day = result["days"][0]
        self.assertEqual(day["delivery_date"], "2026-01-02")
        self.assertEqual(day["eligible_native_forecasts"], 1)
        self.assertEqual(day["actual_half_hours"], 0)

    def test_future_publication_is_not_eligible(self):
        result = report([], [forecast("2026-01-01T09:30:00Z")],
                        date(2026, 1, 1), date(2026, 1, 1), time(9, 20))
        self.assertEqual(result["totals"]["eligible_native_forecasts"], 0)

    def test_london_dst_changes_expected_half_hour_counts(self):
        spring = report([], [], date(2026, 3, 28), date(2026, 3, 28), time(9, 20))
        autumn = report([], [], date(2026, 10, 24), date(2026, 10, 24), time(9, 20))
        self.assertEqual(spring["totals"]["expected_half_hours"], 46)
        self.assertEqual(autumn["totals"]["expected_half_hours"], 50)

    def test_benchmark_quantiles_use_linear_interpolation(self):
        self.assertAlmostEqual(quantile([30, 0, 20, 10], .1), 3)
        self.assertEqual(quantile([42], .9), 42)

    def test_benchmark_metrics_preserve_signed_error(self):
        result = metrics([-3000, 1000])
        self.assertEqual(result["mae_mw"], 2000)
        self.assertEqual(result["bias_mw"], -1000)
        self.assertAlmostEqual(result["rmse_mw"], 2236.06797749979)


if __name__ == "__main__":
    unittest.main()
