from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook

from india_stock_intelligence.agents.local_backend import LocalEvidenceAgentBackend
from india_stock_intelligence.agents.sample_backend import SampleAgentBackend
from india_stock_intelligence.agents.schemas import get_schema
from india_stock_intelligence.agents.validation import OutputValidationError, validate_agent_output
from india_stock_intelligence.config import Settings
from india_stock_intelligence.evidence import EvidenceBundle, EvidenceError, EvidenceRepository, EvidenceStore
from india_stock_intelligence.ingestion import MarketImporter
from india_stock_intelligence.llm.openai_backend import OpenAIResponsesBackend, PaidAPICallNotApproved
from india_stock_intelligence.migrations import apply_migrations
from india_stock_intelligence.orchestrator import ResearchOrchestrator
from india_stock_intelligence.reports import ReportGenerator
from india_stock_intelligence.storage import connect


CSV_HEADER = ["Exchange", "Symbol", "Company Name", "Security ID", "Security Type", "Listing Status",
              "Date", "Open", "High", "Low", "Close", "Volume", "Source Timestamp", "Source"]


def seed_sample_security(settings: Settings) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_HEADER)
    writer.writeheader()
    writer.writerow({"Exchange": "NSE", "Symbol": "DEMO", "Company Name": "Synthetic Demo Company",
                     "Security ID": "SYNTHETIC-DEMO-001", "Security Type": "Equity", "Listing Status": "Active",
                     "Date": "2025-01-02", "Open": "100", "High": "100", "Low": "100", "Close": "100",
                     "Volume": "1000", "Source Timestamp": "", "Source": "SAMPLE_SYNTHETIC"})
    MarketImporter(settings=settings).import_bytes("sample_market_data.csv", buffer.getvalue().encode("utf-8"))
    return "NSE:id:SYNTHETIC-DEMO-001"


@pytest.fixture
def settings(tmp_path):
    configured = Settings(data_dir=tmp_path / "data")
    apply_migrations(configured.database_path)
    return configured


def test_evidence_requires_provenance_archives_bytes_and_excludes_future_retrieval(settings):
    security_key = seed_sample_security(settings)
    document = {"records": [{
        "security_key": security_key, "source_type": "annual_report", "source_name": "Issuer annual report",
        "source_url": "https://example.org/annual-report.pdf", "publication_date": "2024-12-01",
        "reporting_period": "FY2024", "retrieved_at": "2025-01-03T00:00:00Z",
        "claim": "Audited revenue was reported", "value": 1000, "unit": "INR million",
    }]}
    payload = json.dumps(document).encode("utf-8")
    result = EvidenceStore(settings=settings).import_json_bytes("evidence.json", payload)
    assert (settings.financial_raw_dir / f"{hashlib.sha256(payload).hexdigest()}_evidence.json").read_bytes() == payload
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-01-02")
    assert bundle.evidence_items == ()  # retrieval happened after the as-of date
    assert "annual_report" in bundle.missing_categories

    document["records"][0].pop("retrieved_at")
    with pytest.raises(EvidenceError, match="retrieved_at"):
        EvidenceStore(settings=settings).import_json_bytes("bad.json", json.dumps(document).encode("utf-8"))
    with connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 1


def test_all_agents_receive_identical_bundle_and_judge_receives_complete_prior_reports(settings):
    security_key = seed_sample_security(settings)

    class CaptureAndRetryBackend(SampleAgentBackend):
        def __init__(self):
            self.calls = []
            self.failed_once = False

        def generate_json(self, *, agent_id, prompt, payload, schema):
            self.calls.append((agent_id, payload["evidence_bundle"], payload["additional_context"]))
            if agent_id == "equity_research_analyst" and not self.failed_once:
                self.failed_once = True
                raise TimeoutError("synthetic retry fixture")
            return super().generate_json(agent_id=agent_id, prompt=prompt, payload=payload, schema=schema)

    backend = CaptureAndRetryBackend()
    orchestrator = ResearchOrchestrator(settings=settings, backend=backend, max_attempts=2)
    first = orchestrator.run_security(security_key, as_of_date="2025-01-02")
    second = orchestrator.run_security(security_key, as_of_date="2025-01-02")

    assert first.status == second.status == "completed"
    assert first.verdict == second.verdict == "INCONCLUSIVE"
    assert first.risk_score is second.risk_score is None
    assert second.changes["previous_run_id"] == first.research_run_id
    assert {bundle["evidence_digest"] for _, bundle, _ in backend.calls} == {first.evidence_digest}
    judge_call = next(context for agent, _, context in backend.calls if agent == "independent_judge")
    assert set(judge_call["complete_prior_reports"]) == {"equity_research_analyst", "bull_bear_analyst"}
    assert all(judge_call["complete_prior_reports"].values())

    with connect(settings.database_path) as conn:
        retries = conn.execute("""SELECT status FROM agent_outputs WHERE research_run_id=?
                                  AND agent_id='equity_research_analyst' ORDER BY attempt""",
                               (first.research_run_id,)).fetchall()
        metric_outputs = [json.loads(row[0]) for row in conn.execute(
            "SELECT output_json FROM agent_outputs WHERE research_run_id=? AND agent_id='equity_research_analyst' AND status='completed'",
            (first.research_run_id,)).fetchall()]
        assert [row["status"] for row in retries] == ["failed", "completed"]
        assert all(metric["value"] is None and metric["status"] == "not_available"
                   for metric in metric_outputs[0]["financial_metrics"])
        assert conn.execute("SELECT COUNT(*) FROM agent_outputs WHERE research_run_id=? AND status='completed'",
                            (first.research_run_id,)).fetchone()[0] == 3

    reports = ReportGenerator(settings=settings).generate(sample_data=True)
    assert all(Path(path).exists() for path in (first.report_path, reports["files"]["excel"], reports["files"]["pdf"]))
    assert reports["research_coverage"]["completed_runs"] == 1
    workbook = load_workbook(reports["files"]["excel"], read_only=True, data_only=True)
    output_rows = list(workbook["Agent Outputs"].values)
    output_header = output_rows[0]
    assert any("INCONCLUSIVE" in str(row[output_header.index("output_json")]) for row in output_rows[1:])
    workbook.close()
    pdf_bytes = Path(reports["files"]["pdf"]).read_bytes()
    assert pdf_bytes.startswith(b"%PDF") and b"/Type /Page" in pdf_bytes


def test_agent_output_rejects_uncited_or_unknown_evidence(settings):
    security_key = seed_sample_security(settings)
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-01-02")
    backend = SampleAgentBackend()
    payload = {"evidence_bundle": {**bundle.payload(), "evidence_digest": bundle.digest}, "additional_context": {}}
    output = backend.generate_json(agent_id="equity_research_analyst", prompt="", payload=payload, schema={}).output
    output["business_quality"].append({"claim": "Revenue is growing", "evidence_ids": [], "confidence": 90,
                                       "why_it_matters": "Growth can support returns."})
    with pytest.raises(OutputValidationError, match="without an evidence citation"):
        validate_agent_output(output, agent_id="equity_research_analyst", bundle=bundle,
                              schema=get_schema("equity_research_analyst"))


def test_reported_metric_period_must_match_cited_evidence(settings):
    security_key = seed_sample_security(settings)
    store = EvidenceStore(settings=settings)
    record = {"security_key": security_key, "source_type": "annual_report", "source_name": "Synthetic test filing",
              "source_url": "https://example.org/synthetic-test.pdf", "publication_date": "2025-01-01",
              "reporting_period": "FY2024", "retrieved_at": "2025-01-02T00:00:00Z",
              "claim": "Synthetic revenue fixture", "value": 100, "unit": "INR million", "is_synthetic": True}
    store.import_json_bytes("synthetic_facts.json", json.dumps({"records": [record]}).encode("utf-8"))
    bundle = EvidenceRepository(settings=settings).build_bundle(security_key, "2025-01-02")
    assert len(bundle.evidence_items) == 1
    baseline_bundle = replace(bundle, evidence_items=(), sample_data=True)
    payload = {"evidence_bundle": {**baseline_bundle.payload(), "evidence_digest": baseline_bundle.digest},
               "additional_context": {}}
    output = SampleAgentBackend().generate_json(agent_id="equity_research_analyst", prompt="", payload=payload,
                                                 schema=get_schema("equity_research_analyst")).output
    item = bundle.evidence_items[0]
    output["evidence_digest"] = bundle.digest
    output["summary_evidence_ids"] = [item.evidence_id]
    metric = output["financial_metrics"][0]
    metric.update({"value": 100, "reporting_period": "FY2024", "evidence_ids": [item.evidence_id],
                   "status": "reported"})
    validate_agent_output(output, agent_id="equity_research_analyst", bundle=bundle,
                          schema=get_schema("equity_research_analyst"))

    metric["reporting_period"] = "FY2023"
    with pytest.raises(OutputValidationError, match="reporting_period"):
        validate_agent_output(output, agent_id="equity_research_analyst", bundle=bundle,
                              schema=get_schema("equity_research_analyst"))


def test_synthetic_agent_workflow_cites_conflicts_and_judge_stays_inconclusive(settings):
    security_key = seed_sample_security(settings)
    records = []
    for number, (period, value, source) in enumerate((
        ("FY2024", 90, "Synthetic fixture filing prior year"),
        ("FY2025", 100, "Synthetic fixture filing A"),
        ("FY2025", 110, "Synthetic fixture filing B"),
    ), start=1):
        records.append({
            "security_key": security_key, "source_type": "annual_report", "source_name": source,
            "source_url": f"https://example.org/synthetic-agent-{number}.pdf",
            "publication_date": "2025-06-01", "reporting_period": period,
            "retrieved_at": "2025-06-02T10:00:00+00:00", "claim": f"Synthetic revenue for {period}",
            "metric_key": "revenue", "financial_basis": "standalone", "value": value,
            "unit": "INR million", "is_synthetic": True,
        })
    EvidenceStore(settings=settings).import_json_bytes("synthetic_agent_evidence.json",
                                                       json.dumps({"records": records}).encode())

    class SyntheticReviewBackend:
        def __init__(self):
            self.outputs = {}

        def generate_json(self, *, agent_id, prompt, payload, schema):
            evidence_bundle = payload["evidence_bundle"]
            ids = [item["evidence_id"] for item in evidence_bundle["evidence_items"]]
            snapshot = evidence_bundle["fundamental_snapshot"]
            metric = next(item for item in snapshot["metrics"] if item["metric_key"] == "revenue_growth_yoy")
            assert metric["data_status"] == "conflicting"
            claim = {"claim": "Synthetic source records disagree on FY2025 revenue.",
                     "evidence_ids": ids, "confidence": 100,
                     "why_it_matters": "The growth rate cannot be reconciled from conflicting inputs."}
            base = {"agent_id": agent_id, "security_key": security_key,
                    "as_of_date": evidence_bundle["as_of_date"], "evidence_digest": evidence_bundle["evidence_digest"]}
            if agent_id == "equity_research_analyst":
                output = base | {
                    "executive_summary": "Synthetic evidence conflicts; no investment thesis is supportable.",
                    "summary_evidence_ids": ids, "business_overview": [], "business_quality": [],
                    "financial_performance": [claim],
                    "financial_metrics": [{"metric": "revenue_growth_yoy", "value": None, "unit": "%",
                                           "reporting_period": "FY2025", "financial_basis": "standalone",
                                           "evidence_ids": ids, "status": "conflicting",
                                           "note": "Conflicting sourced values; growth is not reported."}],
                    "strengths": [], "weaknesses": [], "governance": [], "valuation": [],
                    "growth_prospects": [], "key_risks": [claim], "investment_thesis": [],
                    "missing_evidence": ["A reconciled financial statement is missing."],
                }
            elif agent_id == "bull_bear_analyst":
                output = base | {
                    "summary": "Synthetic sources disagree, so the bull case is not established.",
                    "summary_evidence_ids": ids, "bull_arguments": [], "bear_arguments": [claim],
                    "contradictory_evidence": [claim], "hidden_risks": [claim], "catalysts": [],
                    "thesis_invalidation": [], "missing_evidence": ["A reconciled financial statement is missing."],
                }
            else:
                output = base | {
                    "verdict": "INCONCLUSIVE", "confidence_score": 0,
                    "confidence_reason": "Conflicting synthetic evidence cannot support a conclusion.",
                    "risk_score": None, "risk_reason": "Risk cannot be scored from conflicting fixture evidence.",
                    "figure_verifications": [{"figure": "FY2025 revenue", "researcher_value": "100 / 110",
                                               "verified_value": None, "status": "contradicted",
                                               "evidence_ids": ids, "reason": "The two fixture sources disagree."}],
                    "key_determinants": [claim], "watchlist_action": "NOT_SET",
                    "conditions_to_revisit": ["Reconcile the source figures."],
                    "missing_evidence": ["A reconciled financial statement is missing."],
                    "independent_reasoning": "INCONCLUSIVE. The available synthetic sources conflict.",
                    "reasoning_evidence_ids": ids,
                }
            self.outputs[agent_id] = output
            return output

    backend = SyntheticReviewBackend()
    result = ResearchOrchestrator(settings=settings, backend=backend).run_security(security_key, as_of_date="2025-06-03")
    assert result.status == "completed"
    assert result.verdict == "INCONCLUSIVE"
    assert set(backend.outputs) == {"equity_research_analyst", "bull_bear_analyst", "independent_judge"}
    assert backend.outputs["equity_research_analyst"]["financial_metrics"][0]["evidence_ids"]
    assert backend.outputs["bull_bear_analyst"]["contradictory_evidence"]
    assert backend.outputs["independent_judge"]["figure_verifications"][0]["status"] == "contradicted"


def test_judge_cannot_return_buy_candidate_when_fundamental_evidence_is_missing():
    bundle = EvidenceBundle(
        security={"security_key": "NSE:symbol:EMPTY"}, as_of_date="2025-06-03", screening_snapshot=None,
        evidence_items=(), quality_flags=(), missing_categories=("annual_report",), sample_data=False,
    )
    output = {
        "agent_id": "independent_judge", "security_key": bundle.security["security_key"],
        "as_of_date": bundle.as_of_date, "evidence_digest": bundle.digest,
        "verdict": "BUY_CANDIDATE", "confidence_score": 85,
        "confidence_reason": "Synthetic unsupported test output.", "risk_score": None,
        "risk_reason": "No risk inputs.", "figure_verifications": [], "key_determinants": [],
        "watchlist_action": "NOT_SET", "conditions_to_revisit": [], "missing_evidence": ["Annual reports"],
        "independent_reasoning": "Unsupported positive claim with no evidence.", "reasoning_evidence_ids": [],
    }
    with pytest.raises(OutputValidationError, match="BUY_CANDIDATE requires verified"):
        validate_agent_output(output, agent_id="independent_judge", bundle=bundle,
                              schema=get_schema("independent_judge"))


def test_openai_backend_requires_explicit_run_approval_and_uses_strict_nonstored_schema(monkeypatch):
    monkeypatch.setenv("INDIA_STOCK_OPENAI_ENABLED", "true")
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"output_text": "{}", "_request_id": "test-request"})()

    client = type("Client", (), {"responses": Responses()})()
    backend = OpenAIResponsesBackend(approved_for_this_run=False, model="explicit-test-model",
                                    api_key="test-key", client=client)
    with pytest.raises(PaidAPICallNotApproved):
        backend.generate_json(agent_id="agent", prompt="prompt", payload={}, schema={})
    assert calls == []

    approved = OpenAIResponsesBackend(approved_for_this_run=True, model="explicit-test-model",
                                      api_key="test-key", client=client)
    result = approved.generate_json(agent_id="agent", prompt="prompt", payload={}, schema={"type": "object"})
    assert result.request_id == "test-request"
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["text"]["format"]["schema"] == {"type": "object"}


def test_local_evidence_agent_backend_evaluates_real_evidence_and_respects_buy_gate(settings):
    from india_stock_intelligence.providers.research import ManualResearchProvider
    from india_stock_intelligence.stock_master import SecurityMasterStore

    master_doc = {
        "source": {
            "source_name": "Stage 4D test listing master",
            "source_url": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
            "publication_date": "2026-06-01",
            "retrieved_at": "2026-06-02T10:00:00+05:30",
        },
        "records": [
            {"symbol": "TCS", "company_name": "Tata Consultancy Services Limited",
             "isin": "INE467B01029", "listing_segment": "MAINBOARD", "security_status": "ACTIVE"},
        ],
    }
    sec_key = ManualResearchProvider().import_stock_master_bytes(
        "master.json", json.dumps(master_doc).encode(), SecurityMasterStore(settings=settings)
    )["security_keys"][0]

    records = [
        {
            "security_key": sec_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2025-26/ar/annual-report-2025-2026.pdf",
            "publication_date": "2026-06-01",
            "reporting_period": "FY2026",
            "retrieved_at": "2026-06-02T10:00:00+05:30",
            "claim": "Consolidated revenue from operations reported at ₹267,021 crore for FY2025-26.",
            "metric_key": "revenue",
            "value": 267021,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "is_synthetic": False,
        },
        {
            "security_key": sec_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2025-26/ar/annual-report-2025-2026.pdf",
            "publication_date": "2026-06-01",
            "reporting_period": "FY2026",
            "retrieved_at": "2026-06-02T10:00:00+05:30",
            "claim": "Consolidated profit attributable to shareholders of ₹49,210 crore for FY2025-26.",
            "metric_key": "net_profit",
            "value": 49210,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "is_synthetic": False,
        },
        {
            "security_key": sec_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2025-26/ar/annual-report-2025-2026.pdf",
            "publication_date": "2026-06-01",
            "reporting_period": "FY2026",
            "retrieved_at": "2026-06-02T10:00:00+05:30",
            "claim": "Average equity attributable to owners of ₹100,998 crore (average of FY26 ₹107,240 cr and FY25 ₹94,756 cr).",
            "metric_key": "average_shareholders_equity",
            "value": 100998,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "is_synthetic": False,
        },
        {
            "security_key": sec_key,
            "source_type": "annual_report",
            "source_name": "TCS Annual Report 2025-26",
            "source_url": "https://www.tcs.com/content/dam/tcs/investor-relations/financial-statements/2025-26/ar/annual-report-2025-2026.pdf",
            "publication_date": "2026-06-01",
            "reporting_period": "FY2026",
            "retrieved_at": "2026-06-02T10:00:00+05:30",
            "claim": "Consolidated operating cash flow of ₹52,094 crore for FY2025-26.",
            "metric_key": "operating_cash_flow",
            "value": 52094,
            "unit": "INR crore",
            "verification_status": "verified",
            "financial_basis": "consolidated",
            "is_synthetic": False,
        },
    ]
    EvidenceStore(settings=settings).import_json_bytes("tcs_evidence.json", json.dumps({"records": records}).encode())

    orchestrator = ResearchOrchestrator(settings=settings, backend=LocalEvidenceAgentBackend())
    res = orchestrator.run_security(sec_key, as_of_date="2026-06-05")
    assert res.status == "completed"
    # Should be WATCH because YoY growth and valuation are missing (so BUY_CANDIDATE is blocked)
    assert res.verdict == "WATCH"
    assert res.confidence_score > 0
    assert res.risk_score is not None
    report_data = json.loads(Path(res.report_path).read_bytes())
    assert set(report_data["agent_reports"]) == {"equity_research_analyst", "bull_bear_analyst", "independent_judge"}
    assert report_data["sample_label"] is None


def test_market_debate_engine_and_watchlist_automation(settings):
    from india_stock_intelligence.market_intelligence import MarketDebateEngine, WatchlistStore

    sec_key = seed_sample_security(settings)
    wl = WatchlistStore(settings=settings)
    wl.add("DEMO", added_by="user", reason="Test watch")
    assert any(item["security_key"] == sec_key for item in wl.load()["items"])

    briefing = MarketDebateEngine(settings=settings).generate_briefings(top_n_debate=5)
    assert briefing["latest_trading_date"] == "2025-01-02"
    assert "market_open_expectation" in briefing
    assert "market_close_summary" in briefing
    assert len(briefing["stock_debates"]) >= 1
    debate = briefing["stock_debates"][0]
    assert debate["bull_case"]
    assert debate["bear_case"]
    assert debate["judge_verdict_reasoning"]

    wl.remove("DEMO")
    assert wl.load()["items"] == []


