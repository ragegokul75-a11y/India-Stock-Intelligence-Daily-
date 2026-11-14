from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
from pathlib import Path

from .config import get_settings
from .evidence import EvidenceStore
from .fundamentals import FundamentalAnalyzer
from .ingestion import MarketImporter
from .agents.local_backend import LocalEvidenceAgentBackend
from .agents.sample_backend import SampleAgentBackend
from .logging_config import configure_logging
from .llm.openai_backend import OpenAIResponsesBackend
from .market_intelligence import (
    MarketDebateEngine,
    OpenSourceNSEReportSource,
    OpenSourceNseUdiffParser,
    WatchlistStore,
    fetch_and_import_nse_stock_master,
)
from .migrations import apply_migrations
from .orchestrator import ResearchOrchestrator
from .providers.evidence import ManualEvidenceProvider
from .providers.research import ManualResearchProvider
from .reports import ReportGenerator
from .screening import run_screening
from .storage import connect_read_only, connect_read_write
from .sync import DisabledNSEReportSource, NseSyncService
from .sync.state import SyncStateStore
from .stock_master import SecurityMasterStore


def main(argv=None) -> int:
    settings = get_settings()
    configure_logging(settings.data_dir)
    parser = argparse.ArgumentParser(description="Auditable India EOD stock screening. Exchange downloads are disabled.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Apply explicit, versioned SQLite migrations")
    sub.add_parser("init-db", help="Deprecated alias for migrate")
    imp = sub.add_parser("import", help="Import a manually downloaded CSV, ZIP, XLSX, TSV or TXT file")
    imp.add_argument("file", type=Path)
    imp.add_argument("--exchange", choices=("NSE", "BSE"), help="Assign exchange when the file omits it")
    imp.add_argument("--source", default="Manual file import")
    imp.add_argument("--replace-existing", action="store_true",
                     help="Explicitly replace existing prices and write a correction audit record")
    imp.add_argument("--correction-reason", help="Required explanation when --replace-existing is used")
    sub.add_parser("screen", help="Calculate screening metrics from imported history")
    sync = sub.add_parser("sync", help="Run the disabled-by-default NSE EOD synchronization framework")
    sync.add_argument("--once", action="store_true", help="Make one discovery attempt; do not wait for delayed publication")
    sub.add_parser("sync-status", help="Show the most recent NSE synchronization state")
    report = sub.add_parser("report", help="Generate Excel, PDF, manifest and local email draft")
    report.add_argument("--sample", action="store_true", help="Prominently label reports as synthetic sample data")
    evidence = sub.add_parser("evidence", help="Import manually verified, fully sourced financial or macro evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_import = evidence_sub.add_parser("import", help="Import a provenance-complete JSON evidence file")
    evidence_import.add_argument("file", type=Path)
    master = sub.add_parser("master", help="Manage the manually supplied NSE stock master")
    master_sub = master.add_subparsers(dest="master_command", required=True)
    master_import = master_sub.add_parser("import", help="Import a provenance-complete NSE listing-master JSON file")
    master_import.add_argument("file", type=Path)
    fundamentals = sub.add_parser("fundamentals", help="Read deterministic fundamentals for one stored security")
    fundamentals.add_argument("--security", required=True, help="Security key, e.g. NSE:id:IN1234567890")
    fundamentals.add_argument("--as-of-date", help="YYYY-MM-DD cutoff; defaults to today")
    research = sub.add_parser("research", help="Run the three research agents on the existing local universe")
    selection = research.add_mutually_exclusive_group(required=True)
    selection.add_argument("--security", help="One exchange-specific security key, e.g. NSE:id:...")
    selection.add_argument("--all", action="store_true", help="Process the securities currently stored in SQLite")
    selection.add_argument("--resume-batch", help="Resume a previously interrupted universe batch by batch ID")
    research.add_argument("--sample", action="store_true", help="Run deterministic offline fixtures only on synthetic-marked records")
    research.add_argument("--local", action="store_true", help="Run zero-cost local deterministic three-agent evaluation on sourced evidence")
    research.add_argument("--confirm-paid-api-calls", action="store_true", help="Explicitly approve live paid OpenAI API calls for this run")
    research.add_argument("--max-attempts", type=int, default=1, help="Bounded agent attempts (default 1)")
    research.add_argument("--confirm-paid-retries", action="store_true", help="Explicitly approve additional billable API attempts")
    auto_run = sub.add_parser("auto-run", help="Fully automatic NSE open-source fetch, screening, 3-agent bull/bear/judge debate, and open/close briefing")
    auto_run.add_argument("--days", type=int, default=2, help="Number of recent trading days to sync from NSE open-source archives (default 2)")
    auto_run.add_argument("--top-debate", type=int, default=15, help="Number of top liquid stocks to include alongside watchlist/fundamental stocks in 3-agent debate")
    watch = sub.add_parser("watch", help="Manage the automatic 3-agent stock watchlist")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    watch_add = watch_sub.add_parser("add", help="Add a stock symbol or security_key to the automatic agent watchlist")
    watch_add.add_argument("symbol_or_key", help="NSE symbol (e.g. RELIANCE, TCS, LT, HDFCBANK) or security_key")
    watch_add.add_argument("--reason", default="Added by user for automatic 3-agent daily debate")
    watch_remove = watch_sub.add_parser("remove", help="Remove a stock from the watchlist")
    watch_remove.add_argument("symbol_or_key", help="NSE symbol or security_key")
    watch_recheck = watch_sub.add_parser("recheck", help="Immediately re-check a stock (or all watched stocks) with the 3-agent Bull/Bear/Judge debate")
    watch_recheck.add_argument("symbol_or_key", nargs="?", default="ALL", help="NSE symbol (e.g. RELIANCE, HDFCBANK, INFY) or ALL to re-check all watched stocks")
    watch_sub.add_parser("list", help="List currently watched stocks")
    args = parser.parse_args(argv)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.command in {"migrate", "init-db"}:
        if args.command == "init-db":
            logging.warning("init-db is deprecated; use the explicit migrate command.")
        print(json.dumps(apply_migrations(settings.database_path), indent=2))
        return 0
    if args.command == "import":
        result = MarketImporter(settings=settings).import_file(
            args.file, source_name=args.source, expected_exchange=args.exchange,
            replace_existing=args.replace_existing, correction_reason=args.correction_reason,
        )
        with connect_read_write(settings.database_path) as conn:
            screened = run_screening(conn)
        print(json.dumps({**result.to_dict(), "screening_rows_saved": screened}, indent=2))
        return 0
    if args.command == "screen":
        with connect_read_write(settings.database_path) as conn:
            print(f"Screening rows saved: {run_screening(conn)}")
        return 0
    if args.command == "sync":
        # The built-in source intentionally performs no network access. NSE's
        # current website terms prohibit systematic or automated data collection.
        reason = (
            "BLOCKED: NSE website terms prohibit systematic or automated data collection. "
            "No approved zero-cost unattended NSE access method is configured."
        )
        service = NseSyncService(settings=settings, source=DisabledNSEReportSource(reason))
        result = service.run_once() if args.once else service.run_window()
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"SUCCESS", "WARNING"} else 3
    if args.command == "sync-status":
        store = SyncStateStore(settings.sync_dir, daily_time=settings.nse_sync_daily_time)
        print(json.dumps(store.load(), indent=2))
        return 0
    if args.command == "report":
        result = ReportGenerator(settings=settings).generate(sample_data=args.sample)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "evidence" and args.evidence_command == "import":
        print(json.dumps(ManualEvidenceProvider().import_file(args.file, EvidenceStore(settings=settings)), indent=2))
        return 0
    if args.command == "master" and args.master_command == "import":
        print(json.dumps(ManualResearchProvider().import_stock_master(args.file, SecurityMasterStore(settings=settings)), indent=2))
        return 0
    if args.command == "fundamentals":
        from .evidence import EvidenceRepository
        bundle = EvidenceRepository(settings=settings).build_bundle(args.security, args.as_of_date)
        print(json.dumps(FundamentalAnalyzer().analyze(bundle).to_dict(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "research":
        if not args.sample and not args.local and not args.confirm_paid_api_calls:
            parser.error("research requires --sample, --local, or the explicit --confirm-paid-api-calls flag")
        if not args.sample and not args.local and os.environ.get("INDIA_STOCK_OPENAI_ENABLED", "").lower() != "true":
            parser.error("Live research is disabled. Set INDIA_STOCK_OPENAI_ENABLED=true after approving API usage.")
        if not args.sample and not args.local and (not os.environ.get("OPENAI_API_KEY") or not os.environ.get("INDIA_STOCK_OPENAI_MODEL")):
            parser.error("Set OPENAI_API_KEY and your explicitly chosen INDIA_STOCK_OPENAI_MODEL; the key is never stored by this app.")
        if not args.sample and not args.local and importlib.util.find_spec("openai") is None:
            parser.error("Optional OpenAI SDK is missing. Review cost settings, then install the openai extra explicitly.")
        if args.max_attempts > 1 and not args.sample and not args.local and not args.confirm_paid_retries:
            parser.error("--max-attempts above 1 may incur additional calls; also pass --confirm-paid-retries")
        if args.sample:
            backend = SampleAgentBackend()
        elif args.local:
            backend = LocalEvidenceAgentBackend()
        else:
            backend = OpenAIResponsesBackend(approved_for_this_run=args.confirm_paid_api_calls)
        orchestrator = ResearchOrchestrator(settings=settings, backend=backend,
                                             max_attempts=args.max_attempts,
                                             allow_paid_retries=args.confirm_paid_retries or args.sample or args.local)
        if args.resume_batch:
            results = orchestrator.run_universe(
                None, sample_only=False, batch_run_id=args.resume_batch, resume=True
            )
        else:
            results = orchestrator.run_universe(
                None if args.all else [args.security],
                sample_only=args.sample and args.all,
            )
        report_result = None
        with connect_read_only(settings.database_path) as conn:
            has_prices = conn.execute("SELECT 1 FROM daily_prices WHERE close IS NOT NULL LIMIT 1").fetchone() is not None
        if has_prices:
            report_result = ReportGenerator(settings=settings).generate(sample_data=args.sample)
        print(json.dumps({"research_runs": [result.__dict__ for result in results],
                          "universe_manifest": orchestrator.last_universe_manifest,
                          "market_report": report_result}, indent=2))
        batch_failed = bool(orchestrator.last_universe_manifest.get("failed_security_ids"))
        return 0 if not batch_failed and all(result.status == "completed" for result in results) else 2
    if args.command == "watch":
        store = WatchlistStore(settings=settings)
        if args.watch_command == "list":
            print(json.dumps(store.load(), indent=2, ensure_ascii=False))
            return 0
        target = args.symbol_or_key.strip().upper()
        sec_key = target
        symbol = target
        company_name = None
        if settings.database_path.exists():
            with connect_read_only(settings.database_path) as conn:
                row = conn.execute(
                    "SELECT security_key, symbol, company_name FROM securities WHERE security_key=? OR upper(symbol)=? ORDER BY exchange LIMIT 1",
                    (target, target),
                ).fetchone()
                if row:
                    sec_key = row["security_key"]
                    symbol = row["symbol"]
                    company_name = row["company_name"]
        if args.watch_command == "add":
            updated = store.add(sec_key, symbol=symbol, company_name=company_name, added_by="user", reason=args.reason)
            if settings.database_path.exists():
                try:
                    MarketDebateEngine(settings=settings).recheck_stock(sec_key, add_to_watchlist=True, reason=args.reason)
                except Exception as exc:
                    logging.warning("Queued %s on watchlist (immediate run skipped: %s)", sec_key, exc)
            print(json.dumps(store.load(), indent=2, ensure_ascii=False))
            return 0
        if args.watch_command == "recheck":
            engine = MarketDebateEngine(settings=settings)
            if target == "ALL":
                briefing = engine.generate_briefings(top_n_debate=15)
                print(json.dumps({"rechecked": "ALL", "debated_stocks": len(briefing.get("stock_debates", []))}, indent=2, ensure_ascii=False))
            else:
                res = engine.recheck_stock(sec_key, add_to_watchlist=True, reason="Re-checked by user")
                print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if args.watch_command == "remove":
            print(json.dumps(store.remove(sec_key), indent=2, ensure_ascii=False))
            return 0
    if args.command == "auto-run":
        from dataclasses import replace as dc_replace
        apply_migrations(settings.database_path)
        master_res = fetch_and_import_nse_stock_master(settings)
        stage4d_ev = Path(__file__).resolve().parents[2] / "stage4d" / "inputs" / "stage4d_financial_evidence.json"
        if stage4d_ev.exists():
            EvidenceStore(settings=settings).import_json_file(stage4d_ev)
        sync_settings = dc_replace(settings, nse_sync_enabled=True)
        sync_service = NseSyncService(
            settings=sync_settings,
            source=OpenSourceNSEReportSource(lookback_days=12, max_trading_days=args.days),
            parser=OpenSourceNseUdiffParser(),
        )
        sync_res = sync_service.run_once()
        with connect_read_write(settings.database_path) as conn:
            screened = run_screening(conn)
        briefing = MarketDebateEngine(settings=settings).generate_briefings(top_n_debate=args.top_debate)
        reports = ReportGenerator(settings=settings).generate(sample_data=False)
        print(json.dumps({
            "master_imported": master_res["records_read"],
            "sync_status": sync_res["status"],
            "latest_trading_date": sync_res.get("latest_trading_date"),
            "imported_price_rows": sync_res.get("imported_rows"),
            "screened_securities": screened,
            "market_stance": briefing["market_open_expectation"]["stance"],
            "debated_stocks": len(briefing["stock_debates"]),
            "briefing_path": briefing["briefing_path"],
            "reports": reports["files"],
        }, indent=2, ensure_ascii=False))
        return 0
    logging.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
