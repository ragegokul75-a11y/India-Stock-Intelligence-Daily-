from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import sqlite3
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Settings, get_settings
from .retry import retry_read_only
from .storage import add_quality_check, connect_read_write, json_text

logger = logging.getLogger(__name__)

ALIASES = {
    "exchange": {"exchange", "exch", "market"},
    "symbol": {"symbol", "tradingsymbol", "scripcode", "sc_code", "securitysymbol", "securitycode"},
    "company_name": {"companyname", "securityname", "issuername", "name", "securitydescription"},
    "security_identifier": {"securityid", "isin", "isinno", "securityidentifier", "scripid"},
    "security_type": {"securitytype", "instrumenttype", "series", "category"},
    "listing_status": {"listingstatus", "status", "activestatus"},
    "date": {"date", "pricedate", "tradingdate", "timestamp", "tradedate", "businessdate"},
    "open": {"open", "openprice", "openrate"},
    "high": {"high", "highprice", "highrate"},
    "low": {"low", "lowprice", "lowrate"},
    "close": {"close", "closeprice", "closerate", "lastprice", "ltp"},
    "volume": {"volume", "tradedquantity", "totaltradedquantity", "qty", "sharestraded"},
    "source_timestamp": {"sourcetimestamp", "retrievaltimestamp", "updatedat", "lastupdated"},
    "source_name": {"source", "datasource", "sourcename"},
}


def _header_key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _normalize_row(raw: dict, extra_aliases: dict[str, set[str]] | None = None) -> dict:
    normalized = {}
    by_key = {_header_key(key): value for key, value in raw.items() if key is not None}
    for field, aliases in ALIASES.items():
        for key in aliases | (extra_aliases or {}).get(field, set()):
            if key in by_key:
                normalized[field] = by_key[key]
                break
    return normalized


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"nan", "none", "null", "na", "n/a", "-"} else None


def _parse_date(value) -> str | None:
    value = _clean(value)
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
        if not pd.isna(parsed):
            return parsed.date().isoformat()
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _number(value) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        number = float(value.replace(",", ""))
        return number if math.isfinite(number) else None
    except (ValueError, AttributeError):
        return None


def _read_csv_bytes(data: bytes) -> list[dict]:
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("CSV encoding is not supported")
    sample = decoded[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(decoded), dialect=dialect))


@dataclass
class ImportSummary:
    source_file: str
    raw_file_path: str
    sha256: str
    rows_read: int = 0
    rows_inserted: int = 0
    securities_upserted: int = 0
    duplicate_rows: int = 0
    invalid_rows: int = 0
    rejected_rows: int = 0
    corrected_rows: int = 0
    source_names: list[str] | None = None

    def to_dict(self):
        return asdict(self)


class MarketImporter:
    def __init__(self, database_path: Path | str | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.database_path = Path(database_path or self.settings.database_path)

    def import_file(self, path: Path | str, source_name: str = "Manual file import", expected_exchange: str | None = None,
                    extra_aliases: dict[str, set[str]] | None = None, *, replace_existing: bool = False,
                    correction_reason: str | None = None) -> ImportSummary:
        source_path = Path(path)
        payload = retry_read_only(source_path.read_bytes, attempts=3)
        return self.import_bytes(source_path.name, payload, source_name, expected_exchange, extra_aliases,
                                 replace_existing=replace_existing, correction_reason=correction_reason)

    def import_bytes(self, filename: str, payload: bytes, source_name: str = "Manual file import",
                     expected_exchange: str | None = None, extra_aliases: dict[str, set[str]] | None = None, *,
                     replace_existing: bool = False, correction_reason: str | None = None) -> ImportSummary:
        if not payload:
            raise ValueError("The uploaded file is empty")
        if replace_existing and not (correction_reason and correction_reason.strip()):
            raise ValueError("A non-empty correction_reason is required when replace_existing=True")
        safe_filename = Path(filename).name
        suffix = Path(safe_filename).suffix.lower()
        sha = hashlib.sha256(payload).hexdigest()
        self.settings.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = self.settings.raw_dir / f"{sha}_{safe_filename}"
        if not raw_path.exists():
            raw_path.write_bytes(payload)
        summary = ImportSummary(safe_filename, str(raw_path), sha)
        expected_exchange = expected_exchange.upper() if expected_exchange else None
        rows = self._extract_rows(suffix, payload)
        summary.rows_read = len(rows)
        if not rows:
            raise ValueError("No records were found in this file")
        now = datetime.now(timezone.utc).isoformat()
        seen_keys: set[tuple[str, str]] = set()
        summary.source_names = []
        seen_security_keys: set[str] = set()
        metadata_checked: set[str] = set()
        configured_aliases = {field: {_header_key(alias) for alias in aliases}
                              for field, aliases in (extra_aliases or {}).items()}
        with connect_read_write(self.database_path) as conn:
            for row_number, raw in enumerate(rows, start=2):
                normalized = _normalize_row(raw, configured_aliases)
                row_source = _clean(normalized.get("source_name")) or source_name
                if row_source not in summary.source_names:
                    summary.source_names.append(row_source)
                exchange = (_clean(normalized.get("exchange")) or expected_exchange or "").upper()
                symbol = (_clean(normalized.get("symbol")) or "").upper()
                identifier = _clean(normalized.get("security_identifier"))
                identifier = identifier.upper() if identifier else None
                security_key = None
                status = "imported"
                if exchange not in {"NSE", "BSE"} or not (identifier or symbol):
                    status = "invalid"
                elif expected_exchange and exchange != expected_exchange:
                    status = "rejected"
                    add_quality_check(conn, checked_at=now, check_type="exchange_mismatch", severity="error",
                                      details=f"Record says {exchange}; import was assigned to {expected_exchange}.", source_file=safe_filename)
                else:
                    # Exchange is always part of the key; ISIN or other explicit identifier is preferred.
                    security_key = f"{exchange}:id:{identifier}" if identifier else f"{exchange}:symbol:{symbol}"
                    conn.execute(
                        """INSERT INTO securities
                           (security_key, exchange, symbol, company_name, security_identifier, security_type,
                            listing_status, source_timestamp, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(security_key) DO UPDATE SET
                             symbol=excluded.symbol,
                             company_name=COALESCE(excluded.company_name, securities.company_name),
                             security_identifier=COALESCE(excluded.security_identifier, securities.security_identifier),
                             security_type=COALESCE(excluded.security_type, securities.security_type),
                             listing_status=COALESCE(excluded.listing_status, securities.listing_status),
                             source_timestamp=COALESCE(excluded.source_timestamp, securities.source_timestamp),
                             updated_at=excluded.updated_at""",
                        (security_key, exchange, symbol or identifier, _clean(normalized.get("company_name")), identifier,
                         _clean(normalized.get("security_type")), _clean(normalized.get("listing_status")),
                         _clean(normalized.get("source_timestamp")), now, now),
                    )
                    if security_key not in seen_security_keys:
                        summary.securities_upserted += 1
                        seen_security_keys.add(security_key)
                    if security_key not in metadata_checked:
                        missing_metadata = [field for field in ("company_name", "security_type", "listing_status")
                                            if not _clean(normalized.get(field))]
                        if missing_metadata:
                            add_quality_check(conn, checked_at=now, check_type="missing_security_metadata", severity="warning",
                                              security_key=security_key,
                                              details="Missing security master field(s): " + ", ".join(missing_metadata) + ".",
                                              source_file=safe_filename)
                        metadata_checked.add(security_key)
                date_text = _parse_date(normalized.get("date"))
                if date_text is None:
                    if status == "imported":
                        status = "invalid"
                    add_quality_check(conn, checked_at=now, check_type="invalid_date", severity="error",
                                      security_key=security_key, details=f"Could not parse date value {_clean(normalized.get('date'))!r}.", source_file=safe_filename)
                open_price = _number(normalized.get("open"))
                high = _number(normalized.get("high"))
                low = _number(normalized.get("low"))
                close = _number(normalized.get("close"))
                volume = _number(normalized.get("volume"))
                missing = [name for name, raw_value, value in (
                    ("close", normalized.get("close"), close),
                    ("volume", normalized.get("volume"), volume),
                ) if _clean(raw_value) is None or value is None]
                if missing:
                    add_quality_check(conn, checked_at=now, check_type="missing_value", severity="warning",
                                      security_key=security_key, record_date=date_text,
                                      details=f"Missing or nonnumeric value(s): {', '.join(missing)}.",
                                      source_file=safe_filename)
                supplied_numeric_fields = (
                    ("open", normalized.get("open"), open_price),
                    ("high", normalized.get("high"), high),
                    ("low", normalized.get("low"), low),
                    ("close", normalized.get("close"), close),
                    ("volume", normalized.get("volume"), volume),
                )
                malformed = [name for name, raw_value, value in supplied_numeric_fields
                             if _clean(raw_value) is not None and value is None]
                missing_close = _clean(normalized.get("close")) is None or close is None
                negative = any(value is not None and value < 0
                               for value in (open_price, high, low, close, volume))
                nonpositive_close = close is not None and close <= 0
                invalid_price = bool(malformed or negative or nonpositive_close)
                invalid_ohlc = high is not None and low is not None and high < low
                if invalid_price:
                    if status == "imported":
                        status = "invalid"
                    add_quality_check(conn, checked_at=now, check_type="invalid_price", severity="error",
                                      security_key=security_key, record_date=date_text,
                                      details=("Nonnumeric supplied field(s): " + ", ".join(malformed) + ".")
                                      if malformed else "Price or volume is negative, or close is not positive.",
                                      source_file=safe_filename)
                if invalid_ohlc:
                    if status == "imported":
                        status = "invalid"
                    add_quality_check(conn, checked_at=now, check_type="invalid_ohlc", severity="error",
                                      security_key=security_key, record_date=date_text,
                                      details="High is below low.", source_file=safe_filename)

                key_date = (security_key, date_text) if security_key and date_text else None
                existing = None
                duplicate_in_file = bool(key_date and key_date in seen_keys)
                if status == "imported" and key_date and close is not None and close > 0 and not invalid_ohlc:
                    existing = conn.execute(
                        "SELECT * FROM daily_prices WHERE security_key=? AND price_date=?", key_date
                    ).fetchone()
                    if duplicate_in_file or existing is not None:
                        if replace_existing and existing is not None and not duplicate_in_file:
                            old_values = {field: existing[field] for field in ("open", "high", "low", "close", "volume")}
                            new_values = {"open": open_price, "high": high, "low": low, "close": close, "volume": volume}
                            status = "corrected" if old_values != new_values else "duplicate"
                        else:
                            status = "duplicate"

                source_cursor = conn.execute(
                    """INSERT OR IGNORE INTO source_records
                       (source_name, source_file, source_sha256, source_row_number, security_key, record_date,
                        raw_record_json, imported_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_source, safe_filename, sha, row_number, security_key, date_text,
                     json_text(raw), now, status),
                )
                if source_cursor.rowcount == 0:
                    # Identical bytes are ordinarily idempotent. An explicitly
                    # approved correction may promote the existing duplicate
                    # source row to the correction reference for its audit entry.
                    if status != "corrected":
                        continue
                    prior_source = conn.execute(
                        "SELECT source_record_id FROM source_records WHERE source_sha256=? AND source_row_number=?",
                        (sha, row_number),
                    ).fetchone()
                    if prior_source is None:
                        continue
                    source_record_id = prior_source["source_record_id"]
                    conn.execute(
                        """UPDATE source_records SET source_name=?,source_file=?,imported_at=?,status='corrected'
                           WHERE source_record_id=?""",
                        (row_source, safe_filename, now, source_record_id),
                    )
                else:
                    source_record_id = source_cursor.lastrowid
                if status == "invalid":
                    summary.invalid_rows += 1
                elif status == "rejected":
                    summary.rejected_rows += 1
                elif status == "duplicate":
                    summary.duplicate_rows += 1
                elif status == "corrected":
                    summary.corrected_rows += 1
                if security_key is None or date_text is None or status in {"rejected"}:
                    continue
                if status == "duplicate":
                    add_quality_check(conn, checked_at=now, check_type="duplicate_record", severity="warning",
                                      security_key=security_key, record_date=date_text,
                                      details="A prior price exists for this security/date; explicit correction was not applied.",
                                      source_file=safe_filename)
                    continue
                if status == "invalid":
                    # Retain a dated missing-close observation for audit/quality review,
                    # but never let invalid numeric values enter price calculations.
                    if missing_close and not invalid_ohlc and not negative and not malformed:
                        current = conn.execute(
                            "SELECT 1 FROM daily_prices WHERE security_key=? AND price_date=?", key_date
                        ).fetchone()
                        if not current and not duplicate_in_file:
                            conn.execute(
                                """INSERT INTO daily_prices
                                   (security_key, price_date, open, high, low, close, volume, source_timestamp, source_record_id)
                                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                                (security_key, date_text, open_price, high, low, volume,
                                 _clean(normalized.get("source_timestamp")), source_record_id),
                            )
                            seen_keys.add(key_date)
                    continue

                if status == "corrected":
                    old_values = {field: existing[field] for field in ("open", "high", "low", "close", "volume")}
                    new_values = {"open": open_price, "high": high, "low": low, "close": close, "volume": volume}
                    conn.execute(
                        """INSERT INTO price_corrections
                           (security_key, security_identifier, exchange, trading_date, old_price, new_price,
                            original_source_record_id, correction_source_record_id, corrected_at, reason,
                            old_values_json, new_values_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (security_key, identifier, exchange, date_text, existing["close"], close,
                         existing["source_record_id"], source_record_id, now, correction_reason.strip(),
                         json_text(old_values), json_text(new_values)),
                    )
                    conn.execute(
                        """UPDATE daily_prices SET open=?, high=?, low=?, close=?, volume=?,
                           source_timestamp=?, source_record_id=?
                           WHERE security_key=? AND price_date=?""",
                        (open_price, high, low, close, volume, _clean(normalized.get("source_timestamp")),
                         source_record_id, security_key, date_text),
                    )
                    seen_keys.add(key_date)
                    continue

                if duplicate_in_file:
                    summary.duplicate_rows += 1
                    add_quality_check(conn, checked_at=now, check_type="duplicate_record", severity="warning",
                                      security_key=security_key, record_date=date_text,
                                      details="More than one source row supplies this security/date; first stored price is retained.",
                                      source_file=safe_filename)
                    continue
                seen_keys.add(key_date)
                conn.execute(
                    """INSERT INTO daily_prices
                       (security_key, price_date, open, high, low, close, volume, source_timestamp, source_record_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (security_key, date_text, open_price, high, low, close, volume,
                     _clean(normalized.get("source_timestamp")), source_record_id),
                )
                summary.rows_inserted += 1
            self._check_price_changes(conn, now, safe_filename)
            self.check_staleness(conn, now)
            conn.commit()
        logger.info("Imported %s: %d records, %d stored prices", safe_filename, summary.rows_read, summary.rows_inserted)
        return summary

    def _extract_rows(self, suffix: str, payload: bytes) -> list[dict]:
        if suffix == ".csv":
            return _read_csv_bytes(payload)
        if suffix == ".zip":
            result = []
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = [info for info in archive.infolist() if not info.is_dir() and Path(info.filename).suffix.lower() in {".csv", ".xlsx"}]
                if len(members) > self.settings.max_zip_members:
                    raise ValueError("ZIP contains too many supported files")
                for info in members:
                    if info.file_size > self.settings.max_zip_member_bytes:
                        raise ValueError(f"ZIP member is too large: {Path(info.filename).name}")
                    content = archive.read(info)
                    if Path(info.filename).suffix.lower() == ".csv":
                        result.extend(_read_csv_bytes(content))
                    else:
                        frame = pd.read_excel(io.BytesIO(content), dtype=object)
                        result.extend(frame.where(pd.notna(frame), None).to_dict(orient="records"))
            return result
        if suffix in {".xlsx", ".xlsm"}:
            frame = pd.read_excel(io.BytesIO(payload), dtype=object)
            return frame.where(pd.notna(frame), None).to_dict(orient="records")
        if suffix in {".txt", ".tsv"}:
            return _read_csv_bytes(payload)
        raise ValueError("Supported formats: CSV, ZIP containing CSV/XLSX, XLSX, XLSM, TSV, and delimited TXT")

    def _check_price_changes(self, conn: sqlite3.Connection, now: str, source_file: str) -> None:
        securities = conn.execute("SELECT security_key FROM securities").fetchall()
        for security in securities:
            rows = conn.execute(
                "SELECT price_date, close FROM daily_prices WHERE security_key=? AND close IS NOT NULL ORDER BY price_date",
                (security["security_key"],),
            ).fetchall()
            for previous, current in zip(rows, rows[1:]):
                if previous["close"] in (None, 0):
                    continue
                change = (float(current["close"]) / float(previous["close"]) - 1) * 100
                if abs(change) >= self.settings.suspicious_change_pct:
                    add_quality_check(conn, checked_at=now, check_type="suspicious_price_change", severity="warning",
                                      security_key=security["security_key"], record_date=current["price_date"],
                                      details=f"Observed {change:.2f}% close-to-close move; verify source and corporate actions.", source_file=source_file)
                if abs(change) >= self.settings.corporate_action_change_pct:
                    add_quality_check(conn, checked_at=now, check_type="possible_corporate_action", severity="warning",
                                      security_key=security["security_key"], record_date=current["price_date"],
                                      details=f"Observed {change:.2f}% move may reflect a corporate action; this is an unverified flag.", source_file=source_file)

    def check_staleness(self, conn: sqlite3.Connection, now: str | None = None, reference_date: str | None = None) -> None:
        if reference_date is None:
            reference_date = date.today().isoformat()
        if not reference_date:
            return
        now = now or datetime.now(timezone.utc).isoformat()
        reference = date.fromisoformat(reference_date)
        for security in conn.execute("SELECT security_key FROM securities"):
            latest_row = conn.execute(
                "SELECT MAX(price_date) AS latest FROM daily_prices WHERE security_key=? AND close IS NOT NULL",
                (security["security_key"],),
            ).fetchone()
            latest = latest_row["latest"]
            if latest is None:
                add_quality_check(conn, checked_at=now, check_type="missing_price_history", severity="warning",
                                  security_key=security["security_key"], record_date=reference_date,
                                  details="Security has no valid close-price history.")
                continue
            age = (reference - date.fromisoformat(latest)).days
            if age > self.settings.stale_after_days:
                add_quality_check(conn, checked_at=now, check_type="stale_data", severity="warning",
                                  security_key=security["security_key"], record_date=latest,
                                  details=f"Latest close is {age} calendar days before dataset date {reference_date}; threshold is {self.settings.stale_after_days} days.")

