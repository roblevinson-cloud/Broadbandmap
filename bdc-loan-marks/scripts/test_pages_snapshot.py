#!/usr/bin/env python3
"""Integration checks for the native Parquet-backed GitHub Pages explorer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    site = args.site.resolve()
    data = site / "data"

    html = (site / "index.html").read_text(encoding="utf-8")
    app = (site / "app.js").read_text(encoding="utf-8")
    assert "chatgpt.site" not in html + app
    assert "iframe" not in html.lower()
    assert "addEventListener('input'" in app
    assert "parquetReadObjects" in app

    summary, search, manifest = (load(data / name) for name in ("summary.json", "search.json", "manifest.json"))
    assert summary["portfolios"] and summary["overview"]["divergence"]
    assert len(search) > 30_000
    avsc = [row for row in search if "avsc" in row["name"].casefold()]
    assert avsc and avsc[0]["name"] == "AVSC Holding Corp."

    issuer_rows = 0
    for relative, info in manifest["files"].items():
        metadata = pq.read_metadata(data / relative)
        assert metadata.num_rows == info["rows"]
        assert (data / relative).stat().st_size == info["bytes"]
        if relative.startswith("issuers/"):
            issuer_rows += metadata.num_rows
    assert issuer_rows == 413_212

    oaktree = pq.read_table(data / "portfolios" / "0001872371.parquet").to_pylist()
    assert len(oaktree) > 250
    assert any(row["type"] == "Revolver" and row["lien"] == "First lien" for row in oaktree)
    assert any(row["type"] == "Bond" for row in oaktree)
    assert any(row["type"] == "Common equity" for row in oaktree)
    assert any(row["type"] == "Warrant" for row in oaktree)
    assert sum(row["maturity"] is not None for row in oaktree) > 250
    assert sum(row["cashBps"] is not None for row in oaktree) > 250

    beech = next(row for row in search if row["name"] == "107-109 Beech OAK22 LLC")
    rows = pq.read_table(data / "issuers" / f"{beech['bucket']}.parquet").to_pylist()
    history = [row for row in rows if row["issuerId"] == beech["id"]]
    assert history and {row["quarter"] for row in history}
    latest = max(history, key=lambda row: row["date"])
    assert latest["type"] == "Revolver"
    assert latest["lien"] == "First lien"
    assert latest["cashBps"] == 1100
    assert latest["maturity"] == "2026-05-27"

    comparison = summary["overview"]["divergence"][0]
    assert len(comparison["holders"]) >= 2
    assert comparison["range"] > 0
    print("PASS: search, portfolio, issuer history, EDGAR detail fields, and cross-holder dispersion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
