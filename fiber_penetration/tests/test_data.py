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
        self.assertGreaterEqual(len(data["cohorts"]), 30)

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
                self.assertIn("<svg", page)
        finally:
            dashboard.OUTPUT = original

    def test_csv_headers_are_unique(self):
        for path in dashboard.DATA.glob("*.csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            self.assertEqual(len(headers), len(set(headers)), path.name)


if __name__ == "__main__":
    unittest.main()
