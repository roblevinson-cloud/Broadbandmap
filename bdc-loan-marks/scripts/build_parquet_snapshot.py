#!/usr/bin/env python3
"""Build the GitHub Pages data lake: quarterly CSV -> partitioned Parquet.

CSV files are the transparent staging layer.  The site itself reads compact,
same-origin Parquet partitions using HTTP range requests.  SEC filing tables
are fetched once and cached, then used to enrich fields that Inline XBRL often
leaves untagged (maturity, coupon, security type, rank and shares).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from build_pages_snapshot import classify, enrich_positions, fetch_filing, load, parse_filing_tables, write


FIELDS = (
    "id", "issuerId", "issuer", "instrumentId", "instrument", "holderCik", "holder",
    "date", "quarter", "industry", "principal", "shares", "cost", "fairValue", "price",
    "fvCost", "allInBps", "cashBps", "pikBps", "floorBps", "maturity", "type",
    "seniority", "lien", "benchmark", "spreadBps", "matchConfidence", "sourceConfidence",
    "sourceFormat", "detailSource", "filingUrl",
)

STRING_FIELDS = {
    "id", "issuerId", "issuer", "instrumentId", "instrument", "holderCik", "holder", "date",
    "quarter", "industry", "maturity", "type", "seniority", "lien", "benchmark",
    "sourceFormat", "detailSource", "filingUrl",
}

SCHEMA = pa.schema([
    pa.field(name, pa.string() if name in STRING_FIELDS else pa.float64()) for name in FIELDS
])


def clean_directory(path: Path, suffixes: tuple[str, ...]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_file() and child.suffix in suffixes:
            child.unlink()


def normalized_row(row: dict) -> dict:
    result = {name: row.get(name) for name in FIELDS}
    kind, seniority, lien = classify(result.get("instrument") or "")
    if kind != "Other investment":
        result["type"] = kind
    if seniority:
        result["seniority"] = seniority
    if lien:
        result["lien"] = lien
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            row = {}
            for name in FIELDS:
                value = source.get(name, "")
                if name in STRING_FIELDS:
                    row[name] = value or None
                else:
                    try:
                        row[name] = float(value) if value != "" else None
                    except ValueError:
                        row[name] = None
            rows.append(row)
    return rows


def write_parquet(path: Path, rows: list[dict], row_group_size: int = 4096) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([normalized_row(row) for row in rows], schema=SCHEMA)
    pq.write_table(
        table,
        path,
        compression="snappy",
        use_dictionary=True,
        write_statistics=True,
        row_group_size=row_group_size,
        version="2.6",
    )


def enrich(rows: list[dict], cache: Path, workers: int, scope: str) -> dict[str, int]:
    latest: dict[str, str] = {}
    for row in rows:
        latest[row["holderCik"]] = max(latest.get(row["holderCik"], ""), row["date"])
    selected = rows if scope == "all" else [row for row in rows if row["date"] == latest[row["holderCik"]]]
    by_url: dict[str, list[dict]] = defaultdict(list)
    for row in selected:
        if row.get("filingUrl"):
            by_url[row["filingUrl"]].append(row)
    stats = {"filings": 0, "tableRows": 0, "positionsEnriched": 0, "failures": 0}
    if scope == "none":
        return stats
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_filing, url, cache): url for url in by_url}
        for count, future in enumerate(as_completed(futures), 1):
            url = futures[future]
            try:
                candidates = parse_filing_tables(future.result())
                stats["filings"] += 1
                stats["tableRows"] += len(candidates)
                stats["positionsEnriched"] += enrich_positions(by_url[url], candidates)
            except Exception as error:
                stats["failures"] += 1
                print(f"warning: {url}: {error}")
            if count % 50 == 0 or count == len(futures):
                print(f"enriched {count}/{len(futures)} filings")
    return stats


def due_bucket(maturity: str | None, coverage_end: str) -> str:
    if not maturity:
        return "Not disclosed"
    from datetime import date
    years = (date.fromisoformat(maturity) - date.fromisoformat(coverage_end)).days / 365.25
    if years <= 0: return "Past due / amended"
    if years <= 1: return "≤1 year"
    if years <= 2: return "1–2 years"
    if years <= 3: return "2–3 years"
    if years <= 5: return "3–5 years"
    return ">5 years"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--csv-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--filing-cache", type=Path, required=True)
    parser.add_argument("--html-scope", choices=("all", "latest", "none"), default="all")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--reuse-csv", action="store_true", help="Package an already-enriched CSV staging directory")
    args = parser.parse_args()

    meta, overview, search = (load(args.source_data / name) for name in ("meta.json", "overview.json", "search-index.json"))
    if args.reuse_csv:
        scrape_stats = {"filings": 0, "tableRows": 0, "positionsEnriched": 0, "failures": 0, "reusedCsv": True}
    else:
        rows: list[dict] = []
        for shard in sorted((args.source_data / "issuers").glob("*.json"), key=lambda path: int(path.stem)):
            rows.extend(load(shard))
        rows.sort(key=lambda row: (row["quarter"], row["holderCik"], row["issuerId"], row["instrumentId"]))
        scrape_stats = enrich(rows, args.filing_cache, args.workers, args.html_scope)

        # A transparent quarter-by-quarter staging layer.  Parquet is always
        # built from these CSVs so the two representations cannot diverge.
        clean_directory(args.csv_output, (".csv",))
        by_quarter: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_quarter[row["quarter"]].append(normalized_row(row))
        for quarter, quarter_rows in sorted(by_quarter.items()):
            write_csv(args.csv_output / f"{quarter}.csv", quarter_rows)

    csv_rows: list[dict] = []
    for path in sorted(args.csv_output.glob("*.csv")):
        csv_rows.extend(read_csv(path))
    if args.reuse_csv:
        enriched_rows = [row for row in csv_rows if row.get("detailSource")]
        scrape_stats["filings"] = len({row["filingUrl"] for row in enriched_rows if row.get("filingUrl")})
        scrape_stats["positionsEnriched"] = len(enriched_rows)
    by_quarter = defaultdict(list)
    for row in csv_rows:
        by_quarter[row["quarter"]].append(row)

    search_by_id = {row["id"]: row for row in search}
    latest_date: dict[str, str] = {}
    for row in csv_rows:
        latest_date[row["holderCik"]] = max(latest_date.get(row["holderCik"], ""), row["date"])
    latest_rows = [row for row in csv_rows if row["date"] == latest_date[row["holderCik"]]]

    issuer_dir = args.output / "issuers"
    portfolio_dir = args.output / "portfolios"
    clean_directory(issuer_dir, (".json", ".parquet"))
    clean_directory(portfolio_dir, (".json", ".parquet"))

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for row in csv_rows:
        item = search_by_id.get(row["issuerId"])
        if item:
            by_bucket[str(item["bucket"]).zfill(2)].append(row)
    for bucket, bucket_rows in sorted(by_bucket.items()):
        bucket_rows.sort(key=lambda row: (row["issuerId"], row["date"], row["holderCik"], row["instrumentId"]))
        write_parquet(issuer_dir / f"{bucket}.parquet", bucket_rows)

    by_holder: dict[str, list[dict]] = defaultdict(list)
    for row in latest_rows:
        by_holder[row["holderCik"]].append(row)
    holder_metrics = {row["holder"]: row for row in overview["holders"]}
    portfolios = []
    for cik, holder_rows in by_holder.items():
        holder_rows.sort(key=lambda row: (-(row.get("fairValue") or 0), row["issuer"]))
        write_parquet(portfolio_dir / f"{cik}.parquet", holder_rows, 1024)
        name = holder_rows[0]["holder"]
        metrics = holder_metrics.get(name, {})
        priced = [(row["price"], row["principal"]) for row in holder_rows if row.get("price") is not None and (row.get("principal") or 0) > 0]
        weighted_price = sum(price * principal for price, principal in priced) / sum(principal for _, principal in priced) if priced else None
        portfolios.append({
            "cik": cik, "holder": name, "date": latest_date[cik], "positions": len(holder_rows),
            "issuers": len({row["issuerId"] for row in holder_rows}),
            "fairValue": sum(row.get("fairValue") or 0 for row in holder_rows),
            "weightedPrice": weighted_price if weighted_price is not None else metrics.get("weightedPrice"),
            "weightedFvCost": metrics.get("weightedFvCost"), "below90Share": metrics.get("below90Share"),
            "pikShare": metrics.get("pikShare"),
        })
    portfolios.sort(key=lambda row: row["fairValue"], reverse=True)

    active_ids = {row["issuerId"] for row in latest_rows}
    present_ids = {row["issuerId"] for row in csv_rows}
    search_rows = [row for row in search if row["id"] in present_ids]
    search_rows.sort(key=lambda row: row["name"].casefold())
    maturity_counts: dict[str, int] = defaultdict(int)
    for row in latest_rows:
        maturity_counts[due_bucket(row.get("maturity"), meta["coverageEnd"])] += 1
    overview["maturity"] = [{"bucket": key, "positions": value} for key, value in maturity_counts.items()]

    meta["searchableIssuers"] = len(search_rows)
    meta["activeIssuers"] = len(active_ids)
    meta["latestSnapshotPositions"] = len(latest_rows)
    meta["filingTableEnrichment"] = scrape_stats
    meta["storage"] = "quarterly CSV staging; partitioned Snappy Parquet delivery"
    write(args.output / "summary.json", {"meta": meta, "overview": overview, "portfolios": portfolios})
    write(args.output / "search.json", search_rows)

    manifest = {"version": 1, "format": "parquet", "compression": "snappy", "files": {}}
    for path in sorted(list(issuer_dir.glob("*.parquet")) + list(portfolio_dir.glob("*.parquet"))):
        key = path.relative_to(args.output).as_posix()
        manifest["files"][key] = {"bytes": path.stat().st_size, "rows": pq.read_metadata(path).num_rows}
    write(args.output / "manifest.json", manifest)
    print(json.dumps({
        "quarters": len(by_quarter), "rows": len(csv_rows), "portfolios": len(portfolios),
        "issuers": len(search_rows), "parquetBytes": sum(item["bytes"] for item in manifest["files"].values()),
        **scrape_stats,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
