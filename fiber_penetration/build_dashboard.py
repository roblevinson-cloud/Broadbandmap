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
    cohorts = read_csv("cohort_observations.csv")

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

    return {"universe": universe, "sources": sources, "aggregate": aggregate, "cohorts": cohorts}


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


def cohort_chart(rows: list[dict[str, str]], names: dict[str, str]) -> str:
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
    for month in (0, 6, 12, 18, 24, 30, 36):
        xx = x(month)
        out.append(f'<line class="tick" x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{height-bottom}"/><text class="axis" x="{xx:.1f}" y="{height-18}" text-anchor="middle">{month}m</text>')

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["provider_id"]].append(row)
    for pid in ("frontier", "kinetic", "shentel_glo"):
        points = grouped.get(pid, [])
        if not points:
            continue
        color = COLORS[pid]
        buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for row in points:
            months = int(row["months_since_launch"])
            pen = float(row["reported_penetration"])
            weight = float(row["cohort_passings"] or 1)
            buckets[months].append((pen, weight))
            title = f'{names[pid]} · {row["cohort_label"]} · {months}m: {pen:.1%}'
            out.append(f'<circle class="raw" cx="{x(months):.1f}" cy="{y(pen):.1f}" r="4" fill="{color}"><title>{esc(title)}</title></circle>')
        avg = []
        for months, values in sorted(buckets.items()):
            weighted = sum(p * w for p, w in values) / sum(w for _, w in values)
            avg.append((x(months), y(weighted)))
        if len(avg) > 1:
            out.append(f'<path class="cohort-line" stroke="{color}" d="M ' + " L ".join(f"{xx:.1f} {yy:.1f}" for xx, yy in avg) + '"/>')
    out.append("</svg>")
    return "".join(out)


def build_dashboard(data: dict[str, list[dict[str, str]]] | None = None) -> Path:
    data = data or validate_data()
    universe = data["universe"]
    aggregate = data["aggregate"]
    cohorts = data["cohorts"]
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
    css = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#172033;--soft:#667085;--line:#dfe5ee;--accent:#0f766e;--navy:#172554}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:auto;padding:28px 22px 70px}.hero{padding:28px 30px;border-radius:20px;background:linear-gradient(120deg,#0f172a,#173b61);color:white;box-shadow:0 18px 45px #1725541a}
h1{font-size:clamp(28px,5vw,48px);line-height:1.04;letter-spacing:-.035em;margin:0 0 12px;max-width:19ch}.hero p{max-width:78ch;color:#d6e1ef;margin:0}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:22px}.kpi{background:#ffffff10;border:1px solid #ffffff25;border-radius:13px;padding:12px 14px}.kpi b{display:block;font-size:23px}.kpi span{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#bfd0e5}
nav{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}nav a,.download{border:1px solid var(--line);background:white;border-radius:999px;padding:7px 12px;text-decoration:none;color:var(--accent);font-weight:700}
h2{font-size:22px;letter-spacing:-.02em;margin:34px 0 5px}.sub{color:var(--soft);margin:0 0 14px;max-width:90ch}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 5px 20px #1725540b}.chart{width:100%;height:auto;min-width:620px}.chart-wrap{overflow-x:auto}.grid{stroke:#dce3ec;stroke-width:1}.tick{stroke:#eef1f5;stroke-width:1}.axis{fill:#697386;font-size:12px}.series{fill:none;stroke-width:3}.cohort-line{fill:none;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}.raw{opacity:.62;stroke:white;stroke-width:1}.legends{display:flex;gap:13px;flex-wrap:wrap;margin:4px 0 8px}.legend{font-size:12px;color:var(--soft);display:inline-flex;align-items:center;gap:5px}.legend i{width:9px;height:9px;border-radius:50%}
.callout{border-left:4px solid #f59e0b;background:#fffbeb;padding:13px 16px;border-radius:8px;margin:16px 0;color:#713f12}.table-wrap{overflow:auto}.table-wrap table{min-width:900px}table{width:100%;border-collapse:collapse}th{text-align:left;color:var(--soft);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;background:#f8fafc}th,td{padding:10px 9px;border-bottom:1px solid var(--line);vertical-align:top}td{font-size:12.5px}.num{font-variant-numeric:tabular-nums}.note{display:block;color:var(--soft);font-size:10.5px;margin-top:2px}.grade{display:inline-grid;place-items:center;width:25px;height:25px;border-radius:50%;font-weight:800}.grade-a{background:#d1fae5;color:#065f46}.grade-b{background:#dbeafe;color:#1e40af}.grade-c{background:#fef3c7;color:#92400e}.grade-d{background:#fee2e2;color:#991b1b}
.downloads{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}.method{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.method div{background:white;border:1px solid var(--line);border-radius:13px;padding:14px}.method b{display:block;margin-bottom:3px}.foot{color:var(--soft);font-size:11px;margin-top:30px}@media(max-width:760px){.kpis{grid-template-columns:repeat(2,1fr)}.method{grid-template-columns:1fr}.hero{padding:22px}.wrap{padding:18px 12px 50px}}
"""
    script = """
document.querySelector('#universeSearch').addEventListener('input', e => {
  const q=e.target.value.toLowerCase();
  document.querySelectorAll('#universe tbody tr').forEach(r => r.hidden=!r.innerText.toLowerCase().includes(q));
});
"""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Residential Fiber Penetration Benchmarks</title><style>{css}</style></head><body><main class="wrap">
<section class="hero"><h1>Residential fiber penetration benchmarks</h1><p>Issuer-reported passings, connections and build-vintage take-up curves, normalized without erasing the definition differences that make broadband comparisons difficult.</p><div class="kpis"><div class="kpi"><b>{len(universe)}</b><span>providers classified</span></div><div class="kpi"><b>{len(aggregate)}</b><span>aggregate observations</span></div><div class="kpi"><b>{len(cohorts)}</b><span>cohort observations</span></div><div class="kpi"><b>{len(sources)}</b><span>primary sources</span></div></div></section>
<nav><a href="index.html">← Explorer home</a><a href="#aggregate">Aggregate curves</a><a href="#cohorts">Cohort curves</a><a href="#universe">Coverage universe</a><a href="#sources">Sources</a></nav>
<section id="aggregate"><h2>Aggregate network penetration</h2><p class="sub">Reported fiber connections divided by the issuer's aligned saleable-location denominator. The lines are useful operating histories, but they are not controlled cohort comparisons.</p><div class="card"><div class="legends">{aggregate_legend}</div><div class="chart-wrap">{aggregate_chart(aggregate, names)}</div></div><div class="callout"><strong>Read aggregate declines carefully.</strong> Frontier's reported penetration fell as it rapidly added fresh passings, even while fiber customers grew. Build-vintage curves below isolate maturation better.</div></section>
<section id="cohorts"><h2>Penetration by months since launch</h2><p class="sub">True or disclosed launch-vintage observations. Frontier points are cumulative within each build year; Kinetic uses launch-year cohorts; Shentel's 2022 disclosure is a cross-section of discrete launch quarters.</p><div class="card"><div class="legends">{cohort_legend}</div><div class="chart-wrap">{cohort_chart(cohorts, names)}</div></div></section>
<section><h2>Latest disclosed aggregate snapshot</h2><p class="sub">Latest loaded observation per network. “Passings” may be blank where only customers and reported penetration were disclosed.</p><div class="card table-wrap"><table><thead><tr><th>Network</th><th>Period</th><th>Passings</th><th>Connections</th><th>Penetration</th><th>Numerator</th><th>Audit trail</th></tr></thead><tbody>{''.join(latest_rows)}</tbody></table></div></section>
<section id="universe"><h2>Provider disclosure universe</h2><p class="sub">The investable and formerly-public universe, plus private targets and international comparables. Grade A is the strongest disclosure; grade D means no defensible public curve.</p><input id="universeSearch" aria-label="Search providers" placeholder="Search company, ticker, status or note…" style="width:100%;max-width:430px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:10px"><div class="card table-wrap"><table><thead><tr><th>Network / parent</th><th>Ticker</th><th>Grade</th><th>Aggregate</th><th>Cohort</th><th>Status</th><th>Type</th><th>Assessment</th></tr></thead><tbody>{''.join(universe_rows)}</tbody></table></div></section>
<section><h2>Reusable data</h2><p class="sub">Analysis-ready CSVs keep the issuer-reported values, the aligned denominator, a recalculated audit field, definitions and source lineage.</p><div class="method"><div><b>Keep reported and calculated fields</b>Rounded passings often make the quotient differ from management's stated penetration.</div><div><b>Separate retail from wholesale</b>Chorus and Openreach take-up is network utilization, not one ISP's retail share.</div><div><b>Flag migrations and transactions</b>Optimum's HFC migrations and ownership changes at Frontier, Lumen, CNSL and Ziply create structural breaks.</div></div><div class="downloads"><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/company_universe.csv">Company universe CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/aggregate_observations.csv">Aggregate observations CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/cohort_observations.csv">Cohort observations CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/sources.csv">Source ledger CSV</a><a class="download" href="https://github.com/roblevinson-cloud/Broadbandmap/blob/main/fiber_penetration/data/metric_dictionary.csv">Metric dictionary CSV</a></div></section>
<section id="sources"><h2>Primary-source ledger</h2><p class="sub">Every loaded observation resolves to a company workbook, release or presentation.</p><div class="card table-wrap"><table><thead><tr><th>Network</th><th>Published</th><th>Document</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div></section>
<p class="foot">Built from public company disclosures. These definitions are not GAAP measures and are not fully standardized across issuers. Generated by <code>fiber_penetration/build_dashboard.py</code>.</p></main><script>{script}</script></body></html>"""
    OUTPUT.write_text(page, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    result = build_dashboard()
    print(f"Wrote {result}")
