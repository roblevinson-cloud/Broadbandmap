from __future__ import annotations

import sys
import unittest
from pathlib import Path


MONITOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR_DIR))

from fiber_monitor import (  # noqa: E402
    candidate_to_record,
    detect_stage,
    infer_geography,
    is_meaningful_alert_change,
    merge_records,
    normalize_county,
    parse_brightspeed_counties,
)


CONFIG = {
    "providers": {
        "Metronet": {"aliases": ["metronet", "vexus fiber"]},
        "Lumos": {"aliases": ["lumos fiber"]},
    },
    "build_terms": ["construction", "permit", "expansion", "engineering"],
    "stage_weights": {
        "announced": 0.35,
        "engineering": 0.60,
        "row_approved": 0.80,
        "permitted": 0.90,
        "construction": 0.95,
        "available": 1.00,
        "blocked": 0.20,
    },
    "market_crosswalk": [
        {"city": "New Bern", "state": "NC", "counties": ["Craven"]},
        {"city": "New Franklin", "state": "OH", "counties": ["Summit"]},
    ],
    "ignored_domains": ["youtube.com"],
}

FOOTPRINT = {
    "states": {
        "NC": {"name": "North Carolina", "counties": ["Craven", "Orange"]},
        "OH": {"name": "Ohio", "counties": ["Stark", "Mahoning", "Trumbull"]},
    }
}


class FiberMonitorTests(unittest.TestCase):
    def test_county_normalization(self) -> None:
        self.assertEqual(normalize_county("  St. Louis County "), "st. louis")
        self.assertEqual(normalize_county("Orleans Parish"), "orleans")

    def test_brightspeed_page_parser(self) -> None:
        source = """
        <div data-state="North Carolina" class="state-description">
          <div class="description-list"><p>Craven, Orange</p></div>
        </div>
        <div data-state="Ohio" class="state-description">
          <div class="description-list"><p>Mahoning, Stark, Trumbull</p></div>
        </div>
        """
        parsed = parse_brightspeed_counties(source)
        self.assertEqual(parsed["NC"], ["Craven", "Orange"])
        self.assertEqual(parsed["OH"], ["Mahoning", "Stark", "Trumbull"])

    def test_stage_classifier_handles_active_and_blocked(self) -> None:
        stage = detect_stage("Fiber construction has begun this week", CONFIG["stage_weights"])
        self.assertEqual(stage, ("construction", "active", 0.95))
        blocked = detect_stage(
            "The requirements made deployment economically unworkable",
            CONFIG["stage_weights"],
        )
        self.assertEqual(blocked, ("blocked", "blocked", 0.20))

    def test_market_crosswalk_confirms_brightspeed_county(self) -> None:
        result = infer_geography(
            "Metronet construction in New Bern, NC", "", CONFIG, FOOTPRINT
        )
        self.assertEqual(result["state"], "NC")
        self.assertEqual(result["counties"], ["Craven"])
        self.assertEqual(result["encroachment"], "confirmed_county")

    def test_crosswalk_can_reject_county_inside_brightspeed_state(self) -> None:
        result = infer_geography(
            "Lumos construction begins in New Franklin, Ohio", "", CONFIG, FOOTPRINT
        )
        self.assertEqual(result["counties"], ["Summit"])
        self.assertEqual(result["encroachment"], "outside_county")

    def test_search_candidate_requires_provider_and_build_signal(self) -> None:
        source = {
            "name": "Search",
            "source_type": "search",
            "provider_hint": "Lumos",
        }
        ignored = candidate_to_record(
            {
                "url": "https://example.com/lighting",
                "title": "A bright lumos lamp",
                "description": "Product review",
            },
            source,
            CONFIG,
            FOOTPRINT,
            "2026-09-02",
        )
        self.assertIsNone(ignored)
        accepted = candidate_to_record(
            {
                "url": "https://example.com/fiber",
                "title": "Lumos Fiber construction begins in New Bern, NC",
                "description": "Crews started construction.",
            },
            source,
            CONFIG,
            FOOTPRINT,
            "2026-09-02",
        )
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted["encroachment"], "confirmed_county")

    def test_merge_prefers_later_verification(self) -> None:
        older = {
            "source_url": "https://example.com/item?utm_source=x",
            "last_verified": "2026-08-01",
            "title": "Older",
        }
        newer = {
            "source_url": "https://example.com/item",
            "detected_on": "2026-09-01",
            "title": "Newer",
        }
        merged = merge_records([older, newer])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "Newer")

    def test_curated_record_is_not_replaced_by_automatic_page_churn(self) -> None:
        curated = {
            "source_url": "https://example.com/project",
            "record_type": "activity",
            "last_verified": "2026-09-01",
            "title": "Curated",
        }
        automatic = {
            "source_url": "https://example.com/project",
            "record_type": "automated_discovery",
            "detected_on": "2026-09-02",
            "title": "Automatic",
        }
        self.assertEqual(merge_records([curated, automatic])[0]["title"], "Curated")

    def test_automatic_discovery_ignores_pre_2026_results(self) -> None:
        source = {
            "name": "Search",
            "source_type": "search",
            "provider_hint": "Metronet",
        }
        old_config = {**CONFIG, "minimum_event_date": "2026-01-01"}
        result = candidate_to_record(
            {
                "url": "https://example.com/old-news",
                "title": "Metronet begins construction",
                "published": "2022-01-10",
            },
            source,
            old_config,
            FOOTPRINT,
            "2026-09-02",
        )
        self.assertIsNone(result)

    def test_only_high_confidence_direct_activity_alerts(self) -> None:
        record = {
            "encroachment": "confirmed_county",
            "confidence": "high",
            "stage": "construction",
            "status": "active",
            "probability": 0.95,
            "matched_brightspeed_counties": ["Craven"],
        }
        self.assertTrue(is_meaningful_alert_change(record, None))
        self.assertFalse(is_meaningful_alert_change({**record, "confidence": "medium"}, None))
        self.assertFalse(is_meaningful_alert_change(record, dict(record)))


if __name__ == "__main__":
    unittest.main()
