from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import Settings, get_settings
from .fundamental_catalog import FINANCIAL_BASES, FUNDAMENTAL_FACT_KEYS
from .storage import connect_read_only, connect_read_write, json_text

logger = logging.getLogger(__name__)

SOURCE_TYPES = {
    "quarterly_results", "annual_report", "corporate_announcement",
    "rbi_economic_indicator", "financial_news", "sector_source", "other",
}
REQUIRED_CATEGORIES = (
    "quarterly_results", "annual_report", "corporate_announcement",
    "rbi_economic_indicator", "financial_news",
)


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    security_key: str | None
    source_type: str
    source_name: str
    source_url: str
    publication_date: str
    reporting_period: str
    retrieved_at: str
    claim: str
    value: object
    unit: str | None
    excerpt: str | None
    source_sha256: str
    source_file: str
    is_synthetic: bool
    metric_key: str | None = None
    verification_status: str = "unverified"
    financial_basis: str = "unspecified"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceBundle:
    security: dict
    as_of_date: str
    screening_snapshot: dict | None
    evidence_items: tuple[EvidenceItem, ...]
    quality_flags: tuple[dict, ...]
    missing_categories: tuple[str, ...]
    sample_data: bool

    def payload(self) -> dict:
        return {
            "security": self.security,
            "as_of_date": self.as_of_date,
            "screening_snapshot": self.screening_snapshot,
            "evidence_items": [item.to_dict() for item in self.evidence_items],
            "quality_flags": list(self.quality_flags),
            "missing_categories": list(self.missing_categories),
            "sample_data": self.sample_data,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def citable_ids(self) -> set[str]:
        return {item.evidence_id for item in self.evidence_items}


class EvidenceStore:
    """Imports cited company or macro facts only when required provenance is present."""

    def __init__(self, database_path=None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.database_path = Path(database_path or self.settings.database_path)

    def import_json_file(self, path: Path | str) -> dict:
        source_path = Path(path)
        return self.import_json_bytes(source_path.name, source_path.read_bytes())

    def import_json_bytes(self, filename: str, payload: bytes) -> dict:
        try:
            document = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("Evidence file must be UTF-8 JSON") from exc
        records = document.get("records") if isinstance(document, dict) else None
        if not isinstance(records, list) or not records:
            raise EvidenceError("Evidence JSON must contain a non-empty 'records' array")
        safe_name = Path(filename).name
        file_hash = hashlib.sha256(payload).hexdigest()
        self.settings.financial_raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = self.settings.financial_raw_dir / f"{file_hash}_{safe_name}"
        if not raw_path.exists():
            raw_path.write_bytes(payload)
        imported_at = datetime.now(timezone.utc).isoformat()
        prepared = []
        with connect_read_write(self.database_path) as conn:
            for row_number, row in enumerate(records, start=1):
                normalized = self._validate_record(row, row_number)
                security_key = normalized["security_key"]
                if security_key and conn.execute("SELECT 1 FROM securities WHERE security_key=?", (security_key,)).fetchone() is None:
                    raise EvidenceError(f"Record {row_number}: unknown security_key {security_key!r}")
                evidence_id = hashlib.sha256(f"{file_hash}:{row_number}".encode("utf-8")).hexdigest()[:32]
                normalized.update({
                    "evidence_id": evidence_id,
                    "source_sha256": file_hash,
                    "source_file": safe_name,
                    "is_synthetic": bool(normalized.get("is_synthetic", False)
                                           or "sample" in safe_name.lower()
                                           or "synthetic" in safe_name.lower()
                                           or "sample" in normalized["source_name"].lower()
                                           or "synthetic" in normalized["source_name"].lower()),
                    "created_at": imported_at,
                })
                prepared.append(normalized)
            inserted = 0
            for row in prepared:
                stored_value = row.get("value")
                if (row.get("metric_key") or row.get("verification_status") != "unverified"
                        or row.get("financial_basis") != "unspecified"):
                    stored_value = {
                        "_evidence_record_version": 2,
                        "value": stored_value,
                        "metric_key": row.get("metric_key"),
                        "verification_status": row.get("verification_status", "unverified"),
                        "financial_basis": row.get("financial_basis", "unspecified"),
                    }
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO evidence_items
                       (evidence_id, security_key, source_type, source_name, source_url, publication_date,
                        reporting_period, retrieved_at, claim, value_json, unit, excerpt, source_sha256,
                        source_file, is_synthetic, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["evidence_id"], row["security_key"], row["source_type"], row["source_name"],
                     row["source_url"], row["publication_date"], row["reporting_period"], row["retrieved_at"],
                     row["claim"], json_text(stored_value), row.get("unit"), row.get("excerpt"),
                     row["source_sha256"], row["source_file"], int(row["is_synthetic"]), row["created_at"]),
                )
                inserted += cursor.rowcount
            conn.commit()
        logger.info("Imported %d sourced evidence records from %s", inserted, safe_name)
        return {"source_file": safe_name, "source_sha256": file_hash, "raw_file_path": str(raw_path),
                "records_read": len(prepared), "records_inserted": inserted, "records_already_present": len(prepared) - inserted}

    @staticmethod
    def _validate_record(row: object, row_number: int) -> dict:
        if not isinstance(row, dict):
            raise EvidenceError(f"Record {row_number} must be an object")
        required = ("source_type", "source_name", "source_url", "publication_date", "reporting_period", "retrieved_at", "claim")
        missing = [key for key in required if not isinstance(row.get(key), str) or not row[key].strip()]
        if missing:
            raise EvidenceError(f"Record {row_number} is missing required provenance/claim field(s): {', '.join(missing)}")
        source_type = row["source_type"].strip()
        if source_type not in SOURCE_TYPES:
            raise EvidenceError(f"Record {row_number} has unsupported source_type {source_type!r}")
        parsed_url = urlsplit(row["source_url"].strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise EvidenceError(f"Record {row_number} source_url must be an http(s) URL")
        try:
            publication_date = date.fromisoformat(row["publication_date"].strip()).isoformat()
        except ValueError as exc:
            raise EvidenceError(f"Record {row_number} publication_date must be YYYY-MM-DD") from exc
        try:
            retrieval = datetime.fromisoformat(row["retrieved_at"].strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise EvidenceError(f"Record {row_number} retrieved_at must be an ISO timestamp") from exc
        if retrieval.tzinfo is None:
            raise EvidenceError(f"Record {row_number} retrieved_at must include a timezone")
        security_key = row.get("security_key")
        if security_key is not None and (not isinstance(security_key, str) or not security_key.strip()):
            raise EvidenceError(f"Record {row_number} security_key must be a non-empty string or null")
        excerpt = row.get("excerpt")
        if excerpt is not None and not isinstance(excerpt, str):
            raise EvidenceError(f"Record {row_number} excerpt must be a string or null")
        unit = row.get("unit")
        if unit is not None and not isinstance(unit, str):
            raise EvidenceError(f"Record {row_number} unit must be a string or null")
        if "is_synthetic" in row and not isinstance(row["is_synthetic"], bool):
            raise EvidenceError(f"Record {row_number} is_synthetic must be boolean")
        metric_key = row.get("metric_key")
        if metric_key is not None and (not isinstance(metric_key, str) or metric_key not in FUNDAMENTAL_FACT_KEYS):
            allowed = ", ".join(sorted(FUNDAMENTAL_FACT_KEYS))
            raise EvidenceError(f"Record {row_number} metric_key must be one of: {allowed}")
        verification_status = row.get("verification_status", "unverified")
        if verification_status not in {"verified", "unverified"}:
            raise EvidenceError(f"Record {row_number} verification_status must be 'verified' or 'unverified'")
        raw_financial_basis = row.get("financial_basis", "unspecified")
        financial_basis = raw_financial_basis.strip().lower() if isinstance(raw_financial_basis, str) else None
        if financial_basis not in FINANCIAL_BASES:
            raise EvidenceError(f"Record {row_number} financial_basis must be one of: {', '.join(FINANCIAL_BASES)}")
        return {
            "security_key": security_key,
            "source_type": source_type,
            "source_name": row["source_name"].strip(),
            "source_url": row["source_url"].strip(),
            "publication_date": publication_date,
            "reporting_period": row["reporting_period"].strip(),
            "retrieved_at": retrieval.astimezone(timezone.utc).isoformat(),
            "claim": row["claim"].strip(),
            "value": row.get("value"),
            "unit": unit,
            "excerpt": excerpt,
            "is_synthetic": row.get("is_synthetic", False),
            "metric_key": metric_key,
            "verification_status": verification_status,
            "financial_basis": financial_basis,
        }


class EvidenceRepository:
    def __init__(self, database_path=None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.database_path = Path(database_path or self.settings.database_path)

    def build_bundle(self, security_key: str, as_of_date: str | None = None) -> EvidenceBundle:
        with connect_read_only(self.database_path) as conn:
            return self.build_bundle_from_connection(conn, security_key, as_of_date)

    def build_bundle_from_connection(self, conn: sqlite3.Connection, security_key: str,
                                     as_of_date: str | None = None) -> EvidenceBundle:
        """Build a bundle from an existing connection without changing its mode."""
        security_row = conn.execute("SELECT * FROM securities WHERE security_key=?", (security_key,)).fetchone()
        if security_row is None:
            raise EvidenceError(f"Security not found: {security_key}")
        if as_of_date is None:
            latest = conn.execute("SELECT MAX(price_date) d FROM daily_prices WHERE close IS NOT NULL").fetchone()["d"]
            as_of_date = latest or date.today().isoformat()
        try:
            as_of_date = date.fromisoformat(as_of_date).isoformat()
        except ValueError as exc:
            raise EvidenceError("as_of_date must be YYYY-MM-DD") from exc
        screen = conn.execute(
            "SELECT data_date, metrics_json FROM screening_results WHERE security_key=? AND data_date<=? ORDER BY data_date DESC LIMIT 1",
            (security_key, as_of_date),
        ).fetchone()
        screening_snapshot = None
        if screen:
            screening_snapshot = {"data_date": screen["data_date"], "metrics": json.loads(screen["metrics_json"]),
                                  "claimable_as_fundamental_evidence": False}
        rows = conn.execute(
            """SELECT * FROM evidence_items WHERE (security_key=? OR security_key IS NULL)
               AND publication_date<=? AND substr(retrieved_at,1,10)<=?
               ORDER BY publication_date DESC, evidence_id LIMIT 1000""",
            (security_key, as_of_date, as_of_date)
        ).fetchall()
        parsed_items = []
        for row in rows:
            stored_value = json.loads(row["value_json"]) if row["value_json"] is not None else None
            metric_key = None
            verification_status = "unverified"
            financial_basis = "unspecified"
            if isinstance(stored_value, dict) and stored_value.get("_evidence_record_version") in {1, 2}:
                metric_key = stored_value.get("metric_key")
                verification_status = stored_value.get("verification_status", "unverified")
                financial_basis = stored_value.get("financial_basis", "unspecified")
                stored_value = stored_value.get("value")
            parsed_items.append(EvidenceItem(
                evidence_id=row["evidence_id"], security_key=row["security_key"], source_type=row["source_type"],
                source_name=row["source_name"], source_url=row["source_url"], publication_date=row["publication_date"],
                reporting_period=row["reporting_period"], retrieved_at=row["retrieved_at"], claim=row["claim"],
                value=stored_value, unit=row["unit"], excerpt=row["excerpt"], source_sha256=row["source_sha256"],
                source_file=row["source_file"], is_synthetic=bool(row["is_synthetic"]),
                metric_key=metric_key, verification_status=verification_status, financial_basis=financial_basis,
            ))
        items = tuple(parsed_items)
        present = {item.source_type for item in items}
        missing = tuple(category for category in REQUIRED_CATEGORIES if category not in present)
        checks = conn.execute(
            """SELECT check_type, severity, record_date, details, source_file FROM data_quality_checks
               WHERE security_key=? AND substr(checked_at,1,10)<=?
                 AND (record_date IS NULL OR record_date<=?)
               ORDER BY check_id DESC LIMIT 100""", (security_key, as_of_date, as_of_date)
        ).fetchall()
        sample_source = conn.execute(
            """SELECT 1 FROM source_records WHERE security_key=? AND
               (lower(source_name) LIKE '%sample%' OR lower(source_name) LIKE '%synthetic%') LIMIT 1""", (security_key,)
        ).fetchone()
        security = {key: security_row[key] for key in (
            "security_key", "exchange", "symbol", "company_name", "security_identifier",
            "security_type", "listing_status", "source_timestamp",
        )}
        if security["source_timestamp"] and str(security["source_timestamp"])[:10] > as_of_date:
            security["source_timestamp"] = None
        return EvidenceBundle(
            security=security,
            as_of_date=as_of_date,
            screening_snapshot=screening_snapshot,
            evidence_items=items,
            quality_flags=tuple(dict(row) for row in checks),
            missing_categories=missing,
            sample_data=bool(sample_source) or any(item.is_synthetic for item in items),
        )
