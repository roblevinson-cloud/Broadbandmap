# Metronet / Lumos forward encroachment monitor

This monitor is an early-warning complement to the FCC BDC overlap snapshot. It
collects public construction signals for Metronet and Lumos, normalizes each item
into a small evidence ledger, and tests named counties against Brightspeed's
published service-area list.

The generated dashboard is published at
[`docs/metronet_lumos_monitor.html`](../docs/metronet_lumos_monitor.html).

## What runs automatically

The weekday GitHub Actions job:

1. refreshes the official Brightspeed state/county footprint;
2. checks the Lumos press-release RSS feed and Metronet newsroom sitemap;
3. checks focused Bing and Google News RSS searches;
4. checks selected municipal/provider project pages for content changes;
5. classifies provider, market, evidence stage, and Brightspeed county match;
6. updates the evidence ledger and rebuilds the static dashboard; and
7. opens one issue digest when a *new* high-confidence permit, construction, or
   availability signal matches a Brightspeed county.

The initial source state is bootstrapped, so enabling the workflow does not open
issues for all historical search results.

## Evidence stages

| Stage | Weight | Typical evidence |
|---|---:|---|
| Announced | 35% | Market or investment announcement |
| Engineering | 60% | Design, pole survey, or make-ready work |
| ROW approved | 80% | Franchise, ROW, or master license approved |
| Permitted | 90% | Construction permit issued |
| Construction | 95% | Boring, conduit, or fiber installation underway |
| Available | 100% | Service orderable / construction complete |
| Blocked | 20% | Suspended or economically unworkable build |

Weights encode evidence maturity. They are not calibrated probabilities and
must not be presented as a statistical forecast.

## Run locally

Python 3.11+ is sufficient; there are no third-party dependencies.

```bash
python -m unittest discover -s monitor/tests -v
python monitor/run_monitor.py bootstrap
python monitor/run_monitor.py collect --alerts-output /tmp/monitor-alerts.json
python monitor/run_monitor.py render
```

`bootstrap` records the items currently exposed by feeds, sitemaps, and watched
pages without adding automated evidence or emitting alerts. Use it when adding a
new source that already has a long history.

## Add a source or market

- Add feeds, sitemaps, watched pages, provider aliases, or city-to-county mappings
  in [`config.json`](config.json).
- Add researched, human-reviewed evidence to
  [`data/manual_evidence.json`](data/manual_evidence.json).
- Do not manually edit `data/source_state.json`,
  `data/collected_evidence.json`, `data/brightspeed_counties.json`, or the
  generated dashboard.

Every evidence record retains its source URL, source type, confidence, stage,
status, and geographic match. Search discoveries are labeled as automated and
do not trigger alerts until the source is high-confidence.

## Geographic limitation

`confirmed_county` means that the activity is in a county Brightspeed publicly
lists—not that Metronet/Lumos and Brightspeed serve the same exact addresses.
Turning county signals into threatened passings requires BSL-level geometry or
provider address qualification. Existing provider overlap totals are gross:
the same Brightspeed location may appear once under each rival.

