from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SampleResult:
    output: dict
    request_id: str | None = None
    model: str | None = "offline-synthetic-demo"


class SampleAgentBackend:
    """Deterministic offline fixtures; produces no network traffic or financial estimates."""

    def generate_json(self, *, agent_id: str, prompt: str, payload: dict, schema: dict) -> SampleResult:
        bundle = payload["evidence_bundle"]
        if not bundle["sample_data"]:
            raise ValueError("Offline sample reports are allowed only for records marked synthetic sample data.")
        if bundle["evidence_items"]:
            raise ValueError("The offline sample backend does not evaluate financial evidence; use explicit approved model calls or remove the sample evidence fixture.")
        security_key = bundle["security"]["security_key"]
        as_of = bundle["as_of_date"]
        digest = self._digest(bundle)
        categories = bundle["missing_categories"]
        missing = [f"No verified {category.replace('_', ' ')} evidence was supplied." for category in categories]
        if not missing:
            missing = ["Offline demo does not evaluate supplied financial facts; run the approved live backend to analyze them."]

        if agent_id == "equity_research_analyst":
            metrics = []
            for name in (
                "revenue_growth_yoy", "profit_growth_yoy", "eps_growth_yoy", "roe", "roce", "operating_margin",
                "ebitda_margin", "net_profit_margin", "debt_to_equity", "operating_cash_flow", "free_cash_flow",
                "promoter_holding", "promoter_pledging", "institutional_holding", "pe_ratio", "pb_ratio",
                "ps_ratio", "ev_to_ebitda", "sector_valuation",
            ):
                metrics.append({"metric": name, "value": None, "unit": None, "reporting_period": None,
                                "financial_basis": "unspecified",
                                "evidence_ids": [], "status": "not_available",
                                "note": "No sourced financial evidence supplied; value intentionally left blank."})
            output = {
                "agent_id": agent_id, "security_key": security_key, "as_of_date": as_of,
                "evidence_digest": digest,
                "executive_summary": "SYNTHETIC DEMO. No verified financial statements or company evidence are present; no investment conclusion is supportable.",
                "summary_evidence_ids": [], "business_overview": [], "business_quality": [],
                "financial_performance": [], "financial_metrics": metrics, "strengths": [], "weaknesses": [],
                "governance": [], "valuation": [], "growth_prospects": [], "key_risks": [], "investment_thesis": [],
                "missing_evidence": missing,
            }
        elif agent_id == "bull_bear_analyst":
            output = {
                "agent_id": agent_id, "security_key": security_key, "as_of_date": as_of,
                "evidence_digest": digest,
                "summary": "SYNTHETIC DEMO. No fundamental facts were supplied, so neither a company-specific bull case nor bear case is invented.",
                "summary_evidence_ids": [], "bull_arguments": [], "bear_arguments": [], "contradictory_evidence": [], "hidden_risks": [],
                "catalysts": [], "thesis_invalidation": [], "missing_evidence": missing,
            }
        elif agent_id == "independent_judge":
            output = {
                "agent_id": agent_id, "security_key": security_key, "as_of_date": as_of,
                "evidence_digest": digest, "verdict": "INCONCLUSIVE", "confidence_score": 0,
                "confidence_reason": "No verified company fundamentals or cited claims were supplied in this offline synthetic demo.",
                "risk_score": None, "risk_reason": "Risk cannot be scored from missing company evidence.",
                "figure_verifications": [], "key_determinants": [], "watchlist_action": "NOT_SET",
                "conditions_to_revisit": ["Provide sourced annual and quarterly filings, announcements, and valuation/sector evidence."],
                "missing_evidence": missing,
                "independent_reasoning": "INCONCLUSIVE. The earlier reports contain no company-specific factual claims to verify. No BUY or AVOID verdict is forced.",
                "reasoning_evidence_ids": [],
            }
        else:
            raise ValueError(f"Unknown agent id: {agent_id}")
        return SampleResult(output=output)

    @staticmethod
    def _digest(bundle: dict) -> str:
        return bundle["evidence_digest"]
