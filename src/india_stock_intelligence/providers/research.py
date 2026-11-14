from __future__ import annotations

from abc import ABC
from pathlib import Path

from .base import ProviderNotConfigured
from .evidence import ManualEvidenceProvider


class ResearchDataProvider(ABC):
    """Common source boundary for listing masters and fundamental evidence.

    Provider implementations are separate adapters. Retrieval stays disabled
    unless a provider explicitly implements and is separately approved for it.
    """

    provider_name = "Research data provider"
    download_enabled = False
    access_basis = "not configured"

    def import_stock_master(self, path: Path, stock_master_store):
        raise NotImplementedError

    def import_fundamental_evidence(self, path: Path, evidence_store):
        raise NotImplementedError

    def import_stock_master_bytes(self, filename: str, payload: bytes, stock_master_store):
        raise NotImplementedError

    def import_fundamental_evidence_bytes(self, filename: str, payload: bytes, evidence_store):
        raise NotImplementedError

    def fetch_stock_master(self, *args, **kwargs):
        raise ProviderNotConfigured(f"{self.provider_name} stock-master retrieval is disabled.")

    def fetch_fundamental_evidence(self, *args, **kwargs):
        raise ProviderNotConfigured(f"{self.provider_name} fundamental-data retrieval is disabled.")


class ManualResearchProvider(ResearchDataProvider):
    """Manual, user-obtained files only. Makes no network requests."""

    provider_name = "Manual research files"
    access_basis = "user-supplied file"

    def __init__(self):
        self._evidence_provider = ManualEvidenceProvider()

    def import_stock_master(self, path: Path, stock_master_store):
        return stock_master_store.import_json_file(path)

    def import_fundamental_evidence(self, path: Path, evidence_store):
        return self._evidence_provider.import_file(path, evidence_store)

    def import_stock_master_bytes(self, filename: str, payload: bytes, stock_master_store):
        return stock_master_store.import_json_bytes(filename, payload)

    def import_fundamental_evidence_bytes(self, filename: str, payload: bytes, evidence_store):
        return evidence_store.import_json_bytes(filename, payload)

