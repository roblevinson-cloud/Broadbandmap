# US Broadband Competition Explorers

Interactive maps and dashboards of US fixed-broadband competition, built from
FCC Broadband Data Collection availability filings (8 semiannual vintages,
June 2022 - December 2025) and Census TIGER/Line 2020 geographies.

Site: https://roblevinson-cloud.github.io/Broadbandmap/

- National provider explorer (70 multi-state providers) + per-provider pages
- 48 state explorers (census tract / block group)
- 40 metro hex maps (H3 res-8)
- Buckeye Broadband Toledo deep-dive (block level)
- Metronet/Lumos forward construction monitor matched to Brightspeed counties
- Residential fiber penetration benchmarks: public-company universe, aggregate
  histories, build-vintage curves, and primary-source ledger

All pages are self-contained static HTML in `docs/`.

The monitor's standard-library collector, evidence ledger, tests, and scheduled
workflow live in [`monitor/`](monitor/).

The penetration benchmark's normalized CSVs, metric dictionary, validation
tests, and dashboard builder live in [`fiber_penetration/`](fiber_penetration/).
Its Shentel panel follows discrete launch-quarter cohorts at observed 3-month
intervals from 0 through 36 months across 15 successive investor decks. The
headline 0/3/6/9/12-month curve is a balanced panel of the same 11 cohorts.
