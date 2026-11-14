# Stage 2 — Official NSE/BSE Data Source and Ingestion Plan

**Prepared:** 2026-11-06  
**Scope:** Read-only project inspection and source research. No source code, database, schema, packages, or tests were changed; no migrations, market-data downloads, or paid API calls were run.

## Executive assessment

The active implementation is under `src/india_stock_intelligence`; root `agents/`, `core/`, and `providers/` are compatibility facades. The dashboard uses the canonical package. NSE/BSE adapters implement manual `import_file` and `import_bytes`; `download_eod` deliberately raises `ProviderNotConfigured`, and downloads are disabled. The CLI supports manual import, not fetching.

The Stage 1 safety work is present in reviewed code and described in the approved implementation and independent verification reports: query-only read connections, explicit migrations (versions 1–3), audited price corrections, retained raw source rows, and resumable research batches. Preserve these behaviors.

NSE’s official reports page lists CM UDiFF Common Bhavcopy (ZIP), a separate SME Bhavcopy (CSV), and security files. NSE states the older CM common bhavcopy was discontinued on 2024-07-08 in favor of UDiFF. BSE publishes official equity history/report pages and EOD products; its dedicated self-data-feed route requires registration/KYC, a plan, pricing/agreement and payment. A zero-cost, unattended BSE download path and its terms were not verified. Recommend a **manual-first** Stage 2, with no automated BSE fetcher until the applicable terms are clear.

The Stage 1 independent verification report says there is no Git metadata, so a VCS diff could not be checked. This assessment is based on current files and the two Stage 1 reports.

## Verified sources and access status

| Source | Official URL and verified contents | Cost/access caveat |
|---|---|---|
| NSE EOD reports | [NSE All Reports](https://www.nseindia.com/all-reports) lists CM-UDiFF Common Bhavcopy Final (ZIP), SME-Bhavcopy (CSV), NSE security files, full bhavcopy/delivery and related reports. | Public report listings are verified; this does not establish permission for unattended polling, bulk retention or redistribution. |
| NSE file format | [NSE Forms & Formats](https://www.nseindia.com/static/resources/forms-formats-members) publishes UDiFF catalogues, file formats, sample reports and circulars. | Use the current catalogue and its version; do not assume dated download filenames are stable endpoints. |
| NSE universe | The reports page includes files labelled “NSE Listed securities” and “NSE Listed and BSE Exclusive securities,” plus securities available for trading. [NSE All Reports](https://www.nseindia.com/all-reports) | The BSE-exclusive security file is auxiliary mapping, not a complete BSE master. |
| NSE issuer filings | [NSE Corporate Filings](https://www.nseindia.com/companies-listing/corporate-filings-application) covers announcements, corporate actions, board meetings, results and shareholding; pages include Equity/SME. | Public browsing is verified; unattended document harvesting/reuse terms still require confirmation. |
| BSE EOD/history | SEBI’s [Equity Cash Market source index](https://www.sebi.gov.in/curation/equity_cash_market.html) points to BSE [price/volume history](https://www.bseindia.com/markets/equity/EQReports/StockPrcHistori.aspx?flag=0), [historical equity reports](https://www.bseindia.com/markets/Equity/EQReports/Historical_EquitySegment.aspx), and [Bhav Copy](https://www.bseindia.com/markets/MarketInfo/BhavCopy.aspx). | Official landing pages are identified. Free bulk access and unattended collection terms were not verified. |
| BSE feed/products | [BSE Self Data Feed](https://marketdata.bseindia.com/) describes equity, corporate and delayed data feeds and requires KYC, plan selection, pricing/agreements and payment. The [BSE information-products tariff](https://www.bseindia.com/downloads1/Information_Products_Pricing_Sheet.pdf) is dated Feb 2025 and lists subscription EOD/reference products. | Do not use this feed or treat its limited free trial as a continuing free license. Paid access needs separate cost and rights approval. |
| BSE SME classification | The [BSE Equity Segment Master Circular](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=8a7fec3a-95bc-4bd2-82de-76e2a84e2915&id=20250429-51) places SME in the equity segment and says SME securities fall in M/MT/MS/TS groups. | This confirms SME coverage exists; group code alone is not a timeless SME flag. Track platform status and migrations from official records. |
| Data sharing | [SEBI’s 2022 access/usage circular](https://www.sebi.gov.in/legal/circulars/feb-2022/approach-to-securities-market-data-access-and-terms-of-usage-of-data-provided-by-data-sources-in-indian-securities-market_56420.html) calls for mandated reporting/disclosure data to be freely disseminated in downloadable form and chargeable data to be identified. The [2024 research/analysis policy](https://www.sebi.gov.in/legal/circulars/dec-2024/policy-for-sharing-data-for-the-purpose-of-research-analysis_90088.html) concerns accredited academic research; NSE says its formal [Data for Research](https://www.nseindia.com/static/research/initiatives-data-for-research) access is academic and non-commercial. NSE’s [Data Sharing & Usage Policy](https://www.nseindia.com/static/market-data/nse-data-policy) requires agreements for subscriber access, prices commercial market data and restricts redistribution absent agreement. | Website access, academic research data, commercial feeds, automation, retention and redistribution are separate permissions. No blanket scraping/API permission was verified. Keep collection manual until source-specific terms are confirmed. |

**Verified vs inferred:** Report listings, UDiFF documentation, BSE feed flow/tariff, BSE SME group rule and cited policy text are verified from exchange/regulator pages. That a BSE public report can be repeatedly fetched at no cost by a scheduled collector is **not verified**. Do not treat it as approved for automation yet.

## Existing data-model fit and gaps

- `MarketImporter` accepts CSV/ZIP/XLSX/TSV/TXT, hashes and retains original bytes, normalizes columns, stores source rows/statuses, validates prices/dates, and prevents replacement unless an audited correction is explicit.
- `source_records` holds source name/file, SHA-256, row number, raw row JSON, import time and status. `daily_prices` is unique by exchange-specific security key and date. `securities` has symbol, identifier, type, listing status and source timestamp.
- Price-row provenance lacks direct source URL, retrieval time separate from import time, report/segment, parser/catalogue version, access basis and artifact-level result. `evidence_items` has richer provenance for research evidence, not price artifacts.
- No explicit board/platform or effective-dated listing history, corporate-action adjustment series, holiday calendar, or verified all-exchange universe exists. README documents these gaps.
- Keep exchange in each security key. NSE and BSE observations for one ISIN remain separate. An absent row means “not observed,” not delisted/inactive.

## Phased ingestion plan

### Phase 0 — Access boundary
Keep manual-only as default. Confirm intended local use, retention, scheduling and redistribution rights per source. For zero cash, exclude BSE’s paid self-data-feed products. If BSE public-page terms do not clearly allow scheduled retrieval, keep BSE collection as user-supplied files. Do not reverse-engineer undocumented endpoints or bypass access controls.

### Phase 1 — Bounded manual fixtures
After approval to use real sample files, manually obtain a small set from official pages: NSE main-market CM UDiFF, NSE SME CSV, BSE equity EOD, and BSE SME examples if identifiable. Include migrated SME examples, non-trading days and empty files. Keep original bytes and compare dates/identifiers against official security/listing files. Do not backfill all history yet.

### Phase 2 — Offline deterministic parsers
Add separate parsers for NSE CM UDiFF, NSE SME CSV and the approved BSE equity format. Map to canonical EOD rows while preserving source fields such as series, group/platform and deliverable quantity when supplied. Store mainboard/SME as explicit sourced, effective-dated classification; do not infer it from names or symbol patterns.

Reuse `MarketImporter` checks, byte retention, duplicate semantics and Stage 1 correction audit. Any schema change must be a separately reviewed, versioned migration; this plan makes none. No added package appears necessary for CSV/ZIP parsing with existing stdlib/pandas.

### Phase 3 — Artifact provenance and reconciliation
Recommended fields: exchange; platform/segment; report type; trade date; landing and artifact URLs; UTC retrieval time; original filename; HTTP status/content type (for a future approved fetch); byte count; SHA-256; parser and source-format version; access/license classification; parse status and row counts. Link imported rows to an artifact record and preserve raw payloads and source-record IDs.

Reconcile duplicates, date/exchange mismatches, unknown identifiers, missing expected members, empty files, changed headers and revisions. Record listing/platform transitions as dated events. Store corporate actions separately; never silently replace raw closes with adjusted prices.

### Phase 4 — Optional scheduled retrieval and issuer evidence
Only after explicit source-specific permission, add a narrow scheduler/fetch path for approved official URLs, with allowlisted exchange hosts, timeouts/rate limits, hashes and transient-only retries. Keep manual import. Add financial results, announcements, shareholding, actions and annual reports later as separate evidence sources. Keep real-time feeds, paid APIs, email and trading outside scope.

## Adapter interface recommendation

Keep current manual methods and disabled-download guarantee. If approved, separate: (1) `discover_reports(date, segment) -> ReportDescriptor`, with no default network effect; (2) `fetch_report(descriptor) -> RawArtifact`, enabled only with verified terms and explicit run approval; (3) pure/offline `parse_report(artifact, format_version) -> NormalizedMarketRow[]`; and (4) `ingest_artifact(artifact, rows, importer)`, reusing the current audited importer. Provider parsers should not write to SQLite or change duplicate/correction behavior.

## Tests to add after implementation approval

Use temporary databases and approved fixtures only; never contact exchanges in tests. Cover NSE UDiFF/SME mapping; each approved BSE file shape; SME classification and migration; symbol/identifier changes; empty/non-trading days; invalid rows and mismatched dates/exchanges; changed headers; duplicate/revised artifact behavior; correction audit linkage; raw-byte hashes and provenance. If fetching is later permitted, mock transport and test disabled-by-default, host allowlisting and transient-only retry. Preserve existing Stage 1 tests. No tests were run for this planning task.

## Approvals required before implementation

1. Confirm local non-redistributive use or describe planned publication/sharing.
2. Approve file scope: NSE CM + SME; BSE EOD + SME classification; later issuer filings.
3. Confirm BSE zero-cost public-download rights; if unclear, approve manual user-supplied BSE files as the boundary.
4. Approve parser/code/test changes. Separately review any migration and backup procedure before running it.
5. Approve automated downloads only after permission is verified. Any paid feed/API requires separate explicit budget and license approval.

**No implementation or access approvals are implied by this plan.**

