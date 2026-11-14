# India Stock Intelligence — Stages 1–3

A local, auditable application for an NSE mainboard and SME stock master, sourced fundamentals, deterministic analysis, price screening, and evidence-backed research. Automatic market-data retrieval remains disabled; no approved automated feed is configured. **This project does not claim live NSE integration or complete market coverage.** No paid API call, trading connection, or automatic email sending is enabled by default.

## Requirements

- Windows 11
- Python 3.12 or newer on PATH
- Internet only for the first-time installation of the free Python packages in `requirements.txt`

## Install

Open PowerShell in the project folder:

```powershell
.\scripts\setup.ps1
```

Or run the equivalent commands directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-build-isolation
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli migrate
```

If PowerShell blocks the script, use the direct commands above. The `.venv`, local database, imported raw files, generated reports, and logs are excluded from Git.

The repository exposes the requested top-level `agents/`, `core/`, and `providers/` modules as compatibility facades. The installable implementation remains under `src/india_stock_intelligence/`. The `.env.example` file is a settings reference; `.env` files are not loaded automatically. Set environment variables in PowerShell or through a secret manager.

The project folders are organized as follows:

```text
app.py, config.py, requirements.txt, .env.example, README.md
agents/       top-level agent entry points
core/         orchestrator, validation, screening, reporting entry points
providers/    NSE, BSE, financial-evidence, and news-evidence adapters
prompts/      human-readable prompt copies
data/market_data/raw/      unchanged exchange files
data/financial_data/raw/   unchanged financial and news evidence files
data/security_master/raw/  unchanged manually supplied listing-master files
data/reports/              generated reports
database/stock_intelligence.db
tests/        agent, data, pipeline, and report tests
src/india_stock_intelligence/  installable canonical implementation
  sync/                       scheduled synchronization framework; source disabled by default
```

After a code update, run the explicit migrate command above before starting the dashboard or import/research commands. Ordinary connections do not create schema or repair data. Read workflows use query-only SQLite connections; writes require an existing migrated database.

## Run the app

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

Upload a permitted official CSV, ZIP, XLSX, XLSM, TSV, or delimited text report. The original bytes are retained in `data/market_data/raw` with their SHA-256 in the filename. ZIP imports accept CSV and XLSX members with size/count limits. If automated access is unavailable or not allowed, use this manual import path.

The dashboard also imports sourced JSON evidence, shows stock-master and fundamental coverage, displays saved agent runs and source references, and can run an offline demo on synthetic securities. The demo makes no API calls and invents no fundamental values.

The importer recognizes common market-data column names. If an input contains no exchange column, choose NSE or BSE in the dashboard, or pass `--exchange` on the CLI:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli import .\path\to\exchange_file.csv --exchange NSE
```

The file needs a symbol or security identifier, a date, and preferably close and volume. Security identity uses exchange plus explicit security identifier, falling back to exchange plus symbol. Company name is never used as the identity key, so equal names across exchanges remain separate securities. SME classifications are retained from source rows; they are not filtered out.

Imports never replace a price already stored for the same security and trading date by default. To apply an explicitly reviewed correction, pass both flags; a reason is mandatory and the old/new values and source-record IDs are stored in the price_corrections audit table:

    .\.venv\Scripts\python.exe -m india_stock_intelligence.cli import .\corrected_file.csv --exchange NSE --replace-existing --correction-reason "Official source correction"

The dashboard offers the same explicit correction checkbox and reason field. Every source row is retained with a final imported, rejected, duplicate, invalid or corrected status. Original uploaded bytes remain archived unchanged.

## Screening and reports

The import command also calculates available metrics. To rerun screening or generate outputs:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli screen
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli report
```

Reports are saved under `data/reports`: Excel, PDF, JSON manifest, and an `.eml` draft with the Excel/PDF attached. The draft is local only; the application has no SMTP configuration or send operation. Configure and explicitly approve an email provider in a later stage before any sending capability is added.

For an offline demo, `sample_data/sample_market_data.csv` is a synthetic file clearly labelled in its `Source` column. Import it through the same pipeline, then create prominently labelled reports:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli import .\sample_data\sample_market_data.csv
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli report --sample
```

Never interpret sample outputs as market data. The importer does not fetch fundamentals and does not fill missing values. Returns and averages remain blank until the required valid close-price history is present.

## NSE automatic EOD synchronization

The synchronization framework is present, but the built-in NSE source adapter is deliberately blocked and makes no network requests. NSE's current website terms prohibit systematic or automated data collection on or in relation to the website. NSE's data policy describes subscriber agreements and a separate research access route; no approved zero-cost unattended feed is configured. Do not enable a third-party or undocumented endpoint as a workaround.

The NSE reports page lists `CM-UDiFF Common Bhavcopy Final (zip)` and says the older CM common CSV was discontinued from July 8, 2024. The official Forms & Formats page links UDiFF catalogues and CM sample files, but the exact catalogue workbook and CM cash-equity field values have not yet been verified in this project. Accordingly, the parser remains fail-closed, no report-page polling is enabled, and live retrieval is not configured. An approved access agreement/source method and reviewable schema artifact are blockers to production synchronization.

Sync state, run history, original artifact copies, and SHA-256-keyed provenance sidecars are stored under `data/market_data/sync/nse`; this does not add database tables or run a migration. The sync service reuses `NSEProvider` and the existing `MarketImporter`, preserves downloaded artifact bytes, uses normal duplicate handling, and never enables automatic price corrections. Agent dispatch is an injectable adapter to the existing research orchestrator. It remains `NOT CONFIGURED` here so the sync command cannot make OpenAI calls.

The following command reports the access blocker and writes a sync status manifest without network access:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli sync --once
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli sync-status
```

The optional Task Scheduler registration script is not run automatically. It creates a daily task at the configured Windows-local time, starts missed runs after restart, and ignores concurrent task instances. When an approved source adapter is later supplied, the sync process polls at the configured interval until the configured window ends. Registering the task while the adapter is blocked only records blocked attempts; it does not retrieve market data.

```powershell
.\scripts\register_nse_sync_task.ps1 -At 18:15
```

Remove the task only through Windows Task Scheduler after reviewing it. No task has been registered by this development work.

## Daily operation

After manually obtaining a file from a source whose access conditions permit your use, run:

```powershell
.\scripts\run_daily.ps1 .\path\to\daily_file.csv -Exchange NSE
```

This script imports and generates reports for that file. It does not download data or create a Windows scheduled task. You can schedule it with Task Scheduler after choosing a compliant file workflow.

## Database and checks

SQLite is stored at `database/stock_intelligence.db`. The previous `data/india_stocks.sqlite3` file, if present, is retained as a migration backup. Tables include:

- `securities` — exchange-specific listing and source timestamp fields
- `daily_prices` — observed OHLCV rows; missing values stay SQL `NULL`
- `source_records` — raw source row payload, original file name/hash, and import time
- `data_quality_checks` — missing values, stale or absent history, duplicate records, invalid dates/prices, suspicious price moves, and possible corporate-action flags
- `screening_results` — returns, moving averages, volatility, drawdown, and volume changes
- `generated_reports` — generated file hashes and the report manifest
- `evidence_items` — sourced claims with URL, publication date, reporting period, retrieval timestamp, raw-file hash, and optional synthetic label
- `research_runs` — as-of date, evidence digest, coverage, verdict, scores, watchlist state, and prior-run changes
- `agent_outputs` — versioned JSON outputs and bounded attempt history for each agent

schema_migrations stores versioned schema migration history. price_corrections stores explicit price replacement audit trails with source references and old/new values. universe_research_runs and universe_research_security_results store batch status, resumable per-security state and manifest details.

Only the explicit migrate command applies schema migrations. Migrations do not delete duplicate screening rows; if the unique screening index cannot be created because legacy duplicates exist, migration rolls back and preserves those rows for review.

Large price changes are review flags, not corporate-action determinations. Staleness is compared with the machine's current local date, using a configurable calendar-day threshold, so an entirely old dataset is still flagged. Validate an exchange holiday calendar and listing-master mapping before using these outputs for investment decisions.

To copy an existing Stage 1 database and raw market archives into the current folders, run this once. It refuses to overwrite an existing target and leaves the legacy copies intact:

```powershell
.\.venv\Scripts\python.exe .\scripts\migrate_layout.py
```

Environment settings:

- `INDIA_STOCK_DATA_DIR` — alternate writable data folder
- `INDIA_STOCK_DATABASE_DIR` — alternate SQLite folder (default `database`)
- `INDIA_STOCK_STALE_DAYS` — staleness threshold (default 5 calendar days)
- `INDIA_STOCK_SUSPICIOUS_CHANGE_PCT` — price-change warning threshold (default 20%)
- `INDIA_STOCK_CORPORATE_ACTION_PCT` — possible corporate-action threshold (default 30%)

Logs rotate in `data/logs/application.log`. The bounded retry helper is for transient read-only operations; it is not used to repeat writes or trading actions.

## Stage 2: evidence and three-agent research

Separate modules under `src/india_stock_intelligence/agents` implement the Equity Research Analyst, Bull and Bear Analyst, and Independent Judge. Each role has a separate prompt and strict JSON schema. The orchestrator runs the analyst, gives the complete analyst report to the challenger, then gives both complete reports to the judge. Every role receives the same evidence bundle and digest. Outputs are schema-validated, and each cited evidence ID must exist in that bundle. Reported financial metrics must cite evidence with a matching reporting period.

The judge may return `BUY_CANDIDATE`, `HOLD`, `WATCH`, `AVOID`, or `INCONCLUSIVE`. Sparse evidence produces `INCONCLUSIVE`; missing values are never filled in. Price-screening metrics are context only and cannot serve as fundamental evidence. Evidence downloads are disabled: manually import reports only when their access conditions permit your use.

## Stage 3: stock intelligence engine

The research engine runs independently of NSE MCP. It combines the existing security and evidence tables with a deterministic fundamentals layer, the existing three agent roles, and the existing dashboard and report writers. No production schema migration is required. The provider boundary supports separate adapters, but retrieval stays disabled; manually supplied data is the only import path in this stage.

The dashboard can import a manually obtained stock-master JSON file and sourced financial-evidence JSON. NSE records should state `MAINBOARD`, `SME`, or `UNKNOWN` explicitly. Unknown classifications stay unknown. The raw listing file is archived unchanged under `data/security_master/raw`; its source name, URL, publication date, retrieval timestamp, and records are retained. Master import writes no price rows.

Stock-master input shape:

```json
{
  "source": {
    "source_name": "Manually obtained NSE listing file",
    "source_url": "https://example.org/listing-file.csv",
    "publication_date": "2025-06-01",
    "retrieved_at": "2025-06-02T10:00:00+05:30"
  },
  "records": [
    {
      "symbol": "EXAMPLE",
      "company_name": "Example Limited",
      "isin": "INE123456789",
      "listing_segment": "SME",
      "security_status": "ACTIVE"
    }
  ]
}
```

Fundamental evidence records may use a `metric_key` from the supported catalog: `revenue`, `net_profit`, `operating_profit`, `ebitda`, `ebit`, `shareholders_equity`, `average_shareholders_equity`, `total_debt`, `capital_employed`, `average_capital_employed`, `capital_expenditure`, `eps`, `revenue_growth_yoy`, `profit_growth_yoy`, `operating_margin`, `ebitda_margin`, `net_profit_margin`, `roe`, `roce`, `debt_to_equity`, `operating_cash_flow`, `free_cash_flow`, `promoter_holding`, `promoter_pledging`, `institutional_holding`, `pe_ratio`, `pb_ratio`, `ps_ratio`, `ev_to_ebitda`, `sector_valuation`, or `eps_growth_yoy`. Add optional `financial_basis` as `standalone`, `consolidated`, or `unspecified`; the default is `unspecified`, and the analyzer never combines bases. Annual or same-quarter year-over-year growth, EBITDA/operating/net margins, ROE, ROCE, and debt/equity require aligned periods and compatible explicit units. ROE and ROCE require sourced average balances. Unavailable or conflicting calculations remain null.

Each metric carries its evidence IDs and source URLs, reporting period, publication date, retrieval timestamp, and status. Status values are `verified`, `unverified`, `conflicting`, `missing`, or `synthetic`. `verified` is a label supplied in the local evidence file; the app does not authenticate the source. Never label a synthetic fixture as real data. Dashboard, Excel, and PDF show status and provenance. Reports can be generated from stock master and fundamental evidence without price history; market-date fields stay empty in that case.

The CLI supports manual imports and a read-only deterministic snapshot:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli master import .\path\to\nse_master.json
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli evidence import .\path\to\fundamentals.json
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli fundamentals --security "NSE:id:EXAMPLE-ISIN"
```

The deterministic engine runs locally without AI. Ollama was not available in the implementation environment, and no model or external AI service was called. Existing paid-API protections remain in place; do not run agents against real investment data without separate approval.

### Import sourced evidence

Create a UTF-8 JSON file containing a `records` array. Each record requires a source URL, publication date, reporting period, timezone-aware retrieval time, source type and name, and a description of the claim. `security_key` may be null for macroeconomic evidence; otherwise it must match an exchange-specific security key already in SQLite.

```json
{
  "records": [
    {
      "security_key": "NSE:id:EXAMPLE-ISIN",
      "source_type": "annual_report",
      "source_name": "Issuer annual report FY2025",
      "source_url": "https://example.org/annual-report.pdf",
      "publication_date": "2025-06-30",
      "reporting_period": "FY2025",
      "retrieved_at": "2025-07-01T10:00:00+05:30",
      "claim": "Revenue reported for the fiscal year",
      "value": 1234.5,
      "unit": "INR million",
      "excerpt": "Optional short supporting excerpt",
      "is_synthetic": false
    }
  ]
}
```

Import through the dashboard or CLI:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli evidence import .\path\to\verified_evidence.json
```

The original JSON bytes are stored unchanged in `data/financial_data/raw`. Market files are archived in `data/market_data/raw`. Evidence published or retrieved after the requested as-of date is excluded from that research bundle. The database deduplicates identical source-file rows by content hash.

### Run the offline demo

Import the included synthetic price file, then run only synthetic-marked securities through the deterministic offline backend:

```powershell
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli import .\sample_data\sample_market_data.csv
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli research --all --sample
```

Per-security research JSON is written under `data/reports`; Excel and PDF include the latest agent research. With no financial evidence, the demo leaves metrics null, assigns no risk score, and returns `INCONCLUSIVE`.

Each universe execution also writes a JSON batch manifest with attempted, successful and failed security IDs, error summaries and timestamps. The CLI prints the batch ID. To retry only failed securities from that batch while skipping completed securities:

    .\.venv\Scripts\python.exe -m india_stock_intelligence.cli research --resume-batch "BATCH-ID" --sample

Do not resume a batch while its original process is still running.

### Optional OpenAI Responses API adapter

The OpenAI adapter is optional and does not make requests when initialized. The project does not install its SDK automatically or choose a model. Continue only after reviewing and approving any API charges. Install the optional dependency explicitly:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[openai]" --no-build-isolation
```

Set `OPENAI_API_KEY`, `INDIA_STOCK_OPENAI_MODEL`, and `INDIA_STOCK_OPENAI_ENABLED=true` only in your current process or an approved secret manager, then pass the explicit per-run approval flag:

```powershell
$env:INDIA_STOCK_OPENAI_ENABLED = "true"
# Set OPENAI_API_KEY through your approved secret manager; never save it in this project.
$env:INDIA_STOCK_OPENAI_MODEL = "your-explicitly-chosen-model"
.\.venv\Scripts\python.exe -m india_stock_intelligence.cli research --security "NSE:id:EXAMPLE-ISIN" --confirm-paid-api-calls
```

`--max-attempts` defaults to one. Additional attempts require `--confirm-paid-retries`, since each may make another billable request. SDK retries are disabled; the orchestrator controls repeat attempts. This API integration has not been used with a real account. Structured output uses the Responses API's strict JSON Schema format described in the [official OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Never use `--all` for live research until you review the local universe, evidence coverage, expected usage, and budget. A run covers only securities present in the local SQLite database; it never claims that this is the complete NSE/BSE universe.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests cover CSV and ZIP imports, raw-file preservation, exchange-separated listings, duplicates, invalid dates/prices, audited corrections, read-only connections, versioned migrations, missing/stale data, price-change flags, metric thresholds, report generation, universe failure isolation/resume, retry classification, disabled exchange downloads, mainboard/SME master import, fundamental provenance/calculation/conflicts, and reports without price data. All tests use temporary databases and synthetic fixtures.

## Current limitations

- No verified all-listings security master is bundled. Mainboard/SME coverage depends on the manually supplied listing file and explicit segment classification; unknown stays unknown.
- NSE and BSE download adapters are extension points only. Automated downloads remain disabled until a provider and its access conditions are independently approved. The NSE MCP is not used by this engine.
- Exchange master files have differing schemas. The manual JSON import uses a documented common shape; other source formats need a reviewed adapter.
- The application does not yet maintain a delisting lifecycle, corporate-action adjustment series, or exchange holiday calendar.
- The OpenAI adapter is optional, disabled unless explicitly enabled and approved per run, and has not been tested with a real API account. No model or API key is supplied.
- The evidence importer checks required provenance fields but does not independently authenticate URLs or verify excerpts against their source. Human review is required before import.
- Agent analysis is only as complete as the evidence manually supplied. The application does not automatically fetch financial filings, announcements, news, or RBI series and does not claim complete research coverage.
- Reports are research workflow outputs, not personalized financial advice. Automatic trading, email sending, subscriptions, and exchange downloads are not implemented.
- The sample market file is synthetic and is included only to exercise the pipeline.

## Remaining gaps

- Financial evidence still needs human sourcing and review. Source URLs and user-entered verification labels are retained, but the app does not authenticate source documents.
- Derived metrics include annual/same-quarter YoY growth, operating/EBITDA/net margins, ROE, ROCE, debt-to-equity, and free cash flow when explicitly comparable inputs exist. ROE/ROCE require sourced average balances; ownership, pledge, and valuation remain direct sourced observations.
- Local AI was unavailable in the implementation environment. Existing research agents require a separately approved backend for real financial evidence; no real-security agent run was performed.
- Do not enable provider retrieval, production synchronization, scheduling, or paid AI use as part of Stage 3. Those require separate review and approval.
