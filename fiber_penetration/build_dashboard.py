#!/usr/bin/env python3
"""Validate fiber penetration ledgers and build a self-contained HTML dashboard."""

from __future__ import annotations

import csv
import html
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = ROOT.parent / "docs" / "fiber_penetration_benchmarks.html"

COLORS = {
    "frontier": "#2563eb",
    "att": "#009fdb",
    "kinetic": "#7c3aed",
    "optimum": "#e11d48",
    "shentel_glo": "#059669",
    "altafiber": "#d97706",
    "hawaiian_telcom": "#0891b2",
}


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(value: str, label: str, *, required: bool = False) -> float | None:
    value = value.strip()
    if not value:
        if required:
            raise ValueError(f"{label} is required")
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def validate_data() -> dict[str, list[dict[str, str]]]:
    universe = read_csv("company_universe.csv")
    sources = read_csv("sources.csv")
    aggregate = read_csv("aggregate_observations.csv")
    shentel_panel = read_csv("shentel_cohort_panel.csv")
    shentel_summary = read_csv("shentel_cohort_summary.csv")
    cohorts = read_csv("cohort_observations.csv") + shentel_panel

    provider_ids = [row["provider_id"] for row in universe]
    source_ids = [row["source_id"] for row in sources]
    if len(provider_ids) != len(set(provider_ids)):
        raise ValueError("provider_id values must be unique")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source_id values must be unique")
    providers = set(provider_ids)
    source_map = {row["source_id"]: row for row in sources}

    seen: set[str] = set()
    for filename, rows in (("aggregate", aggregate), ("cohort", cohorts)):
        for row in rows:
            oid = row["observation_id"]
            if oid in seen:
                raise ValueError(f"duplicate observation_id: {oid}")
            seen.add(oid)
            if row["provider_id"] not in providers:
                raise ValueError(f"{oid}: unknown provider_id")
            if row["source_id"] not in source_map:
                raise ValueError(f"{oid}: unknown source_id")
            if source_map[row["source_id"]]["provider_id"] != row["provider_id"]:
                raise ValueError(f"{oid}: source/provider mismatch")
            if row["quality_grade"] not in {"A", "B", "C", "D"}:
                raise ValueError(f"{oid}: invalid quality grade")
            penetration = _number(row["reported_penetration"], f"{oid}.reported_penetration")
            if penetration is not None and not 0 <= penetration <= 1:
                raise ValueError(f"{oid}: penetration outside [0, 1]")
            if filename == "cohort":
                months = _number(row["months_since_launch"], f"{oid}.months", required=True)
                if months is None or months < 0:
                    raise ValueError(f"{oid}: negative cohort age")
                date.fromisoformat(row["observed_as_of"])
            else:
                date.fromisoformat(row["period_end"])
                denominator = _number(row["aligned_denominator"], f"{oid}.denominator")
                connections = _number(row["reported_connections"], f"{oid}.connections")
                calculated = _number(row["calculated_penetration"], f"{oid}.calculated")
                if denominator is not None and denominator <= 0:
                    raise ValueError(f"{oid}: denominator must be positive")
                if denominator is not None and connections is not None:
                    expected = connections / denominator
                    if calculated is None or abs(calculated - expected) > 0.00011:
                        raise ValueError(f"{oid}: calculated penetration does not reconcile")
                    tolerance = {"A": 0.012, "B": 0.02, "C": 0.02, "D": 0.03}[row["quality_grade"]]
                    if penetration is not None and abs(penetration - expected) > tolerance:
                        raise ValueError(f"{oid}: reported penetration exceeds rounding tolerance")

    for row in shentel_panel:
        launch_year, launch_quarter = row["cohort_launch_period"].split("-Q")
        observed = date.fromisoformat(row["observed_as_of"])
        observed_index = observed.year * 4 + (observed.month - 1) // 3
        launch_index = int(launch_year) * 4 + int(launch_quarter) - 1
        expected_months = (observed_index - launch_index) * 3
        if int(row["months_since_launch"]) != expected_months:
            raise ValueError(f'{row["observation_id"]}: cohort age does not match report quarter')
        if expected_months < 0 or expected_months > 36 or expected_months % 3:
            raise ValueError(f'{row["observation_id"]}: Shentel panel must use 0-36 month quarter steps')

    expected_panels = {
        "balanced_12m": ("2022-Q4", "2025-Q2", 12),
        "balanced_24m": ("2022-Q4", "2024-Q2", 24),
        "balanced_36m": ("2022-Q4", "2023-Q2", 36),
    }
    seen_summary_ages: dict[str, set[int]] = defaultdict(set)
    for row in shentel_summary:
        panel_name = row["panel"]
        if panel_name not in expected_panels:
            raise ValueError(f"unknown Shentel balanced panel: {panel_name}")
        first_launch, last_launch, max_age = expected_panels[panel_name]
        if (
            row["first_launch"] != first_launch
            or row["last_launch"] != last_launch
            or int(row["max_age_months"]) != max_age
        ):
            raise ValueError(f"{panel_name}: panel boundary does not match its definition")
        age = int(row["months_since_launch"])
        seen_summary_ages[panel_name].add(age)
        values = [
            (float(item["reported_penetration"]), float(item["cohort_passings"]))
            for item in shentel_panel
            if first_launch <= item["cohort_launch_period"] <= last_launch
            and int(item["months_since_launch"]) == age
        ]
        total_passings = sum(weight for _, weight in values)
        weighted = sum(penetration * weight for penetration, weight in values) / total_passings
        simple = sum(penetration for penetration, _ in values) / len(values)
        checks = {
            "cohort_count": (int(row["cohort_count"]), len(values)),
            "total_passings": (int(row["total_passings"]), int(total_passings)),
            "passing_weighted_penetration": (float(row["passing_weighted_penetration"]), weighted),
            "simple_average_penetration": (float(row["simple_average_penetration"]), simple),
            "min_penetration": (float(row["min_penetration"]), min(value for value, _ in values)),
            "max_penetration": (float(row["max_penetration"]), max(value for value, _ in values)),
        }
        for field, (reported, calculated) in checks.items():
            if abs(reported - calculated) > 0.000001:
                raise ValueError(f"Shentel {panel_name} age {age}: {field} does not reconcile")
    for panel_name, (_, _, max_age) in expected_panels.items():
        if seen_summary_ages[panel_name] != set(range(0, max_age + 1, 3)):
            raise ValueError(f"{panel_name}: summary does not contain every three-month age")

    return {
        "universe": universe,
        "sources": sources,
        "aggregate": aggregate,
        "cohorts": cohorts,
        "shentel_panel": shentel_panel,
        "shentel_summary": shentel_summary,
    }


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt_count(value: str) -> str:
    if not value.strip():
        return "—"
    n = float(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}m".rstrip("0").rstrip(".")
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return f"{n:,.0f}"


def fmt_pct(value: str) -> str:
    return "—" if not value.strip() else f"{float(value) * 100:.1f}%"


def legend(items: list[tuple[str, str]]) -> str:
    return "".join(
        f'<span class="legend"><i style="background:{color}"></i>{esc(label)}</span>'
        for label, color in items
    )


def aggregate_chart(rows: list[dict[str, str]], names: dict[str, str]) -> str:
    rows = [r for r in rows if r["reported_penetration"].strip()]
    dates = [date.fromisoformat(r["period_end"]) for r in rows]
    x0, x1 = min(dates).toordinal(), max(dates).toordinal()
    width, height = 960, 420
    left, right, top, bottom = 62, 22, 22, 50
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max = 0.55

    def x(d: date) -> float:
        return left + (d.toordinal() - x0) / (x1 - x0) * plot_w

    def y(p: float) -> float:
        return top + (y_max - p) / y_max * plot_h

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Aggregate fiber penetration over time">']
    for pct in (0.1, 0.2, 0.3, 0.4, 0.5):
        yy = y(pct)
        out.append(f'<line class="grid" x1="{left}" x2="{width-right}" y1="{yy:.1f}" y2="{yy:.1f}"/><text class="axis" x="{left-10}" y="{yy+4:.1f}" text-anchor="end">{pct:.0%}</text>')
    for year in range(date.fromordinal(x0).year, date.fromordinal(x1).year + 1):
        d = date(year, 1, 1)
        if x0 <= d.toordinal() <= x1:
            xx = x(d)
            out.append(f'<line class="tick" x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{height-bottom}"/><text class="axis" x="{xx:.1f}" y="{height-18}" text-anchor="middle">{year}</text>')
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["provider_id"]].append(row)
    order = ["frontier", "att", "kinetic", "optimum", "shentel_glo", "altafiber", "hawaiian_telcom"]
    for pid in order:
        points = sorted(grouped.get(pid, []), key=lambda r: r["period_end"])
        if not points:
            continue
        color = COLORS[pid]
        coords = [(x(date.fromisoformat(r["period_end"])), y(float(r["reported_penetration"]))) for r in points]
        if len(coords) > 1:
            out.append(f'<path class="series" stroke="{color}" d="M ' + " L ".join(f"{xx:.1f} {yy:.1f}" for xx, yy in coords) + '"/>')
        for (xx, yy), row in zip(coords, points):
            title = f'{names[pid]} {row["period_label"]}: {float(row["reported_penetration"]):.1%}'
            out.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4" fill="{color}"><title>{esc(title)}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def cohort_chart(
    rows: list[dict[str, str]],
    names: dict[str, str],
    shentel_summary: list[dict[str, str]],
) -> str:
    width, height = 960, 420
    left, right, top, bottom = 62, 22, 22, 50
    plot_w, plot_h = width - left - right, height - top - bottom
    x_max, y_max = 36, 0.50

    def x(months: float) -> float:
        return left + months / x_max * plot_w

    def y(p: float) -> float:
        return top + (y_max - p) / y_max * plot_h

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Fiber penetration by months since launch">']
    for pct in (0.1, 0.2, 0.3, 0.4, 0.5):
        yy = y(pct)
        out.append(f'<line class="grid" x1="{left}" x2="{width-right}" y1="{yy:.1f}" y2="{yy:.1f}"/><text class="axis" x="{left-10}" y="{yy+4:.1f}" text-anchor="end">{pct:.0%}</text>')
    for month in range(0, 37, 3):
        xx = x(month)
        out.append(f'<line class="tick" x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{height-bottom}"/><text class="axis" x="{xx:.1f}" y="{height-18}" text-anchor="middle">{month}m</text>')

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["provider_id"]].append(row)
    for pid in ("frontier", "kinetic", "shentel_glo"):
        points = grouped.get(pid, [])
        if not points:
            continue
        if pid == "shentel_glo":
            points = [
                row
                for row in points
                if "2022-Q4" <= row["cohort_launch_period"] <= "2025-Q2"
                and int(row["months_since_launch"]) <= 12
            ]
        color = COLORS[pid]
        buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for row in points:
            months = int(row["months_since_launch"])
            pen = float(row["reported_penetration"])
            weight = float(row["cohort_passings"] or 1)
            buckets[months].append((pen, weight))
            title = f'{names[pid]} · {row["cohort_label"]} · {months}m: {pen:.1%}'
            out.append(f'<circle class="raw" cx="{x(months):.1f}" cy="{y(pen):.1f}" r="4" fill="{color}"><title>{esc(title)}</title></circle>')
        if pid == "shentel_glo":
            avg = [
                (x(int(row["months_since_launch"])), y(float(row["passing_weighted_penetration"])))
                for row in shentel_summary
                if row["panel"] == "balanced_12m"
            ]
        else:
            avg = []
            for months, values in sorted(buckets.items()):
                weighted = sum(p * w for p, w in values) / sum(w for _, w in values)
                avg.append((x(months), y(weighted)))
        avg.sort()
        if len(avg) > 1:
            out.append(f'<path class="cohort-line" stroke="{color}" d="M ' + " L ".join(f"{xx:.1f} {yy:.1f}" for xx, yy in avg) + '"/>')
    out.append("</svg>")
    return "".join(out)


def shentel_vintage_chart(
    rows: list[dict[str, str]], summary: list[dict[str, str]]
) -> str:
    rows = [r for r in rows if r["cohort_launch_period"] >= "2022-Q4"]
    width, height = 960, 420
    left, right, top, bottom = 62, 22, 22, 50
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max = 0.35
    year_colors = {2022: "#9ca3af", 2023: "#2563eb", 2024: "#7c3aed", 2025: "#e11d48", 2026: "#d97706"}

    def x(months: int) -> float:
        return left + months / 36 * plot_w

    def y(penetration: float) -> float:
        return top + (y_max - penetration) / y_max * plot_h

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Shentel launch-quarter cohort penetration at three-month intervals">']
    for pct in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35):
        yy = y(pct)
        out.append(f'<line class="grid" x1="{left}" x2="{width-right}" y1="{yy:.1f}" y2="{yy:.1f}"/><text class="axis" x="{left-10}" y="{yy+4:.1f}" text-anchor="end">{pct:.0%}</text>')
    for month in range(0, 37, 3):
        xx = x(month)
        out.append(f'<line class="tick" x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{height-bottom}"/><text class="axis" x="{xx:.1f}" y="{height-18}" text-anchor="middle">{month}</text>')

    vintages: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        vintages[row["cohort_launch_period"]].append(row)
    for vintage, points in sorted(vintages.items()):
        points.sort(key=lambda r: int(r["months_since_launch"]))
        color = year_colors[int(vintage[:4])]
        coords = [(x(int(r["months_since_launch"])), y(float(r["reported_penetration"]))) for r in points]
        out.append(f'<path class="vintage-line" stroke="{color}" d="M ' + " L ".join(f"{xx:.1f} {yy:.1f}" for xx, yy in coords) + '"/>')
        for (xx, yy), row in zip(coords, points):
            title = f'{row["cohort_label"]} · {row["months_since_launch"]}m: {float(row["reported_penetration"]):.1%}'
            out.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="3" fill="{color}"><title>{esc(title)}</title></circle>')

    average = [
        (x(int(row["months_since_launch"])), y(float(row["passing_weighted_penetration"])))
        for row in summary
        if row["panel"] == "balanced_12m"
    ]
    average.sort()
    out.append('<path class="average-line" d="M ' + " L ".join(f"{xx:.1f} {yy:.1f}" for xx, yy in average) + '"/>')
    out.append(f'<text class="axis axis-title" x="{left + plot_w / 2:.1f}" y="{height-2}" text-anchor="middle">Months since launch quarter</text>')
    out.append("</svg>")
    return "".join(out)


def shentel_matrix(
    rows: list[dict[str, str]], summary: list[dict[str, str]]
) -> str:
    ages = (0, 3, 6, 9, 12, 18, 24, 36)
    rows = [r for r in rows if r["cohort_launch_period"] >= "2022-Q4"]
    pivot: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        months = int(row["months_since_launch"])
        pivot[row["cohort_launch_period"]][months] = row

    body = []
    for vintage in sorted(pivot, reverse=True):
        cells = []
        for age in ages:
            row = pivot[vintage].get(age)
            if row is None:
                cells.append('<td class="empty">—</td>')
                continue
            penetration = float(row["reported_penetration"])
            alpha = 0.09 + penetration / 0.35 * 0.43
            cells.append(
                f'<td class="heat num" style="background:rgba(5,150,105,{alpha:.2f})">'
                f'<strong>{penetration:.1%}</strong></td>'
            )
        latest_row = max(pivot[vintage].values(), key=lambda r: r["observed_as_of"])
        body.append(
            f'<tr><th scope="row">{esc(vintage)}</th><td class="num">{fmt_count(latest_row["cohort_passings"])}</td>'
            + "".join(cells)
            + "</tr>"
        )

    balanced_12m = {
        int(row["months_since_launch"]): float(row["passing_weighted_penetration"])
        for row in summary
        if row["panel"] == "balanced_12m"
    }
    averages = []
    for age in ages:
        if age not in balanced_12m:
            averages.append('<td class="empty">—</td>')
            continue
        averages.append(f'<td class="num average-cell"><strong>{balanced_12m[age]:.1%}</strong></td>')
    body.append('<tr class="average-row"><th scope="row">Balanced 12m average (11 cohorts)</th><td>—</td>' + "".join(averages) + "</tr>")
    headers = "".join(f"<th>{age}m</th>" for age in ages)
    return f'<table class="matrix"><thead><tr><th>Launch cohort</th><th>Passings</th>{headers}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def build_dashboard(data: dict[str, list[dict[str, str]]] | None = None) -> Path:
    data = data or validate_data()
    universe = data["universe"]
    aggregate = data["aggregate"]
    cohorts = data["cohorts"]
    shentel_panel = data["shentel_panel"]
    shentel_summary = data["shentel_summary"]
    sources = data["sources"]
    names = {r["provider_id"]: r["network_or_brand"] for r in universe}
    source_map = {r["source_id"]: r for r in sources}

    latest: dict[str, dict[str, str]] = {}
    for row in aggregate:
        if row["provider_id"] not in latest or row["period_end"] > latest[row["provider_id"]]["period_end"]:
            latest[row["provider_id"]] = row
    latest_rows = []
    for pid, row in sorted(latest.items(), key=lambda item: float(item[1]["reported_penetration"]), reverse=True):
        src = source_map[row["source_id"]]
        latest_rows.append(
            f'<tr><td><strong>{esc(names[pid])}</strong></td><td>{esc(row["period_label"])}</td>'
            f'<td>{fmt_count(row["reported_passings"])}</td><td>{fmt_count(row["reported_connections"])}</td>'
            f'<td class="num"><strong>{fmt_pct(row["reported_penetration"])}</strong></td><td>{esc(row["customer_scope"])}</td>'
            f'<td><a href="{esc(src["url"])}">source</a><span class="note">{esc(row["caveat"])}</span></td></tr>'
        )

    universe_rows = []
    for row in sorted(universe, key=lambda r: (r["region"] != "US", int(r["priority"]), r["network_or_brand"])):
        universe_rows.append(
            f'<tr data-region="{esc(row["region"])}" data-grade="{esc(row["disclosure_grade"])}">'
            f'<td><strong>{esc(row["network_or_brand"])}</strong><span class="note">{esc(row["company_or_parent"])}</span></td>'
            f'<td>{esc(row["current_ticker"] or "—")}</td><td><span class="grade grade-{esc(row["disclosure_grade"].lower())}">{esc(row["disclosure_grade"])}</span></td>'
            f'<td>{esc(row["aggregate_curve"])}</td><td>{esc(row["cohort_curve"])}</td><td>{esc(row["current_status"])}</td>'
            f'<td>{esc(row["comparison_type"])}</td><td>{esc(row["notes"])}</td></tr>'
        )

    source_rows = []
    for row in sorted(sources, key=lambda r: (r["provider_id"], r["published_date"])):
        source_rows.append(
            f'<tr><td>{esc(names[row["provider_id"]])}</td><td>{esc(row["published_date"])}</td>'
            f'<td><a href="{esc(row["url"])}">{esc(row["title"])}</a><span class="note">{esc(row["source_detail"])}</span></td></tr>'
        )

    aggregate_legend = legend([(names[p], COLORS[p]) for p in ("frontier", "att", "kinetic", "optimum", "shentel_glo", "altafiber", "hawaiian_telcom")])
    cohort_legend = legend([(names[p], COLORS[p]) for p in ("frontier", "kinetic", "shentel_glo")])
    shentel_year_legend = legend([
        ("2022 launches", "#9ca3af"),
        ("2023 launches", "#2563eb"),
        ("2024 launches", "#7c3aed"),
        ("2025 launches", "#e11d48"),
        ("2026 launches", "#d97706"),
        ("Balanced 12m average (11 cohorts)", "#111827"),
    ])
    balanced_12m = sorted(
        (row for row in shentel_summary if row["panel"] == "balanced_12m"),
        key=lambda row: int(row["months_since_launch"]),
    )
    age_kpis = "".join(
        f'<div><b>{float(row["passing_weighted_penetration"]):.1%}</b>'
        f'<span>{row["months_since_launch"]} months</span></div>'
        for row in balanced_12m
    )
    css = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#172033;--soft:#667085;--line:#dfe5ee;--accent:#0f766e;--navy:#172554}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:auto;padding:28px 22px 70px}.hero{padding:28px 30px;border-radius:20px;background:linear-gradient(120deg,#0f172a,#173b61);color:white;box-shadow:0 18px 45px #1725541a}
h1{font-size:clamp(28px,5vw,48px);line-height:1.04;letter-spacing:-.035em;margin:0 0 12px;max-width:19ch}.hero p{max-width:78ch;color:#d6e1ef;margin:0}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:22px}.kpi{background:#ffffff10;border:1px solid #ffffff25;border-radius:13px;padding:12px 14px}.kpi b{display:block;font-size:23px}.kpi span{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#bfd0e5}
nav{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}nav a,.download{border:1px solid var(--line);background:white;border-radius:999px;padding:7px 12px;text-decoration:none;color:var(--accent);font-weight:700}
h2{font-size:22px;letter-spacing:-.02em;margin:34px 0 5px}.sub{color:var(--soft);margin:0 0 14px;max-width:90ch}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 5px 20px #1725540b}.chart{width:100%;height:auto;min-width:760px}.chart-wrap{overflow-x:auto}.grid{stroke:#dce3ec;stroke-width:1}.tick{stroke:#eef1f5;stroke-width:1}.axis{fill:#697386;font-size:12px}.axis-title{font-weight:700}.series{fill:none;stroke-width:3}.cohort-line{fill:none;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}.vintage-line{fill:none;stroke-width:1.7;opacity:.58}.average-line{fill:none;stroke:#111827;stroke-width:5;stroke-linecap:round;stroke-linejoin:round}.raw{opacity:.48;stroke:white;stroke-width:1}.legends{display:flex;gap:13px;flex-wrap:wrap;margin:4px 0 8px}.legend{font-size:12px;color:var(--soft);display:inline-flex;align-items:center;gap:5px}.legend i{width:9px;height:9px;border-radius:50%}
.callout{border-left:4px solid #f59e0b;background:#fffbeb;padding:13px 16px;border-radius:8px;margin:16px 0;color:#713f12}.age-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:14px 0}.age-kpis div{background:white;border:1px solid var(--line);border-radius:12px;padding:11px 13px}.age-kpis b{display:block;font-size:21px;color:#065f46}.age-kpis span{color:var(--soft);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em}.table-wrap{overflow:auto}.table-wrap table{min-width:900px}table{width:100%;border-collapse:collapse}th{text-align:left;color:var(--soft);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;background:#f8fafc}th,td{padding:10px 9px;border-bottom:1px solid var(--line);vertical-align:top}td{font-size:12.5px}.num{font-variant-numeric:tabular-nums}.note{display:block;color:var(--soft);font-size:10.5px;margin-top:2px}.grade{display:inline-grid;place-items:center;width:25px;height:25px;border-radius:50%;font-weight:800}.grade-a{background:#d1fae5;color:#065f46}.grade-b{background:#dbeafe;color:#1e40af}.grade-c{background:#fef3c7;color:#92400e}.grade-d{background:#fee2e2;color:#991b1b}
.matrix th,.matrix td{text-align:center;white-space:nowrap}.matrix th:first-child,.matrix td:first-child{text-align:left}.matrix .heat{border-left:2px solid white}.matrix .empty{color:#98a2b3}.average-row th,.average-cell{background:#111827!important;color:white}.downloads{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}.method{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.method div{background:white;border:1px solid var(--line);border-radius:13px;padding:14px}.method b{display:block;margin-bottom:3px}.foot{color:var(--soft);font-size:11px;margin-top:30px}@media(max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}.age-kpis{grid-template-columns:repeat(2,1fr)}.method{grid-template-columns:1fr}.hero{padding:22px}.wrap{padding:18px 12px 50px}}
"""
    script = """
document.querySelector('#universeSearch').addEventListener('input', e => {
  const q=e.target.value.toLowerCase();
  document.querySelectorAll('#universe tbody tr').forEach(r => r.hidden=!r.innerText.toLowerCase().includes(q));
});
"""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Residential Fiber Penetration Benchmarks</title><style>{css}</style></head><body><main class="wrap">
<section class="hero"><h1>Residential fiber penetration benchmarks</h1><p>Issuer-reported passings, connections and build-vintage take-up curves, normalized without erasing the definition differences that make broadband comparisons difficult.</p><div class="kpis"><div class="kpi"><b>{len(universe)}</b><span>providers classified</span></div><div class="kpi"><b>{len(aggregate)}</b><span>aggregate observations</span></div><div class="kpi"><b>{len(cohorts)}</b><span>cohort observations</span></div><div class="kpi"><b>{len(sources)}</b><span>primary sources</span></div></div></section>
<nav><a href="index.html">← Explorer home</a><a href="#shentel">Shentel cohorts</a><a href="#cohorts">Peer cohorts</a><a href="#aggregate">Aggregate curves</a><a href="#universe">Coverage universe</a><a href="#sources">Sources</a></nav>
<section id="shentel"><h2>Shentel Glo Fiber: launch-quarter cohorts</h2><p class="sub">Each colored line follows one launch-quarter cohort through successive investor decks. The panel contains {len(shentel_panel)} directly observed report-quarter × cohort values at 0, 3, 6, …, 36 months; no values are interpolated.</p><div class="age-kpis">{age_kpis}</div><div class="card"><div class="legends">{shentel_year_legend}</div><div class="chart-wrap">{shentel_vintage_chart(shentel_panel, shentel_summary)}</div></div><div class="callout"><strong>Balanced-cohort method.</strong> The headline 0–12 month curve uses the same 11 launch cohorts (Q4 2022 through Q2 2025) at every age. A cohort shown in its launch-quarter deck is age 0; the same launch quarter in successive decks is age 3, 6, 9, then 12 months. Longer balanced samples contain seven cohorts through 24 months and three through 36 months.</div><h2>Exact Shentel cohort matrix</h2><p class="sub">Rows are launch quarters; columns are months since launch. Newer cohorts remain blank where the required future deck does not yet exist. The final row uses the same 11 cohorts in every displayed 0–12 month cell.</p><div class="card table-wrap">{shentel_matrix(shentel_panel, shentel_summary)}</div></section>
<section id="cohorts"><h2>Standardized cohort comparison</h2><p class="sub">Passing-weighted observations by months since launch. Shentel's line is the balanced 11-cohort 0–12 month curve; Frontier uses cumulative within-year vintages and Kinetic uses launch-year cohorts, so the underlying dots and definitions remain available.</p><div class="card"><div class="legends">{cohort_legend}</div><div class="chart-wrap">{cohort_chart(cohorts, names, shentel_summary)}</div></div></section>
<section id="aggregate"><h2>Aggregate network penetration</h2><p class="sub">Reported fiber connections divided by the issuer's aligned saleable-location denominator. These lines are operating histories, not controlled cohort comparisons.</p><div class="card"><div class="legends">{aggregate_legend}</div><div class="chart-wrap">{aggregate_chart(aggregate, names)}</div></div><div class="callout"><strong>Read aggregate declines carefully.</strong> Frontier's reported penetration fell as it rapidly added fresh passings, even while fiber customers grew. Build-vintage curves above isolate maturation better.</div></section>
<section><h2>Latest disclosed aggregate snapshot</h2><p class="sub">Latest loaded observation per network. “Passings” may be blank where only customers and reported penetration were disclosed.</p><div class="card table-wrap"><table><thead><tr><th>Network</th><th>Period</th><th>Passings</th><th>Connections</th><th>Penetration</th><th>Numerator</th><th>Audit trail</th></tr></thead><tbody>{''.join(latest_rows)}</tbody></table></div></section>
<section id="universe"><h2>Provider disclosure universe</h2><p class="sub">The investable and formerly-public universe, plus private targets and international comparables. Grade A is the strongest disclosure; grade D means no defensible public curve.</p><input id="universeSearch" aria-label="Search providers" placeholder="Search company, ticker, status or note…" style="width:100%;max-width:430px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:10px"><div class="card table-wrap"><table><thead><tr><th>Network / parent</th><th>Ticker</th><th>Grade</th><th>Aggregate</th><th>Cohort</th><th>Status</th><th>Type</th><th>Assessment</th></tr></thead><tbody>{''.join(universe_rows)}</tbody></table></div></section>
<section><h2>Reusable data</h2><p class="sub">Analysis-ready CSVs keep issuer-reported values, cohort ages, passings, definitions and source lineage.</p><div class="method"><div><b>Use balanced age buckets</b>Shentel's 3/6/9/12-month values come from successive decks for the same 11 cohorts, not a fitted or changing-sample curve.</div><div><b>Separate retail from wholesale</b>Chorus and Openreach take-up is network utilization, not one ISP's retail share.</div><div><b>Flag migrations and transactions</b>Optimum's HFC migrations and ownership changes at Frontier, Lumen, CNSL and Ziply create structural breaks.</div></div><div class="downloads"><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/shentel_cohort_panel.csv">Shentel cohort panel CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/shentel_cohort_summary.csv">Shentel balanced curves CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/company_universe.csv">Company universe CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/aggregate_observations.csv">Aggregate observations CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/cohort_observations.csv">Other cohort observations CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/sources.csv">Source ledger CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/metric_dictionary.csv">Metric dictionary CSV</a></div></section>
<section id="sources"><h2>Primary-source ledger</h2><p class="sub">Every loaded observation resolves to a company workbook, release or presentation.</p><div class="card table-wrap"><table><thead><tr><th>Network</th><th>Published</th><th>Document</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div></section>
<p class="foot">Built from public company disclosures. These definitions are not GAAP measures and are not fully standardized across issuers. Generated by <code>fiber_penetration/build_dashboard.py</code>.</p></main><script>{script}</script></body></html>"""
    OUTPUT.write_text(page, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    result = build_dashboard()
    print(f"Wrote {result}")
