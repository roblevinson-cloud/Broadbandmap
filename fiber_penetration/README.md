# Residential fiber penetration benchmarks

This module converts issuer disclosures into comparable residential-fiber
penetration curves. It complements the FCC BDC availability maps in `docs/`:
BDC measures where a provider says service is available, while this dataset
measures how many customers actually subscribe after fiber becomes saleable.

## Files

- `data/company_universe.csv` is the coverage map, including useful public,
  formerly public, transaction-only, and international issuers.
- `data/aggregate_observations.csv` contains time-series passings, connections,
  and penetration observations.
- `data/cohort_observations.csv` contains build-vintage observations at a stated
  number of months after launch.
- `data/shentel_cohort_panel.csv` reconstructs Shentel's repeated quarterly
  launch-vintage charts into a report-quarter × cohort panel at 0, 3, 6, ...,
  36 months. Values are observed, not interpolated.
- `data/shentel_cohort_summary.csv` contains passing-weighted balanced-panel
  curves through 12, 24, and 36 months. Every age within a panel uses the same
  launch cohorts; cohort counts, simple averages, and ranges are retained.
- `data/sources.csv` is the source ledger. Every observation references one row.
- `build_dashboard.py` validates the ledgers and builds
  `docs/fiber_penetration_benchmarks.html`.

## Comparison rules

1. Preserve the reported numerator, denominator, and penetration separately.
2. Do not silently substitute total broadband customers for fiber customers.
3. Keep retail and wholesale networks separate. A wholesale network's take-up
   is useful operationally but is not the same as a retail ISP's market share.
4. Flag migrations. Optimum's fiber additions include migrations from its HFC
   base, so its fiber-network penetration is not a clean greenfield sales curve.
5. Keep aggregate and cohort curves separate. Aggregate penetration can fall
   while the business is healthy because newly built passings dilute the ratio.
6. Retain company definitions and source pages so definition changes can be
   restated later.
7. Use balanced panels for average cohort curves. The primary Shentel 0–12
   month benchmark holds 11 launch cohorts (Q4 2022–Q2 2025) constant at every
   age; longer 24- and 36-month panels use seven and three cohorts respectively.

## Quality grades

| Grade | Meaning |
|---|---|
| A | Aligned fiber numerator and denominator, usually quarterly; cohort detail may also exist |
| B | Usable series with scope/rounding/migration caveats |
| C | Sparse transaction or annual snapshots |
| D | Public parent exists, but disclosed data cannot produce a defensible curve |

## Run

```bash
python -m unittest discover -s fiber_penetration/tests -v
python fiber_penetration/build_dashboard.py
```

The initial checked-in backfill prioritizes the most decision-useful disclosed
curves: Frontier, AT&T, Kinetic/Windstream, Optimum, Shentel, altafiber, and
Hawaiian Telcom. The universe ledger identifies the next backfills and explicit
non-usable names.
