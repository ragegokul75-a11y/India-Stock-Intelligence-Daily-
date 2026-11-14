from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import Settings, get_settings
from .storage import connect_read_write, json_text


class StockMasterError(ValueError):
    pass


LISTING_SEGMENTS = {"MAINBOARD", "SME", "UNKNOWN"}


def normalize_listing_segment(value: object) -> str:
    text = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()
    if text in {"SME", "SME SEGMENT", "NSE SME", "NSE EMERGE", "EMERGE", "SME PLATFORM", "EMERGE SME"}:
        return "SME"
    if text in {"MAINBOARD", "MAIN BOARD", "MAIN MARKET"}:
        return "MAINBOARD"
    return "UNKNOWN"


def security_key_for(exchange: str, symbol: str, isin: str | None) -> str:
    return f"{exchange}:id:{isin}" if isin else f"{exchange}:symbol:{symbol}"


class SecurityMasterStore:
    """Imports a user-supplied NSE listing master without creating price rows."""

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
            raise StockMasterError("Stock master file must be UTF-8 JSON") from exc
        records = document.get("records") if isinstance(document, dict) else None
        source = document.get("source") if isinstance(document, dict) else None
        if not isinstance(records, list) or not records:
            raise StockMasterError("Stock master JSON must contain a non-empty 'records' array")
        normalized_source = self._validate_source(source)
        prepared = [self._validate_record(row, number, normalized_source)
                    for number, row in enumerate(records, start=1)]
        safe_name = Path(filename).name
        source_hash = hashlib.sha256(payload).hexdigest()
        retrieved_at = normalized_source["retrieved_at"]
        imported_at = datetime.now(timezone.utc).isoformat()
        self.settings.security_master_raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = self.settings.security_master_raw_dir / f"{source_hash}_{safe_name}"
        if not raw_path.exists():
            raw_path.write_bytes(payload)

        with connect_read_write(self.database_path) as conn:
            inserted = 0
            for row_number, record in enumerate(prepared, start=1):
                conn.execute(
                    """INSERT INTO securities
                       (security_key, exchange, symbol, company_name, security_identifier, security_type,
                        listing_status, source_timestamp, created_at, updated_at)
                       VALUES (?, 'NSE', ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(security_key) DO UPDATE SET
                         symbol=excluded.symbol,
                         company_name=COALESCE(excluded.company_name,securities.company_name),
                         security_identifier=COALESCE(excluded.security_identifier,securities.security_identifier),
                         security_type=CASE WHEN excluded.security_type='UNKNOWN'
                                            THEN securities.security_type ELSE excluded.security_type END,
                         listing_status=CASE WHEN excluded.listing_status='UNKNOWN'
                                             THEN securities.listing_status ELSE excluded.listing_status END,
                         source_timestamp=excluded.source_timestamp,
                         updated_at=excluded.updated_at""",
                    (record["security_key"], record["symbol"], record["company_name"], record["isin"],
                     record["listing_segment"], record["security_status"], retrieved_at, imported_at, imported_at),
                )
                raw_record = {
                    **record,
                    "source_name": normalized_source["source_name"],
                    "source_url": normalized_source["source_url"],
                    "publication_date": normalized_source["publication_date"],
                    "retrieved_at": retrieved_at,
                    "source_file": safe_name,
                    "source_sha256": source_hash,
                }
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO source_records
                       (source_name,source_file,source_sha256,source_row_number,security_key,record_date,
                        raw_record_json,imported_at,status)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (normalized_source["source_name"], safe_name, source_hash, row_number,
                     record["security_key"], record["effective_date"] or normalized_source["publication_date"],
                     json_text(raw_record), imported_at, "imported"),
                )
                inserted += cursor.rowcount
            conn.commit()
        return {
            "source_file": safe_name,
            "source_sha256": source_hash,
            "raw_file_path": str(raw_path),
            "records_read": len(prepared),
            "source_records_inserted": inserted,
            "source_records_already_present": len(prepared) - inserted,
            "security_keys": [record["security_key"] for record in prepared],
            "price_records_written": 0,
        }

    @staticmethod
    def _validate_source(source: object) -> dict:
        if not isinstance(source, dict):
            raise StockMasterError("Stock master JSON requires a 'source' object")
        for field in ("source_name", "source_url", "publication_date", "retrieved_at"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise StockMasterError(f"Stock master source is missing {field}")
        parsed_url = urlsplit(source["source_url"].strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise StockMasterError("source.source_url must be an http(s) URL")
        try:
            published = date.fromisoformat(source["publication_date"].strip()).isoformat()
            retrieved = datetime.fromisoformat(source["retrieved_at"].strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise StockMasterError("Source publication_date and retrieved_at must use ISO date/time formats") from exc
        if retrieved.tzinfo is None:
            raise StockMasterError("source.retrieved_at must include a timezone")
        return {"source_name": source["source_name"].strip(), "source_url": source["source_url"].strip(),
                "publication_date": published, "retrieved_at": retrieved.astimezone(timezone.utc).isoformat()}

    @staticmethod
    def _validate_record(row: object, row_number: int, source: dict) -> dict:
        if not isinstance(row, dict):
            raise StockMasterError(f"Record {row_number} must be an object")
        exchange = str(row.get("exchange", "NSE")).strip().upper()
        if exchange != "NSE":
            raise StockMasterError(f"Record {row_number}: only NSE listings are accepted by this stock master")
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol or len(symbol) > 32:
            raise StockMasterError(f"Record {row_number}: symbol is required and must be at most 32 characters")
        company_name = row.get("company_name")
        if company_name is not None and not isinstance(company_name, str):
            raise StockMasterError(f"Record {row_number}: company_name must be a string or null")
        isin = row.get("isin")
        if isin is not None:
            if not isinstance(isin, str):
                raise StockMasterError(f"Record {row_number}: isin must be a string or null")
            isin = isin.strip().upper()
            if isin and not re.fullmatch(r"IN[A-Z0-9]{10}", isin):
                raise StockMasterError(f"Record {row_number}: isin must have 12 characters and begin with IN")
            isin = isin or None
        segment = normalize_listing_segment(row.get("listing_segment"))
        status = str(row.get("security_status", "UNKNOWN")).strip().upper() or "UNKNOWN"
        if len(status) > 32:
            raise StockMasterError(f"Record {row_number}: security_status must be at most 32 characters")
        effective_date = row.get("effective_date")
        if effective_date is not None:
            if not isinstance(effective_date, str):
                raise StockMasterError(f"Record {row_number}: effective_date must be YYYY-MM-DD or null")
            try:
                effective_date = date.fromisoformat(effective_date.strip()).isoformat()
            except ValueError as exc:
                raise StockMasterError(f"Record {row_number}: effective_date must be YYYY-MM-DD") from exc
        return {
            "exchange": "NSE",
            "symbol": symbol,
            "company_name": company_name.strip() if company_name else None,
            "isin": isin,
            "listing_segment": segment,
            "security_status": status,
            "effective_date": effective_date,
            "security_key": security_key_for("NSE", symbol, isin),
            "source_name": source["source_name"],
            "source_url": source["source_url"],
        }


class SecurityMasterRepository:
    def __init__(self, database_path=None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.database_path = Path(database_path or self.settings.database_path)

    def list_securities(self) -> list[dict]:
        from .storage import connect_read_only
        with connect_read_only(self.database_path) as conn:
            return self.rows_from_connection(conn)

    @staticmethod
    def rows_from_connection(conn: sqlite3.Connection) -> list[dict]:
        securities = conn.execute("SELECT * FROM securities ORDER BY exchange,symbol").fetchall()
        rows = []
        for security in securities:
            latest = conn.execute(
                """SELECT source_name,source_file,source_sha256,imported_at,raw_record_json
                   FROM source_records WHERE security_key=? ORDER BY imported_at DESC,source_record_id DESC LIMIT 1""",
                (security["security_key"],),
            ).fetchone()
            raw = {}
            if latest:
                try:
                    raw = json.loads(latest["raw_record_json"])
                except (TypeError, json.JSONDecodeError):
                    raw = {}
            segment = normalize_listing_segment(raw.get("listing_segment") or security["security_type"])
            rows.append({
                "security_key": security["security_key"],
                "exchange": security["exchange"],
                "symbol": security["symbol"],
                "company_name": security["company_name"],
                "isin": security["security_identifier"],
                "listing_segment": segment,
                "security_status": security["listing_status"] or "UNKNOWN",
                "source_timestamp": security["source_timestamp"],
                "source_name": latest["source_name"] if latest else None,
                "source_file": latest["source_file"] if latest else None,
                "source_sha256": latest["source_sha256"] if latest else None,
                "source_url": raw.get("source_url"),
                "source_retrieved_at": raw.get("retrieved_at") or (latest["imported_at"] if latest else None),
            })
        return rows

