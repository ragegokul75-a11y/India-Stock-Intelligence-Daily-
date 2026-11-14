from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook

from india_stock_intelligence.config import Settings
from india_stock_intelligence.ingestion import MarketImporter
from india_stock_intelligence.providers.base import ProviderNotConfigured
from india_stock_intelligence.providers.nse import NSEProvider
from india_stock_intelligence.reports import ReportGenerator
from india_stock_intelligence.retry import retry_read_only
from india_stock_intelligence.screening import run_screening
from india_stock_intelligence.storage import connect
from india_stock_intelligence.migrations import apply_migrations


HEADER = ["Exchange", "Symbol", "Company Name", "Security ID", "Security Type", "Listing Status",
          "Date", "Open", "High", "Low", "Close", "Volume", "Source Timestamp", "Source"]


def make_csv(records):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADER)
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue().encode("utf-8")


def row(exchange="NSE", symbol="SAMP", identifier="SAMPLE-ISIN-001", name="Sample Company",
        when="2025-01-01", close="100", volume="1000", source="Manual test file", **extra):
    result = {"Exchange": exchange, "Symbol": symbol, "Company Name": name, "Security ID": identifier,
              "Security Type": "Equity", "Listing Status": "Active", "Date": when,
              "Open": close, "High": close, "Low": close, "Close": close, "Volume": volume,
              "Source Timestamp": "", "Source": source}
    result.update(extra)
    return result


@pytest.fixture
def context(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", stale_after_days=3,
                        suspicious_change_pct=20, corporate_action_change_pct=30)
    apply_migrations(settings.database_path)
    return settings, settings.database_path, MarketImporter(settings=settings)


def test_keeps_exchange_listings_separate_and_preserves_raw(context):
    settings, db, importer = context
    payload = make_csv([
        row("NSE", "DUAL", "SAME-EXPLICIT-ID", "Same Company", "2025-01-01", "100"),
        row("BSE", "DUAL", "SAME-EXPLICIT-ID", "Same Company", "2025-01-01", "101"),
    ])
    result = importer.import_bytes("official.csv", payload)
    with connect(db) as conn:
        securities = conn.execute("SELECT exchange, security_key FROM securities ORDER BY exchange").fetchall()
        prices = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    assert len(securities) == 2
    assert securities[0]["security_key"] != securities[1]["security_key"]
    assert prices == 2
    assert result.rows_inserted == 2
    assert (settings.raw_dir / f"{result.sha256}_official.csv").read_bytes() == payload


def test_missing_and_stale_data_are_flagged_and_metrics_stay_null(context):
    _, db, importer = context
    importer.import_bytes("partial.csv", make_csv([
        row("NSE", "OLD", "OLD-ID", when="2025-01-01", close="40"),
        row("NSE", "OLD", "OLD-ID", when="2025-01-09", close=""),
    ]))
    with connect(db) as conn:
        checks = {row["check_type"] for row in conn.execute("SELECT check_type FROM data_quality_checks")}
        assert "missing_value" in checks
        assert "stale_data" in checks
        run_screening(conn, "2025-01-10")
        metrics = conn.execute("SELECT * FROM screening_results WHERE security_key='NSE:id:OLD-ID'").fetchone()
        stored = conn.execute("SELECT close FROM daily_prices WHERE security_key='NSE:id:OLD-ID' AND price_date='2025-01-09'").fetchone()[0]
    assert stored is None
    assert metrics["return_1d_pct"] is None
    assert metrics["ma_50"] is None
    assert metrics["ma_200"] is None
    assert metrics["return_252d_pct"] is None


def test_duplicate_rows_invalid_dates_and_price_change_flags(context):
    _, db, importer = context
    importer.import_bytes("quality.csv", make_csv([
        row(when="2025-01-01", close="100"),
        row(when="2025-01-01", close="102"),
        row(when="not-a-date", close="99"),
        row(when="2025-01-02", close="60"),
    ]))
    with connect(db) as conn:
        checks = {row["check_type"] for row in conn.execute("SELECT check_type FROM data_quality_checks")}
        prices = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    assert prices == 2
    assert "duplicate_record" in checks
    assert "invalid_date" in checks
    assert "suspicious_price_change" in checks
    assert "possible_corporate_action" in checks


def test_sufficient_history_calculates_metrics(context):
    _, db, importer = context
    records = []
    day = date(2025, 1, 1)
    index = 0
    while len(records) < 270:
        if day.weekday() < 5:
            close = 50 + index * 0.15 + math.sin(index / 7)
            records.append(row(when=day.isoformat(), close=f"{close:.4f}", volume=str(1000 + index * 3)))
            index += 1
        day += timedelta(days=1)
    importer.import_bytes("history.csv", make_csv(records))
    with connect(db) as conn:
        run_screening(conn)
        run_screening(conn)
        assert conn.execute("SELECT COUNT(*) FROM screening_results").fetchone()[0] == 1
        metrics = conn.execute("SELECT * FROM screening_results ORDER BY screening_id DESC LIMIT 1").fetchone()
    assert metrics["ma_50"] is not None
    assert metrics["ma_200"] is not None
    assert metrics["return_252d_pct"] is not None
    assert metrics["volatility_20d_pct"] is not None
    assert metrics["max_drawdown_252d_pct"] is not None
    assert metrics["volume_change_20d_pct"] is not None


def test_zip_csv_import_and_manual_provider_disabled(context):
    _, db, importer = context
    inner = make_csv([row()])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("prices.csv", inner)
    summary = importer.import_bytes("exchange.zip", buffer.getvalue())
    assert summary.rows_inserted == 1
    with pytest.raises(ProviderNotConfigured):
        NSEProvider().download_eod()


def test_provider_specific_column_aliases_are_configurable(context):
    _, db, importer = context
    payload = (
        "Market,Ticker,Issuer,SecId,Instrument,Status,TradingDt,ClosingPx,Qty\n"
        "NSE,CONFIG,Configured Company,CUSTOM-ID,Equity,Active,2025-02-03,25.5,700\n"
    ).encode("utf-8")
    provider = NSEProvider(source_name="NSE mapped CSV", column_aliases={
        "exchange": {"Market"}, "symbol": {"Ticker"}, "company_name": {"Issuer"},
        "security_identifier": {"SecId"}, "security_type": {"Instrument"},
        "listing_status": {"Status"}, "date": {"TradingDt"}, "close": {"ClosingPx"}, "volume": {"Qty"},
    })
    result = provider.import_bytes("mapped.csv", payload, importer)
    with connect(db) as conn:
        row_data = conn.execute("SELECT exchange, symbol, company_name, security_type FROM securities").fetchone()
        source = conn.execute("SELECT source_name FROM source_records").fetchone()[0]
    assert result.rows_inserted == 1
    assert tuple(row_data) == ("NSE", "CONFIG", "Configured Company", "Equity")
    assert source == "NSE mapped CSV"


def test_sample_source_generates_labelled_excel_pdf_manifest_and_email_draft(context):
    settings, db, importer = context
    records = []
    day = date(2025, 1, 1)
    while len(records) < 55:
        if day.weekday() < 5:
            records.append(row(when=day.isoformat(), close=str(100 + len(records)), source="SAMPLE_SYNTHETIC"))
        day += timedelta(days=1)
    importer.import_bytes("sample_market_data.csv", make_csv(records))
    result = ReportGenerator(database_path=db, settings=settings).generate()
    assert result["sample_data"] is True
    assert result["sample_label"] == "SAMPLE DATA - SYNTHETIC; NOT EXCHANGE DATA"
    assert all(Path(result["files"][kind]).exists() for kind in ("excel", "pdf", "manifest", "email_draft"))
    workbook = load_workbook(result["files"]["excel"], read_only=True)
    assert "SAMPLE DATA" in workbook["Read Me"]["A1"].value
    assert Path(result["files"]["pdf"]).read_bytes().startswith(b"%PDF")
    manifest = json.loads(Path(result["files"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["coverage"]["price_records"] == 55
    assert "nothing sent" in Path(result["files"]["email_draft"]).read_text(encoding="utf-8", errors="ignore").lower() or "draft only" in Path(result["files"]["email_draft"]).read_text(encoding="utf-8", errors="ignore").lower()
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generated_reports").fetchone()[0] == 4


def test_retry_is_bounded_and_does_not_retry_data_errors():
    calls = 0

    def transient_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return "ok"

    assert retry_read_only(transient_once, initial_delay=0) == "ok"
    assert calls == 2
    with pytest.raises(ValueError):
        retry_read_only(lambda: (_ for _ in ()).throw(ValueError("bad data")), initial_delay=0)
    calls = 0

    def denied_read():
        nonlocal calls
        calls += 1
        raise PermissionError("access denied")

    with pytest.raises(PermissionError):
        retry_read_only(denied_read, initial_delay=0)
    assert calls == 1
