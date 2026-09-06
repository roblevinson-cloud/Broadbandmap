PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bdcs (
  cik TEXT PRIMARY KEY,
  legal_name TEXT NOT NULL,
  ticker TEXT,
  reporting_file_number TEXT,
  vehicle_type TEXT NOT NULL DEFAULT 'BDC',
  first_observed TEXT,
  last_observed TEXT
);

CREATE TABLE IF NOT EXISTS filings (
  accession TEXT PRIMARY KEY,
  cik TEXT NOT NULL,
  form TEXT NOT NULL,
  filed_date TEXT NOT NULL,
  report_date TEXT NOT NULL,
  filing_url TEXT NOT NULL,
  source_format TEXT NOT NULL,
  extraction_confidence REAL NOT NULL,
  package_name TEXT,
  FOREIGN KEY (cik) REFERENCES bdcs(cik)
);

CREATE TABLE IF NOT EXISTS issuers (
  id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  primary_industry TEXT
);

CREATE TABLE IF NOT EXISTS instruments (
  id TEXT PRIMARY KEY,
  issuer_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  seniority TEXT,
  lien TEXT,
  maturity_date TEXT,
  maturity_precision TEXT,
  benchmark TEXT,
  spread_bps REAL,
  match_confidence REAL NOT NULL,
  match_method TEXT NOT NULL,
  FOREIGN KEY (issuer_id) REFERENCES issuers(id)
);

CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY,
  filing_accession TEXT NOT NULL,
  cik TEXT NOT NULL,
  issuer_id TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  report_date TEXT NOT NULL,
  calendar_quarter TEXT NOT NULL,
  industry TEXT,
  principal REAL,
  shares REAL,
  amortized_cost REAL,
  fair_value REAL NOT NULL,
  price_on_principal REAL,
  fair_value_to_cost REAL,
  all_in_rate_bps REAL,
  cash_rate_bps REAL,
  pik_rate_bps REAL,
  floor_bps REAL,
  non_accrual INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'USD',
  raw_issuer TEXT NOT NULL,
  raw_instrument TEXT NOT NULL,
  source_format TEXT NOT NULL,
  extraction_confidence REAL NOT NULL,
  filing_url TEXT NOT NULL,
  FOREIGN KEY (filing_accession) REFERENCES filings(accession),
  FOREIGN KEY (cik) REFERENCES bdcs(cik),
  FOREIGN KEY (issuer_id) REFERENCES issuers(id),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS coverage (
  package_name TEXT PRIMARY KEY,
  source_url TEXT NOT NULL,
  source_type TEXT NOT NULL,
  period_start TEXT,
  period_end TEXT,
  downloaded_at TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  warning_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bdcs_name ON bdcs(legal_name);
CREATE INDEX IF NOT EXISTS idx_filings_cik_report_date ON filings(cik, report_date);
CREATE INDEX IF NOT EXISTS idx_filings_report_date ON filings(report_date);
CREATE INDEX IF NOT EXISTS idx_issuers_name ON issuers(canonical_name);
CREATE INDEX IF NOT EXISTS idx_instruments_issuer ON instruments(issuer_id);
CREATE INDEX IF NOT EXISTS idx_instruments_maturity ON instruments(maturity_date);
CREATE INDEX IF NOT EXISTS idx_positions_issuer_quarter ON positions(issuer_id, calendar_quarter);
CREATE INDEX IF NOT EXISTS idx_positions_instrument_quarter ON positions(instrument_id, calendar_quarter);
CREATE INDEX IF NOT EXISTS idx_positions_holder_date ON positions(cik, report_date);
CREATE INDEX IF NOT EXISTS idx_positions_industry_date ON positions(industry, report_date);

PRAGMA optimize;
