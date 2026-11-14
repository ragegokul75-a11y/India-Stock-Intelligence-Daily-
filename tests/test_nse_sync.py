from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from india_stock_intelligence.config import Settings
from india_stock_intelligence.migrations import apply_migrations
from india_stock_intelligence.nse_cm_udiff import UnverifiedNseCmUdiffProfile
from india_stock_intelligence.storage import connect_read_only
from india_stock_intelligence.sync import (DisabledNSEReportSource, NseSyncService,
                                           OrchestratorResearchTrigger, SourceAccessBlocked)
from india_stock_intelligence.sync.locking import SyncAlreadyRunning, single_instance_lock
from india_stock_intelligence.sync.models import ReportDescriptor, RetrievedReport
from india_stock_intelligence.sync.retry import TransientNSEError, is_retryable_source_error, retry_source_call


def row(*, symbol="SYNTH", close="100", trading_date="2025-01-02", exchange="NSE"):
    return {
        "exchange": exchange,
        "symbol": symbol,
        "security_identifier": f"SYNTH-{symbol}",
        "company_name": None,
        "security_type": None,
        "listing_status": None,
        "date": trading_date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": "1000",
    }


class FakeParser:
    parser_version = "synthetic-parser-test-v1"

    def __init__(self, records=None):
        self.records = records if records is not None else [row()]
        self.calls = 0

    def parse(self, payload: bytes, *, format_version: str | None = None):
        self.calls += 1
        if payload.startswith(b"invalid-parser-input"):
            raise UnverifiedNseCmUdiffProfile("synthetic schema mismatch")
        return list(self.records)


class FakeSource:
    source_name = "synthetic NSE fixture"
    access_basis = "synthetic_test_fixture_only"

    def __init__(self, reports, artifacts):
        self.reports = reports
        self.artifacts = artifacts
        self.discover_calls = 0
        self.retrieve_calls = 0

    def discover(self, *, after_date=None):
        self.discover_calls += 1
        if callable(self.reports):
            return self.reports(self.discover_calls)
        return list(self.reports)

    def retrieve(self, report, *, timeout_seconds):
        self.retrieve_calls += 1
        payload = self.artifacts[report.report_id]
        return RetrievedReport(
            descriptor=report,
            payload=payload,
            retrieval_method="synthetic fixture; no network",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            content_type="application/octet-stream",
        )


def configured(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", nse_sync_enabled=True, nse_sync_daily_time="18:15",
                        nse_sync_poll_interval_minutes=30, nse_sync_window_minutes=0)
    apply_migrations(settings.database_path)
    return settings


def descriptor(report_id="synthetic-report", filename="synthetic.zip", trading_date="2025-01-02", payload=None):
    return ReportDescriptor(
        report_id=report_id,
        trading_date=trading_date,
        filename=filename,
        source_url="https://example.invalid/report?token=must-not-be-persisted",
        format_version="synthetic-v1",
        expected_sha256=hashlib.sha256(payload).hexdigest() if payload is not None else None,
    )


def test_default_source_is_blocked_and_writes_no_database(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    result = NseSyncService(settings=settings).run_once()

    assert result["status"] == "NOT CONFIGURED"
    assert "automated data collection" in result["message"]
    assert not settings.database_path.exists()
    state = json.loads((settings.sync_dir / "state.json").read_text(encoding="utf-8"))
    assert state["live_retrieval_enabled"] is False
    assert state["status"] == "NOT CONFIGURED"
    assert state["last_successful_sync"] is None


def test_source_access_blocked_never_falls_back_to_web_or_scraping():
    source = DisabledNSEReportSource("synthetic policy gate")
    with pytest.raises(SourceAccessBlocked, match="synthetic policy gate"):
        source.discover(after_date="2025-01-01")


def test_enabled_flag_still_cannot_enable_unapproved_network_source(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", nse_sync_enabled=True)
    result = NseSyncService(settings=settings, source=DisabledNSEReportSource()).run_once()
    assert result["status"] == "BLOCKED"
    assert not settings.database_path.exists()


def test_delayed_report_is_found_by_bounded_polling_and_imported(tmp_path):
    settings = configured(tmp_path)
    report = descriptor()
    payload = b"synthetic delayed report bytes"
    source = FakeSource(lambda call: [] if call == 1 else [report], {report.report_id: payload})
    sleeps = []
    result = NseSyncService(settings=settings, source=source, parser=FakeParser(), sleep=sleeps.append).run_window(
        after_date="2025-01-01", window_minutes=30, poll_interval_minutes=30, max_polls=2
    )

    assert result["status"] == "SUCCESS"
    assert source.discover_calls == 2
    assert source.retrieve_calls == 1
    assert sleeps == [1800]
    assert result["imported_rows"] == 1
    assert result["latest_trading_date"] == "2025-01-02"


def test_artifact_sidecar_and_original_bytes_are_preserved(tmp_path):
    settings = configured(tmp_path)
    report = descriptor(payload=b"synthetic artifact bytes unchanged")
    payload = b"synthetic artifact bytes unchanged"
    service = NseSyncService(settings=settings, source=FakeSource([report], {report.report_id: payload}),
                             parser=FakeParser())
    result = service.run_once(after_date="2025-01-01")
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path = service.state.sidecar_path(digest)
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    artifact_path = settings.sync_dir / "artifacts" / digest / report.filename

    assert result["status"] == "SUCCESS"
    assert artifact_path.read_bytes() == payload
    assert sidecar["artifact_sha256"] == digest
    assert sidecar["artifact_byte_count"] == len(payload)
    assert sidecar["source_url"] == "https://example.invalid/report"
    assert sidecar["schema_profile_version"] == "synthetic-v1"
    assert sidecar["parser_version"] == "synthetic-parser-test-v1"
    assert sidecar["record_count"] == 1
    assert sidecar["accepted_record_count"] == 1
    assert sidecar["processing_complete"] is True


def test_repeated_artifact_does_not_reimport_or_repeat_agent_trigger(tmp_path):
    settings = configured(tmp_path)
    payload = b"synthetic immutable report"
    report = descriptor(payload=payload)
    source = FakeSource([report], {report.report_id: payload})
    agent_calls = []
    service = NseSyncService(settings=settings, source=source, parser=FakeParser(),
                             research_trigger=lambda keys, as_of: agent_calls.append((keys, as_of)))

    first = service.run_once(after_date="2025-01-01")
    second = service.run_once(after_date="2025-01-01")
    with connect_read_only(settings.database_path) as conn:
        price_count = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        correction_count = conn.execute("SELECT COUNT(*) FROM price_corrections").fetchone()[0]
        research_count = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]

    assert first["imported_rows"] == 1
    assert second["status"] == "SUCCESS"
    assert second["imported_rows"] == 0
    assert len(agent_calls) == 1
    assert price_count == 1
    assert correction_count == 0
    assert research_count == 0


def test_changed_artifact_for_existing_date_is_duplicate_not_correction(tmp_path):
    settings = configured(tmp_path)
    first_payload, revised_payload = b"synthetic source version one", b"synthetic source version two"
    report = descriptor(payload=first_payload)
    source = FakeSource([report], {report.report_id: first_payload})
    service = NseSyncService(settings=settings, source=source, parser=FakeParser([row(close="100")]))
    first = service.run_once(after_date="2025-01-01")

    source.artifacts[report.report_id] = revised_payload
    revised_report = descriptor(payload=revised_payload)
    source.reports = [revised_report]
    second = service.run_once(after_date="2025-01-01")
    with connect_read_only(settings.database_path) as conn:
        close = conn.execute("SELECT close FROM daily_prices").fetchone()[0]
        corrections = conn.execute("SELECT COUNT(*) FROM price_corrections").fetchone()[0]

    assert first["imported_rows"] == 1
    assert second["imported_rows"] == 0
    assert close == 100
    assert corrections == 0


def test_invalid_rows_are_rejected_without_partial_import(tmp_path):
    settings = configured(tmp_path)
    report = descriptor(payload=b"synthetic invalid rows")
    invalid = row(exchange="BSE")
    source = FakeSource([report], {report.report_id: b"synthetic invalid rows"})
    result = NseSyncService(settings=settings, source=source, parser=FakeParser([invalid])).run_once(
        after_date="2025-01-01"
    )

    with connect_read_only(settings.database_path) as conn:
        prices = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    assert result["status"] == "FAILED"
    assert result["rejected_rows"] == 1
    assert prices == 0


def test_checksum_mismatch_preserves_bytes_but_prevents_parse_and_import(tmp_path):
    settings = configured(tmp_path)
    payload = b"synthetic checksum mismatch bytes"
    wrong_checksum = hashlib.sha256(b"different synthetic bytes").hexdigest()
    report = ReportDescriptor("checksum-report", "2025-01-02", "checksum.zip",
                              expected_sha256=wrong_checksum, format_version="synthetic-v1")
    parser = FakeParser()
    source = FakeSource([report], {report.report_id: payload})
    service = NseSyncService(settings=settings, source=source, parser=parser)
    result = service.run_once(after_date="2025-01-01")
    digest = hashlib.sha256(payload).hexdigest()
    artifact_path = settings.sync_dir / "artifacts" / digest / report.filename
    sidecar = json.loads(service.state.sidecar_path(digest).read_text(encoding="utf-8"))
    with connect_read_only(settings.database_path) as conn:
        prices = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]

    assert result["status"] == "FAILED"
    assert parser.calls == 0
    assert artifact_path.read_bytes() == payload
    assert sidecar["checksum_status"] == "FAILED"
    assert sidecar["retrieval_outcome"] == "SUCCESS"
    assert sidecar["processing_outcome"] == "FAILED"
    assert prices == 0


def test_failed_report_does_not_stop_later_report(tmp_path):
    settings = configured(tmp_path)
    failed = descriptor("bad-report", "bad.zip", payload=b"invalid-parser-input")
    good = descriptor("good-report", "good.zip", payload=b"synthetic good bytes")
    source = FakeSource([failed, good], {
        failed.report_id: b"invalid-parser-input",
        good.report_id: b"synthetic good bytes",
    })
    result = NseSyncService(settings=settings, source=source, parser=FakeParser()).run_once(
        after_date="2025-01-01"
    )

    assert result["status"] == "WARNING"
    assert result["failed_reports"] == 1
    assert result["imported_rows"] == 1
    assert len(json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))["reports"]) == 2


def test_existing_orchestrator_adapter_uses_same_date_and_security_selection():
    class StubOrchestrator:
        def __init__(self):
            self.call = None

        def run_universe(self, keys, *, as_of_date, sample_only):
            self.call = (keys, as_of_date, sample_only)
            return [SimpleNamespace(status="completed", research_run_id="synthetic-run",
                                    security_key=keys[0], verdict="INCONCLUSIVE")]

    orchestrator = StubOrchestrator()
    result = OrchestratorResearchTrigger(orchestrator)(["NSE:id:SYNTH"], "2025-01-02")

    assert orchestrator.call == (["NSE:id:SYNTH"], "2025-01-02", False)
    assert result[0].verdict == "INCONCLUSIVE"


def test_sync_lock_blocks_concurrent_run_and_releases_on_exit(tmp_path):
    path = tmp_path / "sync.lock"
    with single_instance_lock(path):
        with pytest.raises(SyncAlreadyRunning):
            with single_instance_lock(path):
                pass
    with single_instance_lock(path):
        assert path.exists()


def test_source_retry_retries_transient_errors_only():
    attempts = []
    sleeps = []

    def transient_once():
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("synthetic timeout")
        return "ok"

    assert retry_source_call(transient_once, sleep=sleeps.append) == "ok"
    assert len(attempts) == 2
    assert sleeps == [0.5]
    assert is_retryable_source_error(TransientNSEError("rate limited", status_code=429))
    assert is_retryable_source_error(TransientNSEError("server error", status_code=503))
    assert not is_retryable_source_error(TransientNSEError("unauthorized", status_code=401))
    assert not is_retryable_source_error(PermissionError("denied"))
    assert not is_retryable_source_error(ValueError("invalid data"))
    assert not is_retryable_source_error(TypeError("programming error"))
