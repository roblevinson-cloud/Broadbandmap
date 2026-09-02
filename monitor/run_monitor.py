#!/usr/bin/env python3
"""Command-line entry point for the Metronet/Lumos monitor."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from fiber_monitor import (
    CONFIG_PATH,
    collect_sources,
    format_alerts_markdown,
    load_json,
    refresh_brightspeed_footprint,
    render_dashboard,
    write_text_if_changed,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("bootstrap", "collect", "render", "refresh-footprint", "format-alerts"),
    )
    parser.add_argument("--today", default=date.today().isoformat(), help="ISO run date")
    parser.add_argument("--alerts-output", type=Path)
    parser.add_argument("--input", type=Path, help="Alert JSON for format-alerts")
    parser.add_argument("--output", type=Path, help="Markdown output for format-alerts")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_json(CONFIG_PATH)

    if args.command == "format-alerts":
        if not args.input or not args.output:
            raise SystemExit("format-alerts requires --input and --output")
        alert_doc = load_json(args.input)
        write_text_if_changed(
            args.output,
            format_alerts_markdown(alert_doc.get("alerts", []), alert_doc["run_date"]),
        )
        return 0

    if args.command == "render":
        changed, payload = render_dashboard()
        print(json.dumps({"dashboard_changed": changed, "records": len(payload["records"])}))
        return 0

    footprint, footprint_changed = refresh_brightspeed_footprint(
        config, args.today, strict=args.command in {"bootstrap", "refresh-footprint"}
    )
    if args.command == "refresh-footprint":
        print(
            json.dumps(
                {
                    "footprint_changed": footprint_changed,
                    "states": footprint.get("state_count", len(footprint.get("states", {}))),
                    "counties": footprint.get(
                        "county_count",
                        sum(len(row["counties"]) for row in footprint.get("states", {}).values()),
                    ),
                }
            )
        )
        return 0

    discovered, alerts, data_changed = collect_sources(
        config,
        footprint,
        args.today,
        bootstrap=args.command == "bootstrap",
    )
    dashboard_changed, payload = render_dashboard()
    if args.alerts_output:
        write_text_if_changed(
            args.alerts_output,
            json.dumps(
                {"run_date": args.today, "alerts": alerts},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )
    print(
        json.dumps(
            {
                "mode": args.command,
                "footprint_changed": footprint_changed,
                "data_changed": data_changed,
                "dashboard_changed": dashboard_changed,
                "discovered": len(discovered),
                "alerts": len(alerts),
                "records": len(payload["records"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

