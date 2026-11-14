from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from urllib.parse import urlsplit


SYNC_STATUSES = {"SUCCESS", "WARNING", "FAILED", "BLOCKED", "NOT CONFIGURED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_report_filename(value: str) -> str:
    """Return a path-free filename while retaining a readable audit label."""
    name = PureWindowsPath(str(value)).name
    name = name.replace("/", "_").replace("\\", "_").strip(" .")
    if not name or name in {".", ".."}:
        raise ValueError("Report filename is empty or unsafe")
    return name[:180]


@dataclass(frozen=True)
class ReportDescriptor:
    report_id: str
    trading_date: str
    filename: str
    source_url: str | None = None
    format_version: str | None = None
    expected_sha256: str | None = None
    report_type: str = "CM-UDiFF Common Bhavcopy Final"
    published_at: str | None = None

    def __post_init__(self) -> None:
        if not self.report_id.strip():
            raise ValueError("report_id is required")
        parsed_date = datetime.strptime(self.trading_date, "%Y-%m-%d").date()
        if parsed_date.isoformat() != self.trading_date:
            raise ValueError("trading_date must use ISO YYYY-MM-DD")
        object.__setattr__(self, "filename", safe_report_filename(self.filename))
        if self.source_url:
            parts = urlsplit(self.source_url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError("source_url must be an absolute HTTP(S) URL")
        if self.expected_sha256 is not None:
            expected = self.expected_sha256.lower()
            if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
                raise ValueError("expected_sha256 must be a 64-character hexadecimal digest")


@dataclass(frozen=True)
class RetrievedReport:
    descriptor: ReportDescriptor
    payload: bytes
    retrieval_method: str
    retrieved_at: str
    content_type: str | None = None
    http_status: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("Retrieved artifact payload must be bytes")
        if not self.retrieval_method.strip():
            raise ValueError("retrieval_method is required for provenance")
        try:
            retrieved = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("retrieved_at must be an ISO timestamp") from exc
        if retrieved.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True)
class SyncResult:
    run_id: str
    status: str
    started_at: str
    finished_at: str
    discovered_reports: int = 0
    retrieved_reports: int = 0
    imported_rows: int = 0
    rejected_rows: int = 0
    duplicate_rows: int = 0
    failed_reports: int = 0
    latest_trading_date: str | None = None
    agent_status: str = "NOT CONFIGURED"
    message: str = ""
    manifest_path: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SYNC_STATUSES:
            raise ValueError(f"Unsupported sync status: {self.status}")
