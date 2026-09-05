import { index, integer, real, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const bdcs = sqliteTable(
  "bdcs",
  {
    cik: text("cik").primaryKey(),
    legalName: text("legal_name").notNull(),
    ticker: text("ticker"),
    reportingFileNumber: text("reporting_file_number"),
    vehicleType: text("vehicle_type").notNull().default("BDC"),
    firstObserved: text("first_observed"),
    lastObserved: text("last_observed"),
  },
  (table) => [index("idx_bdcs_name").on(table.legalName)]
);

export const filings = sqliteTable(
  "filings",
  {
    accession: text("accession").primaryKey(),
    cik: text("cik").notNull(),
    form: text("form").notNull(),
    filedDate: text("filed_date").notNull(),
    reportDate: text("report_date").notNull(),
    filingUrl: text("filing_url").notNull(),
    sourceFormat: text("source_format").notNull(),
    extractionConfidence: real("extraction_confidence").notNull(),
    packageName: text("package_name"),
  },
  (table) => [
    index("idx_filings_cik_report_date").on(table.cik, table.reportDate),
    index("idx_filings_report_date").on(table.reportDate),
  ]
);

export const issuers = sqliteTable(
  "issuers",
  {
    id: text("id").primaryKey(),
    canonicalName: text("canonical_name").notNull(),
    normalizedName: text("normalized_name").notNull(),
    primaryIndustry: text("primary_industry"),
  },
  (table) => [
    uniqueIndex("ux_issuers_normalized_name").on(table.normalizedName),
    index("idx_issuers_name").on(table.canonicalName),
  ]
);

export const instruments = sqliteTable(
  "instruments",
  {
    id: text("id").primaryKey(),
    issuerId: text("issuer_id").notNull(),
    displayName: text("display_name").notNull(),
    instrumentType: text("instrument_type").notNull(),
    seniority: text("seniority"),
    lien: text("lien"),
    maturityDate: text("maturity_date"),
    maturityPrecision: text("maturity_precision"),
    benchmark: text("benchmark"),
    spreadBps: real("spread_bps"),
    matchConfidence: real("match_confidence").notNull(),
    matchMethod: text("match_method").notNull(),
  },
  (table) => [
    index("idx_instruments_issuer").on(table.issuerId),
    index("idx_instruments_maturity").on(table.maturityDate),
  ]
);

export const positions = sqliteTable(
  "positions",
  {
    id: text("id").primaryKey(),
    filingAccession: text("filing_accession").notNull(),
    cik: text("cik").notNull(),
    issuerId: text("issuer_id").notNull(),
    instrumentId: text("instrument_id").notNull(),
    reportDate: text("report_date").notNull(),
    calendarQuarter: text("calendar_quarter").notNull(),
    industry: text("industry"),
    principal: real("principal"),
    amortizedCost: real("amortized_cost"),
    fairValue: real("fair_value").notNull(),
    priceOnPrincipal: real("price_on_principal"),
    fairValueToCost: real("fair_value_to_cost"),
    allInRateBps: real("all_in_rate_bps"),
    cashRateBps: real("cash_rate_bps"),
    pikRateBps: real("pik_rate_bps"),
    floorBps: real("floor_bps"),
    nonAccrual: integer("non_accrual", { mode: "boolean" }).notNull().default(false),
    currency: text("currency").notNull().default("USD"),
    rawIssuer: text("raw_issuer").notNull(),
    rawInstrument: text("raw_instrument").notNull(),
    sourceFormat: text("source_format").notNull(),
    extractionConfidence: real("extraction_confidence").notNull(),
    filingUrl: text("filing_url").notNull(),
  },
  (table) => [
    index("idx_positions_issuer_quarter").on(table.issuerId, table.calendarQuarter),
    index("idx_positions_instrument_quarter").on(table.instrumentId, table.calendarQuarter),
    index("idx_positions_holder_date").on(table.cik, table.reportDate),
    index("idx_positions_industry_date").on(table.industry, table.reportDate),
  ]
);

export const coverage = sqliteTable("coverage", {
  packageName: text("package_name").primaryKey(),
  sourceUrl: text("source_url").notNull(),
  sourceType: text("source_type").notNull(),
  periodStart: text("period_start"),
  periodEnd: text("period_end"),
  downloadedAt: text("downloaded_at").notNull(),
  rowCount: integer("row_count").notNull(),
  warningCount: integer("warning_count").notNull().default(0),
});
