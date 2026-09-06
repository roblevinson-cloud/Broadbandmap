#!/usr/bin/env python3
"""Build the BDC Loan Marks database from the SEC's official BDC XBRL packages.

The SEC package is the authoritative source from August 2022 forward.  This
script keeps the as-filed fields, derives comparable loan prices, assigns
deterministic issuer/instrument ids, and writes compact shards for the web
explorer.  Legacy 2018-2022 HTML extractions can be appended by
legacy_edgar_backfill.py and the exports regenerated with --export-only.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Iterator


SEC_BASE = "https://www.sec.gov/files/datastandardsinnovation/data/business-development-company-bdc-data-sets"
USER_AGENT = "BDC Loan Marks research contact@openai.com"
STRUCTURED_PACKAGES = (
    [f"2022q4_bdc.zip"]
    + [f"2023q{q}_bdc.zip" for q in range(1, 5)]
    + [f"2024q{q}_bdc.zip" for q in range(1, 5)]
    + [f"2025q{q}_bdc.zip" for q in range(1, 5)]
    + [f"2026_{m:02d}_bdc.zip" for m in range(1, 8)]
)

DEBT_WORDS = re.compile(
    r"\b(?:loan|debt|note|notes|revolver|revolving|term loan|unitranche|"
    r"debenture|bonds?|first lien|second lien|senior secured|subordinated|"
    r"delayed draw|last out|first out)\b",
    re.I,
)
SECURITY_WORDS = re.compile(
    rf"(?:{DEBT_WORDS.pattern}|common (?:stock|shares?|units?)|preferred "
    r"(?:stock|shares?|units?|equity)|warrants?|equity|membership interest|llc interest)",
    re.I,
)
EQUITY_ONLY_WORDS = re.compile(
    r"\b(?:common (?:stock|shares?|units?)|preferred (?:stock|shares?|units?)|"
    r"warrants?|equity|membership interest|llc interest)\b",
    re.I,
)
FOOTNOTE_SUFFIX = re.compile(r"\s*(?:\(\d+[a-z]?\)|\[[0-9a-z]+\]|\*+)+\s*$", re.I)
LEGAL_SUFFIX = re.compile(
    r"\b(?:incorporated|inc|corp(?:oration)?|company|co|limited|ltd|llc|l\.l\.c|"
    r"lp|l\.p|plc|sa|sarl|holdings?)\b",
    re.I,
)
MEMBER_SUFFIX = re.compile(r"\s*\[(?:domain|member)\]\s*$", re.I)


@dataclass(frozen=True)
class PackageResult:
    name: str
    url: str
    rows: int
    warnings: int
    period_start: str | None
    period_end: str | None


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:20]}"


def clean_member(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("&amp;", "&").replace("\u00a0", " ")
    value = MEMBER_SUFFIX.sub("", value)
    value = re.sub(r"\s+\d+$", "", value)
    return " ".join(value.split()).strip(" ,;|")


def normalize_name(value: str) -> str:
    value = clean_member(value).lower()
    value = re.sub(r"\b(?:f/?k/?a|formerly known as)\b.*$", "", value)
    value = value.replace("&", " and ")
    value = FOOTNOTE_SUFFIX.sub("", value)
    value = LEGAL_SUFFIX.sub(" ", value)
    value = re.sub(r"\b(?:the)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalize_instrument(value: str) -> str:
    value = clean_member(value).lower()
    value = re.sub(r"\([^)]*(?:par|principal|units?|shares?)[^)]*\)", " ", value)
    value = re.sub(r"\b(?:loan|tranche)\s*#?\d+\b", "loan", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text or text in {"—", "–", "-", "nan", "None"}:
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("() ")
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return -result if neg else result


def rate_to_bps(value: str | None) -> float | None:
    number = parse_number(value)
    if number is None:
        return None
    if abs(number) <= 2:
        return round(number * 10000, 2)
    if abs(number) <= 100:
        return round(number * 100, 2)
    return round(number, 2)


def quarter(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year}Q{((parsed.month - 1) // 3) + 1}"


def parse_maturity(explicit: str, instrument: str) -> tuple[str | None, str | None]:
    explicit = clean_member(explicit)
    if explicit:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(explicit, fmt).date().isoformat(), "day"
            except ValueError:
                pass
    match = re.search(r"\bdue\s+(\d{1,2})/(\d{1,2})/(\d{2,4})\b", instrument, re.I)
    if match:
        month, day, year = map(int, match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat(), "day"
        except ValueError:
            pass
    match = re.search(r"\bdue\s+(\d{1,2})/(20\d{2})\b", instrument, re.I)
    if match:
        month, year = map(int, match.groups())
        if 1 <= month <= 12:
            return date(year, month, calendar.monthrange(year, month)[1]).isoformat(), "month"
    match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", instrument)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat(), "day"
        except ValueError:
            pass
    return None, None


def classify_instrument(text: str, principal: float | None) -> tuple[str, str | None, str | None]:
    lower = text.lower()
    if "first lien" in lower or "1st lien" in lower:
        lien = "First lien"
    elif "second lien" in lower or "2nd lien" in lower:
        lien = "Second lien"
    elif "third lien" in lower or "3rd lien" in lower:
        lien = "Third lien"
    elif "unsecured" in lower:
        lien = "Unsecured"
    else:
        lien = None

    if "subordinated" in lower or "sub debt" in lower:
        seniority = "Subordinated"
    elif "senior" in lower or lien in {"First lien", "Second lien", "Third lien"}:
        seniority = "Senior"
    else:
        seniority = None

    if "warrant" in lower:
        kind = "Warrant"
    elif "common stock" in lower or "common share" in lower or "common unit" in lower:
        kind = "Common equity"
    elif "preferred" in lower and any(word in lower for word in ("stock", "share", "unit", "equity")):
        kind = "Preferred equity"
    elif "membership interest" in lower or "llc interest" in lower or re.search(r"\bequity\b", lower):
        kind = "Equity interest"
    elif "revolv" in lower:
        kind = "Revolver"
    elif "delayed draw" in lower or "ddtl" in lower:
        kind = "Delayed-draw term loan"
    elif "unitranche" in lower:
        kind = "Unitranche"
    elif "bond" in lower or "debenture" in lower:
        kind = "Bond"
    elif "note" in lower:
        kind = "Note"
    elif "loan" in lower or principal is not None:
        kind = "Loan"
    else:
        kind = "Debt"
    return kind, seniority, lien


def detect_benchmark(text: str) -> str | None:
    lower = text.lower()
    for needle, label in (
        ("sofr", "SOFR"),
        ("libor", "LIBOR"),
        ("prime", "Prime"),
        ("base rate", "Base rate"),
        ("euribor", "EURIBOR"),
        ("sonia", "SONIA"),
    ):
        if needle in lower:
            return label
    return "Fixed" if re.search(r"\bfixed\b", lower) else None


def load_aliases(path: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not path.exists():
        return aliases
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            alias = normalize_name(row.get("alias", ""))
            canonical = clean_member(row.get("canonical", ""))
            if alias and canonical:
                aliases[alias] = canonical
    return aliases


def canonical_issuer(raw: str, aliases: dict[str, str]) -> tuple[str, str]:
    normalized = normalize_name(raw)
    canonical = aliases.get(normalized, clean_member(raw))
    canonical_norm = normalize_name(canonical)
    return canonical, canonical_norm


def header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def choose_columns(headers: list[str], exact: Iterable[str], tokens: Iterable[tuple[str, ...]]) -> list[str]:
    mapping = {header_key(header): header for header in headers}
    result: list[str] = []
    for candidate in exact:
        if header_key(candidate) in mapping:
            result.append(mapping[header_key(candidate)])
    for token_group in tokens:
        for header in headers:
            key = header_key(header)
            if all(token in key for token in token_group) and header not in result:
                result.append(header)
    return result


def row_value(row: dict[str, str], candidates: list[str]) -> str:
    for candidate in candidates:
        value = row.get(candidate, "")
        if value is not None and str(value).strip():
            return str(value)
    return ""


def split_identifier(row: dict[str, str], columns: dict[str, list[str]]) -> tuple[str, str]:
    explicit_issuer = clean_member(row_value(row, columns["issuer"]))
    identifier = clean_member(row_value(row, columns["identifier"]))
    explicit_instrument = clean_member(row_value(row, columns["instrument"]))
    investment_type = clean_member(row_value(row, columns["investment_type"]))

    issuer = explicit_issuer
    instrument = explicit_instrument
    if normalize_instrument(instrument) in {
        "non affiliated issuer", "non affiliate issuer", "controlled affiliated issuer",
        "affiliated issuer", "debt investments", "equity investments", "investments",
    }:
        instrument = ""
    if identifier:
        pieces = [piece.strip() for piece in re.split(r"\s*\|\s*", identifier) if piece.strip()]
        if len(pieces) >= 2:
            issuer = issuer or pieces[0]
            instrument = instrument or " | ".join(pieces[1:])
        else:
            debt_split = re.split(
                r",\s*(?=(?:first|second|third|senior|junior|subordinated|unitranche|"
                r"one stop|revolving|term|delayed|last out|first out|note|debt|bond|"
                r"common|preferred|warrant|equity|membership|llc interest))",
                identifier,
                maxsplit=1,
                flags=re.I,
            )
            if len(debt_split) == 2:
                issuer = issuer or debt_split[0]
                instrument = instrument or debt_split[1]
            elif not instrument:
                # Many axis members concatenate the legal borrower name and the
                # security description without a delimiter (for example
                # "Acme, Inc. First Lien Term Loan").  Split at the first
                # unambiguous security phrase rather than treating the entire
                # member as both issuer and instrument.
                security = SECURITY_WORDS.search(identifier)
                if security and security.start() > 0:
                    issuer_prefix = identifier[:security.start()].rstrip(" ,;|")
                    if not issuer or normalize_name(issuer) == normalize_name(identifier):
                        issuer = issuer_prefix
                    instrument = identifier[security.start():]
                elif security:
                    instrument = identifier
            issuer = issuer or identifier
    instrument = instrument or investment_type or "Debt investment"
    return clean_member(issuer), clean_member(instrument)


def discover_columns(headers: list[str]) -> dict[str, list[str]]:
    return {
        "issuer": choose_columns(
            headers,
            ["Investment, Issuer Name Axis"],
            [("issuer", "name", "axis"), ("legal", "entity", "axis")],
        ),
        "identifier": choose_columns(
            headers,
            ["Investment, Identifier Axis"],
            [("investment", "identifier", "axis")],
        ),
        "instrument": choose_columns(
            headers,
            ["Investment, Name Axis", "Financial Instrument Axis", "Debt Instrument Axis"],
            [("investment", "name", "axis"), ("financial", "instrument", "axis")],
        ),
        "investment_type": choose_columns(
            headers,
            ["Investment Type Axis"],
            [("investment", "type", "axis")],
        ),
        "industry": choose_columns(
            headers,
            ["Industry Sector Axis"],
            [("industry", "sector", "axis"), ("industry", "axis")],
        ),
        "principal": choose_columns(
            headers,
            ["Investment Owned, Balance, Principal Amount"],
            [("principal", "amount"), ("par", "amount")],
        ),
        "shares": choose_columns(
            headers,
            ["Investment shares"],
            [("investment", "shares"), ("shares",)],
        ),
        "cost": choose_columns(
            headers,
            ["Investment Owned, Cost", "Adjusted cost basis"],
            [("amortized", "cost"), ("cost", "basis"), ("investment", "cost")],
        ),
        "fair": choose_columns(
            headers,
            ["Investment Owned, Fair Value", "Initial fair value of Investment"],
            [("fair", "value", "investment"), ("fair", "value")],
        ),
        "maturity": choose_columns(
            headers,
            ["Investment Maturity Date"],
            [("investment", "maturity", "date"), ("maturity", "date")],
        ),
        "all_in_rate": choose_columns(
            headers,
            ["Investment Interest Rate"],
            [("investment", "interest", "rate")],
        ),
        "spread": choose_columns(
            headers,
            ["Investment, Basis Spread, Variable Rate"],
            [("basis", "spread", "variable")],
        ),
        "cash_rate": choose_columns(
            headers,
            ["Investment, Interest Rate, Paid in Cash"],
            [("interest", "rate", "paid", "cash")],
        ),
        "pik_rate": choose_columns(
            headers,
            ["Investment, Interest Rate, Paid in Kind"],
            [("interest", "rate", "paid", "kind")],
        ),
        "floor": choose_columns(
            headers,
            ["Investment, Interest Rate, Floor"],
            [("interest", "rate", "floor")],
        ),
    }


def download_package(name: str, directory: Path, force: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    if destination.exists() and destination.stat().st_size > 1000 and not force:
        return destination
    request = urllib.request.Request(f"{SEC_BASE}/{name}", headers={"User-Agent": USER_AGENT})
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(destination)
            return destination
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(f"Could not download {name}: {error}") from error
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def create_database(db_path: Path, schema_path: Path, reset: bool) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    return connection


def instrument_identity(
    issuer_id: str,
    instrument: str,
    kind: str,
    seniority: str | None,
    lien: str | None,
    maturity: str | None,
    spread_bps: float | None,
) -> tuple[str, float, str]:
    maturity_month = maturity[:7] if maturity else ""
    spread_bucket = "" if spread_bps is None else str(int(round(spread_bps / 25.0) * 25))
    if maturity_month and (seniority or lien) and spread_bucket:
        key = (issuer_id, kind, seniority, lien, maturity_month, spread_bucket)
        return stable_id("ins", *key), 0.96, "issuer+maturity+structure+spread"
    if maturity_month and (seniority or lien):
        key = (issuer_id, kind, seniority, lien, maturity_month)
        return stable_id("ins", *key), 0.88, "issuer+maturity+structure"
    descriptor = normalize_instrument(instrument)
    if descriptor and spread_bucket:
        key = (issuer_id, kind, seniority, lien, descriptor, spread_bucket)
        # SEC custom members frequently number similar facilities differently
        # across registrants. Without maturity, do not treat these as a
        # cross-holder tranche match even when description and spread agree.
        return stable_id("ins", *key), 0.72, "issuer+description+spread-no-maturity"
    key = (issuer_id, kind, seniority, lien, maturity_month, descriptor)
    confidence = 0.76 if maturity_month else 0.68
    return stable_id("ins", *key), confidence, "issuer+normalized-description"


def iter_soi_rows(path: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        with archive.open("soi.tsv") as raw:
            wrapper = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
            yield from csv.DictReader(wrapper, delimiter="\t")


def ingest_package(
    connection: sqlite3.Connection,
    path: Path,
    aliases: dict[str, str],
) -> PackageResult:
    url = f"{SEC_BASE}/{path.name}"
    iterator = iter_soi_rows(path)
    try:
        first = next(iterator)
    except StopIteration:
        return PackageResult(path.name, url, 0, 1, None, None)
    headers = list(first)
    columns = discover_columns(headers)
    if not columns["fair"] or not columns["cost"]:
        raise RuntimeError(f"{path.name}: could not find cost/fair-value columns")

    warnings = 0
    inserted = 0
    periods: list[str] = []
    seen_positions: set[str] = set()
    issuer_industries: defaultdict[str, Counter[str]] = defaultdict(Counter)

    def rows_with_first() -> Iterator[dict[str, str]]:
        yield first
        yield from iterator

    rows = rows_with_first()
    with connection:
        for row in rows:
            report_date = (row.get("period") or "").strip()
            data_date = (row.get("ddate") or "").strip()
            if not report_date or data_date != report_date:
                continue
            form = (row.get("form") or "").strip()
            if form not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
                continue

            issuer_raw, instrument_raw = split_identifier(row, columns)
            principal = parse_number(row_value(row, columns["principal"]))
            shares = parse_number(row_value(row, columns["shares"]))
            cost = parse_number(row_value(row, columns["cost"]))
            fair = parse_number(row_value(row, columns["fair"]))
            combined_text = " ".join((issuer_raw, instrument_raw, clean_member(row_value(row, columns["investment_type"]))))
            if not issuer_raw or fair is None or (cost is None and principal is None and shares is None):
                continue
            if fair < 0 or (principal is not None and principal < 0) or (cost is not None and cost < 0):
                warnings += 1
                continue

            canonical_name, issuer_norm = canonical_issuer(issuer_raw, aliases)
            if len(issuer_norm) < 2:
                warnings += 1
                continue
            issuer_id = stable_id("iss", issuer_norm)
            industry = clean_member(row_value(row, columns["industry"])) or None
            if industry:
                issuer_industries[issuer_id][industry] += 1
            kind, seniority, lien = classify_instrument(instrument_raw, principal)
            maturity, maturity_precision = parse_maturity(
                row_value(row, columns["maturity"]), instrument_raw
            )
            spread_bps = rate_to_bps(row_value(row, columns["spread"]))
            all_in_bps = rate_to_bps(row_value(row, columns["all_in_rate"]))
            cash_bps = rate_to_bps(row_value(row, columns["cash_rate"]))
            pik_bps = rate_to_bps(row_value(row, columns["pik_rate"]))
            floor_bps = rate_to_bps(row_value(row, columns["floor"]))
            benchmark = detect_benchmark(instrument_raw)
            instrument_id, match_confidence, match_method = instrument_identity(
                issuer_id,
                instrument_raw,
                kind,
                seniority,
                lien,
                maturity,
                spread_bps,
            )
            accession = (row.get("adsh") or "").strip()
            cik = str(row.get("cik") or "").strip().lstrip("0") or "0"
            cik = cik.zfill(10)
            bdc_name = clean_member(row.get("name"))
            filed_date = (row.get("filed") or "").strip()
            filing_url = (row.get("inlineurl") or "").strip()
            price_on_principal = (
                round(100.0 * fair / principal, 4) if principal and principal > 0 else None
            )
            fair_to_cost = round(100.0 * fair / cost, 4) if cost and cost > 0 else None
            position_id = stable_id(
                "pos",
                accession,
                report_date,
                issuer_id,
                instrument_id,
                normalize_instrument(instrument_raw),
                principal,
                cost,
                fair,
            )
            if position_id in seen_positions:
                continue
            seen_positions.add(position_id)
            extraction_confidence = 0.99 if row.get("cstm") == "0" else 0.96

            connection.execute(
                """INSERT INTO bdcs(cik, legal_name, first_observed, last_observed)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(cik) DO UPDATE SET
                     legal_name=excluded.legal_name,
                     first_observed=MIN(COALESCE(bdcs.first_observed, excluded.first_observed), excluded.first_observed),
                     last_observed=MAX(COALESCE(bdcs.last_observed, excluded.last_observed), excluded.last_observed)""",
                (cik, bdc_name, report_date, report_date),
            )
            connection.execute(
                """INSERT OR IGNORE INTO filings(
                     accession,cik,form,filed_date,report_date,filing_url,source_format,
                     extraction_confidence,package_name)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    accession,
                    cik,
                    form,
                    filed_date,
                    report_date,
                    filing_url,
                    "SEC XBRL SOI",
                    extraction_confidence,
                    path.name,
                ),
            )
            connection.execute(
                """INSERT OR IGNORE INTO issuers(id,canonical_name,normalized_name,primary_industry)
                   VALUES(?,?,?,?)""",
                (issuer_id, canonical_name, issuer_norm, industry),
            )
            connection.execute(
                """INSERT OR IGNORE INTO instruments(
                     id,issuer_id,display_name,instrument_type,seniority,lien,maturity_date,
                     maturity_precision,benchmark,spread_bps,match_confidence,match_method)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    instrument_id,
                    issuer_id,
                    instrument_raw,
                    kind,
                    seniority,
                    lien,
                    maturity,
                    maturity_precision,
                    benchmark,
                    spread_bps,
                    match_confidence,
                    match_method,
                ),
            )
            connection.execute(
                """INSERT OR IGNORE INTO positions(
                     id,filing_accession,cik,issuer_id,instrument_id,report_date,calendar_quarter,
                     industry,principal,shares,amortized_cost,fair_value,price_on_principal,fair_value_to_cost,
                     all_in_rate_bps,cash_rate_bps,pik_rate_bps,floor_bps,non_accrual,currency,
                     raw_issuer,raw_instrument,source_format,extraction_confidence,filing_url)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    position_id,
                    accession,
                    cik,
                    issuer_id,
                    instrument_id,
                    report_date,
                    quarter(report_date),
                    industry,
                    principal,
                    shares,
                    cost,
                    fair,
                    price_on_principal,
                    fair_to_cost,
                    all_in_bps,
                    cash_bps,
                    pik_bps,
                    floor_bps,
                    int(bool(re.search(r"non[- ]?accrual", combined_text, re.I))),
                    "USD",
                    issuer_raw,
                    instrument_raw,
                    "SEC XBRL SOI",
                    extraction_confidence,
                    filing_url,
                ),
            )
            inserted += 1
            periods.append(report_date)

        for issuer_id, counts in issuer_industries.items():
            primary = counts.most_common(1)[0][0]
            connection.execute(
                "UPDATE issuers SET primary_industry=COALESCE(primary_industry, ?) WHERE id=?",
                (primary, issuer_id),
            )
        connection.execute(
            """INSERT INTO coverage(package_name,source_url,source_type,period_start,period_end,
                 downloaded_at,row_count,warning_count)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(package_name) DO UPDATE SET
                 downloaded_at=excluded.downloaded_at,row_count=excluded.row_count,
                 warning_count=excluded.warning_count""",
            (
                path.name,
                url,
                "SEC structured BDC data set",
                min(periods) if periods else None,
                max(periods) if periods else None,
                datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                inserted,
                warnings,
            ),
        )
    return PackageResult(
        path.name,
        url,
        inserted,
        warnings,
        min(periods) if periods else None,
        max(periods) if periods else None,
    )


def median(values: Iterable[float | None]) -> float | None:
    cleaned = [value for value in values if value is not None and 0 <= value <= 200]
    return round(statistics.median(cleaned), 2) if cleaned else None


def weighted_mark(rows: Iterable[sqlite3.Row], denominator: str) -> float | None:
    total_fair = 0.0
    total_base = 0.0
    for row in rows:
        base = row[denominator]
        fair = row["fair_value"]
        if base is not None and base > 0 and fair is not None and fair >= 0:
            total_base += base
            total_fair += fair
    return round(100.0 * total_fair / total_base, 2) if total_base else None


def compact_position(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "issuerId": row["issuer_id"],
        "issuer": row["canonical_name"],
        "instrumentId": row["instrument_id"],
        "instrument": row["raw_instrument"],
        "holderCik": row["cik"],
        "holder": row["legal_name"],
        "date": row["report_date"],
        "quarter": row["calendar_quarter"],
        "industry": row["industry"],
        "principal": row["principal"],
        "shares": row["shares"],
        "cost": row["amortized_cost"],
        "fairValue": row["fair_value"],
        "price": row["price_on_principal"],
        "fvCost": row["fair_value_to_cost"],
        "allInBps": row["all_in_rate_bps"],
        "cashBps": row["cash_rate_bps"],
        "pikBps": row["pik_rate_bps"],
        "floorBps": row["floor_bps"],
        "maturity": row["maturity_date"],
        "type": row["instrument_type"],
        "seniority": row["seniority"],
        "lien": row["lien"],
        "benchmark": row["benchmark"],
        "spreadBps": row["spread_bps"],
        "matchConfidence": row["match_confidence"],
        "sourceConfidence": row["extraction_confidence"],
        "sourceFormat": row["source_format"],
        "filingUrl": row["filing_url"],
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


GENERIC_ISSUER_NAMES = re.compile(
    r"^(?:investments?|debt investments?|non controlled|non-affiliated|"
    r"non controlled non affiliated|us corporate debt|u s corporate debt|"
    r"credit fund|co-investment|senior secured loans?|funded investments?|"
    r"investment non affiliated issuer|bank debt)",
    re.I,
)


def issuer_usable(name: str) -> bool:
    """Conservative gate for issuer search and cross-holder matching.

    Some registrants encode an entire schedule row in a custom XBRL member.
    Those rows remain in the analytical database but are not presented as
    normalized issuers until an alias/parser rule resolves them.
    """
    cleaned = " ".join(name.split())
    normalized = normalize_name(cleaned)
    return 2 <= len(cleaned) <= 120 and not GENERIC_ISSUER_NAMES.search(normalized)


def issuer_matchable(name: str) -> bool:
    """Higher precision gate used only for same-tranche comparisons."""
    if not issuer_usable(name):
        return False
    normalized = normalize_name(name)
    if DEBT_WORDS.search(normalized) or " investment" in f" {normalized}":
        return False
    legal_markers = re.findall(r"\b(?:inc|llc|ltd|corp|corporation|lp|plc)\b", name, re.I)
    return len(legal_markers) <= 1


def create_exports(connection: sqlite3.Connection, output: Path, shard_count: int = 128) -> None:
    connection.row_factory = sqlite3.Row
    output.mkdir(parents=True, exist_ok=True)
    (output / "issuers").mkdir(exist_ok=True)

    counts = {}
    for table in ("bdcs", "filings", "issuers", "instruments", "positions"):
        counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    coverage_rows = [dict(row) for row in connection.execute("SELECT * FROM coverage ORDER BY period_start")]
    min_date, max_date = connection.execute(
        "SELECT MIN(report_date), MAX(report_date) FROM positions"
    ).fetchone()

    latest_dates = {
        row["cik"]: row["max_date"]
        for row in connection.execute(
            "SELECT cik, MAX(report_date) AS max_date FROM positions GROUP BY cik"
        )
    }
    latest_rows: list[sqlite3.Row] = []
    for cik, latest_date in latest_dates.items():
        latest_rows.extend(
            connection.execute(
                """SELECT p.*, b.legal_name, i.canonical_name, n.maturity_date,
                          n.instrument_type,n.seniority,n.lien,n.benchmark,n.spread_bps,n.match_confidence
                   FROM positions p
                   JOIN bdcs b ON b.cik=p.cik
                   JOIN issuers i ON i.id=p.issuer_id
                   JOIN instruments n ON n.id=p.instrument_id
                   WHERE p.cik=? AND p.report_date=?""",
                (cik, latest_date),
            ).fetchall()
        )

    trend_rows = connection.execute(
        """SELECT p.*, b.legal_name, i.canonical_name, n.maturity_date,
                  n.instrument_type,n.seniority,n.lien,n.benchmark,n.spread_bps,n.match_confidence
           FROM positions p
           JOIN bdcs b ON b.cik=p.cik
           JOIN issuers i ON i.id=p.issuer_id
           JOIN instruments n ON n.id=p.instrument_id
           ORDER BY p.calendar_quarter"""
    ).fetchall()
    by_quarter: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in trend_rows:
        by_quarter[row["calendar_quarter"]].append(row)
    trend = []
    for qtr, rows in sorted(by_quarter.items()):
        trend.append(
            {
                "quarter": qtr,
                "positions": len(rows),
                "holders": len({row["cik"] for row in rows}),
                "medianPrice": median(row["price_on_principal"] for row in rows),
                "weightedPrice": weighted_mark(rows, "principal"),
                "medianFvCost": median(row["fair_value_to_cost"] for row in rows),
                "weightedFvCost": weighted_mark(rows, "amortized_cost"),
                "pikShare": round(
                    100.0 * sum(1 for row in rows if (row["pik_rate_bps"] or 0) > 0) / len(rows), 2
                ),
                "below90Share": round(
                    100.0
                    * sum(1 for row in rows if row["price_on_principal"] is not None and row["price_on_principal"] < 90)
                    / max(1, sum(1 for row in rows if row["price_on_principal"] is not None)),
                    2,
                ),
            }
        )

    holder_groups: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
    industry_groups: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in latest_rows:
        holder_groups[row["legal_name"]].append(row)
        industry_groups[row["industry"] or "Unclassified"].append(row)

    def group_stats(groups: dict[str, list[sqlite3.Row]], label: str) -> list[dict[str, object]]:
        result = []
        for name, rows in groups.items():
            marks = [row["price_on_principal"] for row in rows if row["price_on_principal"] is not None]
            result.append(
                {
                    label: name,
                    "positions": len(rows),
                    "issuers": len({row["issuer_id"] for row in rows}),
                    "fairValue": round(sum(row["fair_value"] or 0 for row in rows), 2),
                    "medianPrice": median(marks),
                    "weightedPrice": weighted_mark(rows, "principal"),
                    "weightedFvCost": weighted_mark(rows, "amortized_cost"),
                    "below90Share": round(100.0 * sum(1 for mark in marks if mark < 90) / len(marks), 2) if marks else None,
                    "pikShare": round(100.0 * sum(1 for row in rows if (row["pik_rate_bps"] or 0) > 0) / len(rows), 2),
                }
            )
        return sorted(result, key=lambda item: item["fairValue"], reverse=True)

    holders = group_stats(holder_groups, "holder")
    industries = group_stats(industry_groups, "industry")

    as_of = date.fromisoformat(max_date) if max_date else date.today()
    maturity_counts: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"positions": 0, "fairValue": 0.0})
    for row in latest_rows:
        maturity = row["maturity_date"]
        if not maturity:
            bucket = "Not disclosed"
        else:
            days = (date.fromisoformat(maturity) - as_of).days
            if days < 0:
                bucket = "Past due / amended"
            elif days <= 365:
                bucket = "≤1 year"
            elif days <= 730:
                bucket = "1–2 years"
            elif days <= 1095:
                bucket = "2–3 years"
            elif days <= 1825:
                bucket = "3–5 years"
            else:
                bucket = ">5 years"
        maturity_counts[bucket]["positions"] += 1
        maturity_counts[bucket]["fairValue"] += row["fair_value"] or 0
    maturity_order = ["Past due / amended", "≤1 year", "1–2 years", "2–3 years", "3–5 years", ">5 years", "Not disclosed"]
    maturity = [
        {
            "bucket": bucket,
            "positions": int(maturity_counts[bucket]["positions"]),
            "fairValue": round(maturity_counts[bucket]["fairValue"], 2),
        }
        for bucket in maturity_order
        if maturity_counts[bucket]["positions"]
    ]

    aggregated: defaultdict[tuple[str, str, str], dict[str, object]] = defaultdict(
        lambda: {"principal": 0.0, "cost": 0.0, "fair": 0.0, "positions": 0}
    )
    details: dict[tuple[str, str, str], sqlite3.Row] = {}
    for row in trend_rows:
        if row["match_confidence"] < 0.76 or not issuer_matchable(row["canonical_name"]):
            continue
        key = (row["instrument_id"], row["calendar_quarter"], row["cik"])
        agg = aggregated[key]
        agg["principal"] += row["principal"] or 0.0
        agg["cost"] += row["amortized_cost"] or 0.0
        agg["fair"] += row["fair_value"] or 0.0
        agg["positions"] += 1
        details[key] = row
    by_instrument_quarter: defaultdict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for key, agg in aggregated.items():
        instrument_id, qtr, cik = key
        row = details[key]
        principal = float(agg["principal"])
        cost = float(agg["cost"])
        fair = float(agg["fair"])
        mark = 100.0 * fair / principal if principal > 0 else (100.0 * fair / cost if cost > 0 else None)
        if mark is None or not 0 <= mark <= 200:
            continue
        by_instrument_quarter[(instrument_id, qtr)].append(
            {
                "holder": row["legal_name"],
                "cik": cik,
                "mark": round(mark, 2),
                "fairValue": round(fair, 2),
                "basis": "principal" if principal > 0 else "cost",
                "filingUrl": row["filing_url"],
                "issuer": row["canonical_name"],
                "issuerId": row["issuer_id"],
                "instrument": row["raw_instrument"],
                "maturity": row["maturity_date"],
                "matchConfidence": row["match_confidence"],
            }
        )
    divergence_history: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for (instrument_id, qtr), rows in by_instrument_quarter.items():
        if len(rows) < 2:
            continue
        marks = [float(row["mark"]) for row in rows]
        sample = rows[0]
        divergence_history[instrument_id].append(
            {
                "quarter": qtr,
                "range": round(max(marks) - min(marks), 2),
                "median": round(statistics.median(marks), 2),
                "holders": sorted(rows, key=lambda item: item["mark"]),
                "issuer": sample["issuer"],
                "issuerId": sample["issuerId"],
                "instrument": sample["instrument"],
                "maturity": sample["maturity"],
                "matchConfidence": sample["matchConfidence"],
            }
        )
    divergence = []
    for instrument_id, history in divergence_history.items():
        history.sort(key=lambda item: item["quarter"])
        latest = history[-1]
        divergence.append({"instrumentId": instrument_id, **latest, "history": history})
    divergence.sort(key=lambda item: (item["range"], len(item["holders"])), reverse=True)

    issuer_search = []
    issuer_rows = connection.execute(
        """SELECT i.id,i.canonical_name,i.primary_industry,COUNT(*) AS positions,
                  COUNT(DISTINCT p.cik) AS holders,MAX(p.report_date) AS last_report
           FROM issuers i JOIN positions p ON p.issuer_id=i.id
           GROUP BY i.id ORDER BY i.canonical_name"""
    ).fetchall()
    for row in issuer_rows:
        if not issuer_usable(row["canonical_name"]):
            continue
        bucket = int(hashlib.sha1(row["id"].encode()).hexdigest()[:8], 16) % shard_count
        issuer_search.append(
            {
                "id": row["id"],
                "name": row["canonical_name"],
                "industry": row["primary_industry"],
                "positions": row["positions"],
                "holders": row["holders"],
                "lastReport": row["last_report"],
                "bucket": f"{bucket:02d}",
            }
        )

    shard_rows: defaultdict[int, list[dict[str, object]]] = defaultdict(list)
    for row in trend_rows:
        if not issuer_usable(row["canonical_name"]):
            continue
        bucket = int(hashlib.sha1(row["issuer_id"].encode()).hexdigest()[:8], 16) % shard_count
        shard_rows[bucket].append(compact_position(row))
    for bucket in range(shard_count):
        write_json(output / "issuers" / f"{bucket:02d}.json", shard_rows.get(bucket, []))

    current_marks = [row["price_on_principal"] for row in latest_rows if row["price_on_principal"] is not None]
    metadata = {
        "title": "BDC Loan Marks",
        "generatedAt": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "coverageStart": min_date,
        "coverageEnd": max_date,
        "counts": counts,
        "latestSnapshotPositions": len(latest_rows),
        "currentMedianPrice": median(current_marks),
        "crossHolderMatches": len(divergence),
        "searchableIssuers": len(issuer_search),
        "structuredCoverageStarts": "2022-08-01",
        "legacyCoverageTargetStarts": "2018-01-01",
        "shardCount": shard_count,
        "packages": coverage_rows,
        "methodology": {
            "price": "Fair value divided by principal; fair value divided by amortized cost is retained separately.",
            "comparison": "Cross-holder comparisons are grouped by calendar quarter and confidence-scored instrument identity.",
            "source": "SEC BDC Data Sets / EDGAR Schedule of Investments; as filed by each registrant.",
        },
    }
    overview = {
        "trend": trend,
        "holders": holders,
        "industries": industries,
        "maturity": maturity,
        "divergence": divergence[:100],
        "riskPositions": sorted(
            [compact_position(row) for row in latest_rows if row["price_on_principal"] is not None],
            key=lambda item: item["price"],
        )[:250],
    }
    write_json(output / "meta.json", metadata)
    write_json(output / "overview.json", overview)
    write_json(output / "search-index.json", issuer_search)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--db", type=Path, default=Path("data/bdc_loan_marks.sqlite"))
    parser.add_argument("--schema", type=Path, default=Path("data/schema.sql"))
    parser.add_argument("--aliases", type=Path, default=Path("data/issuer_aliases.csv"))
    parser.add_argument("--public-data", type=Path, default=Path("public/data"))
    parser.add_argument("--packages", nargs="*", default=list(STRUCTURED_PACKAGES))
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    arguments = parser.parse_args()

    connection = create_database(arguments.db, arguments.schema, arguments.reset)
    aliases = load_aliases(arguments.aliases)
    results = []
    if not arguments.export_only:
        for index, package in enumerate(arguments.packages, 1):
            print(f"[{index}/{len(arguments.packages)}] {package}", flush=True)
            path = download_package(package, arguments.download_dir, arguments.force_download)
            result = ingest_package(connection, path, aliases)
            results.append(result)
            print(f"  inserted={result.rows:,} warnings={result.warnings:,}", flush=True)
    create_exports(connection, arguments.public_data)
    connection.execute("PRAGMA optimize")
    connection.close()
    if results:
        print(f"Completed {len(results)} packages; {sum(r.rows for r in results):,} position rows parsed.")
    print(f"Database: {arguments.db}")
    print(f"Explorer data: {arguments.public_data}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
