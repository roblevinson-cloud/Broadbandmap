# BDC Loan Marks

Search SEC-disclosed business development company loan holdings and compare fair-value marks across holders and over time.

## Live explorer

- [Public explorer](https://bdc-loan-marks.rob-levinson.chatgpt.site/)
- [GitHub Pages launch URL](https://roblevinson-cloud.github.io/Broadbandmap/bdc-loan-marks/)

## Current snapshot

- 504,388 debt-position observations
- 1,845 quarterly and annual filings
- 185 BDC reporting vehicles
- 40,914 conservatively normalized, searchable issuer labels
- September 2022 through June 2026

The standardized spine comes from the SEC's official BDC Data Sets. The analytical database retains the filing, holder, issuer label, industry, instrument description, maturity, principal, cost, fair value, rates and direct EDGAR URL. Cross-holder comparisons require a high-confidence match using issuer, maturity, capital structure and spread.

Generated JSON snapshots are intentionally not duplicated in this repository. They contain hundreds of megabytes of as-filed observations and are served by the live application. The ingestion and normalization source is included here so the database can be rebuilt from SEC packages.

## Pricing definitions

- **Price:** fair value divided by disclosed principal.
- **FV / cost:** fair value divided by amortized cost.
- Raw principal and fair value remain in each registrant's as-filed units; ratio analysis is the comparable measure across BDCs.
- Ambiguous issuer or tranche matches are excluded from the default cross-holder table.

## Coverage limitation

The SEC standardized data begins in August 2022. The 2018 through mid-2022 history requires registrant-specific parsing of HTML Schedule of Investments tables and is not represented as complete in the published snapshot.

