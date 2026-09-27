-- Migration 0005: research subsystem storage (D-0019, D-0046).
--
-- Advisory-only tables. Nothing in the trading path reads these rows;
-- deleting them at any time is safe. The trading engine is unaffected.

CREATE TABLE research_reports (
    report_id            TEXT PRIMARY KEY,
    operation            TEXT NOT NULL CHECK (operation IN (
                             'researchMarket', 'researchStock',
                             'researchNews', 'researchStrategy',
                             'researchRisk', 'researchBacktestingMethod'
                         )),
    question             TEXT NOT NULL,
    summary              TEXT NOT NULL,
    findings_json        TEXT NOT NULL,
    sources_json         TEXT NOT NULL,
    risks_json           TEXT NOT NULL,
    confidence           TEXT NOT NULL CHECK (confidence IN ('LOW','MEDIUM','HIGH')),
    recommendation       TEXT,
    suggested_experiment TEXT,
    model_served         TEXT NOT NULL,
    generated_at         TEXT NOT NULL,
    subject_symbol       TEXT,
    status               TEXT NOT NULL DEFAULT 'advisory'
                             CHECK (status IN ('advisory','experimental',
                                               'rejected','superseded'))
);

CREATE INDEX idx_research_reports_generated_at
    ON research_reports (generated_at);

CREATE INDEX idx_research_reports_subject
    ON research_reports (subject_symbol, generated_at);

CREATE TABLE capitol_trades_records (
    record_id             TEXT PRIMARY KEY,
    politician            TEXT NOT NULL,
    security_name         TEXT NOT NULL,
    ticker                TEXT,
    transaction_type      TEXT NOT NULL,
    disclosed_trade_date  TEXT,
    publication_date      TEXT NOT NULL,
    transaction_size      TEXT,
    source_url            TEXT NOT NULL,
    extraction_timestamp  TEXT NOT NULL,
    parse_confidence      TEXT NOT NULL CHECK (parse_confidence IN
                              ('LOW','MEDIUM','HIGH')),
    validation_status     TEXT NOT NULL
);

CREATE INDEX idx_capitol_trades_ticker
    ON capitol_trades_records (ticker, publication_date);

CREATE INDEX idx_capitol_trades_politician
    ON capitol_trades_records (politician, publication_date);

CREATE TABLE research_comparisons (
    comparison_id        TEXT PRIMARY KEY,
    ticker               TEXT NOT NULL,
    perplexity_ids_json  TEXT NOT NULL,
    capitol_trades_ids_json TEXT NOT NULL,
    overlap_json         TEXT NOT NULL,
    contradictions_json  TEXT NOT NULL,
    missing_json         TEXT NOT NULL,
    hypothesis           TEXT,
    confidence           TEXT NOT NULL CHECK (confidence IN
                             ('LOW','MEDIUM','HIGH')),
    suggested_experiment TEXT,
    synthesized_at       TEXT NOT NULL
);

CREATE INDEX idx_research_comparisons_ticker
    ON research_comparisons (ticker, synthesized_at);
