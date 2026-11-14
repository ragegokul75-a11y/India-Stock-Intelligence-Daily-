from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
from collections import Counter
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import Settings, get_settings
from .evidence import EvidenceRepository
from .fundamentals import FundamentalAnalyzer
from .ingestion import MarketImporter
from .screening import run_screening
from .stock_master import SecurityMasterRepository
from .storage import connect_read_write, json_text

logger = logging.getLogger(__name__)
SAMPLE_LABEL = "SAMPLE DATA - SYNTHETIC; NOT EXCHANGE DATA"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_write_locked(path: Path) -> bool:
    """Avoid overwriting a report currently open in Windows Excel or a PDF viewer."""
    if not path.exists():
        return False
    try:
        with path.open("r+b"):
            return False
    except PermissionError:
        return True


class ReportGenerator:
    def __init__(self, database_path=None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.database_path = Path(database_path or self.settings.database_path)

    def generate(self, sample_data: bool = False, failed_downloads: list[str] | None = None) -> dict:
        self.settings.report_dir.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now(timezone.utc).isoformat()
        report_as_of_date = date.today().isoformat()
        with connect_read_write(self.database_path) as conn:
            date_row = conn.execute("SELECT MAX(price_date) AS d FROM daily_prices WHERE close IS NOT NULL").fetchone()
            data_date = date_row["d"]
            if data_date is not None:
                MarketImporter(database_path=self.database_path, settings=self.settings).check_staleness(
                    conn, generated_at, report_as_of_date)
                run_screening(conn)
            source_names = [row["source_name"] for row in conn.execute(
                "SELECT DISTINCT source_name FROM source_records ORDER BY source_name").fetchall()]
            source_names.extend(row["source_name"] for row in conn.execute(
                """SELECT DISTINCT source_name FROM evidence_items
                   WHERE publication_date<=? AND substr(retrieved_at,1,10)<=? ORDER BY source_name""",
                (report_as_of_date, report_as_of_date)).fetchall())
            source_names = sorted(set(source_names))
            research_rows = self._latest_research_rows(conn)
            master_rows = SecurityMasterRepository.rows_from_connection(conn)
            fundamentals_rows = self._fundamental_rows(conn, report_as_of_date, master_rows)
            is_sample = (sample_data or any("sample" in name.lower() or "synthetic" in name.lower() for name in source_names)
                         or any(row["sample_data"] for row in research_rows)
                         or any(row["sample_data"] for row in fundamentals_rows))
            by_exchange = {row["exchange"]: {"securities": row["securities"], "prices": row["prices"]}
                           for row in conn.execute(
                               """SELECT s.exchange, COUNT(DISTINCT s.security_key) securities,
                                          COUNT(p.price_id) prices FROM securities s
                                   LEFT JOIN daily_prices p ON p.security_key=s.security_key GROUP BY s.exchange""")}
            coverage = {
                "securities": conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0],
                "price_records": conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0],
                "exchanges": by_exchange,
                "listing_segments": dict(Counter(row["listing_segment"] for row in master_rows)),
                "fundamental_metrics_by_status": dict(Counter(row["data_status"] for row in fundamentals_rows)),
            }
            manifest = {
                "report_version": 2,
                "data_date": data_date,
                "market_data_date": data_date,
                "report_as_of_date": report_as_of_date,
                "retrieval_timestamp": generated_at,
                "sources": source_names,
                "coverage": coverage,
                "failed_downloads": failed_downloads or [],
                "sample_data": is_sample,
                "sample_label": SAMPLE_LABEL if is_sample else None,
                "paid_data_providers": False,
                "automated_exchange_downloads": False,
                "fundamental_metrics": "Deterministic, provenance-linked snapshot; missing and conflicting values remain null.",
                "research_coverage": {
                    "securities_with_runs": len(research_rows),
                    "completed_runs": sum(1 for row in research_rows if row["status"] == "completed"),
                    "verdict_counts": {verdict: sum(1 for row in research_rows if row.get("verdict") == verdict)
                                       for verdict in ("BUY_CANDIDATE", "HOLD", "WATCH", "AVOID", "INCONCLUSIVE")},
                    "research_run_ids": [row["research_run_id"] for row in research_rows],
                    "sample_runs": sum(1 for row in research_rows if row["sample_data"]),
                },
            }
            metrics = conn.execute(
                """SELECT s.exchange, s.symbol, s.company_name, s.security_identifier, s.security_type,
                          s.listing_status, p.price_date AS latest_price_date, p.close AS latest_close,
                          r.return_1d_pct, r.return_20d_pct,
                          r.return_60d_pct, r.return_252d_pct, r.ma_50, r.ma_200, r.volatility_20d_pct,
                          r.max_drawdown_252d_pct, r.volume_change_20d_pct
                   FROM securities s LEFT JOIN daily_prices p ON p.security_key=s.security_key
                        AND p.price_date=(SELECT MAX(p2.price_date) FROM daily_prices p2
                                          WHERE p2.security_key=s.security_key AND p2.close IS NOT NULL AND p2.price_date<=?)
                   LEFT JOIN screening_results r ON r.security_key=s.security_key AND r.data_date=?
                   ORDER BY s.exchange, s.symbol""", (data_date, data_date)).fetchall()
            quality = conn.execute(
                "SELECT check_type, severity, security_key, record_date, details, source_file FROM data_quality_checks ORDER BY check_id DESC LIMIT 1000"
            ).fetchall()
            data_rows = [dict(row) for row in metrics]
            quality_rows = []
            quality_seen = set()
            security_labels = {row["security_key"]: f"{row['exchange']}:{row['symbol']}"
                               for row in conn.execute("SELECT security_key, exchange, symbol FROM securities")}
            for row in quality:
                item = dict(row)
                identity = (item["check_type"], item["security_key"], item["record_date"], item["details"])
                if identity not in quality_seen:
                    quality_seen.add(identity)
                    item["security_key"] = security_labels.get(item["security_key"], item["security_key"])
                    quality_rows.append(item)

            output_date = data_date or report_as_of_date
            base = self.settings.report_dir / f"india_stock_report_{output_date}"
            if is_sample:
                base = self.settings.report_dir / f"SAMPLE_india_stock_report_{output_date}"
            if _is_write_locked(base.with_suffix(".xlsx")) or _is_write_locked(base.with_suffix(".pdf")):
                # Excel and PDF viewers often lock a report while the user reads it.
                # Preserve that open file and write this refreshed report as a version.
                suffix = datetime.now(timezone.utc).strftime("%H%M%S")
                base = base.with_name(f"{base.name}_{suffix}UTC")
            xlsx_path = base.with_suffix(".xlsx")
            pdf_path = base.with_suffix(".pdf")
            manifest_path = base.with_name(base.name + "_manifest.json")
            eml_path = base.with_name(base.name + "_email_ready.eml")
            manifest["report_files"] = {"excel": str(xlsx_path), "pdf": str(pdf_path), "manifest": str(manifest_path), "email_draft": str(eml_path)}
            self._write_excel(xlsx_path, output_date, is_sample, manifest, data_rows, quality_rows,
                              research_rows, master_rows, fundamentals_rows)
            self._write_pdf(pdf_path, output_date, is_sample, manifest, data_rows, quality_rows,
                            research_rows, master_rows, fundamentals_rows)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            self._write_email_draft(eml_path, output_date, is_sample, manifest, xlsx_path, pdf_path)
            for report_type, path in (("excel", xlsx_path), ("pdf", pdf_path), ("manifest", manifest_path), ("email_draft", eml_path)):
                conn.execute(
                    "INSERT INTO generated_reports (report_type,file_path,sha256,data_date,generated_at,manifest_json) VALUES (?,?,?,?,?,?)",
                    (report_type, str(path), _sha(path), data_date, generated_at, json_text(manifest)),
                )
            conn.commit()
        logger.info("Generated reports for %s at %s", data_date, self.settings.report_dir)
        return {**manifest, "files": {"excel": str(xlsx_path), "pdf": str(pdf_path), "manifest": str(manifest_path), "email_draft": str(eml_path)}}

    def _fundamental_rows(self, conn, as_of_date: str, master_rows: list[dict]) -> list[dict]:
        repository = EvidenceRepository(self.database_path, self.settings)
        analyzer = FundamentalAnalyzer()
        rows = []
        for security in master_rows:
            bundle = repository.build_bundle_from_connection(conn, security["security_key"], as_of_date)
            snapshot = analyzer.analyze(bundle)
            for metric in snapshot.metrics:
                rows.append({
                    "security_key": security["security_key"],
                    "exchange": security["exchange"],
                    "symbol": security["symbol"],
                    "company_name": security["company_name"],
                    "listing_segment": security["listing_segment"],
                    "metric_key": metric.metric_key,
                    "metric": metric.label,
                    "value": metric.value,
                    "unit": metric.unit,
                    "reporting_period": metric.reporting_period,
                    "financial_basis": metric.financial_basis,
                    "data_status": metric.data_status,
                    "evidence_ids": "; ".join(metric.evidence_ids),
                    "sources_json": json.dumps(metric.sources, ensure_ascii=False),
                    "source_urls": "; ".join(source["source_url"] for source in metric.sources),
                    "retrieved_at": "; ".join(source["retrieved_at"] for source in metric.sources),
                    "note": metric.note,
                    "sample_data": snapshot.sample_data,
                })
        return rows

    @staticmethod
    def _latest_research_rows(conn) -> list[dict]:
        runs = conn.execute(
            """SELECT r.*, s.exchange, s.symbol, s.company_name FROM research_runs r
               JOIN securities s ON s.security_key=r.security_key
               WHERE r.research_run_id=(SELECT r2.research_run_id FROM research_runs r2
                                        WHERE r2.security_key=r.security_key
                                        ORDER BY r2.started_at DESC LIMIT 1)
               ORDER BY s.exchange,s.symbol"""
        ).fetchall()
        report_paths = {}
        for item in conn.execute("SELECT file_path,manifest_json FROM generated_reports WHERE report_type='ai_research_json'"):
            try:
                report_paths[json.loads(item["manifest_json"])["research_run_id"]] = item["file_path"]
            except (KeyError, json.JSONDecodeError):
                continue
        result = []
        for run in runs:
            item = dict(run)
            outputs = {}
            for output in conn.execute(
                """SELECT agent_id,output_json FROM agent_outputs
                   WHERE research_run_id=? AND status='completed' ORDER BY attempt DESC""", (item["research_run_id"],)
            ):
                if output["agent_id"] not in outputs:
                    try:
                        outputs[output["agent_id"]] = json.loads(output["output_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
            item["evidence_coverage"] = json.loads(item["evidence_coverage_json"] or "{}")
            item["changes"] = json.loads(item["changes_json"] or "{}")
            item["agent_reports"] = outputs
            item["research_json_path"] = report_paths.get(item["research_run_id"])
            item["sample_data"] = bool(item["sample_data"])
            result.append(item)
        return result

    def _write_excel(self, path: Path, data_date: str, is_sample: bool, manifest: dict,
                     data_rows: list[dict], quality_rows: list[dict], research_rows: list[dict],
                     master_rows: list[dict], fundamentals_rows: list[dict]):
        workbook = Workbook()
        summary = workbook.active
        summary.title = "Read Me"
        summary.append([SAMPLE_LABEL if is_sample else "India Stock Intelligence — Research Report"])
        summary.append(["Market data date", manifest["market_data_date"] or "No valid close-price observations stored"])
        summary.append(["Report as of", manifest["report_as_of_date"]])
        summary.append(["Retrieval timestamp (UTC)", manifest["retrieval_timestamp"]])
        summary.append(["Sources", "; ".join(manifest["sources"]) or "None recorded"])
        summary.append(["Coverage", json.dumps(manifest["coverage"], ensure_ascii=False)])
        summary.append(["Failed downloads", "; ".join(manifest["failed_downloads"]) or "None"])
        summary.append(["Data note", "Fundamental values carry source URLs, periods, retrieval timestamps and status. Missing/conflicting values remain blank. Verified is a user-entered label, not automatic source authentication. No values are fabricated."])
        summary["A1"].font = Font(bold=True, color="FFFFFF", size=14)
        summary["A1"].fill = PatternFill("solid", fgColor="9C0006" if is_sample else "17365D")
        summary.merge_cells("A1:B1")
        summary.column_dimensions["A"].width = 28
        summary.column_dimensions["B"].width = 86
        summary.row_dimensions[1].height = 26
        for row_number in range(2, summary.max_row + 1):
            summary.cell(row_number, 1).font = Font(bold=True, color="17365D")
            for cell in summary[row_number]:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        summary.row_dimensions[6].height = 48
        summary.row_dimensions[8].height = 48
        summary.sheet_view.showGridLines = False
        for sheet_name, rows in (("Stock Master", master_rows), ("Fundamentals", fundamentals_rows),
                                 ("Screening", data_rows), ("Data Quality", quality_rows)):
            self._write_excel_table(workbook, sheet_name, rows)
        research_sheet = workbook.create_sheet("Agent Research")
        research_headers = ["Exchange", "Symbol", "Company", "As of", "Run status", "Verdict", "Risk score",
                            "Confidence", "Watchlist action", "Missing evidence", "Changes from previous",
                            "Equity analyst summary", "Bull/Bear summary", "Judge reasoning", "JSON report path", "Synthetic sample"]
        research_sheet.append(research_headers)
        for row in research_rows:
            reports = row["agent_reports"]
            missing_categories = row["evidence_coverage"].get("missing_categories", [])
            missing_evidence = "; ".join(category.replace("_", " ") for category in missing_categories) or "None"
            changes = row["changes"]
            if not changes.get("previous_run_id"):
                changes_summary = "Initial report"
            else:
                changes_summary = (
                    f"Verdict changed: {changes.get('verdict_changed')}; "
                    f"risk delta: {changes.get('risk_score_delta')}; "
                    f"confidence delta: {changes.get('confidence_score_delta')}"
                )
            research_sheet.append([self._excel_safe(value) for value in (
                row["exchange"], row["symbol"], row["company_name"], row["as_of_date"], row["status"],
                row["verdict"], row["risk_score"], row["confidence_score"], row["watchlist_action"],
                missing_evidence, changes_summary,
                (reports.get("equity_research_analyst") or {}).get("executive_summary"),
                (reports.get("bull_bear_analyst") or {}).get("summary"),
                (reports.get("independent_judge") or {}).get("independent_reasoning"),
                row["research_json_path"], "Yes" if row["sample_data"] else "No",
            )])
        detailed_outputs = []
        for row in research_rows:
            for agent_id, output in row["agent_reports"].items():
                detailed_outputs.append({
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "company_name": row["company_name"],
                    "as_of_date": row["as_of_date"],
                    "research_run_id": row["research_run_id"],
                    "agent_id": agent_id,
                    "sample_data": row["sample_data"],
                    "output_json": json.dumps(output, ensure_ascii=False),
                })
        self._write_excel_table(workbook, "Agent Outputs", detailed_outputs)
        for cell in research_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17365D")
        research_sheet.freeze_panes = "A2"
        research_sheet.auto_filter.ref = research_sheet.dimensions
        research_sheet.sheet_view.showGridLines = False
        for column in research_sheet.columns:
            width = min(40, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
            research_sheet.column_dimensions[get_column_letter(column[0].column)].width = width
            for cell in column:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        workbook.save(path)

    def _write_excel_table(self, workbook, sheet_name: str, rows: list[dict]) -> None:
        sheet = workbook.create_sheet(sheet_name)
        if not rows:
            sheet.append(["No records"])
            return
        headers = list(rows[0].keys())
        sheet.append(headers)
        for row in rows:
            sheet.append([self._excel_safe(row.get(header)) for header in headers])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17365D")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            width = min(48, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
            sheet.column_dimensions[get_column_letter(column[0].column)].width = width
            for cell in column:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    @staticmethod
    def _excel_safe(value):
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value

    def _write_pdf(self, path: Path, data_date: str, is_sample: bool, manifest: dict,
                   data_rows: list[dict], quality_rows: list[dict], research_rows: list[dict],
                   master_rows: list[dict], fundamentals_rows: list[dict]):
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], textColor=colors.HexColor("#9C0006" if is_sample else "#17365D"))
        section_heading = ParagraphStyle("SectionHeading", parent=styles["Heading1"], keepWithNext=True)
        security_heading = ParagraphStyle("SecurityHeading", parent=styles["Heading2"], keepWithNext=True)
        small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=7, leading=9, alignment=TA_LEFT)
        story = [Paragraph(SAMPLE_LABEL if is_sample else "India Stock Intelligence - Stock Research", title_style),
                 Paragraph(f"Market data date: {manifest['market_data_date'] or 'None'} | Report as of: {manifest['report_as_of_date']} | Retrieved (UTC): {manifest['retrieval_timestamp']}", styles["Normal"]),
                 Spacer(1, 8), Paragraph(f"Sources: {', '.join(manifest['sources']) or 'None recorded'}", styles["Normal"]),
                 Paragraph("Coverage: " + json.dumps(manifest["coverage"], ensure_ascii=False), small),
                 Paragraph("Fundamental values include source evidence and status. Missing or conflicting values remain blank. 'Verified' is a user-entered label and is not an independent authenticity check.", styles["Normal"]), Spacer(1, 10),
                 Paragraph("Stock Master", styles["Heading1"])]
        master_data = [["Exchange", "Symbol", "Company", "ISIN", "Segment", "Status", "Source"]]
        for row in master_rows:
            master_values = [Paragraph(escape(str(row.get(key) or "-")), small) for key in
                             ("exchange", "symbol", "company_name", "isin", "listing_segment", "security_status")]
            source_url = escape(str(row.get("source_url") or ""), {"\"": "&quot;"})
            source_name = escape(str(row.get("source_name") or "-"))
            master_values.append(Paragraph(f'<link href="{source_url}">{source_name}</link>' if source_url else source_name, small))
            master_data.append(master_values)
        if len(master_data) == 1:
            master_data.append(["No stock master records", "", "", "", "", "", ""])
        master_table = Table(master_data, repeatRows=1, colWidths=[0.62*inch, 0.85*inch, 2.1*inch, 1.1*inch, 0.85*inch, 0.85*inch, 2.3*inch])
        master_table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
                                          ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                                          ("FONTSIZE", (0,0), (-1,-1), 7), ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
                                          ("VALIGN", (0,0), (-1,-1), "TOP")] ))
        story.extend([master_table, Spacer(1, 10)])
        fundamentals_by_security = {}
        for row in fundamentals_rows:
            fundamentals_by_security.setdefault(row["security_key"], []).append(row)
        if not fundamentals_by_security:
            story.extend([Paragraph("Fundamental Metrics and Evidence", section_heading),
                          Paragraph("No stock master records are available, so no per-security fundamental metrics could be evaluated.", styles["Normal"])])
        first_fundamental_group = True
        for security_key, rows in fundamentals_by_security.items():
            first = rows[0]
            group = []
            if first_fundamental_group:
                group.append(Paragraph("Fundamental Metrics and Evidence", section_heading))
                first_fundamental_group = False
            group.append(Paragraph(escape(f"{first['exchange']}:{first['symbol']} — {first['company_name'] or ''} ({first['listing_segment']})"), security_heading))
            metric_data = [["Metric", "Value", "Period / status", "Source evidence", "Analysis note"]]
            for row in rows:
                value = "—" if row["value"] is None else f"{row['value']} {row['unit'] or ''}".strip()
                period_status = f"{row['reporting_period'] or '—'} / {row['financial_basis']} / {row['data_status']}"
                try:
                    sources = json.loads(row["sources_json"] or "[]")
                except json.JSONDecodeError:
                    sources = []
                source_labels = []
                for source in sources:
                    url = escape(str(source.get("source_url") or ""), {"\"": "&quot;"})
                    label = escape(f"{source.get('source_name') or 'Source'} [{source.get('evidence_id') or ''}]")
                    source_labels.append(f'<link href="{url}">{label}</link>')
                evidence_text = "<br/>".join(source_labels) if source_labels else "No source value"
                metric_data.append([Paragraph(escape(str(row["metric"])), small),
                                    Paragraph(escape(value), small),
                                    Paragraph(escape(period_status), small),
                                    Paragraph(evidence_text, small),
                                    Paragraph(escape(str(row["note"] or "")), small)])
            metric_table = Table(metric_data, repeatRows=1, colWidths=[1.4*inch, 0.85*inch, 1.15*inch, 2.6*inch, 2.6*inch])
            metric_table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
                                              ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                                              ("FONTSIZE", (0,0), (-1,-1), 7), ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
                                              ("VALIGN", (0,0), (-1,-1), "TOP"),
                                              ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2),
                                              ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#EEF3F8")])]))
            group.append(metric_table)
            story.append(KeepTogether(group))
            story.append(Spacer(1, 10))
        story.extend([Paragraph("Market Screening", section_heading)])
        if manifest["market_data_date"] is None:
            story.append(Paragraph("No valid close-price observations are stored. Return and price-screening metrics are unavailable.", styles["Normal"]))
        columns = ["exchange", "symbol", "company_name", "latest_price_date", "latest_close", "return_20d_pct", "return_60d_pct", "ma_50", "ma_200", "volatility_20d_pct", "max_drawdown_252d_pct"]
        headings = ["Exchange", "Symbol", "Company", "Price date", "Close", "20d %", "60d %", "MA50", "MA200", "Vol 20d", "DD 252d"]
        def display_value(value):
            if value is None:
                return "-"
            if isinstance(value, float):
                return f"{value:.2f}"
            return str(value)
        if manifest["market_data_date"] is not None:
            table_data = [headings]
            for row in data_rows:
                table_data.append([Paragraph(display_value(row.get(column)), small) for column in columns])
            table = Table(table_data, repeatRows=1, colWidths=[0.6*inch, 0.85*inch, 1.55*inch, 0.82*inch, 0.6*inch, 0.62*inch, 0.62*inch, 0.7*inch, 0.7*inch, 0.72*inch, 0.72*inch])
            table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
                                       ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                                       ("FONTSIZE", (0,0), (-1,-1), 7), ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
                                       ("VALIGN", (0,0), (-1,-1), "TOP"), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#EEF3F8")])]))
            story.append(table)
        story.extend([Spacer(1, 10), Paragraph("Recent data quality flags", styles["Heading2"])])
        quality_data = [["Type", "Severity", "Security", "Date", "Details"]]
        for row in quality_rows[:60]:
            quality_data.append([Paragraph(str(row.get("check_type") or "").replace("_", " "), small),
                                 Paragraph(str(row.get("severity") or ""), small),
                                 Paragraph(str(row.get("security_key") or ""), small),
                                 Paragraph(str(row.get("record_date") or ""), small),
                                 Paragraph(str(row.get("details") or ""), small)])
        if len(quality_data) == 1:
            quality_data.append(["No flags", "", "", "", ""])
        qtable = Table(quality_data, repeatRows=1, colWidths=[1.05*inch, 0.6*inch, 1.3*inch, 0.85*inch, 4.2*inch])
        qtable.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
                                    ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                                    ("FONTSIZE", (0,0), (-1,-1), 7), ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
                                    ("VALIGN", (0,0), (-1,-1), "TOP")]))
        story.append(qtable)
        if research_rows:
            story.extend([PageBreak(), Paragraph("Fundamental Research and Independent Review", styles["Heading1"]),
                          Paragraph("Coverage counts only securities with a saved agent run. Partial evidence is shown explicitly; sample runs are synthetic.", styles["Normal"]),
                          Spacer(1, 8)])
            for row in research_rows:
                reports = row["agent_reports"]
                judge = reports.get("independent_judge") or {}
                analyst = reports.get("equity_research_analyst") or {}
                bull_bear = reports.get("bull_bear_analyst") or {}
                missing_categories = row["evidence_coverage"].get("missing_categories", [])
                missing_text = ", ".join(category.replace("_", " ") for category in missing_categories) or "None"
                risk_text = "Not scored" if row["risk_score"] is None else str(row["risk_score"])
                confidence_text = "-" if row["confidence_score"] is None else str(row["confidence_score"])
                def claim_text(output, field):
                    claims = output.get(field) or []
                    values = [str(item.get("claim", "")) for item in claims if isinstance(item, dict) and item.get("claim")]
                    return "; ".join(values) or "No evidence-supported observations."
                lines = [
                    Paragraph(escape(f"{row['exchange']}:{row['symbol']} - {row['verdict'] or row['status']}"), styles["Heading2"]),
                    Paragraph(escape(f"As of {row['as_of_date']} | Risk score: {risk_text} | Confidence: {confidence_text} | Watchlist: {row['watchlist_action'] or 'NOT_SET'}"), small),
                    Paragraph("Equity research: " + escape((analyst.get("executive_summary", "No completed analyst report."))[:700]), small),
                    Paragraph("Business overview: " + escape(claim_text(analyst, "business_overview")[:500]), small),
                    Paragraph("Financial performance: " + escape(claim_text(analyst, "financial_performance")[:500]), small),
                    Paragraph("Strengths: " + escape(claim_text(analyst, "strengths")[:400]), small),
                    Paragraph("Weaknesses: " + escape(claim_text(analyst, "weaknesses")[:400]), small),
                    Paragraph("Investment thesis: " + escape(claim_text(analyst, "investment_thesis")[:500]), small),
                    Paragraph("Bull/Bear: " + escape((bull_bear.get("summary", "No completed challenge report."))[:700]), small),
                    Paragraph("Contradictory evidence: " + escape(claim_text(bull_bear, "contradictory_evidence")[:500]), small),
                    Paragraph("Independent Judge: " + escape((judge.get("independent_reasoning", "No completed judge report."))[:700]), small),
                    Paragraph("Missing evidence categories: " + escape(missing_text), small),
                    Paragraph("Changes from previous: " + escape(json.dumps(row["changes"], ensure_ascii=False)[:500]), small),
                    Spacer(1, 8),
                ]
                story.append(KeepTogether(lines))
        document = SimpleDocTemplate(str(path), pagesize=landscape(letter), rightMargin=0.35*inch, leftMargin=0.35*inch,
                                     topMargin=0.35*inch, bottomMargin=0.4*inch,
                                      title=f"India Stock Intelligence Research Report {data_date}",
                                     author="India Stock Intelligence")
        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(colors.HexColor("#5B6573"))
            canvas.drawString(doc.leftMargin, 0.18*inch, f"Report as of: {manifest['report_as_of_date']} | {SAMPLE_LABEL if is_sample else 'Stock research'}")
            canvas.drawRightString(landscape(letter)[0] - doc.rightMargin, 0.18*inch, f"Page {doc.page}")
            canvas.restoreState()
        document.build(story, onFirstPage=footer, onLaterPages=footer)

    def _write_email_draft(self, path: Path, data_date: str, is_sample: bool, manifest: dict, xlsx: Path, pdf: Path):
        message = EmailMessage()
        message["Subject"] = f"{'[SAMPLE] ' if is_sample else ''}India Stock Research Report — {data_date}"
        message["To"] = ""
        message["X-India-Stock-Email-Status"] = "Draft only; no email provider configured and nothing sent"
        label = SAMPLE_LABEL if is_sample else "India Stock Intelligence - Stock Research"
        body = (f"{label}\nMarket data date: {manifest['market_data_date'] or 'None'}\nReport as of: {manifest['report_as_of_date']}\nSources: {', '.join(manifest['sources'])}\n"
                f"Coverage: {json.dumps(manifest['coverage'], ensure_ascii=False)}\n\n"
                "Excel and PDF reports are attached to this local draft. No email was sent.")
        message.set_content(body)
        for attachment in (xlsx, pdf):
            mime, _ = mimetypes.guess_type(attachment.name)
            maintype, subtype = (mime or "application/octet-stream").split("/", 1)
            message.add_attachment(attachment.read_bytes(), maintype=maintype, subtype=subtype, filename=attachment.name)
        path.write_bytes(message.as_bytes())
