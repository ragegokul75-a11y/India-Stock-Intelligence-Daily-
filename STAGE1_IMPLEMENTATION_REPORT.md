# INDIA_STOCK_INTELLIGENCE — Stage 1 Implementation Report

**Task:** Stage 1 — Software Reliability and Database Safety  
**Completion date:** 2026-11-06  
**Project root used:** D:\chatgpt\India Stock Intelligence Daily  
**Scope:** Reliability, database safety, import validation, explicit price correction, universe error isolation, retry classification and tests. No live market data, Ollama or paid API integration was added.

The requested path D:\chatgpt\India Stock Intelligence did not exist during this work. The existing workspace was D:\chatgpt\India Stock Intelligence Daily, which contains the project and the active database specified in the task. All changes below were made there.

## A. Backup location and verification results

The configured active database was confirmed as:

    D:\chatgpt\India Stock Intelligence Daily\database\stock_intelligence.db

A consistent SQLite backup was made before any source code changes using Python's SQLite backup API from a read-only source connection:

    D:\chatgpt\India Stock Intelligence Daily\backups\stage1_20261106_142135\stock_intelligence.db

Backup metadata and verification are recorded in:

    D:\chatgpt\India Stock Intelligence Daily\backups\stage1_20261106_142135\backup_verification.json

Results:

- Backup size: 1,159,168 bytes.
- Backup file exists and is non-empty.
- Backup integrity check: ok.
- Backup table names and row counts matched the source snapshot.
- Fifteen existing files selected for modification were copied into source_snapshot; SHA-256 hashes matched.
- The post-migration verification also checked that the backup remained valid.
- SQLite's backup API produced a consistent standalone snapshot; no raw WAL/SHM files were copied directly.

The backup directory is excluded by .gitignore. The source backup is the pre-migration database state.

## B. Files inspected

- All project Python files, including the installable package, root compatibility facades, scripts and tests.
- app.py and scripts/setup.ps1, scripts/run_daily.ps1 and scripts/migrate_layout.py.
- README.md, requirements.txt, pyproject.toml, .env.example and .gitignore. No credentials were printed.
- The active database and legacy database in read-only mode before migration; the active and backup databases in read-only mode after migration.
- Existing synthetic raw input and the test modules.

Static AST parsing covered 50 project Python files (excluding the backup snapshots) with no syntax errors. An import smoke check attempted 40 package/facade/script modules with no import failures. The Streamlit UI was not launched.

## C. Files modified

| File | Change | Why | Verification |
|---|---|---|---|
| src/india_stock_intelligence/storage.py | Added explicit read-only and read/write connections; removed schema setup, journal-mode changes and duplicate deletion from connection creation; kept connect() as a no-setup read/write compatibility alias. | Opening or reading the database must not migrate it or delete records. | Read-only DML rejection and byte-hash test; migration and full suite passed. |
| src/india_stock_intelligence/migrations.py | Added versioned migrations 1–3 and an explicit transactional apply_migrations() entry point. | Schema changes must be deliberate, ordered and rollback-safe. | Fresh/recurring migration and rollback tests; explicit active migration passed. |
| src/india_stock_intelligence/ingestion.py | Validates identity, date and price data before persisting final status; records imported/rejected/duplicate/invalid/corrected; adds audited explicit correction support. | Keep source status accurate and prevent silent price replacement. | Invalid/rejected, duplicate, raw-byte retention and correction audit tests passed. |
| src/india_stock_intelligence/orchestrator.py | Adds per-security batch status, JSON manifest, failure isolation, explicit resume and classified retries with backoff. | One failed security must not stop the batch; successful work must be resumable. | Multi-security failure/resume and retry tests passed. |
| src/india_stock_intelligence/retry.py | Added agent error classification and bounded Retry-After/exponential delay handling. | Avoid retrying errors that cannot recover and respect temporary service limits. | Transient, rate-limit, server, validation and permission tests passed. |
| src/india_stock_intelligence/evidence.py | Uses read/write connection for evidence imports and read-only connection for bundle construction. | Keep read paths query-only. | Existing evidence provenance tests passed. |
| src/india_stock_intelligence/reports.py | Uses the explicit read/write connection because report generation performs screening, quality updates and report-record writes. | Make the connection intent explicit. | Existing Excel/PDF and manifest tests passed. |
| src/india_stock_intelligence/providers/base.py | Forwards explicit correction flag and reason through exchange byte-import adapters. | Make the correction workflow available through configured providers. | Existing provider tests and new correction test passed. |
| src/india_stock_intelligence/cli.py | Added migrate command, correction flags and resumable batch option; query-only connection used for the research market-data check. | Expose the safe workflows while retaining explicit API approval gates. | Tests passed; active migrate command ran successfully. |
| app.py | Dashboard queries now use read-only connections; screening writes use read/write; added audited correction checkbox and reason field. | Prevent ordinary dashboard reads from writing to SQLite and make replacement opt-in. | Static parsing/import checks passed; UI was not launched. |
| scripts/setup.ps1 | Setup completion message now points to the explicit migrate command. | Keep first-run instructions aligned with versioned migrations. | Script inspected; not executed. |
| .gitignore | Added backups/ exclusion. | Keep the required local database backup out of a future Git commit. | Ignore rule inspected; this folder has no Git metadata. |
| README.md | Documented migration, correction, batch resume, test behavior and restore/run procedures. | Make the new safety workflow usable and clear. | Documentation reviewed against implemented commands. |
| tests/test_agents.py | Test fixture explicitly migrates its temporary database. | Connections no longer create schema automatically. | Full test suite passed. |
| tests/test_pipeline.py | Test fixture explicitly migrates a temporary DB; added permission-error no-retry assertion. | Keep fixtures isolated and confirm permission errors are not retried. | Full test suite passed. |
| tests/test_reliability.py | Added read-only, rollback, migration, correction, resume and retry regression tests. | Demonstrate the required safety guarantees. | Full test suite passed. |

## D. Database changes and migration details

The active database was migrated through:

    .\.venv\Scripts\python.exe -m india_stock_intelligence.cli migrate

Three schema versions were recorded in schema_migrations:

1. initial_schema — creates any missing baseline objects and the unique screening index. It does not delete duplicates. If legacy screening duplicates prevent index creation, the migration rolls back and leaves the rows for review.
2. price_correction_audit — adds price_corrections with security ID, exchange, trading date, old/new close and OHLCV JSON, source-record references, timestamp and reason.
3. universe_research_batches — adds batch manifest/result tables and nullable research_runs.batch_run_id.

Original table row counts remained unchanged:

| Existing table | Rows after migration |
|---|---:|
| securities | 3 |
| daily_prices | 981 |
| source_records | 981 |
| data_quality_checks | 6 |
| screening_results | 3 |
| generated_reports | 42 |
| evidence_items | 0 |
| research_runs | 6 |
| agent_outputs | 18 |

New objects are present. schema_migrations has 3 rows; price_corrections, universe_research_runs and universe_research_security_results are empty. The existing database still contains the original synthetic sample data; no prices, research records or reports were added by tests. The legacy data/india_stocks.sqlite3 file remains in place.

Post-migration read-only checks returned integrity_check = ok and zero foreign-key violations. The verified backup also returned integrity_check = ok and zero foreign-key violations.

## E. Import validation improvements

- Source bytes are archived unchanged before database processing.
- Identity, expected exchange, date, close and supplied OHLCV values are validated before the row's final status is stored.
- A wrong explicitly assigned exchange is rejected. Missing/invalid identity, date or price data is invalid.
- Missing close remains a null observation when its security/date is valid; it is not invented or used as a valid close.
- Missing volume is flagged while a valid close can still be retained.
- Duplicate rows are recorded as duplicate and do not replace a stored price.
- Only imported, rejected, duplicate, invalid and corrected are written as final source-row statuses.
- DB writes for a file occur transactionally; exceptions roll back the database transaction while the original raw bytes remain archived.

## F. Price correction workflow

Default import behavior never replaces an existing price for the same exchange/security/date. An explicit correction requires both:

- CLI: --replace-existing and a non-empty --correction-reason.
- Dashboard: the correction checkbox and a non-empty reason.

The replacement records an entry in price_corrections with the internal security key, supplied security identifier, exchange, trading date, previous and new close, full previous/new OHLCV values, original source record, correction source record, UTC timestamp and reason. The daily_prices row is updated only in that explicit path. Identical source bytes remain idempotent unless the user explicitly selects correction and supplies a reason.

## G. Universe error isolation and resume

run_universe now creates a durable batch row and per-security rows. Each security is attempted independently; an error is recorded and later securities continue. A JSON manifest under data/reports records batch ID, as-of date, total/attempted/successful/failed counts, all security IDs, error summaries, start/completion timestamps and results.

The CLI prints the batch ID. To resume a sample batch, use:

    .\.venv\Scripts\python.exe -m india_stock_intelligence.cli research --resume-batch "BATCH-ID" --sample

Completed security runs are skipped; failed/pending securities are retried. Completed research rows carry the batch ID so a process interruption between research completion and manifest update can be reconciled without creating another successful run. Do not resume while the original process is still running.

## H. Retry classification

The orchestrator retries only recognized timeout/connection failures, OpenAI-style API connection/timeouts, HTTP 429 and HTTP 5xx responses. Authentication/permission and other 4xx errors, validation/schema errors, configuration errors and programming errors are recorded and not retried. Numeric or date Retry-After values are honored within a 30-second cap; otherwise bounded exponential backoff is used.

The existing per-run paid API approval and separate explicit approval for extra billable attempts remain in place. No live API request was made.

## I. Test results

| Run | Result |
|---|---|
| Pre-change audit baseline | 20 passed in 3.17 seconds. |
| Final Stage 1 suite | 30 passed in 3.10 seconds using a dedicated .tmp basetemp; tests use temporary SQLite databases and synthetic fixtures. |
| Python AST parse | 50 project Python files; no syntax errors. |
| Module import smoke check | 40 modules; no import failures. |
| Active database after migration | integrity_check ok; 0 foreign-key violations; all original table counts preserved. |
| Backup after migration | integrity_check ok; original table names and row counts match; 15 source snapshots hash-verified. |

During implementation, intermediate test runs exposed two issues: an identical previously imported correction file was skipped by the idempotency guard, and a stale local variable reference remained during the import refactor. Both were corrected; the targeted correction test passed and the final suite is fully green.

Tests did not use the active project database. No package was installed/upgraded. No real NSE/BSE source, paid provider, OpenAI account or Ollama endpoint was contacted.

## J. Remaining risks and limitations

- NSE/BSE downloads remain disabled. The stored project universe is synthetic and partial; no all-listings or SME coverage is claimed.
- Financial evidence remains absent in the active DB. Fundamentals, news and RBI indicators are not automatically collected.
- Ollama and live OpenAI account operation remain untested/unimplemented respectively. The OpenAI SDK is not installed.
- Current active data is still synthetic; this task did not import or correct live market prices.
- The run manifest is per local SQLite universe, not an authoritative market-wide universe.
- Version 1 migration deliberately fails if duplicate screening rows prevent the unique index. No rows are deleted automatically; a reviewed repair is needed before migration can complete on such a database.
- There is no automatic down-migration. Recovery uses the verified backup, followed by the forward migration command when the backup is restored.
- The Streamlit interface was statically checked but not launched in this task.
- Safe resume is intended for a stopped/interrupted batch, not concurrent workers.
- No Git repository exists in the project folder; backups are ignored locally, but Git tracking/remotes could not be audited.

## K. Instructions to run the application

From PowerShell in the project root:

1. Use the existing virtual environment:

       .\.venv\Scripts\Activate.ps1

2. Apply/verify the schema explicitly:

       .\.venv\Scripts\python.exe -m india_stock_intelligence.cli migrate

3. Start the dashboard:

       .\.venv\Scripts\streamlit.exe run app.py

4. Run tests:

       .\.venv\Scripts\python.exe -m pytest

5. Import a permitted manually downloaded exchange file:

       .\.venv\Scripts\python.exe -m india_stock_intelligence.cli import .\path\to\exchange_file.csv --exchange NSE

6. Generate an audited correction only after reviewing the source:

       .\.venv\Scripts\python.exe -m india_stock_intelligence.cli import .\path\to\corrected_file.csv --exchange NSE --replace-existing --correction-reason "Official source correction"

Daily helper scripts still require a manually obtained input file. No scheduled task or live source was configured.

## L. Restore instructions

The verified pre-migration backup is:

    backups\stage1_20261106_142135\stock_intelligence.db

To restore it safely:

1. Stop Streamlit, CLI commands and any other process connected to the active database.
2. Keep the current migrated database and any sidecars as an additional recovery copy. Do not manually delete WAL/SHM files.
3. Use SQLite's backup API to copy the verified backup database into the active database path, then close both connections:

~~~python
import sqlite3
from pathlib import Path

backup = Path(r"D:\chatgpt\India Stock Intelligence Daily\backups\stage1_20261106_142135\stock_intelligence.db")
active = Path(r"D:\chatgpt\India Stock Intelligence Daily\database\stock_intelligence.db")

source = sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True)
destination = sqlite3.connect(active)
source.backup(destination)
destination.close()
source.close()

check = sqlite3.connect(active.resolve().as_uri() + "?mode=ro", uri=True)
assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
check.close()
~~~

4. Re-run the explicit migration command to restore the current schema:

       .\.venv\Scripts\python.exe -m india_stock_intelligence.cli migrate

5. Confirm integrity_check is ok and foreign_key_check returns no rows before restarting the dashboard.

Restoring returns the database to its pre-migration state and removes the migration-added schema from the active database. The backup file itself remains unchanged. The legacy data/india_stocks.sqlite3 file was not deleted or modified by this work.

## Completion

Stage 1 reliability and database-safety work is complete. No Stage 2 work was started. Awaiting review and approval before further changes.
