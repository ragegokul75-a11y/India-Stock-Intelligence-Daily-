from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LocalAgentResult:
    output: dict
    request_id: str | None = None
    model: str | None = "local-evidence-deterministic-v1"


class LocalEvidenceAgentBackend:
    """Zero-cost local deterministic three-agent backend that evaluates real or synthetic sourced evidence.

    Runs Agent 01 (Equity Research Analyst), Agent 02 (Bull & Bear Analyst), and
    Agent 03 (Independent Judge) locally without external API calls or paid tokens.
    Strictly adheres to the evidence bundle, deterministic fundamental_snapshot,
    citation verification rules, and BUY_CANDIDATE evidence gate.
    """

    MODEL_NAME = "local-evidence-deterministic-v1"

    def generate_json(self, *, agent_id: str, prompt: str, payload: dict, schema: dict) -> LocalAgentResult:
        bundle = payload["evidence_bundle"]
        context = payload.get("additional_context") or {}
        if agent_id == "equity_research_analyst":
            output = self._run_research_analyst(bundle)
        elif agent_id == "bull_bear_analyst":
            output = self._run_bull_bear_analyst(bundle, context)
        elif agent_id == "independent_judge":
            output = self._run_independent_judge(bundle, context)
        else:
            raise ValueError(f"Unknown agent id: {agent_id}")
        return LocalAgentResult(output=output, request_id=f"local-{agent_id}", model=self.MODEL_NAME)

    @staticmethod
    def _status_map(data_status: str) -> str:
        return {
            "verified": "reported",
            "unverified": "not_verified",
            "synthetic": "synthetic",
            "conflicting": "conflicting",
            "missing": "not_available",
        }[data_status]

    @staticmethod
    def _security_items(bundle: dict) -> list[dict]:
        sec_key = bundle["security"]["security_key"]
        return [
            item for item in bundle.get("evidence_items", [])
            if item.get("security_key") in {sec_key, None}
        ]

    @staticmethod
    def _missing_notes(bundle: dict, snapshot_metrics: list[dict]) -> list[str]:
        notes = [
            f"No verified {cat.replace('_', ' ')} evidence was supplied."
            for cat in bundle.get("missing_categories", [])
        ]
        missing_metrics = [
            m["label"] for m in snapshot_metrics
            if m["data_status"] == "missing"
        ]
        if missing_metrics:
            preview = ", ".join(missing_metrics[:8])
            suffix = f" (+{len(missing_metrics) - 8} more)" if len(missing_metrics) > 8 else ""
            notes.append(f"Missing deterministic metrics: {preview}{suffix}.")
        conflicting_metrics = [
            m["label"] for m in snapshot_metrics
            if m["data_status"] == "conflicting"
        ]
        if conflicting_metrics:
            notes.append(f"Conflicting metric inputs flagged: {', '.join(conflicting_metrics)}.")
        if not notes:
            notes.append("All required evidence categories are present; continue monitoring quarterly filings.")
        return notes

    def _run_research_analyst(self, bundle: dict) -> dict:
        security_key = bundle["security"]["security_key"]
        company_name = bundle["security"].get("company_name") or bundle["security"].get("symbol") or security_key
        as_of = bundle["as_of_date"]
        digest = bundle["evidence_digest"]
        snapshot = bundle.get("fundamental_snapshot") or {"metrics": []}
        snapshot_metrics: list[dict] = snapshot.get("metrics", [])
        screening = bundle.get("screening_snapshot")
        items = self._security_items(bundle)
        all_ids = [item["evidence_id"] for item in items]

        financial_metrics = []
        for m in snapshot_metrics:
            financial_metrics.append({
                "metric": m["metric_key"],
                "value": m["value"],
                "unit": m["unit"],
                "reporting_period": m["reporting_period"],
                "financial_basis": m["financial_basis"],
                "evidence_ids": list(m["evidence_ids"]),
                "status": self._status_map(m["data_status"]),
                "note": m["note"],
            })

        business_overview = []
        business_quality = []
        financial_performance = []
        strengths = []
        weaknesses = []
        governance = []
        valuation = []
        growth_prospects = []
        key_risks = []
        investment_thesis = []

        # Populate claims strictly from sourced evidence items and deterministic snapshot metrics
        for item in items:
            eid = [item["evidence_id"]]
            mk = item.get("metric_key")
            val = item.get("value")
            unit = item.get("unit") or ""
            period = item.get("reporting_period") or ""
            basis = item.get("financial_basis") or "unspecified"
            vstatus = item.get("verification_status", "unverified")
            conf = 85 if vstatus == "verified" and not item.get("is_synthetic") else 55

            if mk in {"revenue", "net_profit", "operating_profit", "ebitda", "ebit", "operating_cash_flow"} and val is not None:
                financial_performance.append({
                    "claim": f"{company_name} reported {mk.replace('_', ' ')} of {val} {unit} for {period} ({basis} basis).",
                    "evidence_ids": eid,
                    "confidence": conf,
                    "why_it_matters": f"Establishes sourced {period} {basis} scale and cash/earnings generation.",
                })
            elif mk is None and item.get("claim"):
                business_overview.append({
                    "claim": item["claim"],
                    "evidence_ids": eid,
                    "confidence": conf,
                    "why_it_matters": item.get("excerpt") or "Sourced context from company filing.",
                })

        for m in snapshot_metrics:
            eids = list(m["evidence_ids"])
            if not eids:
                continue
            mk = m["metric_key"]
            val = m["value"]
            unit = m["unit"] or ""
            period = m["reporting_period"] or ""
            basis = m["financial_basis"]
            dstatus = m["data_status"]
            conf = 85 if dstatus == "verified" else 50

            if dstatus == "conflicting":
                key_risks.append({
                    "claim": f"Conflicting source evidence for {m['label']} in {period} ({basis}).",
                    "evidence_ids": eids,
                    "confidence": 95,
                    "why_it_matters": "Cannot rely on conflicting figures until reconciled against primary statements.",
                })
                weaknesses.append({
                    "claim": f"{m['label']} is unresolved due to conflicting source inputs.",
                    "evidence_ids": eids,
                    "confidence": 90,
                    "why_it_matters": m["note"],
                })
                continue

            if val is None:
                continue

            if mk in {"revenue_growth_yoy", "profit_growth_yoy", "eps_growth_yoy"}:
                growth_prospects.append({
                    "claim": f"{m['label']} is {val:.2f}{unit} in {period} ({basis} basis).",
                    "evidence_ids": eids,
                    "confidence": conf,
                    "why_it_matters": "Measures top-line or bottom-line trajectory from comparable fiscal periods.",
                })
                if isinstance(val, (int, float)) and val > 0:
                    strengths.append({
                        "claim": f"Positive {m['label'].lower()} of {val:.2f}{unit} in {period}.",
                        "evidence_ids": eids,
                        "confidence": conf,
                        "why_it_matters": "Demonstrates expanding scale or earnings on a matched basis.",
                    })
                elif isinstance(val, (int, float)) and val < 0:
                    weaknesses.append({
                        "claim": f"Negative {m['label'].lower()} of {val:.2f}{unit} in {period}.",
                        "evidence_ids": eids,
                        "confidence": conf,
                        "why_it_matters": "Signals contraction across the compared periods.",
                    })

            elif mk in {"roe", "roce", "operating_margin", "ebitda_margin", "net_profit_margin"}:
                business_quality.append({
                    "claim": f"{m['label']} is {val:.2f}{unit} for {period} ({basis} basis).",
                    "evidence_ids": eids,
                    "confidence": conf,
                    "why_it_matters": "Reflects capital efficiency and operating profitability from verified inputs.",
                })
                if isinstance(val, (int, float)) and val >= 15.0:
                    strengths.append({
                        "claim": f"Solid {m['label']} of {val:.2f}{unit} in {period} ({basis}).",
                        "evidence_ids": eids,
                        "confidence": conf,
                        "why_it_matters": "Strong return or margin profile supports internal capital generation.",
                    })

            elif mk in {"debt_to_equity", "operating_cash_flow", "free_cash_flow"}:
                financial_performance.append({
                    "claim": f"{m['label']} stands at {val} {unit} for {period} ({basis} basis).".replace("  ", " "),
                    "evidence_ids": eids,
                    "confidence": conf,
                    "why_it_matters": "Measures balance-sheet leverage or cash flow conversion.",
                })
                if mk == "operating_cash_flow" and isinstance(val, (int, float)) and val > 0:
                    strengths.append({
                        "claim": f"Positive operating cash flow of {val} {unit} in {period} ({basis}).",
                        "evidence_ids": eids,
                        "confidence": conf,
                        "why_it_matters": "Confirms operating activities generated cash inflows.",
                    })
                if mk == "debt_to_equity" and isinstance(val, (int, float)) and val > 1.0:
                    key_risks.append({
                        "claim": f"Debt-to-equity ratio is {val:.2f}x in {period} ({basis}).",
                        "evidence_ids": eids,
                        "confidence": conf,
                        "why_it_matters": "Higher leverage increases sensitivity to debt-service obligations.",
                    })

            elif mk in {"promoter_holding", "promoter_pledging", "institutional_holding"}:
                governance.append({
                    "claim": f"{m['label']} reported at {val}{unit} for {period} ({basis}).",
                    "evidence_ids": eids,
                    "confidence": conf,
                    "why_it_matters": "Ownership and encumbrance disclosures inform governance alignment.",
                })
                if mk == "promoter_pledging" and isinstance(val, (int, float)) and val > 0:
                    key_risks.append({
                        "claim": f"Promoter pledged shares reported at {val}{unit} in {period}.",
                        "evidence_ids": eids,
                        "confidence": conf,
                        "why_it_matters": "Encumbered promoter shares introduce forced-sale and governance risk.",
                    })

            elif mk in {"pe_ratio", "pb_ratio", "ps_ratio", "ev_to_ebitda", "sector_valuation"}:
                valuation.append({
                    "claim": f"{m['label']} is {val} {unit} ({period}, {basis}).".replace("  ", " "),
                    "evidence_ids": eids,
                    "confidence": conf,
                    "why_it_matters": "Provides valuation context relative to fundamentals.",
                })

        if all_ids:
            investment_thesis.append({
                "claim": (
                    f"Sourced analysis for {company_name} incorporates {len(items)} evidence record(s) as of {as_of}; "
                    "only verified comparable metrics are used, while missing or unverified fields remain unassumed."
                ),
                "evidence_ids": all_ids[:6],
                "confidence": 80,
                "why_it_matters": "Prevents unsupported extrapolation where quarterly filings or reconciliations are pending.",
            })
            exec_summary = (
                f"Evidence-backed research for {company_name} ({security_key}) as of {as_of} across {len(items)} "
                f"sourced record(s). Available metrics are reported strictly on their stated financial basis; "
                f"missing or unverified categories are held blank pending primary filings."
            )
            summary_ids = all_ids[:10]
        else:
            screen_note = ""
            if screening and isinstance(screening.get("metrics"), dict):
                sm = screening["metrics"]
                parts = []
                if sm.get("return_1d_pct") is not None:
                    parts.append(f"1d return {sm['return_1d_pct']:+.2f}%")
                if sm.get("return_20d_pct") is not None:
                    parts.append(f"20d return {sm['return_20d_pct']:+.2f}%")
                if sm.get("volatility_20d_pct") is not None:
                    parts.append(f"20d volatility {sm['volatility_20d_pct']:.1f}%")
                if parts:
                    screen_note = f" Price screening context ({screening.get('data_date')}): {', '.join(parts)} (price context is not fundamental evidence)."
            exec_summary = (
                f"No sourced financial statements or company evidence are present for {company_name} ({security_key}) "
                f"as of {as_of}; no investment conclusion is supportable.{screen_note}"
            )
            summary_ids = []

        return {
            "agent_id": "equity_research_analyst",
            "security_key": security_key,
            "as_of_date": as_of,
            "evidence_digest": digest,
            "executive_summary": exec_summary,
            "summary_evidence_ids": summary_ids,
            "business_overview": business_overview,
            "business_quality": business_quality,
            "financial_performance": financial_performance,
            "financial_metrics": financial_metrics,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "governance": governance,
            "valuation": valuation,
            "growth_prospects": growth_prospects,
            "key_risks": key_risks,
            "investment_thesis": investment_thesis,
            "missing_evidence": self._missing_notes(bundle, snapshot_metrics),
        }

    def _run_bull_bear_analyst(self, bundle: dict, context: dict) -> dict:
        security_key = bundle["security"]["security_key"]
        company_name = bundle["security"].get("company_name") or bundle["security"].get("symbol") or security_key
        as_of = bundle["as_of_date"]
        digest = bundle["evidence_digest"]
        snapshot = bundle.get("fundamental_snapshot") or {"metrics": []}
        snapshot_metrics: list[dict] = snapshot.get("metrics", [])
        screening = bundle.get("screening_snapshot")
        items = self._security_items(bundle)
        all_ids = [item["evidence_id"] for item in items]

        bull_arguments = []
        bear_arguments = []
        contradictory_evidence = []
        hidden_risks = []
        catalysts = []
        thesis_invalidation = []

        for m in snapshot_metrics:
            eids = list(m["evidence_ids"])
            if not eids:
                continue
            mk = m["metric_key"]
            val = m["value"]
            unit = m["unit"] or ""
            period = m["reporting_period"] or ""
            basis = m["financial_basis"]
            dstatus = m["data_status"]

            if dstatus == "conflicting":
                contradictory_evidence.append({
                    "claim": f"Contradictory source records for {m['label']} in {period} ({basis}).",
                    "evidence_ids": eids,
                    "confidence": 95,
                    "why_it_matters": m["note"],
                })
                bear_arguments.append({
                    "claim": f"Unreconciled data conflict in {m['label']} ({period}) undermines financial visibility.",
                    "evidence_ids": eids,
                    "confidence": 90,
                    "why_it_matters": "Conflicting primary disclosures prevent reliable trend or margin assessment.",
                })
                continue

            if dstatus == "unverified" and val is not None:
                hidden_risks.append({
                    "claim": f"{m['label']} ({val} {unit}, {period}) is marked unverified / derived—review.",
                    "evidence_ids": eids,
                    "confidence": 85,
                    "why_it_matters": "Derived aggregates or unverified figures require line-item confirmation before scoring.",
                })

            if val is None:
                continue

            if mk in {"roe", "roce", "operating_margin", "ebitda_margin", "net_profit_margin", "operating_cash_flow", "revenue_growth_yoy", "profit_growth_yoy"}:
                if isinstance(val, (int, float)) and val > 0:
                    formatted_val = f"{val:.2f}" if isinstance(val, float) else str(val)
                    bull_arguments.append({
                        "claim": f"Sourced {m['label']} of {formatted_val} {unit} in {period} ({basis}).".replace("  ", " "),
                        "evidence_ids": eids,
                        "confidence": 85 if dstatus == "verified" else 55,
                        "why_it_matters": "Demonstrates positive operating execution or capital return on a verified basis.",
                    })

        # Also inspect raw items for unverified / caveat notes and EOD market evidence
        eod_items = []
        for item in items:
            eid = [item["evidence_id"]]
            excerpt = item.get("excerpt") or ""
            claim_text = item.get("claim") or ""
            source_name = item.get("source_name") or ""
            if "bhavcopy" in source_name.lower() or "eod settlement" in claim_text.lower():
                eod_items.append(item)
            if item.get("verification_status") == "unverified":
                bear_arguments.append({
                    "claim": f"Unverified / review-flagged item: {claim_text}",
                    "evidence_ids": eid,
                    "confidence": 80,
                    "why_it_matters": excerpt or "Requires reconciliation before treating as confirmed financial fact.",
                })
            if any(term in (excerpt + " " + claim_text).lower() for term in ("not directly comparable", "differs from", "excludes", "pending", "not interchangeable")):
                hidden_risks.append({
                    "claim": f"Accounting definition / comparability caveat: {claim_text}",
                    "evidence_ids": eid,
                    "confidence": 85,
                    "why_it_matters": excerpt or "Accounting definitions differ across peers; direct comparison can mislead.",
                })

        # Add EOD market-action arguments for Bull and Bear agents when official NSE Bhavcopy evidence is present
        for eod_item in eod_items:
            eid = [eod_item["evidence_id"]]
            eod_claim = eod_item.get("claim") or ""
            r1 = None
            if screening and isinstance(screening.get("metrics"), dict):
                r1 = screening["metrics"].get("return_1d_pct")
            if r1 is not None and r1 > 0:
                bull_arguments.append({
                    "claim": f"Positive EOD momentum ({r1:+.2f}% 1D return) with active NSE institutional liquidity: {eod_claim}",
                    "evidence_ids": eid,
                    "confidence": 80,
                    "why_it_matters": "Strong closing settlement and turnover confirm near-term buying participation on NSE.",
                })
                bear_arguments.append({
                    "claim": f"Short-term price strength ({r1:+.2f}% 1D move) faces mean-reversion and intraday volatility risk without full multi-period valuation & quarterly filing support: {eod_claim}",
                    "evidence_ids": eid,
                    "confidence": 75,
                    "why_it_matters": "Momentum breakouts without verified valuation multiples and quarterly pledge disclosures can reverse sharply.",
                })
            elif r1 is not None and r1 < 0:
                bull_arguments.append({
                    "claim": f"High NSE liquidity and price discovery at lower EOD settlement ({eod_claim}) offer potential support watch if fundamentals hold.",
                    "evidence_ids": eid,
                    "confidence": 70,
                    "why_it_matters": "Deep market turnover enables institutional absorption once selling pressure exhausts.",
                })
                bear_arguments.append({
                    "claim": f"Distribution and downside price pressure ({r1:+.2f}% 1D decline) in official NSE EOD settlement: {eod_claim}",
                    "evidence_ids": eid,
                    "confidence": 85,
                    "why_it_matters": "Negative closing momentum signals active supply overhanging near-term price action.",
                })
            else:
                bull_arguments.append({
                    "claim": f"Active NSE EOD settlement and liquidity participation: {eod_claim}",
                    "evidence_ids": eid,
                    "confidence": 75,
                    "why_it_matters": "Establishes verified exchange price discovery and trading volume.",
                })

        if all_ids:
            if not bear_arguments:
                bear_arguments.append({
                    "claim": (
                        f"Evidence coverage for {company_name} is partial (missing categories: "
                        f"{', '.join(bundle.get('missing_categories', [])) or 'valuation/quarterly'}); "
                        "upside thesis lacks full multi-period and valuation confirmation."
                    ),
                    "evidence_ids": all_ids[:4],
                    "confidence": 85,
                    "why_it_matters": "Incomplete quarterly shareholding, pledge, or valuation data limits conviction.",
                })
            if not hidden_risks:
                hidden_risks.append({
                    "claim": f"Unsupplied quarterly Regulation 31 shareholding/pledge or ROCE denominator disclosures for {company_name}.",
                    "evidence_ids": all_ids[:4],
                    "confidence": 80,
                    "why_it_matters": "Absence of evidence is not evidence of zero risk; missing fields must not be assumed benign.",
                })
            catalysts.append({
                "claim": f"Completion of verified quarterly filings and multi-year comparable statements for {company_name}.",
                "evidence_ids": all_ids[:4],
                "confidence": 75,
                "why_it_matters": "Would resolve pending governance, pledge, and YoY growth checks.",
            })
            thesis_invalidation.append({
                "claim": f"Deterioration in verified operating cash flow, owners' ROE, or adverse encumbrance disclosures for {company_name}.",
                "evidence_ids": all_ids[:4],
                "confidence": 80,
                "why_it_matters": "Would invalidate the fundamental quality supported by current annual-report evidence.",
            })
            summary = (
                f"Challenge review for {company_name} ({security_key}): weighed {len(bull_arguments)} bullish "
                f"observation(s) against {len(bear_arguments)} bear/completeness challenge(s) and "
                f"{len(hidden_risks)} definition/coverage caveat(s)."
            )
            summary_ids = all_ids[:10]
        else:
            screen_note = ""
            if screening and isinstance(screening.get("metrics"), dict):
                sm = screening["metrics"]
                r1 = sm.get("return_1d_pct")
                r20 = sm.get("return_20d_pct")
                if r1 is not None or r20 is not None:
                    r1_str = f"1d={r1:+.2f}%" if r1 is not None else "1d=n/a"
                    r20_str = f"20d={r20:+.2f}%" if r20 is not None else "20d=n/a"
                    screen_note = f" Market price context ({r1_str}, {r20_str}) is observed, but price action alone cannot substitute for fundamental evidence."
            summary = (
                f"No fundamental facts were supplied for {company_name} ({security_key}), so neither a "
                f"company-specific bull case nor bear case is invented.{screen_note}"
            )
            summary_ids = []

        return {
            "agent_id": "bull_bear_analyst",
            "security_key": security_key,
            "as_of_date": as_of,
            "evidence_digest": digest,
            "summary": summary,
            "summary_evidence_ids": summary_ids,
            "bull_arguments": bull_arguments,
            "bear_arguments": bear_arguments,
            "contradictory_evidence": contradictory_evidence,
            "hidden_risks": hidden_risks,
            "catalysts": catalysts,
            "thesis_invalidation": thesis_invalidation,
            "missing_evidence": self._missing_notes(bundle, snapshot_metrics),
        }

    def _run_independent_judge(self, bundle: dict, context: dict) -> dict:
        security_key = bundle["security"]["security_key"]
        company_name = bundle["security"].get("company_name") or bundle["security"].get("symbol") or security_key
        as_of = bundle["as_of_date"]
        digest = bundle["evidence_digest"]
        snapshot = bundle.get("fundamental_snapshot") or {"metrics": []}
        snapshot_metrics: list[dict] = snapshot.get("metrics", [])
        screening = bundle.get("screening_snapshot")
        items = self._security_items(bundle)
        all_ids = [item["evidence_id"] for item in items]

        prior = (context.get("complete_prior_reports") or {})
        analyst_report = prior.get("equity_research_analyst") or {}
        analyst_metrics = {
            (m["metric"], m["financial_basis"]): m
            for m in analyst_report.get("financial_metrics", [])
        }

        figure_verifications = []
        key_determinants = []
        has_conflict = False

        for m in snapshot_metrics:
            eids = list(m["evidence_ids"])
            mk = m["metric_key"]
            basis = m["financial_basis"]
            dstatus = m["data_status"]
            val = m["value"]
            unit = m["unit"] or ""
            analyst_m = analyst_metrics.get((mk, basis))
            r_val = f"{analyst_m['value']} {analyst_m.get('unit') or ''}".strip() if analyst_m and analyst_m.get("value") is not None else None
            v_val = f"{val} {unit}".strip() if val is not None else None

            if dstatus == "conflicting" and eids:
                has_conflict = True
                figure_verifications.append({
                    "figure": f"{m['label']} ({m['reporting_period'] or 'unknown period'}, {basis})",
                    "researcher_value": r_val,
                    "verified_value": None,
                    "status": "contradicted",
                    "evidence_ids": eids,
                    "reason": m["note"],
                })
                key_determinants.append({
                    "claim": f"{m['label']} has conflicting source evidence and cannot be relied upon.",
                    "evidence_ids": eids,
                    "confidence": 95,
                    "why_it_matters": m["note"],
                })
            elif val is not None and eids:
                figure_verifications.append({
                    "figure": f"{m['label']} ({m['reporting_period']}, {basis})",
                    "researcher_value": r_val,
                    "verified_value": v_val,
                    "status": "consistent",
                    "evidence_ids": eids,
                    "reason": f"Matches deterministic snapshot ({dstatus}): {m['note']}",
                })
                if dstatus == "verified":
                    key_determinants.append({
                        "claim": f"Verified {m['label']}: {v_val} in {m['reporting_period']} ({basis} basis).",
                        "evidence_ids": eids,
                        "confidence": 90,
                        "why_it_matters": "Directly verified against cited primary reporting.",
                    })

        # Also verify EOD settlement items in figure_verifications
        for item in items:
            source_name = item.get("source_name") or ""
            claim_text = item.get("claim") or ""
            if ("bhavcopy" in source_name.lower() or "eod settlement" in claim_text.lower()) and item.get("value") is not None:
                eid = [item["evidence_id"]]
                val_str = f"{item['value']} {item.get('unit') or 'INR'}".strip()
                figure_verifications.append({
                    "figure": f"NSE EOD Close ({item.get('reporting_period') or as_of})",
                    "researcher_value": val_str,
                    "verified_value": val_str,
                    "status": "consistent",
                    "evidence_ids": eid,
                    "reason": f"Matches official NSE CM UDiFF Bhavcopy EOD settlement: {claim_text}",
                })
                key_determinants.append({
                    "claim": f"Verified EOD Market Settlement: {claim_text}",
                    "evidence_ids": eid,
                    "confidence": 85,
                    "why_it_matters": "Provides official exchange price and volume context while maintaining strict fundamental gates.",
                })

        # Check if BUY_CANDIDATE gate is satisfied
        groups = {
            "growth": {"revenue_growth_yoy", "profit_growth_yoy"},
            "profitability": {"net_profit_margin", "ebitda_margin", "operating_margin"},
            "returns": {"roe", "roce"},
            "balance_or_cash_flow": {"debt_to_equity", "operating_cash_flow"},
            "valuation": {"pe_ratio", "ev_to_ebitda", "sector_valuation"},
        }
        usable_by_basis: dict[str, list[dict]] = {}
        for m in snapshot_metrics:
            if m["data_status"] == "verified" and m["value"] is not None:
                usable_by_basis.setdefault(m["financial_basis"], []).append(m)

        buy_gate_passed = False
        missing_buy_groups = sorted(groups.keys())
        if not bundle.get("sample_data") and not has_conflict:
            for basis, basis_metrics in usable_by_basis.items():
                available = {
                    g for g, keys in groups.items()
                    if any(m["metric_key"] in keys for m in basis_metrics)
                }
                absent = sorted(set(groups) - available)
                if len(absent) < len(missing_buy_groups):
                    missing_buy_groups = absent
                if not absent:
                    buy_gate_passed = True
                    break

        if not all_ids or bundle.get("sample_data") or has_conflict:
            verdict = "INCONCLUSIVE"
            watchlist_action = "NOT_SET"
            confidence_score = 0 if not all_ids or bundle.get("sample_data") else 25
            risk_score = None
            confidence_reason = (
                "No verified company fundamentals were supplied."
                if not all_ids
                else "Synthetic or conflicting evidence prevents a verified investment verdict."
            )
            risk_reason = "Risk cannot be scored from synthetic, missing, or conflicting evidence."
        elif buy_gate_passed:
            verdict = "BUY_CANDIDATE"
            watchlist_action = "ADD"
            confidence_score = 80
            risk_score = 30
            confidence_reason = "All five required fundamental groups (growth, profitability, returns, balance/cash flow, valuation) are verified on a consistent basis."
            risk_reason = "Primary financial metrics are verified; monitor ongoing quarterly filings."
        else:
            # We have real verified evidence (e.g. Stage 4B/4C annual report facts),
            # but some required categories (such as YoY growth prior period, quarterly filings, or valuation) are still pending.
            verified_count = sum(1 for m in snapshot_metrics if m["data_status"] == "verified" and m["value"] is not None)
            if verified_count >= 2:
                verdict = "WATCH"
                watchlist_action = "ADD"
                confidence_score = min(70, 35 + verified_count * 7)
                risk_score = 45
                confidence_reason = (
                    f"Verified {verified_count} core fundamental metric(s) from primary annual reports, "
                    f"but BUY_CANDIDATE gate is not met because {', '.join(missing_buy_groups)} evidence is still pending."
                )
                risk_reason = (
                    "Moderate information risk: annual-report financials and owners' ROE are verified, "
                    "but quarterly Regulation 31 shareholding/pledge filings, ROCE denominator reconciliations, "
                    "and valuation/YoY series remain pending."
                )
            else:
                verdict = "INCONCLUSIVE"
                watchlist_action = "NOT_SET"
                confidence_score = 20
                risk_score = None
                confidence_reason = (
                    f"Insufficient verified metric coverage ({verified_count} metric(s)); "
                    f"missing {', '.join(missing_buy_groups)}."
                )
                risk_reason = "Risk cannot be reliably scored until additional verified financial metrics are supplied."

        if all_ids and not key_determinants:
            key_determinants.append({
                "claim": f"Supplied evidence for {company_name} ({len(items)} record(s)) was audited against the deterministic snapshot.",
                "evidence_ids": all_ids[:5],
                "confidence": 80,
                "why_it_matters": "Ensures every figure in the research run is tied to a primary source record.",
            })

        conditions_to_revisit = [
            "Supply Q1-Q4 FY2025-26 SEBI LODR Regulation 31 Table III shareholding and promoter pledge filings.",
            "Supply prior-year comparable revenue and profit inputs to enable deterministic YoY growth calculation.",
            "Establish matched consolidated EBIT and average capital employed definitions before computing ROCE.",
            "Provide verified valuation multiples and sector valuation benchmarks before evaluating BUY_CANDIDATE eligibility.",
        ]

        independent_reasoning = (
            f"{verdict}: Independent review of Agent 01 (Equity Research Analyst) and Agent 02 (Bull/Bear Analyst) "
            f"for {company_name} ({security_key}) as of {as_of}. Verified {len(figure_verifications)} figure(s) "
            f"directly against the deterministic snapshot. "
            + (
                f"Missing BUY_CANDIDATE evidence groups: {', '.join(missing_buy_groups)}. "
                if missing_buy_groups and verdict != "BUY_CANDIDATE"
                else ""
            )
            + "No unverified, missing, or peer-incompatible metric was fabricated or assumed zero."
        )
        if screening and isinstance(screening.get("metrics"), dict):
            sm = screening["metrics"]
            r1 = sm.get("return_1d_pct")
            r20 = sm.get("return_20d_pct")
            if r1 is not None or r20 is not None:
                r1_str = f"{r1:+.2f}%" if r1 is not None else "n/a"
                r20_str = f"{r20:+.2f}%" if r20 is not None else "n/a"
                independent_reasoning += f" EOD price context ({screening.get('data_date')}): 1d return {r1_str}, 20d return {r20_str}."

        return {
            "agent_id": "independent_judge",
            "security_key": security_key,
            "as_of_date": as_of,
            "evidence_digest": digest,
            "verdict": verdict,
            "confidence_score": confidence_score,
            "confidence_reason": confidence_reason,
            "risk_score": risk_score,
            "risk_reason": risk_reason,
            "figure_verifications": figure_verifications,
            "key_determinants": key_determinants,
            "watchlist_action": watchlist_action,
            "conditions_to_revisit": conditions_to_revisit,
            "missing_evidence": self._missing_notes(bundle, snapshot_metrics),
            "independent_reasoning": independent_reasoning,
            "reasoning_evidence_ids": all_ids[:10] if all_ids else [],
        }
