from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from openpyxl import load_workbook

from india_stock_intelligence.agents.local_backend import LocalEvidenceAgentBackend
from india_stock_intelligence.agents.schemas import get_schema
from india_stock_intelligence.agents.validation import validate_agent_output
from india_stock_intelligence.config import Settings
from india_stock_intelligence.evidence import EvidenceRepository, EvidenceStore
from india_stock_intelligence.fundamentals import FundamentalAnalyzer
from india_stock_intelligence.migrations import apply_migrations
from india_stock_intelligence.orchestrator import ResearchOrchestrator
from india_stock_intelligence.providers.research import ManualResearchProvider
from india_stock_intelligence.reports import ReportGenerator
from india_stock_intelligence.stock_master import SecurityMasterStore
from india_stock_intelligence.storage import connect_read_only

ROOT = Path(__file__).resolve().parent.parent
STAGE4D_DIR = ROOT / "stage4d"
PROD_DB = ROOT / "database" / "stock_intelligence.db"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def build_stage4d_payloads() -> tuple[dict, dict]:
    stock_master = {
        "source": {
            "source_name": "FY2025-26 Official Annual Reports Corporate Identity Register (Stage 4D)",
            "source_url": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
            "publication_date": "2026-06-30",
            "retrieved_at": "2026-11-06T16:00:00+05:30",
        },
        "records": [
            {
                "exchange": "NSE",
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries Limited",
                "isin": "INE002A01018",
                "listing_segment": "MAINBOARD",
                "security_status": "ACTIVE",
                "effective_date": "2026-03-31",
            },
            {
                "exchange": "NSE",
                "symbol": "TCS",
                "company_name": "Tata Consultancy Services Limited",
                "isin": "INE467B01029",
                "listing_segment": "MAINBOARD",
                "security_status": "ACTIVE",
                "effective_date": "2026-03-31",
            },
            {
                "exchange": "NSE",
                "symbol": "LT",
                "company_name": "Larsen & Toubro Limited",
                "isin": "INE018A01030",
                "listing_segment": "MAINBOARD",
                "security_status": "ACTIVE",
                "effective_date": "2026-03-31",
            },
        ],
    }

    ril_url = "https://www.ril.com/reports/RIL-Integrated-Annual-Report-2025-26.pdf"
    tcs_url = "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2025-26/ar/annual-report-2025-2026.pdf"
    lt_url = "https://investors.larsentoubro.com/upload/AnnualRep/FY2026AnnualRepLNTIARFY2026.pdf"
    pub_date = "2026-06-30"
    retrieved = "2026-11-06T16:00:00+05:30"

    ril_key = "NSE:id:INE002A01018"
    tcs_key = "NSE:id:INE467B01029"
    lt_key = "NSE:id:INE018A01030"

    records = [
        # --- RELIANCE (NSE:id:INE002A01018) ---
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated revenue from operations of ₹1,075,675 crore for FY2025-26.",
            "metric_key": "revenue",
            "value": 1075675,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.199, Consolidated Statement of Profit and Loss: Revenue from operations ₹1,075,675 crore.",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated profit attributable to owners of the company of ₹80,775 crore for FY2025-26 (total group profit ₹95,754 crore).",
            "metric_key": "net_profit",
            "value": 80775,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.199, Consolidated Statement of Profit and Loss: Profit attributable to Owners of the Company ₹80,775 crore (matched to owners' equity for ROE).",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated EBITDA of ₹207,911 crore for FY2025-26 (company-defined consolidated EBITDA; not a uniform peer field).",
            "metric_key": "ebitda",
            "value": 207911,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "Key Indicators / MD&A: Consolidated EBITDA ₹207,911 crore; do not treat as directly interchangeable with TCS EBIT or L&T PBDIT.",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated net cash flow from operating activities of ₹192,113 crore for FY2025-26.",
            "metric_key": "operating_cash_flow",
            "value": 192113,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.203, Consolidated Cash Flow Statement: Net cash flow from operating activities ₹192,113 crore.",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated equity attributable to owners of ₹904,030 crore as at 31-Mar-2026 (FY2025: ₹843,200 crore; total equity including NCI: ₹1,085,866 crore).",
            "metric_key": "shareholders_equity",
            "value": 904030,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.197, Consolidated Balance Sheet: Equity share capital + Other equity attributable to owners = ₹904,030 crore (FY25: ₹843,200 crore).",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Average consolidated equity attributable to owners of ₹873,615 crore for FY2025-26 (average of ₹904,030 crore and ₹843,200 crore).",
            "metric_key": "average_shareholders_equity",
            "value": 873615,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.197, Derived from FY26 (₹904,030 crore) and FY25 (₹843,200 crore) consolidated owners' equity; yields 9.25% owners' ROE.",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Promoter and promoter group shareholding of 49.11% (664,54,96,096 shares) as at 31-Mar-2026 in the Corporate Governance Report; quarterly Regulation 31 filings remain pending.",
            "metric_key": "promoter_holding",
            "value": 49.11,
            "unit": "%",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.60-61, Corporate Governance Report: 49.11% promoter and promoter group holding at 31-Mar-2026. Quarterly Regulation 31 Table III filings still required.",
            "is_synthetic": False,
        },
        {
            "security_key": ril_key,
            "source_type": "annual_report",
            "source_name": "RIL Integrated Annual Report 2025-26",
            "source_url": ril_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated total assets of ₹2,178,140 crore and derived borrowings aggregate of ₹374,421 crore (non-current ₹270,751 cr + current ₹103,670 cr; excludes lease liabilities) as at 31-Mar-2026.",
            "metric_key": None,
            "value": 2178140,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.197, Consolidated Balance Sheet. Total borrowings kept unmapped to total_debt pending lease/debt policy review.",
            "is_synthetic": False,
        },

        # --- TCS (NSE:id:INE467B01029) ---
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated revenue from operations of ₹267,021 crore for FY2025-26.",
            "metric_key": "revenue",
            "value": 267021,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.195-201 & p.254, Consolidated Financial Statements: Revenue from operations ₹267,021 crore.",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated profit attributable to shareholders of ₹49,210 crore for FY2025-26 (total group profit ₹49,454 crore).",
            "metric_key": "net_profit",
            "value": 49210,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.195-201, Consolidated Statement of Profit and Loss: Profit attributable to shareholders ₹49,210 crore (matched to owners' equity for ROE).",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated EBIT of ₹66,838 crore for FY2025-26 (reported after depreciation and amortisation of ₹5,560 crore; excludes other income; not interchangeable with EBITDA).",
            "metric_key": "ebit",
            "value": 66838,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.98, Performance Trend - 10 years: Consolidated EBIT ₹66,838 crore; D&A ₹5,560 crore (derived EBITDA proxy ₹72,398 crore is not directly reported).",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated net cash flows generated from operating activities of ₹52,094 crore for FY2025-26.",
            "metric_key": "operating_cash_flow",
            "value": 52094,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.201, Consolidated Statement of Cash Flows: Net cash generated from operating activities ₹52,094 crore.",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated equity attributable to shareholders of ₹107,240 crore as at 31-Mar-2026 (FY2025: ₹94,756 crore; total equity including NCI: ₹108,478 crore).",
            "metric_key": "shareholders_equity",
            "value": 107240,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.195-201, Consolidated Balance Sheet: Equity attributable to shareholders ₹107,240 crore (FY25: ₹94,756 crore).",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Average consolidated equity attributable to shareholders of ₹100,998 crore for FY2025-26 (average of ₹107,240 crore and ₹94,756 crore).",
            "metric_key": "average_shareholders_equity",
            "value": 100998,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.195-201, Derived from FY26 (₹107,240 crore) and FY25 (₹94,756 crore) consolidated owners' equity; yields 48.72% owners' ROE.",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Tata Sons Private Limited (holding company and promoter) held 71.74% (2,59,54,99,419 shares) as at 31-Mar-2026; quarterly Regulation 31 filings remain pending.",
            "metric_key": "promoter_holding",
            "value": 71.74,
            "unit": "%",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "p.227, Consolidated Financial Statement Notes: Tata Sons Private Limited holding 71.74% at 31-Mar-2026. Promoter pledge not separately reported; do not assume zero.",
            "is_synthetic": False,
        },
        {
            "security_key": tcs_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": tcs_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated total assets of ₹182,372 crore as at 31-Mar-2026; no separate borrowings line disclosed (lease liabilities and preference shares are not treated as borrowings aggregate; borrowings left blank, not zero).",
            "metric_key": None,
            "value": 182372,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.195-201, Consolidated Balance Sheet: Total assets ₹182,372 crore. Total borrowings intentionally left blank.",
            "is_synthetic": False,
        },

        # --- L&T (NSE:id:INE018A01030) ---
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated revenue from operations of ₹285,874.36 crore for FY2025-26.",
            "metric_key": "revenue",
            "value": 285874.36,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.43-44, Overall Financial Review & Consolidated Statement of Profit and Loss: Revenue ₹285,874.36 crore.",
            "is_synthetic": False,
        },
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated profit attributable to owners of the parent of ₹16,084 crore (exact ₹16,083.99 crore; total group profit ₹18,953.88 crore) for FY2025-26.",
            "metric_key": "net_profit",
            "value": 16084,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.43-44, Overall Financial Review: Profit attributable to owners ₹16,084 crore (matched to owners' net worth for 15.5% RONW/ROE).",
            "is_synthetic": False,
        },
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated Operating Profit (PBDIT) of ₹29,151 crore for FY2025-26 (before depreciation, interest and tax; excludes exceptional items and other income; financial-services interest included in MCO expenses; not interchangeable with pure EBITDA or EBIT).",
            "metric_key": "operating_profit",
            "value": 29151,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.43-44, Overall Financial Review: Operating Profit (PBDIT) ₹29,151 crore. Retained under operating_profit with explicit definition caveat.",
            "is_synthetic": False,
        },
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated net cash flow from operating activities of ₹16,740.97 crore for FY2025-26.",
            "metric_key": "operating_cash_flow",
            "value": 16740.97,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "Consolidated Statement of Cash Flows: Net cash flow from operating activities ₹16,740.97 crore.",
            "is_synthetic": False,
        },
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated equity attributable to owners (net worth) of ₹109,290 crore as at 31-Mar-2026 (FY2025: ₹97,656 crore; total equity including NCI: ₹128,530.46 crore).",
            "metric_key": "shareholders_equity",
            "value": 109290,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.24, 43-44, Overall Financial Review: Owners' net worth ₹109,290 crore at FY26 (FY25: ₹97,656 crore).",
            "is_synthetic": False,
        },
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Average consolidated equity attributable to owners (average net worth) of ₹103,473 crore for FY2025-26 (average of ₹109,290 crore and ₹97,656 crore).",
            "metric_key": "average_shareholders_equity",
            "value": 103473,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "pp.24, 43-44: Average owners' net worth ₹103,473 crore; calculated ROE of 15.54% agrees with reported 15.5% RONW.",
            "is_synthetic": False,
        },
        {
            "security_key": lt_key,
            "source_type": "annual_report",
            "source_name": "L&T Integrated Annual Report 2025-2026",
            "source_url": lt_url,
            "publication_date": pub_date,
            "reporting_period": "FY2026",
            "retrieved_at": retrieved,
            "claim": "Consolidated total assets of ₹452,549.88 crore and derived borrowings aggregate of ₹121,683.02 crore (non-current ₹63,738.59 cr + current ₹34,778.53 cr + current maturities ₹23,165.90 cr; excludes leases; held-for-sale debt requires review). Promoter holding not separately classified in annual report; left blank.",
            "metric_key": None,
            "value": 452549.88,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "excerpt": "Consolidated Balance Sheet & Overall Financial Review (pp.43-44). Do not reuse unconfirmed Stage 4B page labels 615-621.",
            "is_synthetic": False,
        },
    ]

    return stock_master, {"records": records}


def run_stage4d() -> dict:
    prod_hash_before = sha256_file(PROD_DB)

    if STAGE4D_DIR.exists():
        shutil.rmtree(STAGE4D_DIR)
    STAGE4D_DIR.mkdir(parents=True, exist_ok=True)

    inputs_dir = STAGE4D_DIR / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    stock_master_doc, evidence_doc = build_stage4d_payloads()
    master_json_path = inputs_dir / "stage4d_stock_master.json"
    evidence_json_path = inputs_dir / "stage4d_financial_evidence.json"

    master_json_path.write_text(json.dumps(stock_master_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    evidence_json_path.write_text(json.dumps(evidence_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    # Isolated Stage 4D settings
    settings = Settings(
        data_dir=STAGE4D_DIR / "data",
        database_dir=STAGE4D_DIR / "database",
    )
    migration_info = apply_migrations(settings.database_path)

    provider = ManualResearchProvider()
    master_import = provider.import_stock_master(master_json_path, SecurityMasterStore(settings=settings))
    evidence_import = provider.import_fundamental_evidence(evidence_json_path, EvidenceStore(settings=settings))

    # Run the 3 agents across all 3 companies via ResearchOrchestrator + LocalEvidenceAgentBackend
    backend = LocalEvidenceAgentBackend()
    orchestrator = ResearchOrchestrator(settings=settings, backend=backend, max_attempts=1)
    run_results = orchestrator.run_universe(
        master_import["security_keys"],
        as_of_date="2026-11-14",
        sample_only=False,
    )

    # Generate Excel, PDF, manifest, and EML reports
    report_output = ReportGenerator(settings=settings).generate(sample_data=False)

    # Copy Stage 4B updated and Stage 4C workbooks into stage4d/workbooks for one-stop access
    wb_dir = STAGE4D_DIR / "workbooks"
    wb_dir.mkdir(parents=True, exist_ok=True)
    stage4b_updated = Path(r"C:\Users\Gokul\Documents\Codex\2026-11-14\work-mode-started-your-approval-has\outputs\01a1daa8-eabd-7d30-87d1-b77f25deb370\Stage4B_Annual_Report_Extraction_FY2025-26_updated.xlsx")
    stage4c_wb = Path(r"C:\Users\Gokul\Documents\Codex\2026-11-14\referenced-chatgpt-conversation-this-is-an-4\outputs\01a1dab7-5c83-7ca2-ac15-285319c9ddde\Stage4C_Financial_Evidence_Validation_FY2025-26.xlsx")
    if stage4b_updated.exists():
        shutil.copy2(stage4b_updated, wb_dir / stage4b_updated.name)
    if stage4c_wb.exists():
        shutil.copy2(stage4c_wb, wb_dir / stage4c_wb.name)

    # =========================================================================
    # CHECK 1: Database & Schema & Mathematical Reconciliation
    # =========================================================================
    check1_details = {}
    repo = EvidenceRepository(settings=settings)
    analyzer = FundamentalAnalyzer()
    expected_calc = {
        "NSE:id:INE002A01018": {
            "symbol": "RELIANCE",
            "roe": round(80775 / 873615 * 100, 4),        # 9.2461%
            "ebitda_margin": round(207911 / 1075675 * 100, 4),  # 19.3284%
            "net_profit_margin": round(80775 / 1075675 * 100, 4),  # 7.5092%
            "operating_cash_flow": 192113.0,
            "promoter_holding": 49.11,
            "roce": None,
        },
        "NSE:id:INE467B01029": {
            "symbol": "TCS",
            "roe": round(49210 / 100998 * 100, 4),        # 48.7237%
            "net_profit_margin": round(49210 / 267021 * 100, 4),  # 18.4293%
            "operating_cash_flow": 52094.0,
            "promoter_holding": 71.74,
            "ebitda_margin": None,
            "roce": None,
        },
        "NSE:id:INE018A01030": {
            "symbol": "LT",
            "roe": round(16084 / 103473 * 100, 4),        # 15.5442%
            "operating_margin": round(29151 / 285874.36 * 100, 4),  # 10.1971%
            "net_profit_margin": round(16084 / 285874.36 * 100, 4),  # 5.6263%
            "operating_cash_flow": 16740.97,
            "promoter_holding": None,
            "ebitda_margin": None,
            "roce": None,
        },
    }

    for sec_key, exp in expected_calc.items():
        bundle = repo.build_bundle(sec_key, "2026-11-14")
        snap = analyzer.analyze(bundle)
        by_key = {m.metric_key: m for m in snap.metrics if m.financial_basis == "consolidated"}
        sec_summary = {}
        for mk, exp_val in exp.items():
            if mk == "symbol":
                continue
            actual_val = by_key[mk].value
            if exp_val is None:
                assert actual_val is None, f"{sec_key} {mk} expected None, got {actual_val}"
                assert by_key[mk].data_status == "missing"
            else:
                assert actual_val is not None, f"{sec_key} {mk} expected {exp_val}, got None"
                assert abs(float(actual_val) - exp_val) < 0.01, f"{sec_key} {mk}: {actual_val} != {exp_val}"
                assert by_key[mk].data_status == "verified"
            sec_summary[mk] = {
                "value": round(float(actual_val), 4) if actual_val is not None else None,
                "unit": by_key[mk].unit,
                "status": by_key[mk].data_status,
                "evidence_ids": list(by_key[mk].evidence_ids),
            }
        check1_details[exp["symbol"]] = sec_summary

    # =========================================================================
    # CHECK 2: Independent Re-Validation of Every Agent Output (3 companies x 3 agents = 9 outputs)
    # =========================================================================
    check2_details = {}
    with connect_read_only(settings.database_path) as conn:
        for res in run_results:
            assert res.status == "completed", f"Run failed for {res.security_key}"
            assert res.verdict == "WATCH", f"Expected WATCH for {res.security_key}, got {res.verdict}"
            bundle = repo.build_bundle_from_connection(conn, res.security_key, "2026-11-14")
            agent_rows = conn.execute(
                "SELECT agent_id, output_json FROM agent_outputs WHERE research_run_id=? AND status='completed'",
                (res.research_run_id,),
            ).fetchall()
            assert len(agent_rows) == 3, f"Expected 3 completed agents for {res.security_key}"
            validated_agents = {}
            for row in agent_rows:
                aid = row["agent_id"]
                parsed = json.loads(row["output_json"])
                # Re-validate against JSON schema and citation rules
                validate_agent_output(parsed, agent_id=aid, bundle=bundle, schema=get_schema(aid))
                validated_agents[aid] = {
                    "schema_valid": True,
                    "citations_verified": True,
                    "evidence_digest_matched": parsed["evidence_digest"] == bundle.digest,
                }
            check2_details[res.security_key] = {
                "research_run_id": res.research_run_id,
                "verdict": res.verdict,
                "confidence_score": res.confidence_score,
                "risk_score": res.risk_score,
                "agents": validated_agents,
                "report_path": res.report_path,
            }

    # =========================================================================
    # CHECK 3: Report Artifact & Production DB Isolation Verification
    # =========================================================================
    xlsx_path = Path(report_output["files"]["excel"])
    pdf_path = Path(report_output["files"]["pdf"])
    manifest_path = Path(report_output["files"]["manifest"])
    eml_path = Path(report_output["files"]["email_draft"])

    assert xlsx_path.exists() and xlsx_path.stat().st_size > 0
    assert pdf_path.exists() and pdf_path.read_bytes().startswith(b"%PDF")
    assert manifest_path.exists()
    assert eml_path.exists()

    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    expected_sheets = {"Read Me", "Stock Master", "Fundamentals", "Screening", "Data Quality", "Agent Research", "Agent Outputs"}
    assert expected_sheets.issubset(set(wb.sheetnames))
    agent_research_rows = list(wb["Agent Research"].values)
    agent_outputs_rows = list(wb["Agent Outputs"].values)
    wb.close()
    assert len(agent_research_rows) == 4  # header + 3 companies
    assert len(agent_outputs_rows) == 10  # header + 9 agent outputs (3 companies x 3 agents)

    prod_hash_after = sha256_file(PROD_DB)
    assert prod_hash_before == prod_hash_after, (
        f"Production DB hash changed! Before={prod_hash_before}, After={prod_hash_after}"
    )

    summary_payload = {
        "stage": "Stage 4D — Three-Agent Execution & Triple-Check Verification",
        "migrations": migration_info,
        "master_import": master_import,
        "evidence_import": evidence_import,
        "check_1_deterministic_metrics": check1_details,
        "check_2_three_agent_validation": check2_details,
        "check_3_artifacts_and_isolation": {
            "excel_report": str(xlsx_path),
            "pdf_report": str(pdf_path),
            "manifest_json": str(manifest_path),
            "email_draft": str(eml_path),
            "stage4b_updated_workbook": str(wb_dir / stage4b_updated.name),
            "stage4c_validation_workbook": str(wb_dir / stage4c_wb.name),
            "agent_research_rows_in_excel": len(agent_research_rows) - 1,
            "agent_output_rows_in_excel": len(agent_outputs_rows) - 1,
            "production_db_sha256_before": prod_hash_before,
            "production_db_sha256_after": prod_hash_after,
            "production_db_untouched": prod_hash_before == prod_hash_after,
        },
    }
    summary_path = STAGE4D_DIR / "stage4d_verification_summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary_payload, indent=2, ensure_ascii=False))
    return summary_payload


if __name__ == "__main__":
    run_stage4d()
