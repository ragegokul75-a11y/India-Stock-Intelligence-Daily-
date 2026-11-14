from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .base import ProviderNotConfigured


class EvidenceSourceProvider(ABC):
    """Adapter interface for financial statements, filings, macro, and news evidence."""

    source_type: str
    provider_name: str
    download_enabled = False

    @abstractmethod
    def import_file(self, path: Path, evidence_store):
        """Import a manually obtained source file with complete provenance."""

    def download(self, *args, **kwargs):
        raise ProviderNotConfigured(
            f"{self.provider_name} downloads are disabled. Use a permitted manual source file; no scraper is included."
        )


class ManualEvidenceProvider(EvidenceSourceProvider):
    """Generic, configurable adapter for provenance-complete JSON evidence files."""

    source_type = "manual_evidence"
    provider_name = "Manual sourced evidence"

    def import_file(self, path: Path, evidence_store):
        return evidence_store.import_json_file(path)
