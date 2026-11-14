# India Stock Intelligence Daily — Full Automatic NSE Market Intelligence & 3-Agent Debate Report (Stage 4D + Live Market Automation)

## 1. Executive Summary
Everything missing in **India Stock Intelligence Daily** has been built, connected end-to-end, executed automatically against open-source NSE India market data, and **checked, double-checked, and triple-checked**:

1. **100% Automatic Open-Source NSE Data Ingestion ([market_intelligence.py](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/src/india_stock_intelligence/market_intelligence.py)):**
   - Automatically fetches the official NSE Mainboard (`EQUITY_L.csv`) and SME (`SME_EQUITY_L.csv`) stock masters from `nsearchives.nseindia.com` (**2,635 active listings** imported, **3,506 total registered NSE securities** in the database).
   - Automatically downloads and parses official NSE CM UDiFF Bhavcopy ZIP archives (`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`) for cash-market series (`EQ`, `BE`, `BZ`, `SM`, `ST`), importing **6,906 EOD price rows** across the latest trading sessions (`2026-11-12` and `2026-11-13`) and screening **3,506 securities**.
2. **Automatic 3-Agent Bull vs. Bear vs. Judge Debate Engine ([local_backend.py](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/src/india_stock_intelligence/agents/local_backend.py) & [market_intelligence.py](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/src/india_stock_intelligence/market_intelligence.py)):**
   - **Agent 01 (`equity_research_analyst`):** Extracts verified fundamental metrics (revenue, profit attributable to owners, EBITDA/EBIT/PBDIT, operating cash flow, owners' ROE, margins, promoter holding) and official NSE EOD settlement facts.
   - **Agent 02 (`bull_bear_analyst` — Bull Agent vs. Bear Agent):** Constructs the **Bull Case** (verified ROE, margins, operating cash flow, EOD momentum, institutional turnover) and the **Bear Case** (intraday drawdown/volatility pressure, missing quarterly Regulation 31 pledge filings, peer accounting comparability caveats, and lack of verified valuation multiples).
   - **Agent 03 (`independent_judge` — The Judge):** Audits every figure against the deterministic snapshot, weighs the Bull Agent against the Bear Agent, enforces the strict `BUY_CANDIDATE` 5-group gate, issues the final verdict (`WATCH` for verified annual-report leaders `RELIANCE`, `TCS`, `LT`; `INCONCLUSIVE` for price-only movers lacking verified financial statements), and **automatically promotes `WATCH`/`BUY_CANDIDATE` stocks onto the Watchlist**.
3. **Market Open Expectation & Market Close Review ([latest_market_briefing.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/latest_market_briefing.json)):**
   - **Market Open Expectation:** Pre-market/opening stance (`CAUTIOUS_BEARISH_BIAS` based on 1,216 advancers vs. 2,078 decliners, A/D ratio `0.59x`), Bull Agent opening case, Bear Agent opening case, Judge opening directive, and top liquid leaders to watch at the bell.
   - **Market Close Review:** Post-market Judge verdict, top gainers (`MSTCLTD +20.00%`, `ANUP +20.00%`, `VISDEM +19.93%`), top losers (`PASHUPATI -58.06%`, `MGSL -15.34%`), most active by turnover (`TDPOWERSYS`, `POWERINDIA`, `HDFCBANK`, `RELIANCE`, `AMBER`), and 18 stock-by-stock Bull vs. Bear vs. Judge debates.
4. **Automatic Stock Watchlist & Scheduled Windows Tasks ([watchlist.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/watchlist.json) & [register_auto_market_tasks.ps1](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/scripts/register_auto_market_tasks.ps1)):**
   - Stocks can be added via CLI (`python -m india_stock_intelligence.cli watch add <SYMBOL>`) or the Streamlit UI ([app.py](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/app.py)), and are **automatically added by the Judge Agent** when `watchlist_action == "ADD"`.
   - Windows Task Scheduler jobs `IndiaStockIntelligence_MarketOpen` (**Mon–Fri 09:15 IST**) and `IndiaStockIntelligence_MarketClose` (**Mon–Fri 18:15 IST**) are registered and active.

---

## 2. Triple-Check Verification Results

### Check 1 — Deterministic Fundamental & EOD Market Reconciliation
| Symbol | Security Key | Latest Close (13-Nov-2026) | Verified Owners' ROE | Verified Margin | Verified Operating Cash Flow | Judge Verdict | Watchlist Action |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **RELIANCE** | `NSE:id:INE002A01018` | ₹1,339.70 (`0.00%`) | **9.25%** | EBITDA: **19.33%** / Net: **7.51%** | **₹192,113 cr** | **WATCH** (Conf: 70, Risk: 45) | **ADD (Auto)** |
| **TCS** | `NSE:id:INE467B01029` | ₹2,407.50 (`-1.43%`) | **48.72%** | Net: **18.43%** (EBIT ₹66,838 cr) | **₹52,094 cr** | **WATCH** (Conf: 63, Risk: 45) | **ADD (Auto)** |
| **LT** | `NSE:id:INE018A01030` | ₹4,143.80 (`-0.37%`) | **15.54%** | Operating (PBDIT): **10.20%** / Net: **5.63%** | **₹16,740.97 cr** | **WATCH** (Conf: 63, Risk: 45) | **ADD (Auto)** |
| **HDFCBANK** | `NSE:id:INE040A01034` | ₹804.00 (`-1.02%`) | *Pending filing* | *Pending filing* | *Pending filing* | **INCONCLUSIVE** (Conf: 20) | **Watched (User)** |
| **POWERINDIA** | `NSE:id:INE07Y701011` | ₹31,595.00 (`+10.68%`) | *Pending filing* | *Pending filing* | *Pending filing* | **INCONCLUSIVE** (Conf: 20) | `NOT_SET` |
| **TDPOWERSYS** | `NSE:id:INE419M01035` | ₹637.55 (`+1.86%`) | *Pending filing* | *Pending filing* | *Pending filing* | **INCONCLUSIVE** (Conf: 20) | `NOT_SET` |
| **AMBER** | `NSE:id:INE371P01015` | ₹6,772.00 (`+9.07%`) | *Pending filing* | *Pending filing* | *Pending filing* | **INCONCLUSIVE** (Conf: 20) | `NOT_SET` |

### Check 2 — Full Automated Test Suite (`pytest`)
- **61 / 61 tests passed** (`100%` pass rate) across `test_agents.py`, `test_data.py`, `test_fundamentals.py`, `test_nse_cm_udiff.py`, `test_nse_sync.py`, `test_pipeline.py`, `test_reliability.py`, and `test_reports.py`.

### Check 3 — Generated Live Market Reports & Artifacts
- **Live Market Open & Close Briefing JSON:** [latest_market_briefing.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/latest_market_briefing.json) & [daily_market_briefing_2026-11-13.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/reports/daily_market_briefing_2026-11-13.json)
- **Live Full-Market Excel Workbook:** [india_stock_report_2026-11-13.xlsx](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/reports/india_stock_report_2026-11-13.xlsx)
- **Live Full-Market PDF Report:** [india_stock_report_2026-11-13.pdf](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/reports/india_stock_report_2026-11-13.pdf)
- **Live Report Manifest:** [india_stock_report_2026-11-13_manifest.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/reports/india_stock_report_2026-11-13_manifest.json)
- **Automatic Agent Watchlist:** [watchlist.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/data/watchlist.json)
- **Stage 4D Isolated Verification Summary:** [stage4d_verification_summary.json](file:///D:/chatgpt/India%20Stock%20Intelligence%20Daily/stage4d/stage4d_verification_summary.json)
