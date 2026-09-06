# BDC Loan Marks

Search SEC-disclosed business development company loan holdings and compare fair-value marks across holders and over time.

## Live explorer

- [Native GitHub Pages explorer](https://roblevinson-cloud.github.io/Broadbandmap/bdc-loan-marks/)

## Current snapshot

- 534,624 debt and equity position observations
- 1,859 quarterly and annual filings
- 186 BDC reporting vehicles
- 31,924 searchable issuer labels with usable position data
- September 2022 through June 2026

The standardized spine comes from the SEC's official BDC Data Sets. Filing-table enrichment restores visible but inconsistently tagged maturity, coupon, PIK, rank, lien, security type and share fields. The model includes term loans, delayed draws, revolvers, notes, bonds, common/preferred equity and warrants as well as direct EDGAR links. Cross-holder comparisons require a high-confidence match using issuer, maturity, capital structure and spread.

The build writes one auditable CSV per calendar quarter, then packages the complete history as issuer-bucketed Snappy Parquet plus small latest-portfolio Parquet files. The native GitHub Pages application reads only the selected partition with same-origin HTTP range requests; no iframe or application server is involved. JSON is limited to the search, summary and file-manifest indexes.

## Rebuild the Pages snapshot

```text
python scripts/build_bdc_dataset.py ...
python scripts/build_parquet_snapshot.py --source-data <builder-output> --csv-output <quarterly-csv> --output ../docs/bdc-loan-marks/data --filing-cache <cache>
python scripts/test_pages_snapshot.py --site ../docs/bdc-loan-marks
```

The filing cache prevents repeat EDGAR downloads. `--html-scope latest` is useful for a quick current-portfolio refresh; the default enriches every available filing.

## Pricing definitions

- **Price:** fair value divided by disclosed principal.
- **FV / cost:** fair value divided by amortized cost.
- Raw principal and fair value remain in each registrant's as-filed units; ratio analysis is the comparable measure across BDCs.
- Ambiguous issuer or tranche matches are excluded from the default cross-holder table.

## Coverage limitation

The SEC standardized data begins in August 2022. The 2018 through mid-2022 history requires registrant-specific parsing of HTML Schedule of Investments tables and is not represented as complete in the published snapshot.

