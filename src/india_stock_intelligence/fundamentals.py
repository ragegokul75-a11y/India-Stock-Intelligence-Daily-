from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .evidence import EvidenceBundle, EvidenceItem
from .fundamental_catalog import FUNDAMENTAL_METRICS


@dataclass(frozen=True)
class FundamentalMetric:
    metric_key: str
    label: str
    value: object | None
    unit: str | None
    reporting_period: str | None
    financial_basis: str
    data_status: str
    evidence_ids: tuple[str, ...]
    sources: tuple[dict, ...]
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FundamentalSnapshot:
    security_key: str
    as_of_date: str
    sample_data: bool
    metrics: tuple[FundamentalMetric, ...]

    def to_dict(self) -> dict:
        return {
            "security_key": self.security_key,
            "as_of_date": self.as_of_date,
            "sample_data": self.sample_data,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }

    def rows(self) -> list[dict]:
        return [metric.to_dict() | {"security_key": self.security_key, "as_of_date": self.as_of_date,
                                   "sample_data": self.sample_data}
                for metric in self.metrics]


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _unit_key(unit: str | None) -> str:
    normalized = re.sub(r"\s+", " ", (unit or "").strip().lower().replace("₹", "inr"))
    return {"percent": "%", "percentage": "%", "ratio": "x"}.get(normalized, normalized)


def _value_key(value: object) -> tuple:
    number = _number(value)
    if number is not None:
        return ("number", number.normalize())
    if isinstance(value, str):
        return ("text", re.sub(r"\s+", " ", value.strip()).casefold())
    try:
        stable = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        stable = repr(value)
    return (type(value).__name__, stable)


def _units_match(left: str | None, right: str | None) -> bool:
    left_key, right_key = _unit_key(left), _unit_key(right)
    return bool(left_key and right_key and left_key == right_key)


def _period_key(period: str, publication_date: str) -> tuple:
    text = period.upper().replace("-", " ")
    fy = re.search(r"\bFY\s*(20\d{2})\b", text)
    quarter = re.search(r"\bQ([1-4])\b", text)
    if fy:
        return (int(fy.group(1)), int(quarter.group(1)) if quarter else 5, publication_date, period)
    return (0, 0, publication_date, period)


def _period_identity(period: str) -> tuple:
    text = re.sub(r"\s+", " ", period.upper().replace("-", " ").strip())
    fy = re.search(r"\bFY\s*(20\d{2})\b", text)
    quarter = re.search(r"\bQ([1-4])\b", text)
    if fy:
        return ("FY", int(fy.group(1)), int(quarter.group(1)) if quarter else 5)
    return ("RAW", text)


def _fiscal_period(period: str) -> tuple[int, int | None] | None:
    text = period.upper().replace("-", " ")
    year = re.search(r"\bFY\s*(20\d{2})\b", text)
    quarter = re.search(r"\bQ([1-4])\b", text)
    if not year:
        return None
    return int(year.group(1)), int(quarter.group(1)) if quarter else None


def _result_period_key(result: dict) -> tuple:
    items = result.get("items", ())
    publication_date = max((item.publication_date for item in items), default="")
    return _period_key(result.get("period") or "", publication_date)


def _source_ref(item: EvidenceItem) -> dict:
    return {
        "evidence_id": item.evidence_id,
        "source_name": item.source_name,
        "source_url": item.source_url,
        "publication_date": item.publication_date,
        "reporting_period": item.reporting_period,
        "retrieved_at": item.retrieved_at,
        "verification_status": item.verification_status,
        "financial_basis": item.financial_basis,
        "is_synthetic": item.is_synthetic,
    }


def _status(items: Iterable[EvidenceItem]) -> str:
    facts = tuple(items)
    if any(item.is_synthetic for item in facts):
        return "synthetic"
    if facts and all(item.verification_status == "verified" for item in facts):
        return "verified"
    return "unverified"


class FundamentalAnalyzer:
    """Derive only comparable, explicitly keyed facts from the evidence bundle."""

    def analyze(self, bundle: EvidenceBundle) -> FundamentalSnapshot:
        facts_by_basis: dict[str, dict[str, list[EvidenceItem]]] = {}
        for item in bundle.evidence_items:
            if item.metric_key and item.security_key == bundle.security["security_key"]:
                facts_by_basis.setdefault(item.financial_basis, {}).setdefault(item.metric_key, []).append(item)

        metrics = []
        bases = sorted(facts_by_basis) or ["unspecified"]
        for basis in bases:
            facts = facts_by_basis.get(basis, {})
            for key, label in FUNDAMENTAL_METRICS.items():
                direct_items = facts.get(key, [])
                direct = self._latest_value(direct_items) if direct_items else None
                derived = self._derive(key, facts)
                if (direct and derived
                        and _period_identity(direct.get("period") or "") != _period_identity(derived.get("period") or "")):
                    preferred = max((direct, derived), key=_result_period_key)
                    direct = preferred if preferred is direct else None
                    derived = preferred if preferred is derived else None
                if direct and direct.get("conflicting"):
                    metrics.append(self._metric(key, label, None, direct.get("unit"), direct.get("period"), basis,
                                                "conflicting", direct["items"], direct["note"]))
                    continue
                if derived and derived.get("conflicting"):
                    items = tuple({item.evidence_id: item for item in
                                   (direct["items"] if direct else ()) + derived["items"]}.values())
                    metrics.append(self._metric(key, label, None, derived.get("unit"), derived.get("period"), basis,
                                                "conflicting", items, derived["note"]))
                    continue
                if direct and derived and direct["value"] is not None and derived["value"] is not None:
                    direct_number = _number(direct["value"])
                    derived_number = _number(derived["value"])
                    if (direct_number is None or derived_number is None
                            or _unit_key(direct.get("unit")) != _unit_key(derived.get("unit"))
                            or abs(direct_number - derived_number) > Decimal("0.01")):
                        items = tuple({item.evidence_id: item for item in direct["items"] + derived["items"]}.values())
                        metrics.append(self._metric(key, label, None, direct.get("unit"),
                                                    direct.get("period") or derived.get("period"), basis,
                                                    "conflicting", items,
                                                    "Reported value conflicts with the deterministic calculation from its cited components."))
                        continue
                chosen = direct if direct and direct.get("value") is not None else derived or direct
                if chosen is None or chosen.get("value") is None:
                    evidence_items = chosen.get("items", ()) if chosen else ()
                    status = "conflicting" if chosen and chosen.get("conflicting") else "missing"
                    metrics.append(self._metric(key, label, None, chosen.get("unit") if chosen else None,
                                                chosen.get("period") if chosen else None, basis, status, evidence_items,
                                                chosen.get("note") if chosen else "No metric-keyed evidence was supplied."))
                    continue
                metrics.append(self._metric(key, label, chosen["value"], chosen.get("unit"), chosen.get("period"), basis,
                                            _status(chosen["items"]), chosen["items"], chosen.get("note", "Reported by source.")))

        return FundamentalSnapshot(bundle.security["security_key"], bundle.as_of_date,
                                   bundle.sample_data, tuple(metrics))

    @staticmethod
    def _metric(key, label, value, unit, period, financial_basis, status, items, note) -> FundamentalMetric:
        unique = {item.evidence_id: item for item in items}
        ordered = tuple(sorted(unique.values(), key=lambda item: (item.publication_date, item.evidence_id)))
        return FundamentalMetric(
            metric_key=key,
            label=label,
            value=float(value) if isinstance(value, Decimal) else value,
            unit=unit,
            reporting_period=period,
            financial_basis=financial_basis,
            data_status=status,
            evidence_ids=tuple(item.evidence_id for item in ordered),
            sources=tuple(_source_ref(item) for item in ordered),
            note=note,
        )

    def _latest_value(self, items: list[EvidenceItem]) -> dict | None:
        if not items:
            return None
        by_period: dict[str, list[EvidenceItem]] = {}
        for item in items:
            by_period.setdefault(item.reporting_period, []).append(item)
        period = max(by_period, key=lambda p: _period_key(p, max(i.publication_date for i in by_period[p])))
        return self._period_value(by_period[period], period)

    @staticmethod
    def _period_value(items: list[EvidenceItem], period: str) -> dict:
        present = [item for item in items if item.value is not None]
        if not present:
            return {"value": None, "unit": items[0].unit if items else None, "period": period,
                    "items": tuple(items), "note": "The source record has no value for this metric."}
        fingerprints = {(_value_key(item.value), _unit_key(item.unit)) for item in present}
        if len(fingerprints) > 1:
            return {"value": None, "unit": present[0].unit, "period": period, "items": tuple(present),
                    "conflicting": True, "note": "Sources disagree for this metric and reporting period."}
        number = _number(present[0].value)
        value = number if number is not None else present[0].value
        return {"value": value, "unit": present[0].unit, "period": period,
                "items": tuple(present), "note": "Reported by source; no independent source verification is implied."}

    def _derive(self, key: str, facts: dict[str, list[EvidenceItem]]) -> dict | None:
        if key in {"revenue_growth_yoy", "profit_growth_yoy", "eps_growth_yoy"}:
            source_key = {"revenue_growth_yoy": "revenue", "profit_growth_yoy": "net_profit",
                          "eps_growth_yoy": "eps"}[key]
            periods = self._periods(facts.get(source_key, []))
            fiscal = [(_fiscal_period(period), period) for period in periods]
            fiscal = [(period_id, period) for period_id, period in fiscal if period_id is not None]
            fiscal.sort(key=lambda row: (row[0][0], row[0][1] or 5, _period_key(row[1], "")), reverse=True)
            if not fiscal:
                return None
            (year, quarter), current_period = fiscal[0]
            prior_period = next((period for (prior_year, prior_quarter), period in fiscal
                                 if prior_year == year - 1 and prior_quarter == quarter), None)
            if prior_period is None:
                interval = "same quarter in the prior fiscal year" if quarter is not None else "directly preceding fiscal year"
                return {"value": None, "unit": "%", "period": current_period,
                        "items": tuple(item for item in facts.get(source_key, []) if item.reporting_period == current_period),
                        "note": f"A {interval} value is required to calculate year-over-year growth."}
            current = self._period_value(self._at_period(facts.get(source_key, []), current_period), current_period)
            prior = self._period_value(self._at_period(facts.get(source_key, []), prior_period), prior_period)
            inputs = current["items"] + prior["items"]
            if current["value"] is None or prior["value"] is None:
                return {"value": None, "unit": "%", "period": current_period, "items": inputs,
                        "conflicting": bool(current.get("conflicting") or prior.get("conflicting")),
                        "note": "Growth cannot be calculated because a fiscal-year value is missing or conflicting."}
            if not _units_match(current.get("unit"), prior.get("unit")):
                return {"value": None, "unit": "%", "period": current_period, "items": inputs,
                        "note": "Growth cannot be calculated because source units are missing or differ."}
            current_value, prior_value = _number(current["value"]), _number(prior["value"])
            if current_value is None or prior_value is None or prior_value <= 0:
                return {"value": None, "unit": "%", "period": current_period, "items": inputs,
                        "note": "Growth is not calculated from non-numeric values or a zero/negative base period."}
            return {"value": (current_value / prior_value - 1) * 100, "unit": "%", "period": current_period,
                    "items": inputs, "note": f"Calculated as ({current_period} / {prior_period} - 1) x 100 from sourced values."}

        formulas = {
            "operating_margin": ("operating_profit", "revenue", "percent"),
            "ebitda_margin": ("ebitda", "revenue", "percent"),
            "net_profit_margin": ("net_profit", "revenue", "percent"),
            "debt_to_equity": ("total_debt", "shareholders_equity", "ratio"),
            "roe": ("net_profit", "average_shareholders_equity", "percent"),
            "roce": ("ebit", "average_capital_employed", "percent"),
        }
        if key in formulas:
            numerator_key, denominator_key, output_kind = formulas[key]
            shared = self._latest_shared_period(facts.get(numerator_key, []), facts.get(denominator_key, []))
            if not shared:
                return None
            period, numerator_items, denominator_items = shared
            numerator = self._period_value(numerator_items, period)
            denominator = self._period_value(denominator_items, period)
            items = numerator["items"] + denominator["items"]
            unit = "%" if output_kind == "percent" else "x"
            if numerator["value"] is None or denominator["value"] is None:
                return {"value": None, "unit": unit, "period": period, "items": items,
                        "conflicting": bool(numerator.get("conflicting") or denominator.get("conflicting")),
                        "note": "Calculation cannot be completed because a same-period source value is missing or conflicting."}
            if not _units_match(numerator.get("unit"), denominator.get("unit")):
                return {"value": None, "unit": unit, "period": period, "items": items,
                        "note": "Calculation cannot be completed because source units are missing or differ."}
            top, bottom = _number(numerator["value"]), _number(denominator["value"])
            if top is None or bottom is None or bottom <= 0:
                return {"value": None, "unit": unit, "period": period, "items": items,
                        "note": "Calculation requires numeric values and a positive denominator."}
            value = top / bottom * 100 if output_kind == "percent" else top / bottom
            return {"value": value, "unit": unit, "period": period, "items": items,
                    "note": f"Calculated from same-period {numerator_key} and {denominator_key} source values."
                            + (" ROE uses explicitly reported average shareholders' equity." if key == "roe" else "")
                            + (" ROCE uses explicitly reported EBIT and average capital employed." if key == "roce" else "")}

        if key == "free_cash_flow":
            shared = self._latest_shared_period(facts.get("operating_cash_flow", []), facts.get("capital_expenditure", []))
            if not shared:
                return None
            period, ocf_items, capex_items = shared
            ocf = self._period_value(ocf_items, period)
            capex = self._period_value(capex_items, period)
            items = ocf["items"] + capex["items"]
            if ocf["value"] is None or capex["value"] is None:
                return {"value": None, "unit": ocf.get("unit"), "period": period, "items": items,
                        "conflicting": bool(ocf.get("conflicting") or capex.get("conflicting")),
                        "note": "Free cash flow requires same-period operating cash flow and capital expenditure."}
            if not _units_match(ocf.get("unit"), capex.get("unit")):
                return {"value": None, "unit": ocf.get("unit"), "period": period, "items": items,
                        "note": "Free cash flow cannot be calculated because source units are missing or differ."}
            ocf_value, capex_value = _number(ocf["value"]), _number(capex["value"])
            if ocf_value is None or capex_value is None or capex_value < 0:
                return {"value": None, "unit": ocf.get("unit"), "period": period, "items": items,
                        "note": "Capital expenditure must be supplied as a non-negative outflow; sign is not inferred."}
            return {"value": ocf_value - capex_value, "unit": ocf.get("unit"), "period": period, "items": items,
                    "note": "Calculated as operating cash flow minus positive capital-expenditure outflow."}
        return None

    @staticmethod
    def _at_period(items: list[EvidenceItem], period: str) -> list[EvidenceItem]:
        return [item for item in items if item.reporting_period == period]

    @staticmethod
    def _periods(items: list[EvidenceItem]) -> set[str]:
        return {item.reporting_period for item in items}

    @staticmethod
    def _latest_shared_period(left: list[EvidenceItem], right: list[EvidenceItem]) -> tuple | None:
        left_periods = {item.reporting_period for item in left}
        right_periods = {item.reporting_period for item in right}
        common = left_periods & right_periods
        if not common:
            return None
        period = max(common, key=lambda p: _period_key(p, max(
            item.publication_date for item in left + right if item.reporting_period == p)))
        return period, [item for item in left if item.reporting_period == period], [item for item in right if item.reporting_period == period]
