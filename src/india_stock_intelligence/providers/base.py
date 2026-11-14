from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ProviderNotConfigured(RuntimeError):
    pass


class ExchangeProvider(ABC):
    """Extension point for permitted official sources; import always works offline."""

    exchange: str
    provider_name: str
    download_enabled = False

    def __init__(self, *, source_name: str | None = None, column_aliases: dict[str, set[str]] | None = None):
        self.source_name = source_name or f"{self.provider_name} manual file import"
        self.column_aliases = column_aliases or {}

    @abstractmethod
    def import_file(self, path: Path, importer):
        """Import a user-supplied exchange file through the common audited pipeline."""

    def import_bytes(self, filename: str, payload: bytes, importer, *,
                     replace_existing: bool = False, correction_reason: str | None = None):
        """Import uploaded bytes without a temporary file."""
        return importer.import_bytes(filename, payload, source_name=self.source_name, expected_exchange=self.exchange,
                                     extra_aliases=self.column_aliases, replace_existing=replace_existing,
                                     correction_reason=correction_reason)

    def download_eod(self, *args, **kwargs):
        raise ProviderNotConfigured(
            f"{self.provider_name} automated downloads are disabled. Import an official exchange file manually. "
            "A source-specific integration must be reviewed and explicitly enabled first."
        )
