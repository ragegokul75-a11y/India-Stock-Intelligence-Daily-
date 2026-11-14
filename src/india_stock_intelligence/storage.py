from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS securities (
    security_key TEXT PRIMARY KEY,
    exchange TEXT NOT NULL CHECK(exchange IN ('NSE','BSE')),
    symbol TEXT NOT NULL,
    company_name TEXT,
    security_identifier TEXT,
    security_type TEXT,
    listing_status TEXT,
    source_timestamp TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_securities_exchange_symbol ON securities(exchange, symbol);
CREATE TABLE IF NOT EXISTS source_records (
    source_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    security_key TEXT REFERENCES securities(security_key),
    record_date TEXT,
    raw_record_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(source_sha256, source_row_number)
);
CREATE INDEX IF NOT EXISTS idx_source_records_sha ON source_records(source_sha256);
CREATE TABLE IF NOT EXISTS daily_prices (
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_key TEXT NOT NULL REFERENCES securities(security_key),
    price_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    source_timestamp TEXT,
    source_record_id INTEGER REFERENCES source_records(source_record_id),
    UNIQUE(security_key, price_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_prices_key_date ON daily_prices(security_key, price_date);
CREATE TABLE IF NOT EXISTS data_quality_checks (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    security_key TEXT REFERENCES securities(security_key),
    record_date TEXT,
    check_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('info','warning','error')),
    details TEXT NOT NULL,
    source_file TEXT
);
CREATE INDEX IF NOT EXISTS idx_quality_type_date ON data_quality_checks(check_type, record_date);
CREATE TABLE IF NOT EXISTS screening_results (
    screening_id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_key TEXT NOT NULL REFERENCES securities(security_key),
    data_date TEXT NOT NULL,
    return_1d_pct REAL,
    return_20d_pct REAL,
    return_60d_pct REAL,
    return_252d_pct REAL,
    ma_50 REAL,
    ma_200 REAL,
    volatility_20d_pct REAL,
    max_drawdown_252d_pct REAL,
    volume_change_20d_pct REAL,
    created_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screening_date ON screening_results(data_date, security_key);
CREATE TABLE IF NOT EXISTS generated_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    data_date TEXT,
    generated_at TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id TEXT PRIMARY KEY,
    security_key TEXT REFERENCES securities(security_key),
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    publication_date TEXT NOT NULL,
    reporting_period TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    claim TEXT NOT NULL,
    value_json TEXT,
    unit TEXT,
    excerpt TEXT,
    source_sha256 TEXT NOT NULL,
    source_file TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK(is_synthetic IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_security_type ON evidence_items(security_key, source_type, publication_date);
CREATE TABLE IF NOT EXISTS research_runs (
    research_run_id TEXT PRIMARY KEY,
    security_key TEXT NOT NULL REFERENCES securities(security_key),
    as_of_date TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    evidence_coverage_json TEXT NOT NULL,
    sample_data INTEGER NOT NULL DEFAULT 0 CHECK(sample_data IN (0,1)),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    verdict TEXT,
    risk_score INTEGER,
    confidence_score INTEGER,
    watchlist_action TEXT,
    changes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_research_security_date ON research_runs(security_key, as_of_date, started_at);
CREATE TABLE IF NOT EXISTS agent_outputs (
    agent_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_run_id TEXT NOT NULL REFERENCES research_runs(research_run_id),
    agent_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    output_json TEXT,
    error_message TEXT,
    request_id TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(research_run_id, agent_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_run ON agent_outputs(research_run_id, agent_id, attempt);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def connect_read_only(database_path: Path | str) -> sqlite3.Connection:
    """Open an existing database in query-only mode without schema or journal writes."""
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database does not exist: {path}. Run the dedicated migrate command first.")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, factory=ClosingConnection,
                           timeout=5.0)
    _configure_connection(conn)
    conn.execute("PRAGMA query_only = ON")
    return conn


def connect_read_write(database_path: Path | str) -> sqlite3.Connection:
    """Open an existing database for transactional writes; never creates or migrates it."""
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database does not exist: {path}. Run the dedicated migrate command first.")
    conn = sqlite3.connect(path, factory=ClosingConnection, timeout=5.0)
    return _configure_connection(conn)


def connect(database_path: Path | str) -> sqlite3.Connection:
    """Backward-compatible alias for connect_read_write; performs no setup or cleanup."""
    return connect_read_write(database_path)


def add_quality_check(conn: sqlite3.Connection, *, checked_at: str, check_type: str, severity: str,
                      details: str, security_key: str | None = None, record_date: str | None = None,
                      source_file: str | None = None) -> None:
    conn.execute(
        """INSERT INTO data_quality_checks
           (checked_at, security_key, record_date, check_type, severity, details, source_file)
           SELECT ?, ?, ?, ?, ?, ?, ?
           WHERE NOT EXISTS (
               SELECT 1 FROM data_quality_checks
               WHERE check_type=? AND security_key IS ? AND record_date IS ?
                 AND details=? AND source_file IS ?
           )""",
        (checked_at, security_key, record_date, check_type, severity, details, source_file,
         check_type, security_key, record_date, details, source_file),
    )


def rows_as_dicts(rows):
    return [dict(row) for row in rows]


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
