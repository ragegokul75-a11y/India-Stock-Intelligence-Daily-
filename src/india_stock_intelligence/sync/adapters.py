from __future__ import annotations

from typing import Protocol

from .models import ReportDescriptor, RetrievedReport


class SourceAccessBlocked(RuntimeError):
    """Raised when a source method has not been approved for automated access."""


class NSEReportSource(Protocol):
    """Contract for an explicitly approved NSE report-discovery/retrieval method."""

    source_name: str
    access_basis: str

    def discover(self, *, after_date: str | None = None) -> list[ReportDescriptor]:
        """Discover reports without guessing undocumented endpoints."""

    def retrieve(self, report: ReportDescriptor, *, timeout_seconds: int) -> RetrievedReport:
        """Retrieve one report using the approved source method."""


class DisabledNSEReportSource:
    """Default fail-closed source adapter. It never makes a network request."""

    source_name = "NSE CM UDiFF"
    access_basis = "disabled_pending_written_approved_unattended_access"

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason or (
            "NSE website terms prohibit systematic or automated data collection. "
            "No approved zero-cost unattended NSE access method is configured."
        )

    def discover(self, *, after_date: str | None = None) -> list[ReportDescriptor]:
        raise SourceAccessBlocked(self.reason)

    def retrieve(self, report: ReportDescriptor, *, timeout_seconds: int) -> RetrievedReport:
        raise SourceAccessBlocked(self.reason)
