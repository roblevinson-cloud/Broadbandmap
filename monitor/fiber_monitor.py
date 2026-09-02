#!/usr/bin/env python3
"""Metronet/Lumos construction monitor for the Brightspeed footprint.

The module deliberately uses only the Python standard library so it can run in a
small GitHub Actions job.  It separates discovery from judgment: automated
records are retained with their confidence and geographic match, while only
high-confidence, direct Brightspeed-county matches qualify for issue alerts.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


REPO_ROOT = Path(__file__).resolve().parents[1]
MONITOR_DIR = REPO_ROOT / "monitor"
DATA_DIR = MONITOR_DIR / "data"
CONFIG_PATH = MONITOR_DIR / "config.json"
FOOTPRINT_PATH = DATA_DIR / "brightspeed_counties.json"
MANUAL_EVIDENCE_PATH = DATA_DIR / "manual_evidence.json"
COLLECTED_EVIDENCE_PATH = DATA_DIR / "collected_evidence.json"
SOURCE_STATE_PATH = DATA_DIR / "source_state.json"
BASELINE_PATH = DATA_DIR / "overlap_baseline.json"
DASHBOARD_PATH = REPO_ROOT / "docs" / "metronet_lumos_monitor.html"

USER_AGENT = (
    "Broadbandmap-Metronet-Lumos-Monitor/1.0 "
    "(+https://github.com/roblevinson-cloud/Broadbandmap)"
)

STATE_ABBREVIATIONS = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
ENCROACHMENT_RANK = {
    "unknown": 0,
    "national_scale": 1,
    "outside_state": 2,
    "state_only": 3,
    "outside_county": 4,
    "confirmed_county": 5,
}


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_text_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def write_json_if_changed(path: Path, value: Any) -> bool:
    return write_text_if_changed(path, json_text(value))


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def canonical_url(value: str) -> str:
    """Remove tracking noise without changing meaningful query parameters."""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return value.strip()
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "msockid"}
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def stable_key(*values: str) -> str:
    joined = "\x1f".join(values).encode("utf-8", errors="replace")
    return hashlib.sha256(joined).hexdigest()[:20]


def fetch_url(url: str, timeout: int = 25) -> tuple[str, str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured public URLs
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace"), response.geturl(), content_type


class PageTextParser(HTMLParser):
    """Small metadata/visible-text extractor; tolerant of municipal HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag.lower() == "meta":
            key = (
                attrs_dict.get("property")
                or attrs_dict.get("name")
                or attrs_dict.get("itemprop")
            ).lower()
            value = attrs_dict.get("content", "")
            if key and value:
                self.meta[key] = normalize_space(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._skip_depth:
            value = normalize_space(data)
            if value:
                self.text_parts.append(value)


def extract_page(raw_html: str) -> dict[str, str]:
    parser = PageTextParser()
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    title = normalize_space(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or " ".join(parser.title_parts)
    )
    description = normalize_space(
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or parser.meta.get("twitter:description")
    )
    published = ""
    for key in (
        "article:published_time",
        "datepublished",
        "date",
        "publishdate",
        "date.created",
    ):
        if parser.meta.get(key):
            published = parser.meta[key]
            break
    if not published:
        match = re.search(
            r'"datePublished"\s*:\s*"([^"\\]+)', raw_html, flags=re.IGNORECASE
        )
        if match:
            published = match.group(1)
    return {
        "title": title,
        "description": description,
        "published": published,
        "text": normalize_space(" ".join(parser.text_parts)),
    }


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def first_child_text(element: ElementTree.Element, names: set[str]) -> str:
    for child in element.iter():
        if child is element:
            continue
        if local_name(child.tag) in names:
            if local_name(child.tag) == "link" and child.attrib.get("href"):
                return normalize_space(child.attrib["href"])
            text = "".join(child.itertext())
            if normalize_space(text):
                return normalize_space(text)
    return ""


def parse_feed(xml_text: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    entries: list[dict[str, str]] = []
    for element in root.iter():
        if local_name(element.tag) not in {"item", "entry"}:
            continue
        link = first_child_text(element, {"link"})
        title = first_child_text(element, {"title"})
        summary = first_child_text(
            element, {"description", "summary", "content", "encoded"}
        )
        published = first_child_text(
            element, {"pubdate", "published", "updated", "date"}
        )
        guid = first_child_text(element, {"guid", "id"})
        if link or guid:
            entries.append(
                {
                    "url": link or guid,
                    "title": title,
                    "description": summary,
                    "published": published,
                    "guid": guid,
                }
            )
    return entries


def parse_sitemap(xml_text: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    rows: list[dict[str, str]] = []
    for element in root.iter():
        if local_name(element.tag) != "url":
            continue
        location = first_child_text(element, {"loc"})
        modified = first_child_text(element, {"lastmod"})
        if location:
            rows.append({"url": location, "published": modified, "title": "", "description": ""})
    return rows


def parse_date(value: str | None, fallback: str) -> str:
    raw = normalize_space(value)
    if not raw:
        return fallback
    iso_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", raw)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(0)).isoformat()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return fallback


def normalize_county(value: str) -> str:
    cleaned = normalize_space(value)
    cleaned = re.sub(r"\s+(County|Parish|Borough)$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.casefold()


def detect_provider(
    text: str,
    url: str,
    config: dict[str, Any],
    provider_hint: str | None = None,
    trusted_hint: bool = False,
) -> str | None:
    lowered = f"{text} {url}".casefold()
    if provider_hint and trusted_hint:
        return provider_hint
    for provider, details in config["providers"].items():
        for alias in details["aliases"]:
            if alias.casefold() in lowered:
                if provider == "Lumos" and alias.casefold() == "lumos networks":
                    if "fiber" not in lowered:
                        continue
                return provider
    return None


def is_relevant(text: str, config: dict[str, Any]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in config["build_terms"])


def detect_stage(text: str, weights: dict[str, float]) -> tuple[str, str, float]:
    lowered = text.casefold()
    if any(
        term in lowered
        for term in (
            "economically unworkable",
            "construction suspended",
            "project suspended",
            "deployment suspended",
            "blocked by",
            "unable to proceed",
            "moratorium prevents",
        )
    ):
        return "blocked", "blocked", float(weights["blocked"])
    if any(
        term in lowered
        for term in (
            "now available",
            "service is available",
            "ready for service",
            "construction complete",
            "construction completed",
            "project complete",
            "order service",
        )
    ):
        return "available", "completed", float(weights["available"])
    if any(
        term in lowered
        for term in (
            "construction has begun",
            "construction began",
            "begins construction",
            "begin construction",
            "started construction",
            "construction underway",
            "under construction",
            "currently installing",
            "actively installing",
            "installation underway",
            "crews are installing",
            "crews will begin",
            "boring operations",
            "fiber installation",
            "construction schedule",
        )
    ):
        return "construction", "active", float(weights["construction"])
    if any(
        term in lowered
        for term in (
            "permit issued",
            "permits issued",
            "permit approved",
            "permits approved",
            "obtained a permit",
        )
    ):
        return "permitted", "active", float(weights["permitted"])
    if any(
        term in lowered
        for term in (
            "right-of-way agreement",
            "right of way agreement",
            "franchise approved",
            "franchise agreement",
            "master license agreement",
            "row agreement",
        )
    ):
        return "row_approved", "planned", float(weights["row_approved"])
    if any(
        term in lowered
        for term in (
            "engineering underway",
            "engineering phase",
            "design work",
            "pole survey",
            "make-ready",
        )
    ):
        return "engineering", "planned", float(weights["engineering"])
    return "announced", "planned", float(weights["announced"])


def detect_states(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for name, abbreviation in STATE_ABBREVIATIONS.items():
        for match in re.finditer(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE):
            matches.append((match.start(), abbreviation))
        # Requiring punctuation or a geography word avoids treating IN/OR/ME as words.
        abbreviation_pattern = rf"(?:,|\(|\bstate of)\s*{abbreviation}\b|\b{abbreviation}\s+(?:County|counties|city|town|township)\b"
        for match in re.finditer(abbreviation_pattern, text, flags=re.IGNORECASE):
            matches.append((match.start(), abbreviation))
    ordered: list[str] = []
    for _, abbreviation in sorted(matches):
        if abbreviation not in ordered:
            ordered.append(abbreviation)
    return ordered


def infer_geography(
    title: str,
    body: str,
    config: dict[str, Any],
    footprint: dict[str, Any],
) -> dict[str, Any]:
    search_text = normalize_space(f"{title} {body[:12000]}")
    detected_states = detect_states(search_text)
    city: str | None = None
    state: str | None = detected_states[0] if detected_states else None
    counties: list[str] = []

    for market in config.get("market_crosswalk", []):
        if not re.search(rf"\b{re.escape(market['city'])}\b", search_text, re.IGNORECASE):
            continue
        if detected_states and market["state"] not in detected_states:
            continue
        city = market["city"]
        state = market["state"]
        counties = list(market["counties"])
        break

    explicit_counties = re.findall(
        r"\b([A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){0,2})\s+(?:County|Parish|Borough)\b",
        search_text,
    )
    for county in explicit_counties:
        county = normalize_space(county)
        if county and county not in counties:
            counties.append(county)

    if state and state in footprint.get("states", {}):
        known_names = footprint["states"][state].get("counties", [])
        for known in known_names:
            pattern = rf"\b{re.escape(known)}\s+(?:County|Parish|Borough)\b"
            if re.search(pattern, search_text, flags=re.IGNORECASE) and known not in counties:
                counties.append(known)

    matched: list[str] = []
    if state and state in footprint.get("states", {}):
        known = {
            normalize_county(item): item
            for item in footprint["states"][state].get("counties", [])
        }
        for county in counties:
            normalized = normalize_county(county)
            if normalized in known and known[normalized] not in matched:
                matched.append(known[normalized])

    if matched:
        encroachment = "confirmed_county"
    elif state and counties and state in footprint.get("states", {}):
        encroachment = "outside_county"
    elif state and state in footprint.get("states", {}):
        encroachment = "state_only"
    elif state:
        encroachment = "outside_state"
    else:
        encroachment = "unknown"

    return {
        "state": state,
        "counties": counties,
        "city": city,
        "matched_brightspeed_counties": matched,
        "encroachment": encroachment,
    }


def extract_targets(text: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    compact = normalize_space(text)
    passings = re.search(
        r"\b(?:approximately\s+|about\s+|nearly\s+|more than\s+|over\s+)?"
        r"\d[\d,.]*(?:\s*(?:million|m))?\s+"
        r"(?:homes(?: and businesses)?|households|addresses|locations|passings)\b",
        compact,
        flags=re.IGNORECASE,
    )
    if passings:
        targets["passings"] = normalize_space(passings.group(0))
    capex = re.search(
        r"\$\s*\d+(?:\.\d+)?\s*(?:billion|million|bn|mm|b|m)\b",
        compact,
        flags=re.IGNORECASE,
    )
    if capex:
        targets["capex"] = normalize_space(capex.group(0))
    miles = re.search(
        r"\b(?:approximately\s+|about\s+|nearly\s+|more than\s+|over\s+)?"
        r"\d[\d,.]*\s+(?:route\s+)?miles\b",
        compact,
        flags=re.IGNORECASE,
    )
    if miles:
        targets["route_miles"] = normalize_space(miles.group(0))
    return targets


def candidate_to_record(
    candidate: dict[str, Any],
    source: dict[str, Any],
    config: dict[str, Any],
    footprint: dict[str, Any],
    detected_on: str,
) -> dict[str, Any] | None:
    url = canonical_url(candidate.get("url", ""))
    if not url:
        return None
    if urlsplit(url).scheme.casefold() not in {"http", "https"}:
        return None
    domain = urlsplit(url).netloc.casefold()
    if any(domain == item or domain.endswith(f".{item}") for item in config["ignored_domains"]):
        return None

    page = candidate.get("page") or {}
    title = normalize_space(page.get("title") or candidate.get("title") or source["name"])
    description = normalize_space(page.get("description") or candidate.get("description"))
    page_text = normalize_space(page.get("text"))
    evidence_text = normalize_space(f"{title} {description} {page_text[:16000]}")
    trusted_hint = source.get("source_type") in {"provider", "municipal"}
    provider = detect_provider(
        evidence_text,
        url,
        config,
        provider_hint=source.get("provider_hint"),
        trusted_hint=trusted_hint,
    )
    if not provider or not is_relevant(evidence_text, config):
        return None

    stage, status, probability = detect_stage(evidence_text, config["stage_weights"])
    geography = infer_geography(title, f"{description} {page_text}", config, footprint)
    published = page.get("published") or candidate.get("published")
    observed_date = parse_date(published, detected_on)
    if observed_date < config.get("minimum_event_date", "0000-00-00"):
        return None
    confidence = "high" if trusted_hint else "medium"
    targets = extract_targets(evidence_text)
    summary = (
        "Automated discovery matched the provider and build-activity rules. "
        "Review the linked source before using the item quantitatively."
    )

    record: dict[str, Any] = {
        "id": f"auto-{stable_key(provider, url)}",
        "provider": provider,
        "title": title or f"{provider} build-activity signal",
        "observed_date": observed_date,
        "detected_on": detected_on,
        **geography,
        "stage": stage,
        "status": status,
        "probability": probability,
        "record_type": "automated_discovery",
        "source_type": source.get("source_type", "search"),
        "source_name": source["name"],
        "source_url": url,
        "evidence_summary": summary,
        "confidence": confidence,
        "targets": targets,
    }
    return record


def parse_brightspeed_counties(raw_html: str) -> dict[str, list[str]]:
    markers = list(
        re.finditer(r'data-state=["\']([^"\']+)["\']', raw_html, flags=re.IGNORECASE)
    )
    states: dict[str, list[str]] = {}
    for index, marker in enumerate(markers):
        state_name = html.unescape(marker.group(1)).strip()
        abbreviation = STATE_ABBREVIATIONS.get(state_name)
        if not abbreviation:
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(raw_html)
        chunk = raw_html[marker.end() : end]
        description = re.search(
            r'class=["\'][^"\']*description-list[^"\']*["\'][^>]*>\s*<p[^>]*>(.*?)</p>',
            chunk,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not description:
            continue
        parser = PageTextParser()
        parser.feed(description.group(1))
        text = normalize_space(" ".join(parser.text_parts))
        if not text:
            text = normalize_space(re.sub(r"<[^>]+>", " ", description.group(1)))
        counties = []
        for item in text.split(","):
            county = normalize_space(item)
            if county and county not in counties:
                counties.append(county)
        if counties:
            states[abbreviation] = counties
    return states


def refresh_brightspeed_footprint(
    config: dict[str, Any], today: str, strict: bool = False
) -> tuple[dict[str, Any], bool]:
    existing = load_json(FOOTPRINT_PATH, default={})
    try:
        raw_html, _, _ = fetch_url(config["brightspeed_footprint_url"])
        parsed = parse_brightspeed_counties(raw_html)
        county_count = sum(len(items) for items in parsed.values())
        if len(parsed) < 18 or county_count < 400:
            raise ValueError(
                f"Brightspeed footprint parse returned {len(parsed)} states/{county_count} counties"
            )
        if existing.get("states") == {
            abbr: {"name": next(name for name, code in STATE_ABBREVIATIONS.items() if code == abbr), "counties": values}
            for abbr, values in parsed.items()
        }:
            return existing, False
        value = {
            "schema_version": 1,
            "source_url": config["brightspeed_footprint_url"],
            "as_of": today,
            "state_count": len(parsed),
            "county_count": county_count,
            "states": {
                abbr: {
                    "name": next(
                        name for name, code in STATE_ABBREVIATIONS.items() if code == abbr
                    ),
                    "counties": counties,
                }
                for abbr, counties in parsed.items()
            },
        }
        changed = write_json_if_changed(FOOTPRINT_PATH, value)
        return value, changed
    except Exception as error:
        if existing.get("states"):
            print(f"warning: retaining prior Brightspeed footprint: {error}", file=sys.stderr)
            return existing, False
        if strict:
            raise
        print(f"warning: Brightspeed footprint unavailable: {error}", file=sys.stderr)
        return {"states": {}}, False


def item_identity(item: dict[str, Any]) -> str:
    url = canonical_url(item.get("url", ""))
    return url or item.get("guid") or stable_key(item.get("title", ""), item.get("published", ""))


def fetch_candidate_page(candidate: dict[str, Any]) -> dict[str, Any]:
    url = candidate.get("url", "")
    if not url or url.lower().endswith(".pdf"):
        return candidate
    try:
        raw, final_url, content_type = fetch_url(url)
        if "html" in content_type.casefold() or "<html" in raw[:1000].casefold():
            candidate = dict(candidate)
            candidate["url"] = final_url
            candidate["page"] = extract_page(raw)
    except Exception as error:
        print(f"warning: could not expand {url}: {error}", file=sys.stderr)
    return candidate


def prefetch_sources(sources: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fetch independent source endpoints concurrently and contain failures."""
    source_list = list(sources)
    results: dict[str, dict[str, Any]] = {}
    if not source_list:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(source_list))) as executor:
        futures = {
            executor.submit(fetch_url, source["url"]): source for source in source_list
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                raw, final_url, content_type = future.result()
                results[source["id"]] = {
                    "raw": raw,
                    "final_url": final_url,
                    "content_type": content_type,
                }
            except Exception as error:
                results[source["id"]] = {"error": str(error)}
    return results


def record_freshness(record: dict[str, Any]) -> tuple[int, str, int, int]:
    curated = 0 if record.get("record_type") == "automated_discovery" else 1
    freshness = (
        record.get("detected_on")
        or record.get("last_verified")
        or record.get("observed_date")
        or "0000-00-00"
    )
    confidence = CONFIDENCE_RANK.get(record.get("confidence", "low"), 0)
    geographic = ENCROACHMENT_RANK.get(record.get("encroachment", "unknown"), 0)
    return curated, freshness, confidence, geographic


def merge_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the freshest/best version of each source URL."""
    by_url: dict[str, dict[str, Any]] = {}
    without_url: list[dict[str, Any]] = []
    for record in records:
        url = canonical_url(record.get("source_url", ""))
        if not url:
            without_url.append(record)
            continue
        candidate = dict(record)
        candidate["source_url"] = url
        previous = by_url.get(url)
        if previous is None or record_freshness(candidate) > record_freshness(previous):
            by_url[url] = candidate
    merged = list(by_url.values()) + without_url
    return sorted(
        merged,
        key=lambda row: (
            row.get("observed_date") or "",
            row.get("detected_on") or row.get("last_verified") or "",
            row.get("provider") or "",
        ),
        reverse=True,
    )


def qualifies_for_alert(record: dict[str, Any]) -> bool:
    return (
        record.get("encroachment") == "confirmed_county"
        and record.get("confidence") == "high"
        and record.get("stage") in {"permitted", "construction", "available"}
        and record.get("status") in {"active", "completed"}
        and float(record.get("probability", 0)) >= 0.85
    )


def is_meaningful_alert_change(
    new_record: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    if not qualifies_for_alert(new_record):
        return False
    if previous is None:
        return True
    if not qualifies_for_alert(previous):
        return True
    return (
        previous.get("stage") != new_record.get("stage")
        or previous.get("status") != new_record.get("status")
        or set(previous.get("matched_brightspeed_counties", []))
        != set(new_record.get("matched_brightspeed_counties", []))
    )


def collect_sources(
    config: dict[str, Any],
    footprint: dict[str, Any],
    today: str,
    bootstrap: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    source_state = load_json(
        SOURCE_STATE_PATH, default={"schema_version": 1, "sources": {}}
    )
    source_state.setdefault("sources", {})
    collected_doc = load_json(
        COLLECTED_EVIDENCE_PATH, default={"schema_version": 1, "records": []}
    )
    collected = list(collected_doc.get("records", []))
    manual = load_json(MANUAL_EVIDENCE_PATH, default={"records": []}).get("records", [])
    previous_records = {
        canonical_url(row.get("source_url", "")): row
        for row in merge_records([*manual, *collected])
        if row.get("source_url")
    }
    discovered: list[dict[str, Any]] = []

    all_sources = [
        *config.get("feed_sources", []),
        *config.get("sitemap_sources", []),
        *config.get("watched_pages", []),
    ]
    fetched_sources = prefetch_sources(all_sources)

    for source in config.get("feed_sources", []):
        fetched = fetched_sources.get(source["id"], {})
        try:
            if fetched.get("error"):
                raise RuntimeError(fetched["error"])
            items = parse_feed(fetched["raw"])
        except Exception as error:
            print(f"warning: feed {source['id']} failed: {error}", file=sys.stderr)
            continue
        state = source_state["sources"].get(source["id"], {})
        prior_seen = set(state.get("seen", []))
        current_keys = [item_identity(item) for item in items]
        if not bootstrap and state:
            for item, key in zip(items, current_keys):
                if key in prior_seen:
                    continue
                # Search results are untrusted discovery metadata.  Do not follow
                # arbitrary result links; reviewers can open the retained source.
                expanded = (
                    item
                    if source.get("source_type") == "search"
                    else fetch_candidate_page(item)
                )
                record = candidate_to_record(expanded, source, config, footprint, today)
                if record:
                    discovered.append(record)
        source_state["sources"][source["id"]] = {
            "kind": "feed",
            "seen": sorted(prior_seen.union(current_keys))[-3000:],
        }

    for source in config.get("sitemap_sources", []):
        fetched = fetched_sources.get(source["id"], {})
        try:
            if fetched.get("error"):
                raise RuntimeError(fetched["error"])
            items = parse_sitemap(fetched["raw"])
        except Exception as error:
            print(f"warning: sitemap {source['id']} failed: {error}", file=sys.stderr)
            continue
        state = source_state["sources"].get(source["id"], {})
        prior_seen = set(state.get("seen", []))
        current_keys = [item_identity(item) for item in items]
        if not bootstrap and state:
            for item, key in zip(items, current_keys):
                if key in prior_seen:
                    continue
                expanded = fetch_candidate_page(item)
                record = candidate_to_record(expanded, source, config, footprint, today)
                if record:
                    discovered.append(record)
        source_state["sources"][source["id"]] = {
            "kind": "sitemap",
            "seen": sorted(prior_seen.union(current_keys))[-5000:],
        }

    for source in config.get("watched_pages", []):
        fetched = fetched_sources.get(source["id"], {})
        try:
            if fetched.get("error"):
                raise RuntimeError(fetched["error"])
            final_url = fetched["final_url"]
            page = extract_page(fetched["raw"])
            page_hash = hashlib.sha256(
                normalize_space(
                    f"{page['title']} {page['description']} {page['text']}"
                ).encode("utf-8")
            ).hexdigest()
        except Exception as error:
            print(f"warning: watched page {source['id']} failed: {error}", file=sys.stderr)
            continue
        state = source_state["sources"].get(source["id"], {})
        if not bootstrap and state and state.get("content_hash") != page_hash:
            item = {"url": final_url, "page": page, "published": page["published"]}
            record = candidate_to_record(item, source, config, footprint, today)
            if record:
                discovered.append(record)
        source_state["sources"][source["id"]] = {
            "kind": "watched_page",
            "content_hash": page_hash,
        }

    alerts: list[dict[str, Any]] = []
    for record in discovered:
        previous = previous_records.get(canonical_url(record["source_url"]))
        if is_meaningful_alert_change(record, previous):
            alerts.append(record)

    if discovered:
        collected = merge_records([*collected, *discovered])[:500]
    state_changed = write_json_if_changed(SOURCE_STATE_PATH, source_state)
    evidence_changed = write_json_if_changed(
        COLLECTED_EVIDENCE_PATH,
        {"schema_version": 1, "records": collected},
    )
    return discovered, alerts, state_changed or evidence_changed


def _dashboard_template() -> str:
    return r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Metronet and Lumos forward construction signals matched to Brightspeed's published county footprint.">
<title>Metronet / Lumos Forward Encroachment Monitor</title>
<style>
:root{--bg:#f4f6f8;--panel:#fff;--ink:#17212b;--muted:#637083;--line:#dce2e8;--navy:#173b57;--blue:#2563eb;--purple:#7047a5;--green:#16745b;--orange:#b55b08;--red:#b42318;--shadow:0 1px 2px rgba(23,33,43,.05)}
@media(prefers-color-scheme:dark){:root{--bg:#0e141a;--panel:#151d25;--ink:#edf2f7;--muted:#9bacbd;--line:#2b3742;--navy:#b7d8ed;--blue:#6ea8fe;--purple:#c1a1e8;--green:#61c7a4;--orange:#f3a657;--red:#ff8178;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI",system-ui,sans-serif;line-height:1.45}.wrap{max-width:1420px;margin:auto;padding:26px 24px 58px}a{color:var(--blue)}.topline{display:flex;gap:12px;justify-content:space-between;align-items:center;margin-bottom:18px}.back{font-size:13px;text-decoration:none;font-weight:650}.asof{font-size:12px;color:var(--muted)}h1{font-size:clamp(25px,3.4vw,39px);line-height:1.08;letter-spacing:-.035em;margin:0;color:var(--navy)}.dek{max-width:900px;color:var(--muted);font-size:14.5px;margin:10px 0 20px}.notice{background:color-mix(in srgb,var(--blue) 7%,var(--panel));border:1px solid color-mix(in srgb,var(--blue) 28%,var(--line));border-radius:10px;padding:11px 14px;font-size:12.5px;margin-bottom:18px}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}.kpi,.panel{background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:12px}.kpi{padding:15px}.kpi .label{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700}.kpi .value{font-size:27px;line-height:1.15;margin-top:5px;font-weight:750;font-variant-numeric:tabular-nums}.kpi .sub{color:var(--muted);font-size:11.5px;margin-top:3px}.grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr);gap:12px}.panel{padding:16px;min-width:0}.panel h2{font-size:15px;margin:0 0 4px}.panel .hint{font-size:11.5px;color:var(--muted);margin:0 0 13px}.baseline{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.provider-card{border:1px solid var(--line);border-radius:10px;padding:12px}.provider-card h3{font-size:14px;margin:0;display:flex;justify-content:space-between;gap:10px}.provider-card.metronet{border-left:4px solid var(--blue)}.provider-card.lumos{border-left:4px solid var(--purple)}.provider-total{font-variant-numeric:tabular-nums}.state-chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}.chip{font-size:10.5px;border-radius:20px;padding:3px 7px;background:var(--bg);border:1px solid var(--line);font-variant-numeric:tabular-nums}.funnel{display:grid;gap:7px}.funnel-row{display:grid;grid-template-columns:95px 1fr 26px;gap:8px;align-items:center;font-size:11.5px}.track{height:8px;background:var(--bg);border-radius:5px;overflow:hidden}.bar{height:100%;background:var(--blue);border-radius:5px}.filters{display:grid;grid-template-columns:1.5fr repeat(4,minmax(120px,.7fr));gap:9px;margin:18px 0 10px}.filters input,.filters select{width:100%;height:38px;padding:0 10px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;font:inherit;font-size:12px}.table-panel{padding:0;overflow:hidden}.table-head{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line)}.table-head h2{margin:0}.showing{font-size:11.5px;color:var(--muted)}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;min-width:1120px}th{text-align:left;padding:9px 11px;background:var(--bg);border-bottom:1px solid var(--line);font-size:10.5px;text-transform:uppercase;letter-spacing:.055em;color:var(--muted);position:sticky;top:0}td{padding:11px;border-bottom:1px solid var(--line);font-size:12px;vertical-align:top}tbody tr:last-child td{border-bottom:0}.date{white-space:nowrap;font-variant-numeric:tabular-nums}.provider{font-weight:750}.provider.Metronet{color:var(--blue)}.provider.Lumos{color:var(--purple)}.market{font-weight:650}.subtext{display:block;color:var(--muted);font-size:10.5px;margin-top:2px}.badge{display:inline-block;border:1px solid var(--line);background:var(--bg);border-radius:20px;padding:3px 7px;font-size:10.5px;white-space:nowrap}.badge.direct{border-color:color-mix(in srgb,var(--red) 40%,var(--line));color:var(--red)}.badge.outside{color:var(--green)}.badge.review{color:var(--orange)}.badge.blocked{color:var(--red)}.summary{max-width:420px}.source-link{font-weight:650;text-decoration:none}.source-link:hover{text-decoration:underline}.empty{text-align:center;color:var(--muted);padding:32px}.method{font-size:12px;color:var(--muted);margin-top:16px}.method strong{color:var(--ink)}.legend{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}.prob{font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap}@media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.filters input{grid-column:1/-1}}@media(max-width:520px){.wrap{padding:18px 13px 42px}.kpis,.baseline{grid-template-columns:1fr}.filters{grid-template-columns:1fr}.filters input{grid-column:auto}.kpi .value{font-size:23px}}
</style>
</head>
<body><main class="wrap">
<div class="topline"><a class="back" href="index.html">← All Broadbandmap explorers</a><span class="asof">Evidence verified through <strong id="updatedThrough">__UPDATED__</strong></span></div>
<h1>Metronet / Lumos Forward Encroachment Monitor</h1>
<p class="dek">An early-warning view of construction, permitting, engineering, and announced expansion—matched against Brightspeed's published county footprint. It complements the lagged FCC availability data by showing where rival fiber may be moving next.</p>
<div class="notice"><strong>Interpretation guardrail:</strong> a county match means potential encroachment, not proof that the same Brightspeed broadband-serviceable locations are affected. Exact threatened passings require a location-level spatial or address qualification match.</div>
<section class="kpis" aria-label="Monitor summary">
  <div class="kpi"><div class="label">Existing gross overlap</div><div class="value" id="grossOverlap">—</div><div class="sub">Metronet + Lumos, FCC BDC 12/31/25</div></div>
  <div class="kpi"><div class="label">Direct active signals</div><div class="value" id="directSignals">—</div><div class="sub">Construction / permitted / available</div></div>
  <div class="kpi"><div class="label">Brightspeed counties flagged</div><div class="value" id="directCounties">—</div><div class="sub">Unique provider-state-county matches</div></div>
  <div class="kpi"><div class="label">Blocked / verify</div><div class="value" id="exceptions">—</div><div class="sub">Signals that should not be treated as active</div></div>
</section>
<section class="grid">
  <div class="panel"><h2>Known fiber overlap at 12/31/25</h2><p class="hint">Gross provider overlaps, not unique Brightspeed locations.</p><div class="baseline" id="baselineCards"></div></div>
  <div class="panel"><h2>Encroachment funnel</h2><p class="hint">Evidence records by stage; the percentage is a stage weight, not a statistical forecast.</p><div class="funnel" id="funnel"></div></div>
</section>
<section class="filters" aria-label="Evidence filters">
  <input id="search" type="search" placeholder="Search city, county, title, or source…" aria-label="Search evidence">
  <select id="providerFilter" aria-label="Provider"><option value="">All providers</option><option>Metronet</option><option>Lumos</option></select>
  <select id="stateFilter" aria-label="State"><option value="">All states</option></select>
  <select id="stageFilter" aria-label="Stage"><option value="">All stages</option></select>
  <select id="matchFilter" aria-label="Brightspeed match"><option value="">All geography</option><option value="confirmed_county">Brightspeed county</option><option value="outside_county">Outside BS county</option><option value="state_only">State only / review</option><option value="national_scale">National scale</option><option value="unknown">Unknown / review</option></select>
</section>
<section class="panel table-panel">
  <div class="table-head"><h2>Evidence ledger</h2><span class="showing" id="showing"></span></div>
  <div class="scroll"><table><thead><tr><th>Date</th><th>Provider</th><th>Market</th><th>Stage / weight</th><th>Brightspeed test</th><th>Evidence</th><th>Source</th></tr></thead><tbody id="evidenceBody"></tbody></table></div>
</section>
<p class="method"><strong>Method.</strong> The scheduled collector watches provider feeds and sitemaps, focused web/news RSS searches, and selected municipal project pages. It extracts build signals, maps named markets to counties, and tests those counties against Brightspeed's published service-area list. Only new high-confidence records at permit, construction, or available stage inside a Brightspeed county qualify for a GitHub issue alert. Automated discoveries remain explicitly labeled for review.</p>
<div class="legend"><span class="badge direct">Brightspeed county</span><span class="badge outside">Outside published county</span><span class="badge review">Needs geographic review</span><span class="badge blocked">Blocked / suspended</span></div>
</main>
<script id="monitorData" type="application/json">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById('monitorData').textContent);
const records=DATA.records;
const fmt=new Intl.NumberFormat('en-US');
const stageLabels={announced:'Announced',engineering:'Engineering',row_approved:'ROW approved',permitted:'Permitted',construction:'Construction',available:'Available',blocked:'Blocked'};
const matchLabels={confirmed_county:'Brightspeed county',outside_county:'Outside BS county',state_only:'Brightspeed state—review',outside_state:'Outside BS state',national_scale:'National scale',unknown:'Unknown—review'};
const matchClass={confirmed_county:'direct',outside_county:'outside',outside_state:'outside',state_only:'review',national_scale:'review',unknown:'review'};
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const gross=Object.values(DATA.baseline.provider_overlap).reduce((sum,row)=>sum+row.total,0);
document.getElementById('grossOverlap').textContent=fmt.format(gross);
const direct=records.filter(r=>r.encroachment==='confirmed_county'&&['permitted','construction','available'].includes(r.stage)&&['active','completed'].includes(r.status));
document.getElementById('directSignals').textContent=fmt.format(direct.length);
const countyKeys=new Set(direct.flatMap(r=>(r.matched_brightspeed_counties||[]).map(c=>`${r.provider}|${r.state}|${c}`)));
document.getElementById('directCounties').textContent=fmt.format(countyKeys.size);
document.getElementById('exceptions').textContent=fmt.format(records.filter(r=>['blocked','needs_verification'].includes(r.status)).length);
document.getElementById('baselineCards').innerHTML=Object.entries(DATA.baseline.provider_overlap).map(([provider,row])=>`<article class="provider-card ${provider.toLowerCase()}"><h3><span>${esc(provider)}</span><span class="provider-total">${fmt.format(row.total)}</span></h3><div class="state-chips">${Object.entries(row.states).sort((a,b)=>b[1]-a[1]).map(([state,count])=>`<span class="chip">${state} ${fmt.format(count)}</span>`).join('')}</div></article>`).join('');
const stages=['announced','engineering','row_approved','permitted','construction','available','blocked'];
const counts=Object.fromEntries(stages.map(stage=>[stage,records.filter(r=>r.stage===stage).length]));
const maxCount=Math.max(1,...Object.values(counts));
document.getElementById('funnel').innerHTML=stages.map(stage=>`<div class="funnel-row"><span>${stageLabels[stage]}</span><div class="track"><div class="bar" style="width:${Math.max(3,counts[stage]/maxCount*100)}%"></div></div><strong>${counts[stage]}</strong></div>`).join('');
const stateFilter=document.getElementById('stateFilter');
[...new Set(records.map(r=>r.state).filter(Boolean))].sort().forEach(state=>stateFilter.insertAdjacentHTML('beforeend',`<option>${esc(state)}</option>`));
const stageFilter=document.getElementById('stageFilter');
stages.forEach(stage=>stageFilter.insertAdjacentHTML('beforeend',`<option value="${stage}">${stageLabels[stage]}</option>`));
const filters=['search','providerFilter','stateFilter','stageFilter','matchFilter'].map(id=>document.getElementById(id));
function marketText(r){const parts=[];if(r.city)parts.push(r.city);if((r.counties||[]).length)parts.push(r.counties.map(c=>`${c} County`).join(', '));if(r.state)parts.push(r.state);return parts.length?parts.join(' · '):'National / not specified'}
function targetsText(r){const values=Object.values(r.targets||{});return values.length?`<span class="subtext">${values.map(esc).join(' · ')}</span>`:''}
function render(){const query=document.getElementById('search').value.trim().toLowerCase();const provider=document.getElementById('providerFilter').value;const state=stateFilter.value;const stage=stageFilter.value;const match=document.getElementById('matchFilter').value;const filtered=records.filter(r=>{const hay=[r.title,r.evidence_summary,r.city,r.state,...(r.counties||[]),r.source_name].join(' ').toLowerCase();return(!query||hay.includes(query))&&(!provider||r.provider===provider)&&(!state||r.state===state)&&(!stage||r.stage===stage)&&(!match||r.encroachment===match)});document.getElementById('showing').textContent=`Showing ${filtered.length} of ${records.length}`;document.getElementById('evidenceBody').innerHTML=filtered.length?filtered.map(r=>`<tr><td class="date">${esc(r.observed_date)}</td><td><span class="provider ${esc(r.provider)}">${esc(r.provider)}</span><span class="subtext">${esc(r.record_type.replaceAll('_',' '))}</span></td><td><span class="market">${esc(marketText(r))}</span></td><td><span class="badge ${r.stage==='blocked'?'blocked':''}">${esc(stageLabels[r.stage]||r.stage)}</span><span class="subtext prob">${Math.round(Number(r.probability)*100)}% stage weight · ${esc(r.status.replaceAll('_',' '))}</span></td><td><span class="badge ${matchClass[r.encroachment]||'review'}">${esc(matchLabels[r.encroachment]||r.encroachment)}</span>${(r.matched_brightspeed_counties||[]).length?`<span class="subtext">Matched: ${r.matched_brightspeed_counties.map(esc).join(', ')}</span>`:''}</td><td class="summary">${esc(r.evidence_summary)}${targetsText(r)}${r.record_type==='automated_discovery'?'<span class="subtext">Automated—review required</span>':''}</td><td><a class="source-link" href="${esc(r.source_url)}" target="_blank" rel="noopener">${esc(r.source_name)}</a><span class="subtext">${esc(r.source_type)} · ${esc(r.confidence)} confidence</span></td></tr>`).join(''):`<tr><td class="empty" colspan="7">No evidence matches these filters.</td></tr>`}
filters.forEach(control=>control.addEventListener(control.tagName==='INPUT'?'input':'change',render));render();
</script>
</body></html>'''


def render_dashboard() -> tuple[bool, dict[str, Any]]:
    manual_doc = load_json(MANUAL_EVIDENCE_PATH)
    collected_doc = load_json(COLLECTED_EVIDENCE_PATH)
    baseline = load_json(BASELINE_PATH)
    records = merge_records(
        [*manual_doc.get("records", []), *collected_doc.get("records", [])]
    )
    verification_dates = [
        row.get("detected_on")
        or row.get("last_verified")
        or row.get("observed_date")
        for row in records
    ]
    updated_through = max((item for item in verification_dates if item), default="—")
    payload = {
        "schema_version": 1,
        "updated_through": updated_through,
        "baseline": baseline,
        "records": records,
    }
    embedded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    output = (
        _dashboard_template()
        .replace("__UPDATED__", html.escape(updated_through))
        .replace("__DATA__", embedded)
    )
    changed = write_text_if_changed(DASHBOARD_PATH, output)
    return changed, payload


def format_alerts_markdown(alerts: list[dict[str, Any]], run_date: str) -> str:
    lines = [
        f"## Metronet / Lumos Brightspeed encroachment alerts — {run_date}",
        "",
        "These are new high-confidence permit, construction, or availability signals in a county listed in Brightspeed's published footprint.",
        "",
    ]
    for record in alerts:
        market = ", ".join(
            [
                *(record.get("matched_brightspeed_counties") or record.get("counties") or []),
                *([record["state"]] if record.get("state") else []),
            ]
        )
        lines.extend(
            [
                f"### {record['provider']} — {record['title']}",
                "",
                f"- **Market:** {market or 'Needs review'}",
                f"- **Stage:** {record['stage'].replace('_', ' ')} ({float(record['probability']):.0%} stage weight)",
                f"- **Observed:** {record['observed_date']}",
                f"- **Source:** [{record['source_name']}]({record['source_url']})",
                "",
            ]
        )
    lines.extend(
        [
            "County matches are early-warning indicators, not proof of location-level overlap. Review the source and qualify addresses before assigning threatened Brightspeed passings.",
            "",
        ]
    )
    return "\n".join(lines)
