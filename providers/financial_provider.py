from india_stock_intelligence.providers.evidence import ManualEvidenceProvider


class FinancialProvider(ManualEvidenceProvider):
    provider_name = "Manual financial evidence"


__all__ = ["FinancialProvider", "ManualEvidenceProvider"]
