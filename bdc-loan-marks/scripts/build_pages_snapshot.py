#!/usr/bin/env python3
"""Build the native GitHub Pages snapshot and enrich untagged filing-table fields.

The SEC BDC data sets remain the numeric spine.  Inline filing tables are fetched
once per latest filing and used only to backfill visible fields that registrants
did not tag consistently (maturity, security type/rank, coupon, PIK and shares).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

USER_AGENT = "Broadbandmap BDC Loan Marks research roblevinson@gmail.com"
MATURITY_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")
GENERIC_INSTRUMENTS = {
    "debt", "debt investment", "debt investments", "equity investments", "investment",
    "investments", "loan", "non affiliate issuer", "non affiliated issuer",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def clean(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\xa0", " ")
    return " ".join(text.split()).strip(" ,;|")


def normalize(value: object) -> str:
    text = clean(value).lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)|\[[^]]*\]|\*+", " ", text)
    text = re.sub(r"\b(?:incorporated|inc|corp(?:oration)?|company|co|limited|ltd|llc|l\.l\.c|lp|l\.p|plc|sa|sarl|holdings?)\b", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def parse_number(value: object) -> float | None:
    text = clean(value).replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    if not text or text in {"—", "–", "-", "−"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    return -number if negative else number


def parse_rate(value: object) -> float | None:
    number = parse_number(value)
    if number is None:
        return None
    return round(number * (100 if "%" in clean(value) or abs(number) > 2 else 10000), 2)


def parse_date(value: object) -> str | None:
    text = clean(value)
    for pattern in MATURITY_FORMATS:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def direct_filing_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.path == "/ix":
        document = urllib.parse.parse_qs(parsed.query).get("doc", [""])[0]
        if document:
            return urllib.parse.urljoin("https://www.sec.gov", document)
    return url


def filing_key(url: str) -> str:
    match = re.search(r"/data/\d+/(\d{18,20})/", direct_filing_url(url))
    return match.group(1) if match else re.sub(r"\W+", "_", url)[-80:]


def fetch_filing(url: str, cache: Path) -> Path:
    target = cache / f"{filing_key(url)}.html"
    if target.exists() and target.stat().st_size > 1000:
        return target
    request = urllib.request.Request(direct_filing_url(url), headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = response.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            return target
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def expanded_cells(row) -> list[str]:
    values: list[str] = []
    for cell in row.find_all(["th", "td"], recursive=False):
        value = clean(cell.get_text(" ", strip=True))
        values.extend([value] * max(1, int(cell.get("colspan", 1))))
    return values


def header_kind(value: str) -> str | None:
    key = " ".join(re.sub(r"[^a-z0-9]+", " ", clean(value).lower()).split())
    if any(term in key for term in ("portfolio company", "portfolio investment", "issuer name", "company name", "investment name")):
        return "issuer"
    if "industry" in key:
        return "industry"
    if any(term in key for term in ("type of investment", "investment type", "security type", "description of investment")):
        return "instrument"
    if key in {"index", "reference rate", "benchmark"}:
        return "benchmark"
    if "spread" in key or "margin" in key:
        return "spreadBps"
    if "pik" in key or "paid in kind" in key:
        return "pikBps"
    if any(term in key for term in ("cash interest", "interest rate", "coupon", "effective yield")):
        return "cashBps"
    if "maturity" in key or "due date" in key:
        return "maturity"
    if "share" in key or "units" == key:
        return "shares"
    if "principal" in key or "par amount" in key or "face amount" in key:
        return "principal"
    if "cost" in key:
        return "cost"
    if "fair value" in key or "fairvalue" in key:
        return "fairValue"
    return None


def header_groups(values: list[str]) -> dict[str, tuple[int, int]]:
    groups: dict[str, tuple[int, int]] = {}
    index = 0
    while index < len(values):
        end = index + 1
        while end < len(values) and values[end] == values[index]:
            end += 1
        kind = header_kind(values[index])
        if kind and kind not in groups:
            groups[kind] = (index, end)
        index = end
    return groups


def group_text(values: list[str], span: tuple[int, int], numeric: bool = False) -> str:
    start, end = span
    unique = []
    for value in values[start:end]:
        if value and value not in unique:
            unique.append(value)
    if not unique:
        return ""
    if numeric:
        candidates = [value for value in unique if re.search(r"\d", value)]
        return candidates[-1] if candidates else ""
    return unique[0]


def parse_filing_tables(path: Path) -> list[dict[str, object]]:
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    results: list[dict[str, object]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for header_index, header in enumerate(rows):
            header_values = expanded_cells(header)
            groups = header_groups(header_values)
            if "issuer" not in groups or len(groups) < 3:
                continue
            for row in rows[header_index + 1:]:
                values = expanded_cells(row)
                if len(values) < groups["issuer"][1]:
                    continue
                issuer = group_text(values, groups["issuer"])
                if not issuer or header_kind(issuer) or normalize(issuer).startswith(("total ", "subtotal ")):
                    continue
                item: dict[str, object] = {"issuer": issuer}
                for key in ("industry", "instrument", "benchmark"):
                    if key in groups:
                        item[key] = group_text(values, groups[key]) or None
                for key in ("spreadBps", "cashBps", "pikBps"):
                    if key in groups:
                        item[key] = parse_rate(group_text(values, groups[key], numeric=True))
                if "maturity" in groups:
                    item["maturity"] = parse_date(group_text(values, groups["maturity"], numeric=True))
                for key in ("shares", "principal", "cost", "fairValue"):
                    if key in groups:
                        item[key] = parse_number(group_text(values, groups[key], numeric=True))
                if any(item.get(key) is not None for key in ("instrument", "maturity", "cashBps", "pikBps", "shares")):
                    results.append(item)
            break
    return results


def magnitude_close(left: object, right: object) -> bool:
    if left in (None, 0) or right in (None, 0):
        return False
    ratio = abs(float(left) / float(right))
    return any(.985 <= ratio / scale <= 1.015 for scale in (1, 10, 100, 1000, 10000, 1000000))


def classify(text: str) -> tuple[str, str | None, str | None]:
    lower = text.lower()
    lien = "First lien" if re.search(r"\b(?:first|1st) lien\b", lower) else "Second lien" if re.search(r"\b(?:second|2nd) lien\b", lower) else "Third lien" if re.search(r"\b(?:third|3rd) lien\b", lower) else "Unsecured" if "unsecured" in lower else None
    seniority = "Subordinated" if any(term in lower for term in ("subordinated", "sub debt")) else "Senior" if lien or "senior" in lower else None
    if "warrant" in lower: kind = "Warrant"
    elif any(term in lower for term in ("common stock", "common share", "common unit")): kind = "Common equity"
    elif "preferred" in lower and any(term in lower for term in ("stock", "share", "unit", "equity")): kind = "Preferred equity"
    elif any(term in lower for term in ("membership interest", "llc interest", "equity")): kind = "Equity interest"
    elif "revolv" in lower: kind = "Revolver"
    elif "delayed draw" in lower or "ddtl" in lower: kind = "Delayed-draw term loan"
    elif "unitranche" in lower: kind = "Unitranche"
    elif "bond" in lower or "debenture" in lower: kind = "Bond"
    elif "note" in lower: kind = "Note"
    elif "term loan" in lower: kind = "Term loan"
    elif "loan" in lower: kind = "Loan"
    else: kind = "Other investment"
    return kind, seniority, lien


def match_score(position: dict, candidate: dict) -> float:
    left, right = normalize(position.get("issuer")), normalize(candidate.get("issuer"))
    if not left or not right:
        return -1
    score = 10 if left == right else 7 if left in right or right in left else -10
    instrument, candidate_instrument = normalize(position.get("instrument")), normalize(candidate.get("instrument"))
    if instrument and candidate_instrument:
        overlap = len(set(instrument.split()) & set(candidate_instrument.split()))
        score += min(5, overlap)
    for key in ("fairValue", "principal", "shares"):
        if magnitude_close(position.get(key), candidate.get(key)):
            score += 3
    for position_key, candidate_key in (("allInBps", "cashBps"), ("cashBps", "cashBps"), ("spreadBps", "spreadBps")):
        if position.get(position_key) is not None and candidate.get(candidate_key) is not None and abs(position[position_key] - candidate[candidate_key]) <= 5:
            score += 4
    if position.get("maturity") and position["maturity"] == candidate.get("maturity"):
        score += 4
    return score


def enrich_positions(positions: list[dict], candidates: list[dict]) -> int:
    used: set[int] = set()
    enriched = 0
    exact: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        key = normalize(candidate.get("issuer"))
        if key:
            exact[key].append((index, candidate))
    for position in positions:
        key = normalize(position.get("issuer"))
        pool = exact.get(key)
        if not pool:
            # Fuzzy fallback is deliberately local to plausible borrower names;
            # the normal case is an O(1) exact lookup rather than an O(n²) scan.
            pool = [
                (index, candidate) for candidate_key, members in exact.items()
                if key and (key in candidate_key or candidate_key in key)
                for index, candidate in members
            ]
        ranked = sorted(
            ((match_score(position, candidate), index, candidate) for index, candidate in pool if index not in used),
            reverse=True,
            key=lambda item: item[0],
        )
        # Issuer identity alone is not enough when a borrower has several
        # facilities or equity positions. Require at least one corroborating
        # amount, rate, maturity or instrument token.
        if not ranked or ranked[0][0] < 13:
            continue
        _, index, candidate = ranked[0]
        used.add(index)
        changed = False
        for key in ("industry", "maturity", "benchmark", "spreadBps", "cashBps", "pikBps", "shares"):
            if position.get(key) is None and candidate.get(key) is not None:
                position[key] = candidate[key]
                changed = True
        candidate_instrument = clean(candidate.get("instrument"))
        if candidate_instrument:
            current = normalize(position.get("instrument"))
            if not current or current in GENERIC_INSTRUMENTS:
                position["instrument"] = candidate_instrument
                changed = True
            # Classify the instrument actually retained on the position.  A
            # borrower can have several facilities; a high-confidence amount
            # match may backfill maturity/rate from a nearby row, but must not
            # turn an already-labelled revolver into a term loan.
            kind, seniority, lien = classify(position.get("instrument") or candidate_instrument)
            for key, value in (("type", kind), ("seniority", seniority), ("lien", lien)):
                generic = key != "type" or position.get(key) in (None, "", "Debt", "Loan", "Other investment")
                if value and generic and position.get(key) != value:
                    position[key] = value
                    changed = True
        if changed:
            position["detailSource"] = "SEC filing table"
            enriched += 1
    return enriched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-data", type=Path, default=Path("public/data"))
    parser.add_argument("--output", type=Path, default=Path("../docs/bdc-loan-marks/data"))
    parser.add_argument("--filing-cache", type=Path, default=Path("data/filings"))
    parser.add_argument("--skip-html", action="store_true")
    args = parser.parse_args()

    meta, overview, search = (load(args.source_data / name) for name in ("meta.json", "overview.json", "search-index.json"))
    shard_paths = sorted((args.source_data / "issuers").glob("*.json"), key=lambda path: int(path.stem))
    latest_date: dict[str, str] = {}
    for path in shard_paths:
        for row in load(path):
            cik = row["holderCik"]
            latest_date[cik] = max(latest_date.get(cik, ""), row["date"])

    portfolios: dict[str, list[dict]] = defaultdict(list)
    all_rows: list[dict] = []
    for path in shard_paths:
        for row in load(path):
            if row["date"] == latest_date[row["holderCik"]]:
                portfolios[row["holderCik"]].append(row)
                all_rows.append(row)

    scrape_stats = {"filings": 0, "tableRows": 0, "positionsEnriched": 0, "failures": 0}
    if not args.skip_html:
        by_url: dict[str, list[dict]] = defaultdict(list)
        for row in all_rows:
            if row.get("filingUrl"):
                by_url[row["filingUrl"]].append(row)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_filing, url, args.filing_cache): url for url in by_url}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    candidates = parse_filing_tables(future.result())
                    scrape_stats["filings"] += 1
                    scrape_stats["tableRows"] += len(candidates)
                    scrape_stats["positionsEnriched"] += enrich_positions(by_url[url], candidates)
                except Exception as error:
                    scrape_stats["failures"] += 1
                    print(f"warning: {url}: {error}")

    active_ids = {row["issuerId"] for row in all_rows}
    holdings: dict[str, list[dict]] = defaultdict(list)
    for row in all_rows:
        holdings[row["issuerId"]].append(row)
    history: dict[str, dict[tuple[str, str], dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: {"price": [], "fvCost": []}))
    for path in shard_paths:
        for row in load(path):
            if row["issuerId"] not in active_ids:
                continue
            values = history[row["issuerId"]][(row["quarter"], row["holder"])]
            for metric in ("price", "fvCost"):
                value = row.get(metric)
                if value is not None and 0 <= value <= 200:
                    values[metric].append(value)

    args.output.mkdir(parents=True, exist_ok=True)
    holder_summary = {row["holder"]: row for row in overview["holders"]}
    portfolio_index = []
    for cik, rows in portfolios.items():
        name = rows[0]["holder"]
        summary = holder_summary.get(name, {})
        portfolio_index.append({
            "cik": cik, "holder": name, "date": latest_date[cik], "positions": len(rows),
            "issuers": len({row["issuerId"] for row in rows}),
            "fairValue": sum(row.get("fairValue", 0) or 0 for row in rows),
            "weightedPrice": summary.get("weightedPrice"), "weightedFvCost": summary.get("weightedFvCost"),
            "below90Share": summary.get("below90Share"), "pikShare": summary.get("pikShare"),
        })
        write(args.output / "portfolios" / f"{cik}.json", rows)
    portfolio_index.sort(key=lambda row: row["fairValue"], reverse=True)

    search_active = sorted((row for row in search if row["id"] in active_ids), key=lambda row: row["name"].casefold())
    search_by_id = {row["id"]: row for row in search_active}
    issuer_buckets: dict[str, dict[str, dict]] = defaultdict(dict)
    for issuer_id, issuer_holdings in holdings.items():
        points = []
        for (quarter, holder), values in sorted(history[issuer_id].items()):
            points.append({
                "quarter": quarter, "holder": holder,
                "price": round(statistics.median(values["price"]), 2) if values["price"] else None,
                "fvCost": round(statistics.median(values["fvCost"]), 2) if values["fvCost"] else None,
            })
        bucket = str(search_by_id[issuer_id]["bucket"])
        issuer_buckets[bucket][issuer_id] = {"holdings": issuer_holdings, "history": points}
    for bucket, detail in issuer_buckets.items():
        write(args.output / "issuers" / f"{int(bucket):02d}.json", detail)

    meta["searchableIssuers"] = len(search_active)
    meta["latestSnapshotPositions"] = len(all_rows)
    meta["filingTableEnrichment"] = scrape_stats
    write(args.output / "summary.json", {"meta": meta, "overview": overview, "portfolios": portfolio_index})
    write(args.output / "search.json", search_active)
    print(json.dumps({"portfolios": len(portfolios), "positions": len(all_rows), "issuers": len(search_active), **scrape_stats}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
