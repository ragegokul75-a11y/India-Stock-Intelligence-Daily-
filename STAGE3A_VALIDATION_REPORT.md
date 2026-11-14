# Stage 3A — Real-Data Readiness and Validation

**Project:** INDIA_STOCK_INTELLIGENCE  
**Validation date:** 2026-11-06  
**Scope:** Local, synthetic-only validation. No real securities were researched, no real market or financial data was downloaded or imported, and no production database writes or migrations were performed.

## 1. Summary

The deterministic fundamentals engine, evidence handling, synthetic agent workflow, and Excel/PDF exports passed the checks below using temporary SQLite databases. The final existing test suite passed **59/59 tests**. The local dashboard was launched for a runtime check, but the environment denied access to its loopback health URL, so page rendering and interactive controls could not be confirmed. The application is **not cleared for real-data research or production integration**: source-use permissions remain unresolved, local AI is unavailable, and dashboard runtime still needs verification in an environment that permits local browser access.

## 2. Existing implementation inspected and reused

- `app.py`: existing Streamlit dashboard, manual import controls, fundamentals view, research results, and report controls.
- `src/india_stock_intelligence/stock_master.py`: manual Stock Master import, NSE segment normalization, source record retention, and read-only Stock Master view.
- `src/india_stock_intelligence/providers/base.py`, `providers/research.py`, and `providers/evidence.py`: common provider boundaries and manual file adapters. Automatic data retrieval remains disabled.
- `src/india_stock_intelligence/evidence.py`, `fundamental_catalog.py`, and `fundamentals.py`: provenance-linked financial evidence and deterministic calculations.
- `src/india_stock_intelligence/agents/`: Research Analyst, Bull/Bear Analyst, Independent Judge, output schemas, evidence validation, and the deterministic synthetic backend.
- `src/india_stock_intelligence/reports.py`: existing Excel and PDF report generation.
- `src/india_stock_intelligence/storage.py`, `migrations.py`, Stage 1 ingestion safeguards, and Stage 2 sync services/tests: checked for isolation and existing safeguards. No production migration or sync was run.

The existing dashboard, SQLite access layer, import stores, agent orchestrator, and report generator were reused. Changes were limited to provenance/basis-aware metric support and validation, evidence-gated agent output, dashboard/report presentation, and tests. No Stage 1 production safeguards were changed.

## 3. Files changed

- `app.py`
- `README.md`
- `src/india_stock_intelligence/fundamental_catalog.py`
- `src/india_stock_intelligence/evidence.py`
- `src/india_stock_intelligence/fundamentals.py`
- `src/india_stock_intelligence/agents/validation.py`
- `src/india_stock_intelligence/agents/sample_backend.py`
- `src/india_stock_intelligence/prompts/equity_research_analyst.md`
- `src/india_stock_intelligence/prompts/independent_judge.md`
- `src/india_stock_intelligence/schemas/equity_research_analyst.json`
- `src/india_stock_intelligence/reports.py`
- `prompts/research_prompt.txt`, `prompts/judge_prompt.txt`, and `prompts/bull_bear_prompt.txt` (kept aligned with packaged prompts)
- `tests/test_fundamentals.py` and `tests/test_agents.py`

The project directory has no Git repository metadata, so a source-control diff against a prior revision was not available. The list above records the files changed in this work; other inspected modules were reused without changes.

## 4. Dashboard runtime and manual imports

Streamlit was invoked locally on `127.0.0.1:8765`. The health request to `http://127.0.0.1:8765/_stcore/health` returned this Windows error:

> An attempt was made to access a socket in a way forbidden by its access permissions. (127.0.0.1:8765)

This prevented a browser response. Therefore **successful startup, page loading, and interactive dashboard behavior are not verified**. Python syntax parsing succeeded for `app.py` and the 41 Python files under `src`.

The import path was exercised directly against temporary databases: synthetic Stock Master records classified as MAINBOARD, SME, and UNKNOWN; importing the master wrote zero price rows. Invalid manual imports returned clear validation errors, including `Stock master JSON must contain a non-empty 'records' array` and `Evidence file must be UTF-8 JSON`. These checks validate import services and messages, but the Streamlit upload controls themselves could not be clicked or runtime-tested due to the loopback restriction.

## 5. Financial calculation validation

All financial fixtures were synthetic and stored only in temporary databases. Expected results were independently recalculated from the fixture values. Units were INR million unless stated otherwise.

| Case | Fixture inputs | Expected and observed |
|---|---|---|
| Annual, standalone | FY2023/FY2024 revenue 100/120; profit 8/12; FY2024 EBITDA 24; operating profit 18; average equity 60; EBIT 24; average capital employed 120; debt/equity inputs 30/60; operating cash flow 35 | Revenue growth 20%; profit growth 50%; EBITDA margin 20%; net margin 10%; ROE 20%; ROCE 20%; debt/equity 0.5; operating cash flow 35 |
| Quarterly, consolidated | Q1 FY2024/FY2025 revenue 40/50; profit 4/6; Q1 FY2025 EBITDA 10; average equity 30; EBIT 9; average capital employed 60; debt/equity inputs 18/30; operating cash flow 13 | Same-quarter YoY revenue growth 25%; profit growth 50%; EBITDA margin 20%; net margin 12%; ROE 20%; ROCE 15%; debt/equity 0.6; operating cash flow 13 |

Additional cases confirmed:

- Annual and quarterly periods are kept distinct; quarterly YoY compares the same fiscal quarter in the prior year.
- Standalone, consolidated, and unspecified evidence are not combined. A cross-basis EBITDA/revenue pair produces no margin.
- Conflicting source values produce a null metric with `conflicting` status. Incompatible units and missing values do not produce a number or default to zero.
- ROE and ROCE calculations require explicit average-equity and average-capital-employed evidence. The engine does not infer average balances from a single closing balance.
- Source name/URL, reporting period, retrieval timestamp, evidence IDs, financial basis, and data status flow into metric results. `verified` is a user-entered label; the application does not authenticate the source document.

## 6. Agent workflow validation

Only synthetic evidence and the deterministic sample backend were used; no external AI was called.

- The Research Analyst output is checked against supplied evidence IDs and the deterministic metric snapshot.
- The Bull/Bear Analyst identifies risks and contradictory evidence instead of treating conflicting values as facts.
- The Independent Judge receives the complete prior analyst reports and can return `INCONCLUSIVE` when evidence is insufficient.
- A synthetic no-evidence case returned `INCONCLUSIVE`; a `BUY_CANDIDATE` with missing evidence was rejected by validation. Synthetic evidence cannot pass the BUY evidence gate.
- Missing facts remain explicitly unavailable. No real-security agent execution was performed.

## 7. Excel and PDF export validation

Reports were generated from temporary databases and synthetic fixtures. The Excel workbook includes `Stock Master`, `Fundamentals`, `Agent Research`, and `Agent Outputs` sheets. Tests confirmed the basis, reporting period, missing/conflicting status, source URL, and `INCONCLUSIVE` agent output are retained. No production workbook or report was written.

PDFs were rendered with the installed Poppler tools and visually inspected. The fundamentals-only sample produced a five-page landscape-letter report; the synthetic research sample produced a four-page report. The metric tables show period/basis/status, source evidence, notes, and blank missing/conflicting values. The agent report page shows the `INCONCLUSIVE` verdict, reasoning, risks/contradictions, and missing evidence categories. Page breaks keep the metric tables and section headings readable. Poppler printed `Syntax Error: No display font for 'Symbol'`; the rendered pages still displayed their contents and no clipping was observed in the inspected pages.

## 8. Real-data readiness checklist and blank template

No real company, security identifier, financial value, or source reference is supplied here. Fill these slots only after choosing a source whose terms permit the intended manual retention and research use. This is a preparation template, **not a valid import file until completed and reviewed**.

### Stock Master slots

| Slot | Exchange | Segment | Symbol | Company name | ISIN | Security status | Effective date | Source name | Source URL | Publication date | Retrieved at (timezone required) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Company 1 | NSE | MAINBOARD |  |  |  |  |  |  |  |  |  |
| Company 2 | NSE | MAINBOARD |  |  |  |  |  |  |  |  |  |
| Company 3 | NSE | SME |  |  |  |  |  |  |  |  |  |

### One row per financial datapoint

| Company slot / security key | Metric key | Value | Unit/currency | Reporting period | Standalone/consolidated | Source type | Source name | Source URL | Publication date | Retrieved at (timezone required) | Verification status | Excerpt/evidence note |
|---|---|---:|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |  | unverified |  |

### Before importing

1. Confirm the source's terms permit local retention and the intended research/AI use. Record the source page and access date; unresolved permission means do not import.
2. Record the source name, direct URL when available, publication date, retrieval timestamp with timezone, and source file checksum. Keep original files unchanged.
3. Verify NSE symbol, company name, ISIN, listing segment, and security status against the selected source. Leave unknown fields blank or `UNKNOWN`; do not infer a classification.
4. Enter one financial datapoint per evidence row with its actual reporting period, unit, and statement basis. Do not mix standalone and consolidated facts.
5. Preserve missing values as blank/null, never zero. Preserve conflicting source values with their separate provenance; do not choose one without review.
6. Check period alignment, unit consistency, duplicate evidence, dates, and source links. Mark `verified` only after a human has reviewed the source; that label is not automated authentication.
7. Import only into an approved staging database first, generate both reports, inspect provenance/status labels, and resolve validation flags before any later production approval.

## 9. Local AI, execution cost, and remaining blockers

- **Local AI:** unavailable. No Ollama command, process, or Python package was found; the `openai` package is also absent. The deterministic calculations and synthetic backend work without an LLM.
- **Cost and packages:** ₹0 spent; no packages installed; no paid services or external AI calls used.
- **Provider status:** the common provider interface is separate from research logic. Manual file adapters work offline. Automatic NSE/other-provider retrieval remains disabled, and no provider's legal authority for automated storage or AI research is assumed.
- **Remaining blockers:** dashboard browser runtime is unverified because loopback access was denied; data-source permission has not been resolved; no real data or security has been validated; local AI is unavailable. ROE/ROCE and other derived values require appropriately reported, period-aligned evidence. Direct valuation metrics require supplied, sourced evidence and are not filled from invented prices.

## 10. Production database integrity

The production database and its SQLite WAL/SHM files match the pre-validation SHA-256 values:

| File | Size | SHA-256 | Result |
|---|---:|---|---|
| `database/stock_intelligence.db` | 1,200,128 bytes | `66EFFE3DCEFB532534C55EE0BB6D708C3C8BB70512CAC1E4A9A6A27AB31C44B4` | Unchanged |
| `database/stock_intelligence.db-wal` | 0 bytes | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` | Unchanged |
| `database/stock_intelligence.db-shm` | 32,768 bytes | `FD4C9FDA9CD3F9AE7C962B0DDF37232294D55580E1AA165AA06129B8549389EB` | Unchanged |

All validation databases and generated test reports were under `.tmp/`. No production schema migration, production data write, scheduled task, live ingestion, external API call, or real-security research run occurred.

## 11. Test results and stop point

- Full suite: **59 passed** (`.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp .tmp/stage3a_pytest_full_final`).
- Focused Stage 3A calculations, import validation, exports, and agent checks: **22 passed**.
- Final synthetic Excel/PDF generation check: **1 passed**; PDF rendered and visually inspected.
- Static validation: `app.py` plus 41 source Python files parsed; all JSON schemas parsed.

Stage 3A validation ends here. Real-data import, production integration, live provider setup, and Stage 4 work require separate approval.
