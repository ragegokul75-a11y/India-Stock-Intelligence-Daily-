from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from ..config import Settings, get_settings
from ..ingestion import MarketImporter
from ..nse_cm_udiff import NseCmUdiffParser
from ..providers.nse import NSEProvider
from ..storage import connect_read_only
from .adapters import DisabledNSEReportSource, NSEReportSource, SourceAccessBlocked
from .locking import SyncAlreadyRunning, single_instance_lock
from .models import ReportDescriptor, RetrievedReport, SyncResult, utc_now
from .retry import retry_source_call
from .state import SyncStateStore

logger = logging.getLogger(__name__)

CANONICAL_CSV_FIELDS = (
    "Exchange", "Symbol", "Company Name", "Security ID", "Security Type", "Listing Status",
    "Date", "Open", "High", "Low", "Close", "Volume", "Source Timestamp", "Source",
)


def _safe_source_url(value: str | None) -> str | None:
    """Drop credentials and query parameters before persisting a source URL."""
    if not value:
        return None
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _as_decimal(value, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} is missing or invalid")
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{field_name} is not numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return number


def validate_canonical_records(records: list[dict], trading_date: str) -> tuple[list[dict], list[str]]:
    """Validate parser output without guessing source-specific UDiFF field meanings."""
    expected_date = date.fromisoformat(trading_date)
    accepted: list[dict] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"row {index}: parser output is not an object")
            continue
        row = {str(key).strip().lower(): value for key, value in record.items()}
        exchange = str(row.get("exchange") or "").strip().upper()
        symbol = str(row.get("symbol") or "").strip().upper()
        identifier = str(row.get("security_identifier") or row.get("security_id") or "").strip().upper()
        row_date = str(row.get("date") or row.get("trading_date") or "").strip()
        try:
            parsed_date = date.fromisoformat(row_date)
        except ValueError:
            errors.append(f"row {index}: date must be ISO YYYY-MM-DD")
            continue
        if parsed_date != expected_date:
            errors.append(f"row {index}: record date does not match report trading date")
            continue
        if exchange != "NSE":
            errors.append(f"row {index}: exchange must be NSE")
            continue
        if not (symbol or identifier):
            errors.append(f"row {index}: symbol or security identifier is required")
            continue
        try:
            open_price = _as_decimal(row.get("open"), "open")
            high = _as_decimal(row.get("high"), "high")
            low = _as_decimal(row.get("low"), "low")
            close = _as_decimal(row.get("close"), "close")
            volume = _as_decimal(row.get("volume"), "volume")
        except ValueError as exc:
            errors.append(f"row {index}: {exc}")
            continue
        if min(open_price, high, low, close) <= 0:
            errors.append(f"row {index}: OHLC values must be positive")
            continue
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            errors.append(f"row {index}: OHLC relationship is invalid")
            continue
        if volume < 0 or volume != volume.to_integral_value():
            errors.append(f"row {index}: volume must be a non-negative whole number")
            continue
        accepted.append({
            "exchange": "NSE",
            "symbol": symbol,
            "company_name": row.get("company_name"),
            "security_identifier": identifier or None,
            "security_type": row.get("security_type"),
            "listing_status": row.get("listing_status"),
            "date": parsed_date.isoformat(),
            "open": str(open_price),
            "high": str(high),
            "low": str(low),
            "close": str(close),
            "volume": str(volume),
            "source_timestamp": row.get("source_timestamp"),
        })
    return accepted, errors


def _canonical_csv(records: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CANONICAL_CSV_FIELDS)
    writer.writeheader()
    for row in records:
        writer.writerow({
            "Exchange": row["exchange"],
            "Symbol": row["symbol"],
            "Company Name": row.get("company_name") or "",
            "Security ID": row.get("security_identifier") or "",
            "Security Type": row.get("security_type") or "",
            "Listing Status": row.get("listing_status") or "",
            "Date": row["date"],
            "Open": row["open"],
            "High": row["high"],
            "Low": row["low"],
            "Close": row["close"],
            "Volume": row["volume"],
            "Source Timestamp": row.get("source_timestamp") or "",
            "Source": "NSE CM UDiFF parsed artifact",
        })
    return output.getvalue().encode("utf-8")


def _agent_run_summaries(output) -> list[dict]:
    if output is None:
        return []
    items = output if isinstance(output, (list, tuple)) else [output]
    allowed = {"research_run_id", "security_key", "status", "verdict", "risk_score",
               "confidence_score", "report_path", "evidence_digest"}
    summaries = []
    for item in items:
        document = item if isinstance(item, dict) else getattr(item, "__dict__", {})
        summaries.append({key: document[key] for key in allowed if key in document})
    return summaries


class NseSyncService:
    """Coordinates report sync and delegates all price persistence to MarketImporter."""

    def __init__(self, *, settings: Settings | None = None, source: NSEReportSource | None = None,
                 parser=None, importer: MarketImporter | None = None,
                 research_trigger: Callable[[list[str], str], object] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings or get_settings()
        self.source = source or DisabledNSEReportSource()
        self.parser = parser or NseCmUdiffParser()
        self.importer = importer or MarketImporter(settings=self.settings)
        self.research_trigger = research_trigger
        self.sleep = sleep
        if self.settings.nse_sync_timeout_seconds < 1 or self.settings.nse_sync_timeout_seconds > 300:
            raise ValueError("NSE sync timeout must be between 1 and 300 seconds")
        if self.settings.nse_sync_poll_interval_minutes < 1 or self.settings.nse_sync_poll_interval_minutes > 1440:
            raise ValueError("NSE sync poll interval must be between 1 and 1440 minutes")
        if self.settings.nse_sync_window_minutes < 0 or self.settings.nse_sync_window_minutes > 1440:
            raise ValueError("NSE sync polling window must be between 0 and 1440 minutes")
        self.state = SyncStateStore(
            self.settings.sync_dir,
            daily_time=self.settings.nse_sync_daily_time,
        )

    def run_once(self, *, after_date: str | None = None) -> dict:
        return self.run_window(after_date=after_date, window_minutes=0, max_polls=1)

    def run_window(self, *, after_date: str | None = None, window_minutes: int | None = None,
                   poll_interval_minutes: int | None = None, max_polls: int | None = None) -> dict:
        """Poll only through the injected source adapter; the default source never uses network."""
        window = self.settings.nse_sync_window_minutes if window_minutes is None else window_minutes
        poll_interval = self.settings.nse_sync_poll_interval_minutes if poll_interval_minutes is None else poll_interval_minutes
        if window < 0 or poll_interval < 1:
            raise ValueError("Sync window must be non-negative and polling interval must be positive")
        calculated_polls = max(1, math.ceil(window / poll_interval) + 1)
        poll_limit = max_polls if max_polls is not None else calculated_polls
        if poll_limit < 1:
            raise ValueError("max_polls must be positive")
        run_id = uuid.uuid4().hex
        started = utc_now()
        try:
            with single_instance_lock(self.state.lock_path):
                if not self.settings.nse_sync_enabled:
                    return self._finish(
                        run_id, started, "NOT CONFIGURED",
                        "NSE sync is disabled in configuration. Current NSE website terms prohibit systematic or automated data collection on or in relation to the website; an approved access adapter is required.",
                        0, 0, 0, 0, 0, 0, None, "NOT CONFIGURED", [], [], {},
                    )
                return self._run_locked(run_id, started, after_date, window, poll_interval, poll_limit)
        except SyncAlreadyRunning as exc:
            logger.info("NSE sync skipped because an earlier run holds the lock")
            return {
                "run_id": run_id, "status": "WARNING", "started_at": started,
                "finished_at": utc_now(), "message": str(exc), "agent_status": "NOT CONFIGURED",
                "manifest_path": None,
            }

    def _run_locked(self, run_id: str, started: str, after_date: str | None,
                    window: int, interval: int, poll_limit: int) -> dict:
        attempted_at = datetime.now(timezone.utc)
        deadline = attempted_at + timedelta(minutes=window)
        errors: list[dict] = []
        report_results: list[dict] = []
        processed: dict[str, str] = {}
        discovered_count = retrieved_count = imported_count = rejected_count = duplicate_count = 0
        agent_status = "NOT CONFIGURED"
        latest_date: str | None = None
        no_report_message = "No eligible report was available before the configured polling window ended."
        try:
            for poll_number in range(poll_limit):
                descriptors = retry_source_call(
                    lambda: self.source.discover(after_date=after_date), sleep=self.sleep
                )
                discovered_count += len(descriptors)
                if descriptors:
                    for descriptor in descriptors:
                        latest_date = max(latest_date or descriptor.trading_date, descriptor.trading_date)
                        if after_date and descriptor.trading_date <= after_date:
                            continue
                        result = self._process_report(descriptor)
                        report_results.append(result)
                        retrieved_count += int(result.get("retrieved", False))
                        imported_count += int(result.get("imported_rows", 0))
                        rejected_count += int(result.get("rejected_rows", 0))
                        duplicate_count += int(result.get("duplicate_rows", 0))
                        processed[descriptor.report_id] = result.get("artifact_sha256", "")
                        if result.get("agent_status") not in {None, "NOT CONFIGURED"}:
                            agent_status = result["agent_status"]
                        if result.get("error_type"):
                            errors.append({"report_id": descriptor.report_id, "error_type": result["error_type"]})
                    break
                if poll_number + 1 >= poll_limit or datetime.now(timezone.utc) >= deadline:
                    break
                self.sleep(interval * 60)
        except SourceAccessBlocked as exc:
            return self._finish(
                run_id, started, "BLOCKED", str(exc), discovered_count, retrieved_count,
                imported_count, rejected_count, duplicate_count, len(errors), latest_date,
                agent_status, report_results, errors, processed,
            )
        except Exception as exc:
            logger.error("NSE sync failed (%s); details withheld from persistent state", type(exc).__name__)
            errors.append({"error_type": type(exc).__name__})
            return self._finish(
                run_id, started, "FAILED", "Report discovery failed; see application log.",
                discovered_count, retrieved_count, imported_count, rejected_count,
                duplicate_count, len(errors), latest_date, agent_status,
                report_results, errors, processed,
            )

        if not report_results:
            return self._finish(
                run_id, started, "WARNING", no_report_message, discovered_count, retrieved_count,
                imported_count, rejected_count, duplicate_count, 0, latest_date,
                agent_status, report_results, errors, processed,
            )
        has_failure = any(item.get("error_type") for item in report_results)
        has_warning = any(item.get("rejected_rows", 0) or item.get("agent_status") == "FAILED" for item in report_results)
        if has_failure and not imported_count:
            status = "FAILED"
        elif has_failure or has_warning:
            status = "WARNING"
        else:
            status = "SUCCESS"
        return self._finish(
            run_id, started, status,
            "One or more reports were processed." if not has_failure else "Some reports could not be processed; see manifest.",
            discovered_count, retrieved_count, imported_count, rejected_count,
            duplicate_count, sum(bool(item.get("error_type")) for item in report_results),
            latest_date, agent_status, report_results, errors, processed,
            successful=bool(retrieved_count and not (has_failure and not imported_count)),
        )

    def _process_report(self, descriptor: ReportDescriptor) -> dict:
        result = {"report_id": descriptor.report_id, "report_date": descriptor.trading_date,
                  "filename": descriptor.filename, "retrieved": False, "imported_rows": 0,
                  "rejected_rows": 0, "duplicate_rows": 0, "agent_status": "NOT CONFIGURED"}
        artifact = None
        artifact_path = None
        try:
            artifact: RetrievedReport = retry_source_call(
                lambda: self.source.retrieve(descriptor, timeout_seconds=self.settings.nse_sync_timeout_seconds),
                sleep=self.sleep,
            )
            if not isinstance(artifact.payload, bytes) or not artifact.payload:
                raise ValueError("Retrieved artifact is empty or is not bytes")
            digest = artifact.sha256
            result.update({"retrieved": True, "artifact_sha256": digest})
            sidecar = self.state.read_sidecar(digest)
            if sidecar and sidecar.get("processing_complete"):
                if (self.research_trigger is not None and sidecar.get("agent_status") == "NOT CONFIGURED"
                        and int(sidecar.get("imported_rows", 0)) > 0):
                    sidecar["agent_status"] = "RUNNING"
                    self.state.write_sidecar(digest, sidecar)
                    try:
                        dispatch_output = self.research_trigger(
                            sidecar.get("candidate_security_keys", []), descriptor.trading_date
                        )
                        sidecar["agent_runs"] = _agent_run_summaries(dispatch_output)
                        sidecar["agent_status"] = "SUCCESS"
                    except Exception as exc:
                        logger.error("Research dispatch failed (%s)", type(exc).__name__)
                        sidecar["agent_status"] = "FAILED"
                        sidecar["agent_error"] = type(exc).__name__
                    self.state.write_sidecar(digest, sidecar)
                result.update({"skipped": True, "retrieved": False,
                               "duplicate_rows": int(sidecar.get("duplicate_rows", 0)),
                               "agent_status": sidecar.get("agent_status", "NOT CONFIGURED"),
                               "parser_status": sidecar.get("parse_status", "NOT CONFIGURED"),
                               "ingestion_status": sidecar.get("ingestion_status", "NOT CONFIGURED")})
                return result
            was_ingestion_in_progress = bool(sidecar and sidecar.get("ingestion_status") == "IN_PROGRESS")
            if sidecar and sidecar.get("agent_status") == "RUNNING":
                # A process may have stopped during dispatch. Do not risk duplicate agent runs.
                sidecar.update({"agent_status": "FAILED", "agent_error": "dispatch_interrupted_not_retried",
                                "processing_complete": True})
                self.state.write_sidecar(digest, sidecar)
                result.update({"skipped": True, "agent_status": "FAILED"})
                return result
            artifact_path = self.state.write_artifact(descriptor, artifact.payload, digest)
            if descriptor.expected_sha256 and digest.lower() != descriptor.expected_sha256.lower():
                result["checksum_status"] = "FAILED"
                raise ValueError("Retrieved artifact checksum does not match the source descriptor")
            manifest = self._base_sidecar(artifact, digest, artifact_path)
            manifest["checksum_status"] = "MATCHED" if descriptor.expected_sha256 else "NO_SOURCE_CHECKSUM_PROVIDED"
            self.state.write_sidecar(digest, manifest | {"retrieval_outcome": "SUCCESS", "parse_status": "PENDING"})
            parsed = self.parser.parse(artifact.payload, format_version=descriptor.format_version)
            if not isinstance(parsed, list):
                raise ValueError("Parser output must be a list of canonical records")
            valid, validation_errors = validate_canonical_records(parsed, descriptor.trading_date)
            manifest.update({
                "parse_status": "SUCCESS" if not validation_errors else "WARNING",
                "schema_profile_version": descriptor.format_version,
                "parser_version": getattr(self.parser, "parser_version", "unknown"),
                "record_count": len(parsed), "accepted_record_count": len(valid),
                "rejected_record_count": len(parsed) - len(valid),
                "validation_results": {"accepted": len(valid), "rejected": len(parsed) - len(valid),
                                       "errors": validation_errors[:100]},
            })
            result["rejected_rows"] = len(parsed) - len(valid)
            result["parser_status"] = manifest["parse_status"]
            result["parsed_records"] = len(parsed)
            result["accepted_records"] = len(valid)
            if not valid:
                raise ValueError("Parser produced no valid canonical NSE CM equity records")
            normalized = _canonical_csv(valid)
            normalized_sha = hashlib.sha256(normalized).hexdigest()
            security_keys = sorted({
                f"NSE:id:{row['security_identifier']}" if row.get("security_identifier")
                else f"NSE:symbol:{row['symbol']}"
                for row in valid
            })
            manifest.update({"normalized_payload_sha256": normalized_sha,
                             "candidate_security_keys": security_keys,
                             "ingestion_status": "IN_PROGRESS", "agent_status": "NOT CONFIGURED"})
            self.state.write_sidecar(digest, manifest)
            provider = NSEProvider(source_name=f"NSE CM UDiFF {descriptor.trading_date}")
            summary = provider.import_bytes(
                f"{Path(descriptor.filename).stem}_normalized.csv", normalized, self.importer
            )
            result.update({"imported_rows": summary.rows_inserted,
                           "rejected_rows": result["rejected_rows"] + summary.invalid_rows + summary.rejected_rows,
                           "duplicate_rows": summary.duplicate_rows, "ingestion_status": "SUCCESS"})
            recovered_keys = self._recover_ingested_keys(summary.sha256, descriptor.trading_date) if was_ingestion_in_progress else []
            dispatch_keys = sorted(set(security_keys if summary.rows_inserted > 0 else recovered_keys))
            manifest.update({"ingestion_status": "SUCCESS", "normalized_payload_sha256": summary.sha256,
                             "import_summary": summary.to_dict(), "duplicate_rows": summary.duplicate_rows,
                             "imported_rows": summary.rows_inserted,
                             "recovered_after_interruption": bool(recovered_keys),
                             "rejected_rows": result["rejected_rows"], "processing_complete": False})
            # Persist ingestion before optional research dispatch for recoverability.
            self.state.write_sidecar(digest, manifest)
            if dispatch_keys:
                if self.research_trigger is None:
                    result["agent_status"] = "NOT CONFIGURED"
                else:
                    manifest["agent_status"] = "RUNNING"
                    self.state.write_sidecar(digest, manifest)
                    try:
                        dispatch_output = self.research_trigger(dispatch_keys, descriptor.trading_date)
                        manifest["agent_runs"] = _agent_run_summaries(dispatch_output)
                        result["agent_status"] = "SUCCESS"
                    except Exception as exc:
                        logger.error("Research dispatch failed (%s)", type(exc).__name__)
                        result["agent_status"] = "FAILED"
                        manifest["agent_error"] = type(exc).__name__
                manifest["agent_status"] = result["agent_status"]
            else:
                manifest["agent_status"] = "NOT CONFIGURED"
                manifest["research_skipped_reason"] = "No new price rows were inserted; dataset is unchanged."
            manifest["processing_complete"] = True
            manifest["retrieval_outcome"] = "SUCCESS"
            result["artifact_sha256"] = digest
            result["source_record_payload_sha256"] = summary.sha256
            self.state.write_sidecar(digest, manifest)
            return result
        except Exception as exc:
            logger.error("NSE report %s failed (%s)", descriptor.report_id, type(exc).__name__)
            result["error_type"] = type(exc).__name__
            result["retrieved"] = bool(result.get("artifact_sha256"))
            digest = result.get("artifact_sha256")
            if digest:
                existing = self.state.read_sidecar(digest) or self._base_sidecar(artifact, digest, artifact_path)
                existing.update({"retrieval_outcome": "SUCCESS", "processing_outcome": "FAILED",
                                 "processing_complete": False, "error_type": type(exc).__name__})
                if result.get("checksum_status"):
                    existing["checksum_status"] = result["checksum_status"]
                self.state.write_sidecar(digest, existing)
            return result

    def _recover_ingested_keys(self, source_payload_sha256: str, trading_date: str) -> list[str]:
        """Recover security keys after a stop between SQLite commit and sidecar update."""
        try:
            with connect_read_only(self.importer.database_path) as conn:
                rows = conn.execute(
                    """SELECT DISTINCT security_key FROM source_records
                       WHERE source_sha256=? AND record_date=? AND status='imported'
                         AND security_key IS NOT NULL""",
                    (source_payload_sha256, trading_date),
                ).fetchall()
        except FileNotFoundError:
            return []
        return sorted(row["security_key"] for row in rows)

    def _base_sidecar(self, artifact: RetrievedReport, digest: str, artifact_path: Path | None) -> dict:
        descriptor = artifact.descriptor
        return {
            "manifest_version": 1,
            "artifact_sha256": digest,
            "source": "NSE CM UDiFF",
            "source_url": _safe_source_url(descriptor.source_url),
            "report_id": descriptor.report_id,
            "report_type": descriptor.report_type,
            "report_filename": descriptor.filename,
            "trading_date": descriptor.trading_date,
            "published_at": descriptor.published_at,
            "retrieved_at": artifact.retrieved_at,
            "retrieval_method": artifact.retrieval_method,
            "http_status": artifact.http_status,
            "content_type": artifact.content_type,
            "schema_profile_version": descriptor.format_version,
            "expected_sha256": descriptor.expected_sha256,
            "parser_version": getattr(self.parser, "parser_version", "unknown"),
            "access_basis": getattr(self.source, "access_basis", "unspecified"),
            "artifact_path": str(artifact_path) if artifact_path else None,
            "artifact_byte_count": len(artifact.payload),
            "retrieval_outcome": "PENDING",
            "processing_complete": False,
        }

    def _finish(self, run_id: str, started: str, status: str, message: str,
                discovered: int, retrieved: int, imported: int, rejected: int, duplicates: int,
                failed: int, latest_date: str | None, agent_status: str,
                report_results: list[dict], errors: list[dict], processed: dict[str, str],
                successful: bool = False) -> dict:
        finished = utc_now()
        manifest = {
            "manifest_version": 1, "run_id": run_id, "source": "NSE CM UDiFF",
            "status": status, "message": message, "started_at": started, "finished_at": finished,
            "discovered_reports": discovered, "retrieved_reports": retrieved,
            "imported_rows": imported, "rejected_rows": rejected, "duplicate_rows": duplicates,
            "failed_reports": failed, "latest_trading_date": latest_date,
            "agent_execution_status": agent_status, "reports": report_results,
            "errors": errors, "live_retrieval_enabled": False,
        }
        manifest_path = self.state.write_run_manifest(run_id, manifest)
        result = SyncResult(
            run_id=run_id, status=status, started_at=started, finished_at=finished,
            discovered_reports=discovered, retrieved_reports=retrieved, imported_rows=imported,
            rejected_rows=rejected, duplicate_rows=duplicates, failed_reports=failed,
            latest_trading_date=latest_date, agent_status=agent_status, message=message,
            manifest_path=str(manifest_path),
        )
        result_dict = result.__dict__
        manifest["manifest_path"] = str(manifest_path)
        self.state.write_run_manifest(run_id, manifest)
        self.state.record(result_dict, successful=successful, latest_trading_date=latest_date,
                          processed_artifacts=processed)
        return result_dict
