# INDIA_STOCK_INTELLIGENCE — Independent Stage 1 Verification

**Verification date:** 2026-11-06  
**Scope:** Read-only audit of Stage 1 reliability and database safety. No project source, configuration, production database, or backup was modified. Test and behavioral-test databases were created under the ignored `.tmp` folder only. No live data, external API, email, or dashboard session was started.

## 1. Project identity

| Check | Result | Finding |
|---|---|---|
| Actual project root | VERIFIED | `D:\chatgpt\India Stock Intelligence Daily` |
| Same workspace as Stage 1 implementation report | VERIFIED | The project root and active database path match the preceding report. The separately mentioned `D:\chatgpt\India Stock Intelligence` directory is not the workspace in use. |
| Active database | VERIFIED | `D:\chatgpt\India Stock Intelligence Daily\database\stock_intelligence.db`, resolved through `get_settings()` in the existing virtual environment. |
| Python environment | VERIFIED | `D:\chatgpt\India Stock Intelligence Daily\.venv\Scripts\python.exe` exists and was used for checks and tests. |
| Git status | NOT TESTED | No `.git` metadata exists at the project root, so a repository status or tracked-file check was unavailable. The root `.gitignore` exists. |

## 2. Backup verification

**Status: VERIFIED**

Backup inspected: `backups/stage1_20261106_142135/stock_intelligence.db`.

| Check | Independent result |
|---|---|
| File exists | Yes |
| Size | 1,159,168 bytes |
| SQLite integrity | `PRAGMA integrity_check` returned `ok` |
| Foreign keys | `PRAGMA foreign_key_check` returned no rows |
| Backup verification JSON | Present; its original snapshot metadata reports matching table names, row counts, positive size, verified file hashes, and `verified: true`. The post-migration section also records the backup as valid. |
| Snapshot copies | All 15 source snapshot copies match the SHA-256 values recorded in the JSON. |

Backup table counts independently read in read-only mode:

| Table | Rows |
|---|---:|
| agent_outputs | 18 |
| daily_prices | 981 |
| data_quality_checks | 6 |
| evidence_items | 0 |
| generated_reports | 42 |
| research_runs | 6 |
| screening_results | 3 |
| securities | 3 |
| source_records | 981 |

The backup was not restored or written to.

## 3. Database connection safety

**Status: VERIFIED**

Behavioral checks used an isolated copy of the backup and temporary databases:

- `connect_read_only()` allowed a SELECT and rejected INSERT, UPDATE, and DELETE with SQLite `OperationalError` due to read-only/query-only mode.
- The isolated database file SHA-256 was unchanged after the read and rejected write attempts.
- `connect_read_write()` and its compatibility alias did not create schema or delete rows when opening an existing database. Two deliberately duplicated screening rows remained two rows.
- Opening a nonexistent path through the ordinary read/write connector raised `FileNotFoundError` and did not create a database.
- Source inspection found no schema setup, journal-mode change, or duplicate screening-row cleanup in normal connector code.
- In application code, schema migrations are invoked by the explicit `migrate` CLI command. `init-db` remains a deprecated explicit alias. The dashboard and ordinary connection helpers do not auto-migrate. Tests call the migration function directly against isolated test databases.

## 4. Migration safety

**Status: VERIFIED**

Source inspection confirmed migration versions 1–3: initial schema, price correction audit, and universe research batches. `schema_migrations` stores applied version, name, and timestamp. Migrations run transactionally and perform a foreign-key check before commit.

Behavioral checks were run against a temporary copy of the backup, never the active database:

- Versions 1, 2, and 3 applied to the temporary copy.
- Existing table row counts were unchanged after migration.
- A second migration run applied no migrations, confirming idempotence.
- A separate temporary legacy database with duplicate screening rows raised `IntegrityError`; the duplicate rows remained, and migration-history and later migration tables were absent after rollback.
- The active database was not passed to `apply_migrations()` during this verification.

The implementation intentionally refuses to delete legacy duplicates to satisfy a unique index; a duplicate-containing migration fails and rolls back instead.

## 5. Price correction workflow

**Status: VERIFIED**

Inspection of `ingestion.py`, provider forwarding, and the reliability tests confirmed:

- A normal re-import leaves the existing security/date close unchanged and marks the incoming row duplicate.
- Replacing an existing price requires `replace_existing=True` and a non-empty correction reason.
- A correction records old/new prices and OHLCV values, exchange, security identifier, trading date, source-record IDs, timestamp, and reason in `price_corrections`.
- The original and correction source records retain their statuses and raw imported bytes are preserved.
- Invalid price input is recorded as invalid and is not inserted as a valid price; a row rejected for the wrong exchange is recorded rejected.
- A dated observation with missing close can be retained as a null observation for audit, not as a fabricated price.

These behaviors passed synthetic-database tests in the regression suite. No correction was run against production data.

## 6. Universe orchestration

**Status: VERIFIED**

The synthetic regression test confirmed that a missing security fails without stopping subsequent valid securities, records failed/successful identifiers and errors in a JSON manifest, and resumes without duplicating completed securities.

An additional isolated interruption simulation persisted a completed research run linked to a batch while leaving its batch-result row in `running` state, representing a stop between those writes. Resume reconciled the row to completed, wrote the manifest, returned no new research runs, and left exactly one research run for the security in that batch.

## 7. Retry classification

**Status: VERIFIED**

Source inspection and synthetic tests confirmed:

- Timeout and connection errors: retryable.
- HTTP 429 and 5xx: retryable; numeric `Retry-After` is honored and backoff is bounded.
- Authentication/permission HTTP errors and other 4xx responses: not retryable.
- `PermissionError`, validation `ValueError`, `TypeError`, and assertion/programming errors: not retryable.
- A non-retryable synthetic agent error was recorded once despite a configured maximum of three attempts.

## 8. Regression tests

**Status: VERIFIED**

Command used the existing virtual environment, disabled pytest’s cache provider, and selected a new dedicated temporary base directory:

~~~text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp\stage1_independent_verification_20261106_150500
~~~

Result: **30 passed in 4.28 seconds.**

Test fixtures create databases under pytest `tmp_path`; source inspection confirmed the test databases are temporary. The active database SHA-256 before and after the test suite was identical: `66effe3dcefb532534c55ee0bb6d708c3c8bb70512cac1e4a9a6a27ab31c44b4`. Active table row counts were also identical to the captured pre-test baseline. No production database path was used as a test target.

The stale/missing-data test specifically checks both quality flags and null stored price/screening metrics, including moving averages and long-period return. No missing value is replaced with invented data in that path.

## 9. Dashboard verification

**Status: PARTIALLY VERIFIED**

Static inspection only; the dashboard was not launched.

- Startup/dashboard queries use `connect_read_only()`.
- User-triggered market import and screening use `connect_read_write()`.
- The import UI exposes an explicit correction checkbox and reason field, passed through to the importer.
- The dashboard exposes manual price/evidence imports and an explicit report-generation action for Excel, PDF, manifest, and a local email-ready draft.
- The dashboard text states exchange downloads are disabled and email remains an unsent draft.
- Startup calls logging configuration, which may write log files; the dashboard was therefore not launched during this read-only verification.

Runtime rendering, UI actions, Excel/PDF generation from the dashboard, and email delivery were not tested here. No email was sent.

## 10. Discrepancies and limitations versus the implementation report

**Status: PARTIALLY VERIFIED**

- **Project path:** Confirmed as the `India Stock Intelligence Daily` workspace described by the preceding report. The other path is not the active workspace.
- **Backup claims:** Independently confirmed. The backup is the pre-migration snapshot, so it has nine original tables; the active database has those tables plus four migration-related tables. Existing active records remain unchanged.
- **Database preservation:** The active database hash and row counts remained unchanged across this verification. Its current counts are 3 securities, 981 prices, 981 source records, 6 data-quality checks, 3 screenings, 42 generated reports, 0 evidence items, 6 research runs, and 18 agent outputs; migration tables contain 3 history rows and zero correction/batch rows.
- **Original file snapshot hashes:** The 15 archived source copies match their recorded original hashes. Current working source files differ from those originals where Stage 1 intentionally changed them; the metadata hashes describe the archived pre-change copies, not expected hashes for the updated code.
- **Git:** The prior report did not establish repository status. There is no root `.git` directory, so tracked/untracked state could not be independently reported.
- **Dashboard/runtime:** The prior report already said the UI was not runtime-tested; this audit also used static inspection only.
- **Live data and AI:** This verification does not validate real NSE/BSE downloads, financial-data providers, Ollama, OpenAI, or any claim of complete market coverage. Providers remain disabled/manual-import based. This is consistent with the stated Stage 1 scope, but the application is not approved for live-market investment conclusions on this evidence.

## Recommendation

# STAGE 1 APPROVED

Approval is limited to the verified Stage 1 reliability, SQLite safety, synthetic/manual-import workflows, and regression-test scope above. It does not approve live NSE/BSE integration, full-market coverage, automated financial analysis, email sending, or investment recommendations based on live data. No fixes or Stage 2 work were performed during this verification. Awaiting review.
