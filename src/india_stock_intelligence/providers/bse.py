from pathlib import Path

from .base import ExchangeProvider


class BSEProvider(ExchangeProvider):
    exchange = "BSE"
    provider_name = "BSE"

    def import_file(self, path: Path, importer):
        return importer.import_file(path, source_name=self.source_name, expected_exchange=self.exchange,
                                    extra_aliases=self.column_aliases)
