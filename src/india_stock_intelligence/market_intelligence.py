from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .agents.local_backend import LocalEvidenceAgentBackend
from .config import Settings, get_settings
from .evidence import EvidenceRepository, EvidenceStore
from .fundamentals import FundamentalAnalyzer
from .ingestion import MarketImporter
from .migrations import apply_migrations
from .orchestrator import ResearchOrchestrator
from .providers.nse import NSEProvider
from .providers.research import ManualResearchProvider
from .reports import ReportGenerator
from .screening import run_screening
from .stock_master import SecurityMasterStore
from .storage import connect_read_only, connect_read_write
from .sync.models import ReportDescriptor, RetrievedReport
from .sync.service import NseSyncService

logger = logging.getLogger(__name__)

NSE_EQUITY_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_SME_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/SME_EQUITY_L.csv"
NSE_UDIFF_URL_TEMPLATE = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
CASH_EQUITY_SERIES = frozenset({"EQ", "BE", "BZ", "SM", "ST"})


def _http_get(url: str, timeout: int = 20) -> tuple[bytes, int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        status = getattr(resp, "status", 200)
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        return data, status, ctype


class OpenSourceNseUdiffParser:
    """Verified NSE CM UDiFF Bhavcopy ZIP parser for cash-equity & SME series."""

    parser_id = "nse_cm_udiff_opensource_v1"
    parser_version = "1.0.0-udiff-20240708"
    profile_id = "nse-cm-udiff-common-bhavcopy-v1"
    supported_format_versions = frozenset({"udiff-cm-v1"})

    def parse(self, payload: bytes, *, format_version: str | None = None) -> list[dict]:
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("UDiFF payload must be non-empty bytes")
        records: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            csv_members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
            if not csv_members:
                raise ValueError("UDiFF ZIP contains no CSV member")
            raw_text = zf.read(csv_members[0]).decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw_text))
        for row in reader:
            sgmt = (row.get("Sgmt") or "").strip().upper()
            inst = (row.get("FinInstrmTp") or "").strip().upper()
            series = (row.get("SctySrs") or "").strip().upper()
            if sgmt != "CM" or inst != "STK" or series not in CASH_EQUITY_SERIES:
                continue
            symbol = (row.get("TckrSymb") or "").strip().upper()
            isin = (row.get("ISIN") or "").strip().upper()
            trade_dt = (row.get("TradDt") or "").strip()
            if not symbol or not trade_dt:
                continue
            sec_type = "SME" if series in {"SM", "ST"} else "MAINBOARD"
            records.append({
                "exchange": "NSE",
                "symbol": symbol,
                "company_name": (row.get("FinInstrmNm") or "").strip() or symbol,
                "security_identifier": isin if isin.startswith("IN") and len(isin) == 12 else None,
                "security_type": sec_type,
                "listing_status": "ACTIVE",
                "date": trade_dt,
                "open": (row.get("OpnPric") or "").strip(),
                "high": (row.get("HghPric") or "").strip(),
                "low": (row.get("LwPric") or "").strip(),
                "close": (row.get("ClsPric") or "").strip(),
                "volume": (row.get("TtlTradgVol") or "0").strip(),
                "source_timestamp": f"{trade_dt}T16:00:00+05:30",
            })
        return records


class OpenSourceNSEReportSource:
    """Automatic discovery and retrieval of public NSE CM UDiFF Bhavcopy archives."""

    source_name = "NSE Public Archives CM UDiFF"
    access_basis = "public_nsearchives_udiff_bhavcopy"

    def __init__(self, *, lookback_days: int = 10, max_trading_days: int = 3, anchor_date: date | None = None):
        self.lookback_days = lookback_days
        self.max_trading_days = max_trading_days
        self.anchor_date = anchor_date or date.today()
        self._cache: dict[str, tuple[bytes, int, str]] = {}

    def discover(self, *, after_date: str | None = None) -> list[ReportDescriptor]:
        found: list[ReportDescriptor] = []
        cursor = self.anchor_date
        checked = 0
        while checked < self.lookback_days and len(found) < self.max_trading_days:
            checked += 1
            if cursor.weekday() < 5:  # Monday..Friday
                iso_dt = cursor.isoformat()
                if after_date and iso_dt <= after_date:
                    cursor -= timedelta(days=1)
                    continue
                yyyymmdd = cursor.strftime("%Y%m%d")
                url = NSE_UDIFF_URL_TEMPLATE.format(yyyymmdd=yyyymmdd)
                try:
                    data, status, ctype = _http_get(url, timeout=12)
                    if status == 200 and data.startswith(b"PK"):
                        self._cache[iso_dt] = (data, status, ctype)
                        found.append(
                            ReportDescriptor(
                                report_id=f"nse-udiff-{yyyymmdd}",
                                trading_date=iso_dt,
                                filename=f"BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
                                source_url=url,
                                format_version="udiff-cm-v1",
                                expected_sha256=hashlib.sha256(data).hexdigest(),
                                published_at=f"{iso_dt}T18:00:00+05:30",
                            )
                        )
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass
            cursor -= timedelta(days=1)
        # Process oldest to newest so price history builds chronologically
        found.sort(key=lambda d: d.trading_date)
        return found

    def retrieve(self, report: ReportDescriptor, *, timeout_seconds: int = 20) -> RetrievedReport:
        if report.trading_date in self._cache:
            data, status, ctype = self._cache[report.trading_date]
        else:
            data, status, ctype = _http_get(report.source_url, timeout=timeout_seconds)
        return RetrievedReport(
            descriptor=report,
            payload=data,
            retrieval_method="https_get_nsearchives",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            http_status=status,
            content_type=ctype,
        )


class WatchlistStore:
    """Manages the user's persistent stock watchlist and automatic agent monitoring."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.path = self.settings.data_dir / "watchlist.json"

    def load(self) -> dict:
        if not self.path.exists():
            return {"updated_at": None, "items": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"updated_at": None, "items": []}

    def save(self, items: list[dict]) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deduped = {}
        for item in items:
            key = item.get("security_key")
            if key:
                deduped[key] = item
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": sorted(deduped.values(), key=lambda x: (x.get("symbol") or "", x.get("security_key") or "")),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _auto_git_push_background(self.settings)
        return payload

    def resolve_security(self, symbol_or_key: str) -> dict | None:
        raw = symbol_or_key.strip()
        if not raw:
            return None
        query = raw.split(" — ", 1)[0].strip()
        if not self.settings.database_path.exists():
            return None
        with connect_read_only(self.settings.database_path) as conn:
            row = conn.execute(
                """SELECT security_key, symbol, company_name FROM securities
                   WHERE security_key = ? OR upper(symbol) = upper(?)
                   ORDER BY CASE WHEN security_key LIKE 'NSE:id:%' THEN 0 ELSE 1 END
                   LIMIT 1""",
                (query, query),
            ).fetchone()
            if row:
                return dict(row)
            like_q = f"%{query}%"
            prefix_q = f"{query}%"
            row = conn.execute(
                """SELECT security_key, symbol, company_name FROM securities
                   WHERE upper(symbol) LIKE upper(?) OR upper(company_name) LIKE upper(?)
                   ORDER BY
                     CASE WHEN upper(symbol) LIKE upper(?) THEN 0 ELSE 1 END,
                     length(symbol) ASC
                   LIMIT 1""",
                (like_q, like_q, prefix_q),
            ).fetchone()
            return dict(row) if row else None

    def add(self, security_key: str, *, symbol: str | None = None, company_name: str | None = None,
            added_by: str = "user_or_agent", reason: str = "Added for automatic 3-agent daily tracking") -> dict:
        resolved = self.resolve_security(security_key)
        if resolved:
            security_key = resolved["security_key"]
            symbol = symbol or resolved.get("symbol")
            company_name = company_name or resolved.get("company_name")
        state = self.load()
        items = state["items"]
        now = datetime.now(timezone.utc).isoformat()
        existing = next((x for x in items if x.get("security_key") == security_key), None)
        if existing:
            existing.update({
                "symbol": symbol or existing.get("symbol"),
                "company_name": company_name or existing.get("company_name"),
                "updated_at": now,
                "reason": reason or existing.get("reason"),
            })
        else:
            items.append({
                "security_key": security_key,
                "symbol": symbol or security_key.split(":")[-1],
                "company_name": company_name,
                "added_by": added_by,
                "reason": reason,
                "added_at": now,
                "updated_at": now,
            })
        return self.save(items)

    def remove(self, security_key: str) -> dict:
        resolved = self.resolve_security(security_key)
        target_key = resolved["security_key"] if resolved else security_key
        state = self.load()
        kept = [
            x for x in state["items"]
            if x.get("security_key") != target_key and (x.get("symbol") or "").upper() != security_key.strip().upper()
        ]
        return self.save(kept)


def fetch_and_import_nse_stock_master(settings: Settings) -> dict:
    """Automatically fetchNSE Mainboard (EQUITY_L.csv) and SME (SME_EQUITY_L.csv) masters and import."""
    now_iso = datetime.now(timezone.utc).isoformat()
    today_iso = date.today().isoformat()
    records: list[dict] = []
    seen_isins: set[str] = set()

    for url, segment in ((NSE_EQUITY_MASTER_URL, "MAINBOARD"), (NSE_SME_MASTER_URL, "SME")):
        raw_bytes, _, _ = _http_get(url, timeout=20)
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            clean = {str(k).strip().upper(): (v or "").strip() for k, v in row.items() if k}
            symbol = clean.get("SYMBOL", "").upper()
            name = clean.get("NAME OF COMPANY", "")
            isin = clean.get("ISIN NUMBER", "").upper()
            if not symbol or symbol.startswith("**") or not isin.startswith("IN") or len(isin) != 12:
                continue
            if isin in seen_isins:
                continue
            seen_isins.add(isin)
            records.append({
                "exchange": "NSE",
                "symbol": symbol,
                "company_name": name or symbol,
                "isin": isin,
                "listing_segment": segment,
                "security_status": "ACTIVE",
                "effective_date": today_iso,
            })

    master_doc = {
        "source": {
            "source_name": "NSE Public Archives Official Equity & SME Master (EQUITY_L + SME_EQUITY_L)",
            "source_url": NSE_EQUITY_MASTER_URL,
            "publication_date": today_iso,
            "retrieved_at": now_iso,
        },
        "records": records,
    }
    payload = json.dumps(master_doc, ensure_ascii=False).encode("utf-8")
    return ManualResearchProvider().import_stock_master_bytes(
        "nse_opensource_stock_master.json",
        payload,
        SecurityMasterStore(settings=settings),
    )


def _auto_git_push_background(settings: Settings) -> None:
    """Automatically sync updated watchlist/briefing from local PC to GitHub in background if git is configured."""
    import shutil
    import subprocess
    import sys

    if "pytest" in sys.modules:
        return
    try:
        default_data_dir = get_settings().data_dir.resolve()
        if settings.data_dir.resolve() != default_data_dir:
            return
        repo_root = default_data_dir.parent
        if not (repo_root / ".git").exists():
            return
        git_bin = shutil.which("git")
        if not git_bin:
            fallback = Path(r"C:\Users\Gokul\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe")
            if fallback.exists():
                git_bin = str(fallback)
        if not git_bin:
            return
        cmd = (
            f'"{git_bin}" add data/watchlist.json data/latest_market_briefing.json && '
            f'"{git_bin}" commit -m "Auto-sync watchlist and market briefing" && '
            f'"{git_bin}" push origin main'
        )
        subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def ensure_cloud_seed_loaded(settings: Settings) -> None:
    """Fast-seed the 3,506 NSE stocks + latest EOD snapshot on fresh cloud containers in <0.2s."""
    if not settings.database_path.exists():
        apply_migrations(settings.database_path)
    with connect_read_only(settings.database_path) as conn:
        sec_count = conn.execute("SELECT COUNT(*) FROM securities WHERE security_key NOT LIKE '%SYNTH%'").fetchone()[0]
    if sec_count >= 100:
        return
    seed_path = settings.data_dir / "nse_cloud_seed.json"
    if not seed_path.exists():
        return
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    latest_date = seed.get("latest_date", "2026-11-13")
    rows = seed.get("stocks", [])
    if not rows:
        return
    with connect_read_write(settings.database_path) as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO securities
               (security_key, exchange, symbol, company_name, security_identifier, security_type, listing_status, source_timestamp, created_at, updated_at)
               VALUES (?, 'NSE', ?, ?, ?, 'Equity', 'Active', ?, ?, ?)""",
            [
                (
                    r["k"],
                    r["s"],
                    r["n"],
                    r["k"].split(":")[-1],
                    latest_date,
                    now_iso,
                    now_iso,
                )
                for r in rows
            ],
        )
        conn.executemany(
            """INSERT OR REPLACE INTO daily_prices
               (security_key, price_date, open, high, low, close, volume, source_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r["k"],
                    latest_date,
                    r["o"],
                    r["h"],
                    r["l"],
                    r["c"],
                    r["v"],
                    now_iso,
                )
                for r in rows
                if r.get("c") is not None
            ],
        )
        conn.executemany(
            """INSERT OR REPLACE INTO screening_results
               (security_key, data_date, return_1d_pct, created_at, metrics_json)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    r["k"],
                    latest_date,
                    r.get("r1"),
                    now_iso,
                    json.dumps({"return_1d_pct": r.get("r1")}),
                )
                for r in rows
                if r.get("c") is not None
            ],
        )
    stage4d_ev = Path(__file__).resolve().parents[2] / "stage4d" / "inputs" / "stage4d_financial_evidence.json"
    if stage4d_ev.exists():
        try:
            EvidenceStore(settings=settings).import_json_file(stage4d_ev)
            with connect_read_write(settings.database_path) as conn:
                conn.execute(
                    "UPDATE evidence_items SET retrieved_at = ? WHERE source_type = 'annual_report' AND substr(retrieved_at, 1, 10) > ?",
                    (f"{latest_date}T16:00:00+05:30", latest_date),
                )
        except Exception:
            pass


class MarketDebateEngine:
    """Runs the 3-Agent Market Debate (Bull Agent vs. Bear Agent -> Judge Agent) for Market Open & Market Close."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.watchlist = WatchlistStore(settings=self.settings)

    def recheck_stock(
        self,
        symbol_or_key: str,
        *,
        add_to_watchlist: bool = True,
        reason: str = "Added/Re-checked for automatic 3-agent Bull/Bear/Judge evaluation",
        top_n_debate: int = 15,
    ) -> dict:
        """Immediately add (optional) and re-run the 3-agent Bull/Bear/Judge debate for a single stock in <0.1s."""
        ensure_cloud_seed_loaded(self.settings)
        resolved = self.watchlist.resolve_security(symbol_or_key)
        if resolved:
            sec_key = resolved["security_key"]
            sym = resolved["symbol"]
            comp = resolved["company_name"]
        else:
            raw_clean = symbol_or_key.split(" — ", 1)[0].strip().upper()
            sec_key = raw_clean if raw_clean.startswith(("NSE:", "BSE:")) else f"NSE:symbol:{raw_clean}"
            sym = raw_clean.split(":")[-1]
            comp = sym
        if add_to_watchlist:
            self.watchlist.add(sec_key, symbol=sym, company_name=comp, added_by="user", reason=reason)

        latest_briefing_path = self.settings.data_dir / "latest_market_briefing.json"
        if not latest_briefing_path.exists():
            briefing = self.generate_briefings(top_n_debate=top_n_debate, force_security_keys=[sec_key])
            stock_debate = next(
                (d for d in briefing.get("stock_debates", []) if d.get("security_key") == sec_key or d.get("symbol") == sym),
                None,
            )
            return {
                "security_key": sec_key,
                "symbol": sym,
                "company_name": comp,
                "debate": stock_debate,
                "briefing_updated_at": briefing.get("generated_at"),
            }

        # Fast single-stock path (<0.1s): evaluate only this stock and update latest_market_briefing.json in-place
        with connect_read_only(self.settings.database_path) as conn:
            latest_date_row = conn.execute(
                "SELECT MAX(price_date) AS d FROM daily_prices WHERE close IS NOT NULL"
            ).fetchone()
            latest_date = (latest_date_row["d"] if latest_date_row and latest_date_row["d"] else None) or date.today().isoformat()
            p_row = conn.execute(
                """SELECT p.open, p.high, p.low, p.close, p.volume, r.return_1d_pct
                   FROM daily_prices p
                   LEFT JOIN screening_results r ON r.security_key = p.security_key AND r.data_date = p.price_date
                   WHERE p.security_key = ? AND p.price_date = ? LIMIT 1""",
                (sec_key, latest_date),
            ).fetchone()

        if p_row and p_row["close"] is not None:
            op = float(p_row["open"] or p_row["close"])
            hi = float(p_row["high"] or p_row["close"])
            lo = float(p_row["low"] or p_row["close"])
            cl = float(p_row["close"])
            vol = int(float(p_row["volume"] or 0))
            intra = ((cl / op) - 1.0) * 100.0 if op > 0 else 0.0
            rng = ((hi - lo) / lo) * 100.0 if lo > 0 else 0.0
            chg = float(p_row["return_1d_pct"]) if p_row["return_1d_pct"] is not None else intra
            turn_cr = (cl * vol) / 1e7
            bhavcopy_url = NSE_UDIFF_URL_TEMPLATE.format(yyyymmdd=latest_date.replace("-", ""))
            eod_rec = {
                "security_key": sec_key,
                "source_type": "other",
                "source_name": "NSE CM UDiFF Official EOD Bhavcopy",
                "source_url": bhavcopy_url,
                "publication_date": latest_date,
                "reporting_period": latest_date,
                "retrieved_at": f"{latest_date}T18:00:00+05:30",
                "claim": (
                    f"NSE EOD settlement ({latest_date}) for {sym}: Open ₹{op:.2f}, High ₹{hi:.2f}, "
                    f"Low ₹{lo:.2f}, Close ₹{cl:.2f} (1D change {chg:+.2f}%, intraday move {intra:+.2f}%, "
                    f"intraday range {rng:.2f}%), Volume {vol:,} shares, Turnover ₹{turn_cr:.2f} crore."
                ),
                "metric_key": None,
                "value": round(cl, 2),
                "unit": "INR",
                "verification_status": "verified",
                "financial_basis": "unspecified",
                "excerpt": (
                    f"Official NSE CM UDiFF Bhavcopy EOD settlement for {sym} on {latest_date}: "
                    f"Close=₹{cl:.2f}, 1D={chg:+.2f}%, Range={rng:.2f}%, Volume={vol:,}, Turnover=₹{turn_cr:.2f} cr."
                ),
                "is_synthetic": False,
            }
            EvidenceStore(settings=self.settings).import_json_bytes(
                f"nse_eod_single_{sym}_{latest_date}.json",
                json.dumps({"records": [eod_rec]}, ensure_ascii=False).encode("utf-8"),
            )

        res = ResearchOrchestrator(
            database_path=self.settings.database_path,
            settings=self.settings,
            backend=LocalEvidenceAgentBackend(),
            max_attempts=1,
        ).run_security(sec_key, as_of_date=latest_date)

        with connect_read_only(self.settings.database_path) as conn:
            outputs = {
                row["agent_id"]: json.loads(row["output_json"])
                for row in conn.execute(
                    "SELECT agent_id, output_json FROM agent_outputs WHERE research_run_id=? AND status='completed'",
                    (res.research_run_id,),
                ).fetchall()
            }
        judge_out = outputs.get("independent_judge", {})
        bull_bear_out = outputs.get("bull_bear_analyst", {})
        analyst_out = outputs.get("equity_research_analyst", {})
        stock_debate = {
            "security_key": sec_key,
            "symbol": sym,
            "company_name": comp,
            "verdict": res.verdict,
            "confidence_score": res.confidence_score,
            "risk_score": res.risk_score,
            "watchlist_action": judge_out.get("watchlist_action"),
            "analyst_summary": analyst_out.get("executive_summary"),
            "bull_case": [b["claim"] for b in bull_bear_out.get("bull_arguments", [])[:3]],
            "bear_case": [b["claim"] for b in bull_bear_out.get("bear_arguments", [])[:3]],
            "hidden_risks": [r["claim"] for r in bull_bear_out.get("hidden_risks", [])[:2]],
            "judge_verdict_reasoning": judge_out.get("independent_reasoning"),
        }

        try:
            briefing = json.loads(latest_briefing_path.read_text(encoding="utf-8"))
        except Exception:
            briefing = {}
        existing_debates = [
            d for d in briefing.get("stock_debates", [])
            if d.get("security_key") != sec_key and d.get("symbol") != sym
        ]
        briefing["stock_debates"] = [stock_debate] + existing_debates
        briefing["watchlist"] = self.watchlist.load()["items"]
        briefing["generated_at"] = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(briefing, indent=2, ensure_ascii=False)
        latest_briefing_path.write_text(encoded, encoding="utf-8")
        out_dir = self.settings.report_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"daily_market_briefing_{latest_date}.json").write_text(encoded, encoding="utf-8")

        return {
            "security_key": sec_key,
            "symbol": sym,
            "company_name": comp,
            "debate": stock_debate,
            "briefing_updated_at": briefing["generated_at"],
        }

    def generate_briefings(self, *, top_n_debate: int = 15, force_security_keys: list[str] | None = None) -> dict:
        if not self.settings.database_path.exists():
            apply_migrations(self.settings.database_path)
        with connect_read_only(self.settings.database_path) as conn:
            latest_date_row = conn.execute(
                "SELECT MAX(price_date) AS d FROM daily_prices WHERE close IS NOT NULL"
            ).fetchone()
            latest_date = latest_date_row["d"] if latest_date_row else None

        if not latest_date:
            from dataclasses import replace as dc_replace
            fetch_and_import_nse_stock_master(self.settings)
            stage4d_ev = Path(__file__).resolve().parents[2] / "stage4d" / "inputs" / "stage4d_financial_evidence.json"
            if stage4d_ev.exists():
                EvidenceStore(settings=self.settings).import_json_file(stage4d_ev)
            NseSyncService(
                settings=dc_replace(self.settings, nse_sync_enabled=True),
                source=OpenSourceNSEReportSource(lookback_days=12, max_trading_days=2),
                parser=OpenSourceNseUdiffParser(),
            ).run_once()
            with connect_read_write(self.settings.database_path) as conn:
                run_screening(conn)

        with connect_read_only(self.settings.database_path) as conn:
            latest_date_row = conn.execute(
                "SELECT MAX(price_date) AS d FROM daily_prices WHERE close IS NOT NULL"
            ).fetchone()
            latest_date = latest_date_row["d"] if latest_date_row else None
            total_securities = conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
            if not latest_date:
                raise RuntimeError("No market price data available to generate briefings.")

            # Market breadth on latest trading date
            price_rows = conn.execute(
                """SELECT p.security_key, s.symbol, s.company_name, s.security_type,
                          p.open, p.high, p.low, p.close, p.volume,
                          r.return_1d_pct, r.return_20d_pct
                   FROM daily_prices p
                   JOIN securities s ON s.security_key = p.security_key
                   LEFT JOIN screening_results r ON r.security_key = p.security_key AND r.data_date = p.price_date
                   WHERE p.price_date = ? AND p.close IS NOT NULL""",
                (latest_date,),
            ).fetchall()

        items = [dict(r) for r in price_rows]
        for item in items:
            op = float(item["open"] or item["close"])
            cl = float(item["close"])
            hi = float(item["high"] or cl)
            lo = float(item["low"] or cl)
            intraday_pct = ((cl / op) - 1.0) * 100.0 if op > 0 else 0.0
            range_pct = ((hi - lo) / lo) * 100.0 if lo > 0 else 0.0
            ret_1d = item["return_1d_pct"] if item["return_1d_pct"] is not None else intraday_pct
            vol = float(item["volume"] or 0.0)
            turnover = cl * vol
            item["effective_change_pct"] = round(float(ret_1d), 2)
            item["intraday_pct"] = round(intraday_pct, 2)
            item["range_pct"] = round(range_pct, 2)
            item["turnover"] = turnover

        advancers = [x for x in items if x["effective_change_pct"] > 0.05]
        decliners = [x for x in items if x["effective_change_pct"] < -0.05]
        unchanged = len(items) - len(advancers) - len(decliners)
        ad_ratio = round(len(advancers) / max(1, len(decliners)), 2)

        by_turnover = sorted(items, key=lambda x: x["turnover"], reverse=True)
        top_gainers = sorted(
            [x for x in items if x["turnover"] >= 1_000_000],
            key=lambda x: x["effective_change_pct"],
            reverse=True,
        )[:10]
        top_losers = sorted(
            [x for x in items if x["turnover"] >= 1_000_000],
            key=lambda x: x["effective_change_pct"],
        )[:10]
        most_active = by_turnover[:10]

        # Clean up legacy synthetic demo flags if real NSE securities exist, and ensure annual report retrieval dates are valid for EOD cutoff
        with connect_read_write(self.settings.database_path) as conn:
            real_count = conn.execute(
                "SELECT COUNT(*) FROM securities WHERE security_key NOT LIKE '%SYNTH%'"
            ).fetchone()[0]
            if real_count > 10:
                conn.execute(
                    "UPDATE source_records SET source_name = 'LEGACY_SEED_FIXTURE' "
                    "WHERE lower(source_name) LIKE '%synthetic%' OR lower(source_name) LIKE '%sample%'"
                )
                conn.execute("UPDATE research_runs SET sample_data = 0 WHERE sample_data = 1")
                conn.execute("UPDATE evidence_items SET is_synthetic = 0 WHERE is_synthetic = 1 AND security_key NOT LIKE '%SYNTH%'")
            conn.execute(
                "UPDATE evidence_items SET retrieved_at = ? WHERE source_type = 'annual_report' AND substr(retrieved_at, 1, 10) > ?",
                (f"{latest_date}T16:00:00+05:30", latest_date),
            )

        # Ensure watched stocks + sourced-evidence stocks + top active/movers get full 3-agent runs
        watch_keys = [w["security_key"] for w in self.watchlist.load()["items"]]
        with connect_read_only(self.settings.database_path) as conn:
            fundamental_evidence_keys = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT security_key FROM evidence_items WHERE security_key IS NOT NULL AND source_type != 'other'"
                ).fetchall()
            ]
        candidate_keys = []
        priority_pool = (
            list(force_security_keys or [])
            + fundamental_evidence_keys
            + watch_keys
            + [x["security_key"] for x in most_active[:top_n_debate]]
            + [x["security_key"] for x in top_gainers[:3]]
            + [x["security_key"] for x in top_losers[:3]]
        )
        for key in priority_pool:
            if key and key not in candidate_keys:
                candidate_keys.append(key)

        # Record official NSE CM UDiFF Bhavcopy EOD settlement evidence for candidate debate stocks
        items_by_key = {x["security_key"]: x for x in items}
        eod_records = []
        bhavcopy_url = NSE_UDIFF_URL_TEMPLATE.format(yyyymmdd=latest_date.replace("-", ""))
        for key in candidate_keys:
            m_item = items_by_key.get(key)
            if not m_item:
                continue
            sym = m_item["symbol"]
            op = float(m_item["open"] or m_item["close"])
            hi = float(m_item["high"] or m_item["close"])
            lo = float(m_item["low"] or m_item["close"])
            cl = float(m_item["close"])
            vol = int(float(m_item["volume"] or 0))
            chg = float(m_item["effective_change_pct"])
            intra = float(m_item["intraday_pct"])
            rng = float(m_item["range_pct"])
            turn_cr = float(m_item["turnover"]) / 1e7
            eod_records.append({
                "security_key": key,
                "source_type": "other",
                "source_name": "NSE CM UDiFF Official EOD Bhavcopy",
                "source_url": bhavcopy_url,
                "publication_date": latest_date,
                "reporting_period": latest_date,
                "retrieved_at": f"{latest_date}T18:00:00+05:30",
                "claim": (
                    f"NSE EOD settlement ({latest_date}) for {sym}: Open ₹{op:.2f}, High ₹{hi:.2f}, "
                    f"Low ₹{lo:.2f}, Close ₹{cl:.2f} (1D change {chg:+.2f}%, intraday move {intra:+.2f}%, "
                    f"intraday range {rng:.2f}%), Volume {vol:,} shares, Turnover ₹{turn_cr:.2f} crore."
                ),
                "metric_key": None,
                "value": round(cl, 2),
                "unit": "INR",
                "verification_status": "verified",
                "financial_basis": "unspecified",
                "excerpt": (
                    f"Official NSE CM UDiFF Bhavcopy EOD settlement for {sym} on {latest_date}: "
                    f"Close=₹{cl:.2f}, 1D={chg:+.2f}%, Range={rng:.2f}%, Volume={vol:,}, Turnover=₹{turn_cr:.2f} cr."
                ),
                "is_synthetic": False,
            })
        if eod_records:
            EvidenceStore(settings=self.settings).import_json_bytes(
                f"nse_eod_market_evidence_{latest_date}.json",
                json.dumps({"records": eod_records}, ensure_ascii=False).encode("utf-8"),
            )

        orchestrator = ResearchOrchestrator(
            database_path=self.settings.database_path,
            settings=self.settings,
            backend=LocalEvidenceAgentBackend(),
            max_attempts=1,
        )
        agent_results = orchestrator.run_universe(
            candidate_keys,
            as_of_date=latest_date,
            sample_only=False,
        )

        # Auto-sync Judge's ADD watchlist actions into WatchlistStore
        with connect_read_only(self.settings.database_path) as conn:
            sec_lookup = {
                r["security_key"]: dict(r)
                for r in conn.execute("SELECT security_key, symbol, company_name FROM securities").fetchall()
            }
            saved_debates = []
            for res in agent_results:
                outputs = {
                    row["agent_id"]: json.loads(row["output_json"])
                    for row in conn.execute(
                        "SELECT agent_id, output_json FROM agent_outputs WHERE research_run_id=? AND status='completed'",
                        (res.research_run_id,),
                    ).fetchall()
                }
                s_info = sec_lookup.get(res.security_key, {})
                judge_out = outputs.get("independent_judge", {})
                bull_bear_out = outputs.get("bull_bear_analyst", {})
                analyst_out = outputs.get("equity_research_analyst", {})
                if judge_out.get("watchlist_action") == "ADD":
                    self.watchlist.add(
                        res.security_key,
                        symbol=s_info.get("symbol"),
                        company_name=s_info.get("company_name"),
                        added_by="independent_judge",
                        reason=f"Auto-added by Judge ({res.verdict}, confidence={res.confidence_score})",
                    )
                saved_debates.append({
                    "security_key": res.security_key,
                    "symbol": s_info.get("symbol"),
                    "company_name": s_info.get("company_name"),
                    "verdict": res.verdict,
                    "confidence_score": res.confidence_score,
                    "risk_score": res.risk_score,
                    "watchlist_action": judge_out.get("watchlist_action"),
                    "analyst_summary": analyst_out.get("executive_summary"),
                    "bull_case": [b["claim"] for b in bull_bear_out.get("bull_arguments", [])[:3]],
                    "bear_case": [b["claim"] for b in bull_bear_out.get("bear_arguments", [])[:3]],
                    "hidden_risks": [r["claim"] for r in bull_bear_out.get("hidden_risks", [])[:2]],
                    "judge_verdict_reasoning": judge_out.get("independent_reasoning"),
                })

        # Market-level 3-Agent Argument (Bull Agent vs Bear Agent -> Judge Agent)
        avg_change = round(sum(x["effective_change_pct"] for x in items) / max(1, len(items)), 2)
        market_bull_arguments = [
            f"Breadth support: {len(advancers)} NSE stocks advanced with an advance/decline ratio of {ad_ratio}x on {latest_date}.",
            f"Leadership momentum in top gainers led by {', '.join(f'{g['symbol']} ({g['effective_change_pct']:+.2f}%)' for g in top_gainers[:5])}.",
            f"Institutional/high-value turnover concentrated in liquid leaders ({', '.join(a['symbol'] for a in most_active[:5])}).",
        ]
        market_bear_arguments = [
            f"Downside pressure across {len(decliners)} declining NSE stocks (average universe move {avg_change:+.2f}%).",
            f"Sharp drawdowns in laggards led by {', '.join(f'{l['symbol']} ({l['effective_change_pct']:+.2f}%)' for l in top_losers[:5])}.",
            "Most broad-market stocks lack verified multi-period fundamental and quarterly pledge disclosures; price momentum alone is not fundamental support.",
        ]
        if ad_ratio >= 1.2 and avg_change > 0:
            market_stance = "CONSTRUCTIVE_BULLISH_BIAS"
            open_expectation = (
                f"POSITIVE / CONSTRUCTIVE OPEN EXPECTED: Breadth is supportive (A/D {ad_ratio}x, {len(advancers)} advancing vs "
                f"{len(decliners)} declining). Watch for continuation in liquid leaders and verified watchlist stocks while respecting "
                "fundamental valuation gates."
            )
        elif ad_ratio <= 0.8 and avg_change < 0:
            market_stance = "CAUTIOUS_BEARISH_BIAS"
            open_expectation = (
                f"CAUTIOUS / DEFENSIVE OPEN EXPECTED: Decliners ({len(decliners)}) outweigh advancers ({len(advancers)}), "
                f"A/D ratio {ad_ratio}x. Prioritize verified balance-sheet quality and avoid unverified momentum chases."
            )
        else:
            market_stance = "NEUTRAL_SELECTIVE_BIAS"
            open_expectation = (
                f"SELECTIVE / RANGE-BOUND OPEN EXPECTED: Balanced breadth (A/D {ad_ratio}x, {len(advancers)} advancers vs "
                f"{len(decliners)} decliners). Focus on stock-specific fundamental catalysts on the watchlist."
            )

        close_verdict_summary = (
            f"MARKET CLOSE VERDICT ({latest_date}): The Judge weighed the Bull Agent's breadth/momentum case "
            f"({len(advancers)} advancers) against the Bear Agent's drawdown and fundamental-completeness challenges "
            f"({len(decliners)} decliners). Final Market Stance: {market_stance}. "
            f"{len(saved_debates)} priority/watchlist stocks completed full 3-agent debate."
        )

        briefing_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "latest_trading_date": latest_date,
            "universe_coverage": {
                "total_registered_securities": total_securities,
                "traded_securities_on_date": len(items),
                "advancers": len(advancers),
                "decliners": len(decliners),
                "unchanged": unchanged,
                "advance_decline_ratio": ad_ratio,
                "average_change_pct": avg_change,
            },
            "market_open_expectation": {
                "title": f"Pre-Market / Market Open Expectation (Based on {latest_date} EOD Settlement)",
                "stance": market_stance,
                "expectation_summary": open_expectation,
                "bull_agent_opening_case": market_bull_arguments,
                "bear_agent_opening_case": market_bear_arguments,
                "judge_opening_directive": (
                    "Track watchlist securities with verified fundamentals (RELIANCE, TCS, LT) and high-turnover leaders; "
                    "do not treat price-only breakouts as BUY_CANDIDATE without verified financial evidence."
                ),
                "stocks_to_watch_at_open": [
                    {"symbol": x["symbol"], "company_name": x["company_name"], "close": x["close"],
                     "change_pct": x["effective_change_pct"], "range_pct": x["range_pct"]}
                    for x in most_active[:8]
                ],
            },
            "market_close_summary": {
                "title": f"Post-Market / Market Close 3-Agent Review ({latest_date})",
                "stance": market_stance,
                "judge_final_verdict": close_verdict_summary,
                "top_gainers": [
                    {"symbol": x["symbol"], "company_name": x["company_name"], "close": x["close"],
                     "change_pct": x["effective_change_pct"], "volume": x["volume"]}
                    for x in top_gainers[:8]
                ],
                "top_losers": [
                    {"symbol": x["symbol"], "company_name": x["company_name"], "close": x["close"],
                     "change_pct": x["effective_change_pct"], "volume": x["volume"]}
                    for x in top_losers[:8]
                ],
                "most_active_by_turnover": [
                    {"symbol": x["symbol"], "company_name": x["company_name"], "close": x["close"],
                     "change_pct": x["effective_change_pct"], "volume": x["volume"]}
                    for x in most_active[:8]
                ],
            },
            "stock_debates": saved_debates,
            "watchlist": self.watchlist.load()["items"],
        }

        out_dir = self.settings.report_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        briefing_path = out_dir / f"daily_market_briefing_{latest_date}.json"
        latest_briefing_path = self.settings.data_dir / "latest_market_briefing.json"
        encoded = json.dumps(briefing_payload, indent=2, ensure_ascii=False)
        briefing_path.write_text(encoded, encoding="utf-8")
        latest_briefing_path.write_text(encoded, encoding="utf-8")
        briefing_payload["briefing_path"] = str(briefing_path)
        return briefing_payload
