from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .storage import ClosingConnection, SCHEMA


def _initial_schema(conn: sqlite3.Connection) -> None:
    # Execute statements individually so the migration remains inside its transaction.
    for statement in SCHEMA.split(";"):
        statement = statement.strip()
        if statement and not statement.upper().startswith("PRAGMA "):
            conn.execute(statement)
    # Do not discard existing duplicate rows. If duplicates exist, fail safely and
    # require an explicit, reviewed data-repair migration before adding this index.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_screening_security_date "
        "ON screening_results(security_key, data_date)"
    )


def _price_corrections(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS price_corrections (
            correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            security_key TEXT NOT NULL REFERENCES securities(security_key),
            security_identifier TEXT,
            exchange TEXT NOT NULL CHECK(exchange IN ('NSE','BSE')),
            trading_date TEXT NOT NULL,
            old_price REAL,
            new_price REAL NOT NULL,
            original_source_record_id INTEGER REFERENCES source_records(source_record_id),
            correction_source_record_id INTEGER NOT NULL REFERENCES source_records(source_record_id),
            corrected_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            old_values_json TEXT NOT NULL,
            new_values_json TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_corrections_security_date "
        "ON price_corrections(security_key, trading_date, corrected_at)"
    )


def _universe_runs(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS universe_research_runs (
            batch_run_id TEXT PRIMARY KEY,
            as_of_date TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','completed','completed_with_failures')),
            total_securities INTEGER NOT NULL,
            attempted_securities INTEGER NOT NULL DEFAULT 0,
            successful_securities INTEGER NOT NULL DEFAULT 0,
            failed_securities INTEGER NOT NULL DEFAULT 0,
            security_ids_json TEXT NOT NULL DEFAULT '[]',
            successful_security_ids_json TEXT NOT NULL DEFAULT '[]',
            failed_security_ids_json TEXT NOT NULL DEFAULT '[]',
            errors_json TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            manifest_path TEXT,
            manifest_json TEXT NOT NULL DEFAULT '{}'
        )"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
    if "batch_run_id" not in columns:
        conn.execute(
            "ALTER TABLE research_runs ADD COLUMN batch_run_id TEXT "
            "REFERENCES universe_research_runs(batch_run_id)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_batch ON research_runs(batch_run_id, security_key)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS universe_research_security_results (
            batch_run_id TEXT NOT NULL REFERENCES universe_research_runs(batch_run_id),
            security_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
            research_run_id TEXT REFERENCES research_runs(research_run_id),
            error_summary TEXT,
            started_at TEXT,
            finished_at TEXT,
            PRIMARY KEY(batch_run_id, security_key)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_universe_security_status "
        "ON universe_research_security_results(batch_run_id, status)"
    )


MIGRATIONS = (
    (1, "initial_schema", _initial_schema),
    (2, "price_correction_audit", _price_corrections),
    (3, "universe_research_batches", _universe_runs),
)


def apply_migrations(database_path: Path | str) -> dict:
    """Apply numbered migrations; this is the only schema-writing entry point."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, factory=ClosingConnection, timeout=10.0)
    conn.row_factory = sqlite3.Row
    applied_now: list[dict] = []
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )"""
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        known_versions = {version for version, _, _ in MIGRATIONS}
        unknown = applied - known_versions
        if unknown:
            raise RuntimeError(f"Database has migration version(s) unknown to this application: {sorted(unknown)}")
        for version, name, migrate in MIGRATIONS:
            if version in applied:
                continue
            migrate(conn)
            applied_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, applied_at),
            )
            applied_now.append({"version": version, "name": name, "applied_at": applied_at})
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"Foreign-key check failed with {len(violations)} violation(s)")
        conn.commit()
        current_version = max(known_versions, default=0)
        return {
            "database_path": str(path.resolve()),
            "schema_version": current_version,
            "migrations_applied": applied_now,
            "foreign_key_violations": 0,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
