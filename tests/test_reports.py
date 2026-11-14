from core.orchestrator import ResearchOrchestrator
from core.report_generator import ReportGenerator
from india_stock_intelligence.orchestrator import ResearchOrchestrator as PackageOrchestrator
from india_stock_intelligence.reports import ReportGenerator as PackageReportGenerator


def test_requested_top_level_core_modules_are_compatibility_facades():
    assert ResearchOrchestrator is PackageOrchestrator
    assert ReportGenerator is PackageReportGenerator
