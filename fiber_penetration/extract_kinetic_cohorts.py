#!/usr/bin/env python3
"""Build audited Kinetic cohort CSVs from values printed in investor decks.

The source decks use two incompatible cohort presentations:

* Quarterly launch cohorts, with 3/6/9-month current observations and
  12/24-month anniversary milestones.
* Annual launch-year roll-ups, whose values can change as additional quarterly
  subcohorts reach a milestone.

They are intentionally written to separate files. No values are interpolated.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


DATA = Path(__file__).resolve().parent / "data"


def quarter_range(first: str, last: str) -> list[str]:
    year, quarter = int(first[:4]), int(first[-1])
    end_year, end_quarter = int(last[:4]), int(last[-1])
    values = []
    while (year, quarter) <= (end_year, end_quarter):
        values.append(f"{year}-Q{quarter}")
        quarter += 1
        if quarter == 5:
            year, quarter = year + 1, 1
    return values


def shift_quarter(period: str, months: int) -> str:
    year, quarter = int(period[:4]), int(period[-1])
    index = year * 4 + quarter - 1 + months // 3
    return f"{index // 4}-Q{index % 4 + 1}"


def quarter_end(period: str) -> str:
    year, quarter = int(period[:4]), int(period[-1])
    return f"{year}-{quarter * 3:02d}-{'31' if quarter in (1, 4) else '30'}"


QUARTERLY_DECKS = [
    {
        "report_period": "2023-Q3",
        "source_id": "kinetic_q3_2023_presentation",
        "year_1": dict(zip(quarter_range("2020-Q2", "2022-Q3"), [18, 16, 22, 15, 15, 20, 22, 25, 26, 27])),
        "year_2": dict(zip(quarter_range("2020-Q2", "2021-Q3"), [23, 23, 29, 22, 21, 26])),
        "early": {("2023-Q1", 6): 27, ("2023-Q2", 3): 22},
    },
    {
        "report_period": "2024-Q1",
        "source_id": "kinetic_q1_2024_presentation",
        "year_1": dict(zip(quarter_range("2020-Q2", "2023-Q1"), [18, 16, 22, 15, 15, 20, 22, 25, 26, 27, 24, 29])),
        "year_2": dict(zip(quarter_range("2020-Q2", "2022-Q1"), [23, 23, 29, 22, 21, 26, 27, 29])),
        "early": {("2023-Q2", 9): 27, ("2023-Q3", 6): 23, ("2023-Q4", 3): 20},
    },
    {
        "report_period": "2024-Q2",
        "source_id": "kinetic_q2_2024_presentation",
        "year_1": dict(zip(quarter_range("2020-Q2", "2023-Q2"), [18, 16, 22, 15, 15, 20, 22, 25, 26, 27, 24, 29, 28])),
        "year_2": dict(zip(quarter_range("2020-Q2", "2022-Q2"), [23, 23, 29, 22, 21, 26, 27, 29, 30])),
        "early": {("2023-Q3", 9): 25, ("2023-Q4", 6): 24, ("2024-Q1", 3): 20},
    },
    {
        "report_period": "2024-Q3",
        "source_id": "kinetic_q3_2024_presentation",
        "year_1": dict(zip(quarter_range("2020-Q2", "2023-Q3"), [18, 16, 22, 15, 15, 20, 22, 25, 26, 27, 24, 29, 28, 26])),
        "year_2": dict(zip(quarter_range("2020-Q2", "2022-Q3"), [23, 23, 29, 22, 21, 26, 27, 29, 30, 30])),
        "early": {("2023-Q4", 9): 26, ("2024-Q1", 6): 24, ("2024-Q2", 3): 19},
    },
    {
        "report_period": "2025-Q2",
        "source_id": "kinetic_q2_2025_presentation",
        "year_1": {},
        "year_2": {},
        "early": {("2024-Q3", 9): 29, ("2024-Q4", 6): 32, ("2025-Q1", 3): 23},
    },
]


ANNUAL_DECKS = [
    ("2024-Q4", "kinetic_q4_2024_presentation", {2021: {12: 18, 24: 24, 36: 26}, 2022: {12: 25, 24: 29}, 2023: {12: 28}}),
    ("2025-Q1", "kinetic_q1_2025_presentation", {2022: {12: 25, 24: 29, 36: 30}, 2023: {12: 28, 24: 32}, 2024: {12: 26}}),
    ("2025-Q2", "kinetic_q2_2025_presentation", {2022: {12: 25, 24: 29, 36: 30}, 2023: {12: 28, 24: 31}, 2024: {12: 28}}),
    ("2025-Q3", "kinetic_q3_2025_presentation", {2022: {12: 25, 24: 29, 36: 31}, 2023: {12: 28, 24: 30}, 2024: {12: 30}}),
    ("2025-Q4", "kinetic_q4_2025_presentation", {2022: {12: 25, 24: 29, 36: 31}, 2023: {12: 28, 24: 30, 36: 31}, 2024: {12: 31}}),
    ("2026-Q1", "kinetic_q1_2026_presentation", {2023: {12: 28, 24: 30, 36: 32}, 2024: {12: 31, 24: 32}, 2025: {12: 34}}),
    ("2026-Q2", "kinetic_q2_2026_presentation", {2023: {12: 28, 24: 30, 36: 33}, 2024: {12: 31, 24: 34}, 2025: {12: 35}}),
]


def quarterly_snapshot_rows() -> list[dict[str, object]]:
    rows = []
    for deck in QUARTERLY_DECKS:
        report_period = str(deck["report_period"])
        source_id = str(deck["source_id"])
        for age, field in ((12, "year_1"), (24, "year_2")):
            values = deck[field]
            assert isinstance(values, dict)
            for launch, percent in values.items():
                rows.append(
                    {
                        "observation_id": f"kinetic_{report_period.lower().replace('-', '')}_{launch.lower().replace('-', '')}_{age}",
                        "provider_id": "kinetic",
                        "report_period": report_period,
                        "report_as_of": quarter_end(report_period),
                        "cohort_label": f"{launch} launch cohort",
                        "cohort_launch_period": launch,
                        "months_since_launch": age,
                        "measurement_as_of": quarter_end(shift_quarter(launch, age)),
                        "measurement_basis": "anniversary_milestone",
                        "cohort_passings": "",
                        "reported_penetration": percent / 100,
                        "quality_grade": "A",
                        "source_id": source_id,
                        "cohort_definition": "Consumers on 1G-capable facilities within a discrete quarterly launch cohort",
                        "caveat": "Cohort passings are not disclosed; milestone bars may be repeated in later decks.",
                    }
                )
        early = deck["early"]
        assert isinstance(early, dict)
        for (launch, age), percent in early.items():
            rows.append(
                {
                    "observation_id": f"kinetic_{report_period.lower().replace('-', '')}_{launch.lower().replace('-', '')}_{age}",
                    "provider_id": "kinetic",
                    "report_period": report_period,
                    "report_as_of": quarter_end(report_period),
                    "cohort_label": f"{launch} launch cohort",
                    "cohort_launch_period": launch,
                    "months_since_launch": age,
                    "measurement_as_of": quarter_end(report_period),
                    "measurement_basis": "current_at_report",
                    "cohort_passings": "",
                    "reported_penetration": percent / 100,
                    "quality_grade": "A",
                    "source_id": source_id,
                    "cohort_definition": "Consumers on 1G-capable facilities within a discrete quarterly launch cohort",
                    "caveat": "Under-one-year observation at report quarter-end; cohort passings are not disclosed.",
                }
            )
    return rows


def unique_curve_rows(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in snapshots:
        grouped[(str(row["cohort_launch_period"]), int(row["months_since_launch"]))].append(row)
    rows = []
    for (launch, age), values in sorted(grouped.items()):
        penetrations = {float(row["reported_penetration"]) for row in values}
        if len(penetrations) != 1:
            raise ValueError(f"Kinetic quarterly cohort {launch} at {age} months changed across decks")
        values.sort(key=lambda row: str(row["report_period"]))
        first, last = values[0], values[-1]
        rows.append(
            {
                "observation_id": f"kinetic_{launch.lower().replace('-', '')}_{age}",
                "provider_id": "kinetic",
                "cohort_label": first["cohort_label"],
                "cohort_launch_period": launch,
                "observed_as_of": first["measurement_as_of"],
                "months_since_launch": age,
                "cohort_passings": "",
                "reported_penetration": first["reported_penetration"],
                "quality_grade": "A",
                "source_id": first["source_id"],
                "cohort_definition": first["cohort_definition"],
                "caveat": "No interpolation and no cohort passings disclosed; do not passing-weight.",
                "first_report_period": first["report_period"],
                "last_report_period": last["report_period"],
                "times_disclosed": len(values),
            }
        )
    return rows


def annual_snapshot_rows() -> list[dict[str, object]]:
    rows = []
    for report_period, source_id, cohorts in ANNUAL_DECKS:
        for year, milestones in cohorts.items():
            for age, percent in milestones.items():
                rows.append(
                    {
                        "observation_id": f"kinetic_annual_{report_period.lower().replace('-', '')}_{year}_{age}",
                        "provider_id": "kinetic",
                        "report_period": report_period,
                        "report_as_of": quarter_end(report_period),
                        "cohort_label": f"{year} launch-year cohort",
                        "cohort_launch_period": str(year),
                        "months_since_launch": age,
                        "cohort_passings": "",
                        "reported_penetration": percent / 100,
                        "quality_grade": "A",
                        "source_id": source_id,
                        "cohort_definition": "Consumers on 1G-capable facilities; quarterly launch cohorts summarized by launch year",
                        "caveat": "Rolling annual aggregate of all subcohorts that had reached the milestone by report date; values may change as cohort membership expands.",
                    }
                )
    return rows


def write_csv(name: str, rows: list[dict[str, object]]) -> None:
    path = DATA / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    snapshots = quarterly_snapshot_rows()
    curves = unique_curve_rows(snapshots)
    annual = annual_snapshot_rows()
    if (len(snapshots), len(curves), len(annual)) != (96, 38, 43):
        raise ValueError("unexpected Kinetic cohort row counts")
    write_csv("kinetic_quarterly_cohort_snapshots.csv", snapshots)
    write_csv("kinetic_quarterly_cohort_curves.csv", curves)
    write_csv("kinetic_annual_cohort_snapshots.csv", annual)
    print(f"Wrote {len(snapshots)} quarterly snapshots, {len(curves)} curve points, and {len(annual)} annual snapshots")


if __name__ == "__main__":
    main()
