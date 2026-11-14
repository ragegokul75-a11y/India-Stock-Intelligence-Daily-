from india_stock_intelligence.providers.evidence import ManualEvidenceProvider


class NewsProvider(ManualEvidenceProvider):
    provider_name = "Manual news evidence"


__all__ = ["NewsProvider", "ManualEvidenceProvider"]
