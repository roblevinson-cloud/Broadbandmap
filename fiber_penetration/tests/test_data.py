import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "build_dashboard.py"
SPEC = importlib.util.spec_from_file_location("fiber_dashboard", MODULE_PATH)
dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(dashboard)


class FiberPenetrationDataTests(unittest.TestCase):
    def test_ledgers_validate(self):
        data = dashboard.validate_data()
        self.assertGreaterEqual(len(data["universe"]), 25)
        self.assertGreaterEqual(len(data["aggregate"]), 50)
        self.assertGreaterEqual(len(data["cohorts"]), 220)
        self.assertEqual(len(data["kinetic_snapshots"]), 96)
        self.assertEqual(len(data["kinetic_curves"]), 38)
        self.assertEqual(len(data["kinetic_annual"]), 43)
        self.assertEqual(len(data["shentel_panel"]), 195)
        self.assertEqual(len(data["shentel_summary"]), 27)

    def test_every_loaded_provider_is_classified(self):
        data = dashboard.validate_data()
        classified = {row["provider_id"] for row in data["universe"]}
        loaded = {row["provider_id"] for key in ("aggregate", "cohorts") for row in data[key]}
        self.assertTrue(loaded <= classified)

    def test_dashboard_builds_with_download_links(self):
        original = dashboard.OUTPUT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dashboard.OUTPUT = Path(tmp) / "dashboard.html"
                output = dashboard.build_dashboard()
                page = output.read_text(encoding="utf-8")
                self.assertIn("Aggregate network penetration", page)
                self.assertIn("cohort_observations.csv", page)
                self.assertIn("Exact Kinetic quarterly cohort matrix", page)
                self.assertIn("kinetic_quarterly_cohort_snapshots.csv", page)
                self.assertIn("No defensible Kinetic balanced 3/6/9/12 curve", page)
                self.assertIn("Exact Shentel cohort matrix", page)
                self.assertIn("shentel_cohort_panel.csv", page)
                self.assertIn("Balanced 12m average (11 cohorts)", page)
                self.assertIn("<svg", page)
        finally:
            dashboard.OUTPUT = original

    def test_csv_headers_are_unique(self):
        for path in dashboard.DATA.glob("*.csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            self.assertEqual(len(headers), len(set(headers)), path.name)

    def test_shentel_q4_2022_cohort_has_observed_quarter_steps(self):
        panel = dashboard.validate_data()["shentel_panel"]
        curve = {
            int(row["months_since_launch"]): float(row["reported_penetration"])
            for row in panel
            if row["cohort_launch_period"] == "2022-Q4"
        }
        self.assertEqual(
            {month: curve[month] for month in (0, 3, 6, 9, 12)},
            {0: 0.040, 3: 0.132, 6: 0.140, 9: 0.178, 12: 0.185},
        )

    def test_kinetic_q4_2023_has_only_observed_early_steps(self):
        curves = dashboard.validate_data()["kinetic_curves"]
        curve = {
            int(row["months_since_launch"]): float(row["reported_penetration"])
            for row in curves
            if row["cohort_launch_period"] == "2023-Q4"
        }
        self.assertEqual(curve, {3: 0.20, 6: 0.24, 9: 0.26})
        self.assertNotIn(12, curve)

    def test_kinetic_annual_rollup_preserves_report_date_restatements(self):
        annual = dashboard.validate_data()["kinetic_annual"]
        series = {
            row["report_period"]: float(row["reported_penetration"])
            for row in annual
            if row["cohort_launch_period"] == "2024"
            and int(row["months_since_launch"]) == 12
        }
        self.assertEqual(
            series,
            {
                "2025-Q1": 0.26,
                "2025-Q2": 0.28,
                "2025-Q3": 0.30,
                "2025-Q4": 0.31,
                "2026-Q1": 0.31,
                "2026-Q2": 0.31,
            },
        )

    def test_shentel_12_month_curve_uses_a_balanced_cohort_set(self):
        summary = dashboard.validate_data()["shentel_summary"]
        rows = [row for row in summary if row["panel"] == "balanced_12m"]
        self.assertEqual({int(row["cohort_count"]) for row in rows}, {11})
        self.assertEqual(
            {
                int(row["months_since_launch"]): round(
                    float(row["passing_weighted_penetration"]), 6
                )
                for row in rows
            },
            {
                0: 0.047199,
                3: 0.105540,
                6: 0.122295,
                9: 0.139145,
                12: 0.152714,
            },
        )


if __name__ == "__main__":
    unittest.main()
