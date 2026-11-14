from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

from india_stock_intelligence.agents.sample_backend import SampleAgentBackend
from india_stock_intelligence.config import Settings
from india_stock_intelligence.ingestion import MarketImporter
from india_stock_intelligence.migrations import apply_migrations
from india_stock_intelligence.orchestrator import ResearchOrchestrator
from india_stock_intelligence.retry import agent_retry_delay, is_retryable_agent_error
from india_stock_intelligence.storage import SCHEMA, connect_read_only, connect_read_write


HEADER = [
    "Exchange", "Symbol", "Company Name", "Security ID", "Security Type",
    "Listing Status", "Date", "Open", "High", "Low", "Close", "Volume", "Source",
]


def make_csv(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADER)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def make_row(*, exchange="NSE", symbol="TEST", security_id="TEST-ID", close="100",
             source="SAMPLE_SYNTHETIC", day="2025-01-02"):
    return {
        "Exchange": exchange, "Symbol": symbol, "Company Name": "Synthetic Test Company",
        "Security ID": security_id, "Security Type": "Equity", "Listing Status": "Active",
        "Date": day, "Open": close, "High": close, "Low": close,
        "Close": close, "Volume": "1000", "Source": source,
    }


@pytest.fixture
def configured(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    apply_migrations(settings.database_path)
    return settings


def test_read_only_connection_does_not_modify_database(configured):
    with connect_read_write(configured.database_path) as conn:
        conn.execute(
            """INSERT INTO securities
               (security_key,exchange,symbol,created_at,updated_at)
               VALUES ('NSE:symbol:RO','NSE','RO','now','now')"""
        )
    before = hashlib.sha256(configured.database_path.read_bytes()).hexdigest()

    with connect_read_only(configured.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM securities")
    after = hashlib.sha256(configured.database_path.read_bytes()).hexdigest()

    assert after == before
    with connect_read_only(configured.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 1


def test_normal_connection_does_not_create_or_migrate_missing_database(tmp_path):
    missing = tmp_path / "not-created" / "stock.db"
    with pytest.raises(FileNotFoundError, match="migrate"):
        connect_read_write(missing)
    assert not missing.exists()


def test_write_connection_rolls_back_transaction_on_exception(configured):
    with pytest.raises(RuntimeError, match="abort"):
        with connect_read_write(configured.database_path) as conn:
            conn.execute(
                """INSERT INTO securities
                   (security_key,exchange,symbol,created_at,updated_at)
                   VALUES ('NSE:symbol:ROLLBACK','NSE','ROLLBACK','now','now')"""
            )
            raise RuntimeError("abort transaction")
    with connect_read_only(configured.database_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM securities WHERE security_key='NSE:symbol:ROLLBACK'"
        ).fetchone()[0] == 0


def test_migrations_are_versioned_idempotent_and_preserve_existing_rows(configured):
    with connect_read_write(configured.database_path) as conn:
        conn.execute(
            """INSERT INTO securities
               (security_key,exchange,symbol,created_at,updated_at)
               VALUES ('NSE:symbol:KEEP','NSE','KEEP','now','now')"""
        )
    first = apply_migrations(configured.database_path)
    second = apply_migrations(configured.database_path)

    assert first["schema_version"] == 3
    assert [item["version"] for item in first["migrations_applied"]] == []
    assert second["migrations_applied"] == []
    with connect_read_only(configured.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 1
        assert [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3]
        assert {"price_corrections", "universe_research_runs",
                "universe_research_security_results"} <= {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }


def test_migration_refuses_duplicate_screening_rows_without_deleting_them(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            """INSERT INTO securities
               (security_key,exchange,symbol,created_at,updated_at)
               VALUES ('NSE:symbol:DUP','NSE','DUP','now','now')"""
        )
        for _ in range(2):
            conn.execute(
                """INSERT INTO screening_results
                   (security_key,data_date,created_at,metrics_json)
                   VALUES ('NSE:symbol:DUP','2025-01-02','now','{}')"""
            )
    with pytest.raises(sqlite3.IntegrityError):
        apply_migrations(database_path)
    with sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM screening_results").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()[0] == 0


def test_invalid_and_rejected_source_rows_have_final_status_and_keep_raw_bytes(configured):
    importer = MarketImporter(settings=configured)
    invalid_payload = make_csv([make_row(close="-4")])
    invalid = importer.import_bytes("invalid.csv", invalid_payload)
    rejected_payload = make_csv([make_row(exchange="BSE", security_id="BSE-ID", source="manual")])
    importer.import_bytes("wrong-exchange.csv", rejected_payload, expected_exchange="NSE")

    with connect_read_only(configured.database_path) as conn:
        statuses = [row[0] for row in conn.execute(
            "SELECT status FROM source_records ORDER BY source_record_id"
        )]
        prices_for_invalid = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE security_key='NSE:id:TEST-ID'"
        ).fetchone()[0]
    assert statuses == ["invalid", "rejected"]
    assert invalid.invalid_rows == 1
    assert prices_for_invalid == 0
    assert (configured.raw_dir / f"{invalid.sha256}_invalid.csv").read_bytes() == invalid_payload


def test_duplicate_import_is_safe_and_explicit_correction_is_audited(configured):
    importer = MarketImporter(settings=configured)
    original_payload = make_csv([make_row(close="100")])
    first = importer.import_bytes("original.csv", original_payload)
    duplicate = importer.import_bytes("later.csv", make_csv([make_row(close="101")]))
    with connect_read_only(configured.database_path) as conn:
        unchanged = conn.execute(
            "SELECT close FROM daily_prices WHERE security_key='NSE:id:TEST-ID' AND price_date='2025-01-02'"
        ).fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM price_corrections").fetchone()[0] == 0
    assert duplicate.duplicate_rows == 1
    assert unchanged == 100

    correction_payload = make_csv([make_row(close="101")])
    with pytest.raises(ValueError, match="correction_reason"):
        importer.import_bytes("correction.csv", correction_payload, replace_existing=True)
    correction = importer.import_bytes(
        "correction.csv", correction_payload, replace_existing=True,
        correction_reason="Official exchange file correction",
    )
    with connect_read_only(configured.database_path) as conn:
        price = conn.execute(
            "SELECT close,source_record_id FROM daily_prices WHERE security_key='NSE:id:TEST-ID' AND price_date='2025-01-02'"
        ).fetchone()
        audit = conn.execute("SELECT * FROM price_corrections").fetchone()
        sources = conn.execute("SELECT source_record_id,status FROM source_records ORDER BY source_record_id").fetchall()
    assert correction.corrected_rows == 1
    assert price["close"] == 101
    assert audit["security_identifier"] == "TEST-ID"
    assert audit["exchange"] == "NSE"
    assert audit["trading_date"] == "2025-01-02"
    assert audit["old_price"] == 100
    assert audit["new_price"] == 101
    assert audit["reason"] == "Official exchange file correction"
    assert audit["original_source_record_id"] == sources[0]["source_record_id"]
    assert audit["correction_source_record_id"] == price["source_record_id"]
    assert [source["status"] for source in sources] == ["imported", "corrected"]
    assert (configured.raw_dir / f"{hashlib.sha256(correction_payload).hexdigest()}_correction.csv").read_bytes() == correction_payload
    assert json.loads(audit["old_values_json"])["close"] == 100
    assert json.loads(audit["new_values_json"])["close"] == 101


def test_universe_failure_isolation_manifest_and_resume_do_not_duplicate_successes(configured):
    importer = MarketImporter(settings=configured)
    importer.import_bytes("universe.csv", make_csv([
        make_row(symbol="GOOD1", security_id="GOOD1", source="SAMPLE_SYNTHETIC"),
        make_row(exchange="BSE", symbol="GOOD2", security_id="GOOD2", source="SAMPLE_SYNTHETIC"),
    ]))
    keys = ["NSE:id:DOES-NOT-EXIST", "NSE:id:GOOD1", "BSE:id:GOOD2"]
    orchestrator = ResearchOrchestrator(settings=configured, backend=SampleAgentBackend())

    results = orchestrator.run_universe(keys, as_of_date="2025-01-02")
    manifest = orchestrator.last_universe_manifest
    batch_id = manifest["batch_run_id"]
    assert [result.security_key for result in results] == ["NSE:id:GOOD1", "BSE:id:GOOD2"]
    assert manifest["attempted_securities"] == 3
    assert manifest["successful_security_ids"] == ["NSE:id:GOOD1", "BSE:id:GOOD2"]
    assert manifest["failed_security_ids"] == ["NSE:id:DOES-NOT-EXIST"]
    assert "EvidenceError" in manifest["errors"][0]["error_summary"]
    assert Path(manifest["manifest_path"]).is_file()

    resumed = orchestrator.run_universe(batch_run_id=batch_id, resume=True)
    assert resumed == []
    assert orchestrator.last_universe_manifest["failed_security_ids"] == ["NSE:id:DOES-NOT-EXIST"]
    with connect_read_only(configured.database_path) as conn:
        for key in ("NSE:id:GOOD1", "BSE:id:GOOD2"):
            assert conn.execute(
                "SELECT COUNT(*) FROM research_runs WHERE security_key=? AND batch_run_id=?",
                (key, batch_id),
            ).fetchone()[0] == 1


def test_retry_classifier_retries_only_transient_errors():
    class HttpError(RuntimeError):
        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.response = type("Response", (), {"headers": headers or {}})()

    assert is_retryable_agent_error(TimeoutError("temporary"))
    assert is_retryable_agent_error(ConnectionError("temporary"))
    assert is_retryable_agent_error(HttpError(429))
    assert is_retryable_agent_error(HttpError(503))
    assert not is_retryable_agent_error(HttpError(401))
    assert not is_retryable_agent_error(HttpError(422))
    assert not is_retryable_agent_error(ValueError("invalid schema"))
    assert not is_retryable_agent_error(PermissionError("not permitted"))
    assert agent_retry_delay(TimeoutError("temporary"), 2) == 0.5
    assert agent_retry_delay(HttpError(429, {"Retry-After": "3"}), 1) == 3.0


def test_nonretryable_agent_error_is_recorded_once(configured):
    importer = MarketImporter(settings=configured)
    importer.import_bytes("sample.csv", make_csv([make_row()]))

    class InvalidDataOnceBackend(SampleAgentBackend):
        failed = False

        def generate_json(self, *, agent_id, prompt, payload, schema):
            if agent_id == "equity_research_analyst" and not self.failed:
                self.failed = True
                raise ValueError("invalid synthetic response")
            return super().generate_json(agent_id=agent_id, prompt=prompt, payload=payload, schema=schema)

    result = ResearchOrchestrator(
        settings=configured, backend=InvalidDataOnceBackend(), max_attempts=3
    ).run_security("NSE:id:TEST-ID", as_of_date="2025-01-02")
    assert result.status == "incomplete"
    with connect_read_only(configured.database_path) as conn:
        attempts = conn.execute(
            """SELECT COUNT(*) FROM agent_outputs WHERE research_run_id=?
               AND agent_id='equity_research_analyst'""", (result.research_run_id,)
        ).fetchone()[0]
        failure = conn.execute(
            """SELECT status,error_message FROM agent_outputs WHERE research_run_id=?
               AND agent_id='equity_research_analyst'""", (result.research_run_id,)
        ).fetchone()
    assert attempts == 1
    assert failure["status"] == "failed"
    assert "ValueError" in failure["error_message"]
