from __future__ import annotations

import json
import math
import re

from ..fundamental_catalog import FUNDAMENTAL_METRICS
from ..fundamentals import FundamentalAnalyzer

class OutputValidationError(ValueError):
    pass


_ANALYST_METRIC_INPUTS = {
    "revenue_growth_yoy": {"revenue"},
    "profit_growth_yoy": {"net_profit"},
    "eps_growth_yoy": {"eps"},
    "operating_margin": {"operating_profit", "revenue"},
    "ebitda_margin": {"ebitda", "revenue"},
    "net_profit_margin": {"net_profit", "revenue"},
    "roe": {"roe", "net_profit", "average_shareholders_equity"},
    "roce": {"roce", "ebit", "average_capital_employed"},
    "debt_to_equity": {"debt_to_equity", "total_debt", "shareholders_equity"},
    "operating_cash_flow": {"operating_cash_flow"},
    "free_cash_flow": {"free_cash_flow", "operating_cash_flow", "capital_expenditure"},
    "promoter_holding": {"promoter_holding"},
    "promoter_pledging": {"promoter_pledging"},
    "institutional_holding": {"institutional_holding"},
    "pe_ratio": {"pe_ratio"},
    "pb_ratio": {"pb_ratio"},
    "ps_ratio": {"ps_ratio"},
    "ev_to_ebitda": {"ev_to_ebitda"},
    "sector_valuation": {"sector_valuation"},
}


def _canonical_metric_key(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    aliases = {
        "revenue growth": "revenue_growth_yoy",
        "profit growth": "profit_growth_yoy",
        "net profit growth": "profit_growth_yoy",
        "eps growth": "eps_growth_yoy",
        "net margin": "net_profit_margin",
    }
    if normalized in aliases:
        return aliases[normalized]
    for key, label in FUNDAMENTAL_METRICS.items():
        if normalized in {re.sub(r"[^a-z0-9]+", " ", key.casefold()).strip(),
                          re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()}:
            return key
    return None


def _matches_type(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_json_schema(value, schema: dict, path: str = "$" ) -> None:
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_matches_type(value, kind) for kind in expected_type):
            raise OutputValidationError(f"{path} has the wrong type; expected one of {expected_type}")
    elif expected_type and not _matches_type(value, expected_type):
        raise OutputValidationError(f"{path} has the wrong type; expected {expected_type}")
    if "anyOf" in schema:
        matched = False
        for alternative in schema["anyOf"]:
            try:
                validate_json_schema(value, alternative, path)
                matched = True
                break
            except OutputValidationError:
                continue
        if not matched:
            raise OutputValidationError(f"{path} does not match any allowed schema")
    if "enum" in schema and value not in schema["enum"]:
        raise OutputValidationError(f"{path} must be one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        raise OutputValidationError(f"{path} must equal {schema['const']!r}")
    if isinstance(value, dict):
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise OutputValidationError(f"{path} is missing required field(s): {', '.join(missing)}")
        properties = schema.get("properties", {})
        extras = [key for key in value if key not in properties]
        if schema.get("additionalProperties") is False and extras:
            raise OutputValidationError(f"{path} has unsupported field(s): {', '.join(extras)}")
        for key, child in properties.items():
            if key in value:
                validate_json_schema(value[key], child, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate_json_schema(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise OutputValidationError(f"{path} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise OutputValidationError(f"{path} is above maximum {schema['maximum']}")


def _verify_citations(result: dict, bundle, agent_id: str) -> None:
    evidence_by_id = {item.evidence_id: item for item in bundle.evidence_items}
    fundamental_snapshot = FundamentalAnalyzer().analyze(bundle) if agent_id == "equity_research_analyst" else None

    def check_ids(ids, where, required: bool):
        if required and not ids:
            raise OutputValidationError(f"{where} makes a factual claim without an evidence citation")
        for evidence_id in ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                raise OutputValidationError(f"{where} cites evidence ID not in the shared bundle: {evidence_id}")
            if not (item.source_url and item.publication_date and item.reporting_period and item.retrieved_at):
                raise OutputValidationError(f"{where} cites evidence with incomplete provenance: {evidence_id}")

    claim_arrays = {
        "equity_research_analyst": ("business_overview", "business_quality", "financial_performance", "strengths",
                                    "weaknesses", "governance", "valuation", "growth_prospects", "key_risks",
                                    "investment_thesis"),
        "bull_bear_analyst": ("bull_arguments", "bear_arguments", "contradictory_evidence", "hidden_risks",
                              "catalysts", "thesis_invalidation"),
        "independent_judge": ("key_determinants",),
    }[agent_id]
    for field in claim_arrays:
        for index, claim in enumerate(result[field]):
            check_ids(claim["evidence_ids"], f"{field}[{index}]", required=True)
    if agent_id == "equity_research_analyst":
        for metric in result["financial_metrics"]:
            check_ids(metric["evidence_ids"], f"financial_metrics[{metric['metric']}].evidence_ids", required=False)
            for evidence_id in metric["evidence_ids"]:
                if evidence_by_id[evidence_id].financial_basis != metric["financial_basis"]:
                    raise OutputValidationError(
                        f"financial_metrics[{metric['metric']}] mixes financial bases or mislabels its cited evidence"
                    )
            if metric["status"] in {"not_available", "conflicting"} and metric["value"] is not None:
                raise OutputValidationError(
                    f"financial_metrics[{metric['metric']}] cannot provide a value with status {metric['status']}"
                )
            if metric["status"] in {"reported", "not_verified", "synthetic"} and metric["value"] is not None:
                check_ids(metric["evidence_ids"], f"financial_metrics[{metric['metric']}].evidence_ids", required=True)
                periods = {evidence_by_id[evidence_id].reporting_period for evidence_id in metric["evidence_ids"]}
                if metric["reporting_period"] not in periods:
                    raise OutputValidationError(
                        f"financial_metrics[{metric['metric']}] reporting_period is not present in its cited evidence"
                    )
                if metric["status"] == "synthetic" and not all(evidence_by_id[evidence_id].is_synthetic
                                                                  for evidence_id in metric["evidence_ids"]):
                    raise OutputValidationError(
                        f"financial_metrics[{metric['metric']}] is marked synthetic but cites non-synthetic evidence"
                    )
            elif metric["status"] == "not_available" and metric["value"] is not None:
                raise OutputValidationError(f"financial_metrics[{metric['metric']}] cannot supply a value when status is not_available")
            else:
                check_ids(metric["evidence_ids"], f"financial_metrics[{metric['metric']}].evidence_ids", required=False)
            metric_key = _canonical_metric_key(metric["metric"])
            if metric_key:
                relevant_keys = _ANALYST_METRIC_INPUTS.get(metric_key, {metric_key})
                basis_items = [item for item in bundle.evidence_items
                               if item.security_key == bundle.security["security_key"]
                               and item.financial_basis == metric["financial_basis"]
                               and item.metric_key in relevant_keys]
                if basis_items:
                    expected = next((item for item in fundamental_snapshot.metrics
                                     if item.metric_key == metric_key
                                     and item.financial_basis == metric["financial_basis"]), None)
                    if expected is None:
                        raise OutputValidationError(f"No deterministic snapshot exists for {metric['metric']}")
                    expected_status = {
                        "verified": "reported", "unverified": "not_verified", "synthetic": "synthetic",
                        "conflicting": "conflicting", "missing": "not_available",
                    }[expected.data_status]
                    numeric_match = False
                    if isinstance(expected.value, (int, float)) and not isinstance(expected.value, bool):
                        numeric_match = (isinstance(metric["value"], (int, float))
                                         and not isinstance(metric["value"], bool)
                                         and math.isclose(float(metric["value"]), float(expected.value),
                                                          rel_tol=1e-9, abs_tol=0.005))
                    else:
                        numeric_match = metric["value"] == expected.value
                    if (not numeric_match or metric["status"] != expected_status
                            or metric["unit"] != expected.unit
                            or metric["reporting_period"] != expected.reporting_period
                            or set(metric["evidence_ids"]) != set(expected.evidence_ids)):
                        raise OutputValidationError(
                            f"financial_metrics[{metric['metric']}] does not match the deterministic sourced snapshot"
                        )
        summary_ids = result["summary_evidence_ids"]
    elif agent_id == "bull_bear_analyst":
        summary_ids = result["summary_evidence_ids"]
    else:
        for index, item in enumerate(result["figure_verifications"]):
            check_ids(item["evidence_ids"], f"figure_verifications[{index}]", required=item["status"] != "not_verifiable")
        check_ids(result["reasoning_evidence_ids"], "independent_reasoning", required=bool(bundle.evidence_items))
        summary_ids = result["reasoning_evidence_ids"]
    check_ids(summary_ids, "summary", required=bool(bundle.evidence_items))


def _enforce_buy_evidence_gate(result: dict, bundle) -> None:
    if result.get("verdict") != "BUY_CANDIDATE":
        return
    if bundle.sample_data:
        raise OutputValidationError("BUY_CANDIDATE is blocked for synthetic evidence; use INCONCLUSIVE.")

    snapshot = FundamentalAnalyzer().analyze(bundle)
    groups = {
        "growth": {"revenue_growth_yoy", "profit_growth_yoy"},
        "profitability": {"net_profit_margin", "ebitda_margin", "operating_margin"},
        "returns": {"roe", "roce"},
        "balance_or_cash_flow": {"debt_to_equity", "operating_cash_flow"},
        "valuation": {"pe_ratio", "ev_to_ebitda", "sector_valuation"},
    }
    usable = {}
    for metric in snapshot.metrics:
        if metric.data_status == "verified" and metric.value is not None:
            usable.setdefault(metric.financial_basis, []).append(metric)

    determinant_ids = {evidence_id for item in result.get("key_determinants", [])
                       for evidence_id in item.get("evidence_ids", [])}
    missing_groups = set(groups)
    for metrics in usable.values():
        available = {
            group for group, metric_keys in groups.items()
            if any(metric.metric_key in metric_keys for metric in metrics)
        }
        cited_groups = {
            group for group, metric_keys in groups.items()
            if any(metric.metric_key in metric_keys and set(metric.evidence_ids) & determinant_ids for metric in metrics)
        }
        if available == set(groups) and cited_groups == set(groups):
            return
        absent = set(groups) - available
        if len(absent) < len(missing_groups):
            missing_groups = absent or {"Judge determinant citations"}
    raise OutputValidationError(
        "BUY_CANDIDATE requires verified, non-synthetic evidence for growth, profitability, returns, "
        "balance/cash flow, and valuation under one financial basis, cited by Judge determinants; "
        f"insufficient coverage ({', '.join(sorted(missing_groups)) or 'determinant citations'})."
    )


def validate_agent_output(raw_output, *, agent_id: str, bundle, schema: dict) -> dict:
    if isinstance(raw_output, str):
        try:
            result = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OutputValidationError("Agent output is not valid JSON") from exc
    elif isinstance(raw_output, dict):
        result = raw_output
    else:
        raise OutputValidationError("Agent output must be a JSON object")
    validate_json_schema(result, schema)
    if result["agent_id"] != agent_id:
        raise OutputValidationError("Agent ID does not match the requested agent")
    if result["security_key"] != bundle.security["security_key"]:
        raise OutputValidationError("Agent changed the security identity")
    if result["as_of_date"] != bundle.as_of_date:
        raise OutputValidationError("Agent changed the evidence as-of date")
    if result["evidence_digest"] != bundle.digest:
        raise OutputValidationError("Agent did not use the exact shared evidence bundle")
    _verify_citations(result, bundle, agent_id)
    if agent_id == "independent_judge":
        _enforce_buy_evidence_gate(result, bundle)
    return result
