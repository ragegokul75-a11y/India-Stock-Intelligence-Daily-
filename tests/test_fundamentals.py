from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from india_stock_intelligence.config import Settings
from india_stock_intelligence.evidence import EvidenceBundle, EvidenceError, EvidenceItem, EvidenceRepository, EvidenceStore
from india_stock_intelligence.fundamentals import FundamentalAnalyzer
from india_stock_intelligence.migrations import apply_migrations
from india_stock_intelligence.providers.base import ProviderNotConfigured
from india_stock_intelligence.providers.research import ManualResearchProvider
from india_stock_intelligence.reports import ReportGenerator
from india_stock_intelligence.stock_master import SecurityMasterRepository, SecurityMasterStore, StockMasterError
from india_stock_intelligence.storage import connect


def _settings(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    apply_migrations(settings.database_path)
    return settings


def _import_master(settings):
    document = {
        "source": {
            "source_name": "Synthetic test listing fixture",
            "source_url": "https://example.org/nse-master.csv",
            "publication_date": "2025-06-01",
            "retrieved_at": "2025-06-02T10:00:00+05:30",
        },
        "records": [
            {"symbol": "MAIN", "company_name": "Mainboard Fixture Ltd", "isin": "INE123456789",
             "listing_segment": "MAINBOARD", "security_status": "ACTIVE"},
            {"symbol": "SMECO", "company_name": "SME Fixture Ltd", "isin": "INE987654321",
             "listing_segment": "NSE EMERGE", "security_status": "ACTIVE"},
            {"symbol": "UNKNOWN", "company_name": "Unclassified Fixture Ltd"},
        ],
    }
    result = ManualResearchProvider().import_stock_master_bytes(
        "master.json", json.dumps(document).encode(), SecurityMasterStore(settings=settings))
    assert result["price_records_written"] == 0
    return result["security_keys"]


def _record(security_key, metric_key, value, period, *, row_number=1, unit="INR million",
            verification_status="verified", is_synthetic=True, source_name="Synthetic test filing",
            financial_basis="unspecified"):
    return {
        "security_key": security_key,
        "source_type": "annual_report",
        "source_name": source_name,
        "source_url": f"https://example.org/source-{row_number}.pdf",
        "publication_date": "2025-06-01",
        "reporting_period": period,
        "retrieved_at": "2025-06-02T10:00:00+05:30",
        "claim": f"{metric_key} value for {period}",
        "metric_key": metric_key,
        "value": value,
        "unit": unit,
        "verification_status": verification_status,
        "is_synthetic": is_synthetic,
        "financial_basis": financial_basis,
    }


def test_stock_master_covers_mainboard_and_sme_without_writing_prices(tmp_path):
    settings = _settings(tmp_path)
    main_key, sme_key, unknown_key = _import_master(settings)
    rows = SecurityMasterRepository(settings=settings).list_securities()
    by_key = {row["security_key"]: row for row in rows}

    assert by_key[main_key]["listing_segment"] == "MAINBOARD"
    assert by_key[sme_key]["listing_segment"] == "SME"
    assert by_key[unknown_key]["listing_segment"] == "UNKNOWN"
    assert by_key[main_key]["isin"] == "INE123456789"
    assert by_key[main_key]["source_url"] == "https://example.org/nse-master.csv"
    with connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0] == 0


def test_research_provider_has_no_automatic_retrieval():
    provider = ManualResearchProvider()
    with pytest.raises(ProviderNotConfigured, match="retrieval is disabled"):
        provider.fetch_stock_master()
    with pytest.raises(ProviderNotConfigured, match="retrieval is disabled"):
        provider.fetch_fundamental_evidence()


def test_invalid_manual_imports_return_specific_validation_errors(tmp_path):
    settings = _settings(tmp_path)
    provider = ManualResearchProvider()
    with pytest.raises(StockMasterError, match="non-empty 'records' array"):
        provider.import_stock_master_bytes("bad.json", b"{}", SecurityMasterStore(settings=settings))
    with pytest.raises(EvidenceError, match="UTF-8 JSON"):
        provider.import_fundamental_evidence_bytes("bad.json", b"not json", EvidenceStore(settings=settings))


def test_fundamental_snapshot_calculates_only_comparable_provenance_linked_values(tmp_path):
    settings = _settings(tmp_path)
    security_key = _import_master(settings)[0]
    records = [
        _record(security_key, "revenue", 100, "FY2024", row_number=1),
        _record(security_key, "revenue", 150, "FY2025", row_number=2),
        _record(security_key, "net_profit", 10, "FY2024", row_number=3),
        _record(security_key, "net_profit", 12, "FY2025", row_number=4),
        _record(security_key, "operating_profit", 30, "FY2025", row_number=5),
        _record(security_key, "total_debt", 30, "FY2025", row_number=6),
        _record(security_key, "shareholders_equity", 60, "FY2025", row_number=7),
        _record(security_key, "operating_cash_flow", 25, "FY2025", row_number=8),
        _record(security_key, "capital_expenditure", 10, "FY2025", row_number=9),
        _record(security_key, "roe", 20, "FY2025", row_number=10, unit="%"),
        _record(security_key, "roce", 18, "FY2025", row_number=11, unit="%",
                verification_status="unverified"),
        _record(security_key, "promoter_holding", 51, "Q4 FY2025", row_number=12, unit="%",
                is_synthetic=True),
        _record(None, "revenue", 999, "FY2025", row_number=13),
    ]
    payload = json.dumps({"records": records}).encode()
    imported = EvidenceStore(settings=settings).import_json_bytes("fundamentals.json", payload)
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-06-03")
    snapshot = FundamentalAnalyzer().analyze(bundle)
    metrics = {item.metric_key: item for item in snapshot.metrics}

    assert imported["records_inserted"] == len(records)
    assert metrics["revenue_growth_yoy"].value == 50.0
    assert metrics["revenue_growth_yoy"].data_status == "synthetic"
    assert metrics["revenue_growth_yoy"].reporting_period == "FY2025"
    assert len(metrics["revenue_growth_yoy"].evidence_ids) == 2
    assert metrics["operating_margin"].value == 20.0
    assert metrics["net_profit_margin"].value == 8.0
    assert metrics["debt_to_equity"].value == 0.5
    assert metrics["free_cash_flow"].value == 15.0
    assert metrics["roe"].data_status == "synthetic"
    assert metrics["roce"].data_status == "synthetic"
    assert next(item for item in bundle.evidence_items if item.metric_key == "roce").verification_status == "unverified"
    assert metrics["promoter_holding"].data_status == "synthetic"
    assert snapshot.sample_data is True
    assert metrics["institutional_holding"].value is None
    assert metrics["institutional_holding"].data_status == "missing"
    source = metrics["operating_margin"].sources[0]
    assert source["source_url"].startswith("https://example.org/")
    assert source["retrieved_at"].endswith("+00:00")


def test_annual_and_quarterly_calculations_cover_metrics_and_keep_statement_bases_separate(tmp_path):
    settings = _settings(tmp_path)
    security_key = _import_master(settings)[0]
    records = []
    annual_facts = [
        ("revenue", 100, "FY2023"), ("revenue", 120, "FY2024"),
        ("net_profit", 8, "FY2023"), ("net_profit", 12, "FY2024"),
        ("ebitda", 24, "FY2024"), ("operating_profit", 18, "FY2024"),
        ("total_debt", 30, "FY2024"), ("shareholders_equity", 60, "FY2024"),
        ("average_shareholders_equity", 60, "FY2024"), ("ebit", 24, "FY2024"),
        ("average_capital_employed", 120, "FY2024"), ("operating_cash_flow", 35, "FY2024"),
    ]
    quarter_facts = [
        ("revenue", 40, "Q1 FY2024"), ("revenue", 50, "Q1 FY2025"),
        ("net_profit", 4, "Q1 FY2024"), ("net_profit", 6, "Q1 FY2025"),
        ("ebitda", 10, "Q1 FY2025"), ("total_debt", 18, "Q1 FY2025"),
        ("shareholders_equity", 30, "Q1 FY2025"), ("average_shareholders_equity", 30, "Q1 FY2025"),
        ("ebit", 9, "Q1 FY2025"), ("average_capital_employed", 60, "Q1 FY2025"),
        ("operating_cash_flow", 13, "Q1 FY2025"),
    ]
    for index, (key, value, period) in enumerate(annual_facts, start=1):
        records.append(_record(security_key, key, value, period, row_number=index, financial_basis="standalone"))
    for index, (key, value, period) in enumerate(quarter_facts, start=len(records) + 1):
        records.append(_record(security_key, key, value, period, row_number=index, financial_basis="consolidated"))
    EvidenceStore(settings=settings).import_json_bytes("basis_facts.json", json.dumps({"records": records}).encode())
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-06-03")
    snapshot = FundamentalAnalyzer().analyze(bundle)
    metrics = {(item.metric_key, item.financial_basis): item for item in snapshot.metrics}

    assert metrics[("revenue_growth_yoy", "standalone")].value == 20.0
    assert metrics[("profit_growth_yoy", "standalone")].value == 50.0
    assert metrics[("ebitda_margin", "standalone")].value == 20.0
    assert metrics[("net_profit_margin", "standalone")].value == 10.0
    assert metrics[("roe", "standalone")].value == 20.0
    assert metrics[("roce", "standalone")].value == 20.0
    assert metrics[("debt_to_equity", "standalone")].value == 0.5
    assert metrics[("operating_cash_flow", "standalone")].value == 35

    assert metrics[("revenue_growth_yoy", "consolidated")].value == 25.0
    assert metrics[("profit_growth_yoy", "consolidated")].value == 50.0
    assert metrics[("ebitda_margin", "consolidated")].value == 20.0
    assert metrics[("net_profit_margin", "consolidated")].value == 12.0
    assert metrics[("roe", "consolidated")].value == 20.0
    assert metrics[("roce", "consolidated")].value == 15.0
    assert metrics[("debt_to_equity", "consolidated")].value == 0.6
    assert metrics[("operating_cash_flow", "consolidated")].value == 13
    assert metrics[("revenue_growth_yoy", "standalone")].reporting_period == "FY2024"
    assert metrics[("revenue_growth_yoy", "consolidated")].reporting_period == "Q1 FY2025"


def test_standalone_and_consolidated_inputs_are_never_combined(tmp_path):
    settings = _settings(tmp_path)
    security_key = _import_master(settings)[0]
    records = [
        _record(security_key, "ebitda", 25, "FY2025", row_number=1, financial_basis="standalone"),
        _record(security_key, "revenue", 100, "FY2025", row_number=2, financial_basis="consolidated"),
    ]
    EvidenceStore(settings=settings).import_json_bytes("mixed_basis.json", json.dumps({"records": records}).encode())
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-06-03")
    metrics = {(item.metric_key, item.financial_basis): item for item in FundamentalAnalyzer().analyze(bundle).metrics}

    assert metrics[("ebitda_margin", "standalone")].value is None
    assert metrics[("ebitda_margin", "standalone")].data_status == "missing"
    assert metrics[("ebitda_margin", "consolidated")].value is None
    assert metrics[("ebitda_margin", "consolidated")].data_status == "missing"


def test_verified_and_unverified_labels_are_preserved_for_non_synthetic_evidence():
    item = EvidenceItem(
        evidence_id="unit-test-evidence", security_key="NSE:symbol:TEST", source_type="annual_report",
        source_name="Unit test input", source_url="https://example.org/unit-test.pdf",
        publication_date="2025-06-01", reporting_period="FY2025", retrieved_at="2025-06-02T00:00:00+00:00",
        claim="Unit test ROE value", value=20, unit="%", excerpt=None, source_sha256="0" * 64,
        source_file="unit-test.json", is_synthetic=False, metric_key="roe", verification_status="verified",
    )
    bundle = EvidenceBundle(
        security={"security_key": "NSE:symbol:TEST"}, as_of_date="2025-06-03", screening_snapshot=None,
        evidence_items=(item,), quality_flags=(), missing_categories=(), sample_data=False,
    )
    metrics = {metric.metric_key: metric for metric in FundamentalAnalyzer().analyze(bundle).metrics}
    assert metrics["roe"].value == 20
    assert metrics["roe"].data_status == "verified"

    from dataclasses import replace
    unverified_bundle = replace(bundle, evidence_items=(replace(item, verification_status="unverified"),))
    unverified = {metric.metric_key: metric for metric in FundamentalAnalyzer().analyze(unverified_bundle).metrics}
    assert unverified["roe"].data_status == "unverified"


def test_conflicting_sources_and_incompatible_units_never_produce_a_number(tmp_path):
    settings = _settings(tmp_path)
    security_key = _import_master(settings)[0]
    records = [
        _record(security_key, "revenue", 100, "FY2024", row_number=1),
        _record(security_key, "revenue", 150, "FY2025", row_number=2),
        _record(security_key, "revenue", 155, "FY2025", row_number=3, source_name="Other fixture filing"),
        _record(security_key, "operating_profit", 30, "FY2025", row_number=4),
        _record(security_key, "operating_margin", 25, "FY2025", row_number=5, unit="%"),
        _record(security_key, "net_profit", 12, "FY2025", row_number=6),
        _record(security_key, "shareholders_equity", 1, "FY2025", row_number=7, unit="INR thousand"),
        _record(security_key, "total_debt", 30, "FY2025", row_number=8, unit="INR million"),
    ]
    EvidenceStore(settings=settings).import_json_bytes("conflicts.json", json.dumps({"records": records}).encode())
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-06-03")
    metrics = {item.metric_key: item for item in FundamentalAnalyzer().analyze(bundle).metrics}

    assert metrics["revenue_growth_yoy"].value is None
    assert metrics["revenue_growth_yoy"].data_status == "conflicting"
    assert metrics["operating_margin"].value is None
    assert metrics["operating_margin"].data_status == "conflicting"
    assert metrics["debt_to_equity"].value is None
    assert metrics["debt_to_equity"].data_status == "missing"


def test_excel_and_pdf_reports_generate_from_fundamentals_without_market_prices(tmp_path):
    settings = _settings(tmp_path)
    security_key = _import_master(settings)[0]
    records = [
        _record(security_key, "roe", 17.5, "FY2025", unit="%", row_number=1,
                source_name="Synthetic test filing"),
        _record(security_key, "revenue", 90, "FY2024", row_number=2),
        _record(security_key, "revenue", 100, "FY2025", row_number=3),
        _record(security_key, "revenue", 110, "FY2025", row_number=4,
                source_name="Synthetic conflicting test filing"),
    ]
    EvidenceStore(settings=settings).import_json_bytes("fundamentals.json", json.dumps({"records": records}).encode())

    result = ReportGenerator(settings=settings).generate()
    workbook = load_workbook(result["files"]["excel"], read_only=True, data_only=True)
    assert {"Stock Master", "Fundamentals", "Agent Research", "Agent Outputs"}.issubset(workbook.sheetnames)
    fundamentals = list(workbook["Fundamentals"].values)
    headers = fundamentals[0]
    roe_row = next(row for row in fundamentals[1:] if row[headers.index("metric_key")] == "roe")
    assert roe_row[headers.index("value")] == 17.5
    assert roe_row[headers.index("data_status")] == "synthetic"
    assert roe_row[headers.index("financial_basis")] == "unspecified"
    assert result["sample_data"] is True
    assert "https://example.org/source-1.pdf" in roe_row[headers.index("source_urls")]
    growth_row = next(row for row in fundamentals[1:] if row[headers.index("metric_key")] == "revenue_growth_yoy")
    assert growth_row[headers.index("value")] is None
    assert growth_row[headers.index("data_status")] == "conflicting"
    missing_row = next(row for row in fundamentals[1:] if row[headers.index("metric_key")] == "institutional_holding")
    assert missing_row[headers.index("data_status")] == "missing"
    workbook.close()

    assert result["market_data_date"] is None
    assert result["report_as_of_date"]
    pdf_bytes = Path(result["files"]["pdf"]).read_bytes()
    assert pdf_bytes.startswith(b"%PDF")
    assert b"/Type /Page" in pdf_bytes and b"/Annots" in pdf_bytes
    with connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0] == 0
