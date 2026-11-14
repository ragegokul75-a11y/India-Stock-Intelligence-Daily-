from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import streamlit as st

from india_stock_intelligence.agents.local_backend import LocalEvidenceAgentBackend
from india_stock_intelligence.agents.sample_backend import SampleAgentBackend
from india_stock_intelligence.config import get_settings
from india_stock_intelligence.evidence import EvidenceRepository, EvidenceStore
from india_stock_intelligence.fundamentals import FundamentalAnalyzer
from india_stock_intelligence.ingestion import MarketImporter
from india_stock_intelligence.logging_config import configure_logging
from india_stock_intelligence.market_intelligence import (
    MarketDebateEngine,
    OpenSourceNSEReportSource,
    OpenSourceNseUdiffParser,
    WatchlistStore,
    fetch_and_import_nse_stock_master,
)
from india_stock_intelligence.migrations import apply_migrations
from india_stock_intelligence.orchestrator import ResearchOrchestrator
from india_stock_intelligence.providers.bse import BSEProvider
from india_stock_intelligence.providers.nse import NSEProvider
from india_stock_intelligence.providers.research import ManualResearchProvider
from india_stock_intelligence.reports import ReportGenerator
from india_stock_intelligence.screening import run_screening
from india_stock_intelligence.storage import connect_read_only, connect_read_write
from india_stock_intelligence.stock_master import SecurityMasterRepository, SecurityMasterStore
from india_stock_intelligence.sync.state import SyncStateStore

settings = get_settings()
configure_logging(settings.data_dir)
st.set_page_config(page_title="India Stock Intelligence Daily", layout="wide", initial_sidebar_state="collapsed")
st.title("📊 India Stock Intelligence Daily")
st.caption("100% Automatic NSE Market Intelligence: Bull Agent vs. Bear Agent → Judge Agent, Market Open Expectations, Market Close Review & Watchlist (Desktop + Mobile Ready).")

briefing_file = settings.data_dir / "latest_market_briefing.json"
watchlist_store = WatchlistStore(settings=settings)


def _run_full_auto_pipeline() -> None:
    from dataclasses import replace as dc_replace
    from india_stock_intelligence.sync.service import NseSyncService
    apply_migrations(settings.database_path)
    fetch_and_import_nse_stock_master(settings)
    stage4d_ev = ROOT / "stage4d" / "inputs" / "stage4d_financial_evidence.json"
    if stage4d_ev.exists():
        EvidenceStore(settings=settings).import_json_file(stage4d_ev)
    sync_service = NseSyncService(
        settings=dc_replace(settings, nse_sync_enabled=True),
        source=OpenSourceNSEReportSource(lookback_days=12, max_trading_days=2),
        parser=OpenSourceNseUdiffParser(),
    )
    sync_service.run_once()
    with connect_read_write(settings.database_path) as conn:
        run_screening(conn)
    MarketDebateEngine(settings=settings).generate_briefings(top_n_debate=15)
    ReportGenerator(settings=settings).generate(sample_data=False)


# Automatic cloud bootstrap if deployed on a fresh container without database/briefing
if not settings.database_path.exists() or not briefing_file.exists():
    with st.spinner("First-time cloud/server startup: automatically fetching open-source NSE market data and running Bull/Bear/Judge agents..."):
        try:
            _run_full_auto_pipeline()
        except Exception as boot_exc:
            apply_migrations(settings.database_path)
            st.warning(f"Initial auto-sync notice: {boot_exc}")

# Load all NSE symbols for fast mobile dropdown search
all_nse_options: list[str] = []
symbol_map: dict[str, str] = {}
if settings.database_path.exists():
    try:
        with connect_read_only(settings.database_path) as conn:
            sec_rows = conn.execute(
                "SELECT security_key, symbol, company_name FROM securities WHERE security_key NOT LIKE '%SYNTH%' ORDER BY symbol"
            ).fetchall()
            for r in sec_rows:
                label = f"{r['symbol']} — {r['company_name'] or r['symbol']}"
                all_nse_options.append(label)
                symbol_map[label] = r["symbol"]
    except Exception:
        pass

# Top Action Bar (Mobile & Desktop friendly)
with st.container(border=True):
    st.markdown("#### ⚡ Quick Actions (Add Stock, Re-Check 3 Agents, or Refresh Full Market)")
    qa1, qa2, qa3 = st.columns([2, 1, 1])
    with qa1:
        selected_stock_label = st.selectbox(
            "🔍 Search & Pick Any NSE Stock (3,500+ Stocks)",
            options=[""] + all_nse_options,
            index=0,
            placeholder="Type symbol or company name (e.g. INFY, SBIN, TATAMOTORS, RELIANCE)...",
            key="top_stock_picker",
        )
        custom_sym = st.text_input(
            "Or type NSE symbol directly",
            placeholder="e.g. HDFCBANK, ITC, BAJFINANCE",
            key="top_custom_symbol",
        )
    target_sym = custom_sym.strip().upper() if custom_sym.strip() else symbol_map.get(selected_stock_label, "")
    with qa2:
        st.write("")
        st.write("")
        if st.button("➕ Add & 🔄 Re-Check This Stock Now", type="primary", use_container_width=True, disabled=not bool(target_sym)):
            with st.spinner(f"Running Bull Agent vs. Bear Agent → Judge Agent on {target_sym}..."):
                res = MarketDebateEngine(settings=settings).recheck_stock(
                    target_sym,
                    add_to_watchlist=True,
                    reason="Added & re-checked via Quick Action bar",
                )
                st.session_state["last_rechecked_result"] = res
                st.rerun()
        if st.button("🔄 Re-Check All Watched Stocks Now", use_container_width=True):
            with st.spinner("Re-checking all watched stocks and top market movers with Bull, Bear & Judge agents..."):
                MarketDebateEngine(settings=settings).generate_briefings(top_n_debate=15)
                st.success("All watched stocks and market debates re-checked!")
                st.rerun()
    with qa3:
        st.write("")
        st.write("")
        if st.button("🚀 Full NSE Market Sync & Debate", use_container_width=True):
            with st.spinner("Fetching open-source NSE master & EOD Bhavcopy, running screening, and executing Bull vs. Bear vs. Judge debate..."):
                _run_full_auto_pipeline()
                st.rerun()

if "last_rechecked_result" in st.session_state and st.session_state["last_rechecked_result"]:
    last_res = st.session_state["last_rechecked_result"]
    deb = last_res.get("debate") or {}
    with st.expander(f"✅ Latest Re-Check Result: {last_res.get('symbol')} ({last_res.get('company_name')}) — Verdict: {deb.get('verdict', 'COMPLETED')}", expanded=True):
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            st.markdown("**🐂 Bull Agent Arguments**")
            for b in deb.get("bull_case", []):
                st.markdown(f"- {b}")
        with rc2:
            st.markdown("**🐻 Bear Agent Arguments**")
            for b in deb.get("bear_case", []):
                st.markdown(f"- {b}")
        with rc3:
            st.markdown(f"**⚖️ Judge Verdict: `{deb.get('verdict')}` (Confidence: {deb.get('confidence_score')})**")
            st.write(deb.get("judge_verdict_reasoning", ""))
        if st.button("Dismiss Result", key="dismiss_recheck"):
            st.session_state.pop("last_rechecked_result", None)
            st.rerun()

if briefing_file.exists():
    try:
        briefing_data = json.loads(briefing_file.read_text(encoding="utf-8"))
    except Exception:
        briefing_data = None
else:
    briefing_data = None

if briefing_data:
    cov = briefing_data.get("universe_coverage", {})
    open_exp = briefing_data.get("market_open_expectation", {})
    close_sum = briefing_data.get("market_close_summary", {})

    st.subheader(f"Full NSE Market Pulse ({briefing_data.get('latest_trading_date')})")
    b_cols = st.columns(6)
    b_cols[0].metric("NSE Universe", cov.get("total_registered_securities", 0))
    b_cols[1].metric("Traded Stocks", cov.get("traded_securities_on_date", 0))
    b_cols[2].metric("Advancers (Bull)", cov.get("advancers", 0))
    b_cols[3].metric("Decliners (Bear)", cov.get("decliners", 0))
    b_cols[4].metric("A/D Ratio", f"{cov.get('advance_decline_ratio', 0)}x")
    b_cols[5].metric("Judge Market Stance", open_exp.get("stance", "—"))

    tab_open, tab_close, tab_debate, tab_watch, tab_mobile = st.tabs([
        "🌅 Market Open Expectation",
        "🌇 Market Close Review",
        "⚖️ Bull vs. Bear vs. Judge Debates",
        "👁️ Watchlist & Re-Check",
        "📱 Mobile & Cloud Access",
    ])

    with tab_open:
        st.markdown(f"### {open_exp.get('title', 'Market Open Expectation')}")
        st.info(open_exp.get("expectation_summary", ""))
        oc1, oc2, oc3 = st.columns(3)
        with oc1:
            st.markdown("#### 🐂 Bull Agent Opening Case")
            for item in open_exp.get("bull_agent_opening_case", []):
                st.markdown(f"- {item}")
        with oc2:
            st.markdown("#### 🐻 Bear Agent Opening Case")
            for item in open_exp.get("bear_agent_opening_case", []):
                st.markdown(f"- {item}")
        with oc3:
            st.markdown("#### ⚖️ Judge Opening Directive")
            st.write(open_exp.get("judge_opening_directive", ""))
        if open_exp.get("stocks_to_watch_at_open"):
            st.markdown("**High-Activity Stocks to Watch at Market Open**")
            st.dataframe(pd.DataFrame(open_exp["stocks_to_watch_at_open"]), width="stretch", hide_index=True)

    with tab_close:
        st.markdown(f"### {close_sum.get('title', 'Market Close Summary')}")
        st.success(close_sum.get("judge_final_verdict", ""))
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("#### Top Gainers (Liquid)")
            st.dataframe(pd.DataFrame(close_sum.get("top_gainers", [])), width="stretch", hide_index=True)
        with cc2:
            st.markdown("#### Top Losers (Liquid)")
            st.dataframe(pd.DataFrame(close_sum.get("top_losers", [])), width="stretch", hide_index=True)
        st.markdown("#### Most Active by Turnover")
        st.dataframe(pd.DataFrame(close_sum.get("most_active_by_turnover", [])), width="stretch", hide_index=True)

    with tab_debate:
        st.markdown("### 3-Agent Stock Debates (Bull Agent vs. Bear Agent → Judge Agent)")
        debates = briefing_data.get("stock_debates", [])
        if debates:
            for idx, d in enumerate(debates):
                sym = d.get("symbol") or d.get("security_key")
                comp = d.get("company_name") or sym
                verdict = d.get("verdict")
                conf = d.get("confidence_score")
                with st.expander(f"⚖️ {sym} ({comp}) — Judge Verdict: {verdict} (Confidence: {conf})", expanded=(idx < 4)):
                    dc1, dc2, dc3 = st.columns([2, 2, 2])
                    with dc1:
                        st.markdown("**🐂 Bull Agent Arguments**")
                        for b in d.get("bull_case", []):
                            st.markdown(f"- {b}")
                    with dc2:
                        st.markdown("**🐻 Bear Agent Arguments**")
                        for b in d.get("bear_case", []):
                            st.markdown(f"- {b}")
                        if d.get("hidden_risks"):
                            st.markdown("**⚠️ Caveats / Hidden Risks**")
                            for hr in d.get("hidden_risks", []):
                                st.caption(f"• {hr}")
                    with dc3:
                        st.markdown("**👨‍⚖️ Independent Judge Reasoning**")
                        st.write(d.get("judge_verdict_reasoning", ""))
                        btn_c1, btn_c2 = st.columns(2)
                        if btn_c1.button(f"🔄 Re-Check {sym}", key=f"recheck_debate_{sym}_{idx}"):
                            with st.spinner(f"Re-checking {sym} with Bull, Bear & Judge agents..."):
                                res = MarketDebateEngine(settings=settings).recheck_stock(sym, add_to_watchlist=False)
                                st.session_state["last_rechecked_result"] = res
                                st.rerun()
                        if btn_c2.button(f"➕ Watch {sym}", key=f"watch_debate_{sym}_{idx}"):
                            with st.spinner(f"Adding {sym} to Watchlist and re-checking..."):
                                res = MarketDebateEngine(settings=settings).recheck_stock(sym, add_to_watchlist=True)
                                st.session_state["last_rechecked_result"] = res
                                st.rerun()

            st.markdown("#### Full Debate Table View")
            debate_df = pd.DataFrame([
                {
                    "Symbol": d.get("symbol"),
                    "Company": d.get("company_name"),
                    "Judge Verdict": d.get("verdict"),
                    "Confidence": d.get("confidence_score"),
                    "Risk Score": d.get("risk_score"),
                    "Watchlist Action": d.get("watchlist_action"),
                    "Bull Arguments": " | ".join(d.get("bull_case", [])) or "None",
                    "Bear Arguments": " | ".join(d.get("bear_case", [])) or "Missing verified fundamental statements",
                    "Judge Reasoning": d.get("judge_verdict_reasoning"),
                }
                for d in debates
            ])
            st.dataframe(debate_df, width="stretch", hide_index=True)

    with tab_watch:
        st.markdown("### 👁️ Automatic Agent Stock Watchlist (Add, Re-Check, or Remove)")
        w_items = watchlist_store.load().get("items", [])
        if w_items:
            for w_idx, w in enumerate(w_items):
                w_sym = w.get("symbol") or w.get("security_key")
                w_comp = w.get("company_name") or w_sym
                wc1, wc2, wc3, wc4 = st.columns([3, 3, 1, 1])
                wc1.markdown(f"**{w_sym}** — {w_comp}")
                wc2.caption(f"Added by: `{w.get('added_by')}` | {w.get('reason')}")
                if wc3.button("🔄 Re-Check", key=f"wl_recheck_{w_sym}_{w_idx}"):
                    with st.spinner(f"Re-checking {w_sym} with 3 agents..."):
                        res = MarketDebateEngine(settings=settings).recheck_stock(w_sym, add_to_watchlist=True, reason="Re-checked from Watchlist")
                        st.session_state["last_rechecked_result"] = res
                        st.rerun()
                if wc4.button("🗑️ Remove", key=f"wl_remove_{w_sym}_{w_idx}"):
                    watchlist_store.remove(w_sym)
                    st.rerun()
            st.divider()
        w_col1, w_col2, w_col3 = st.columns([2, 2, 1])
        new_watch_sym = w_col1.text_input("Stock Symbol to Watch (e.g. HDFCBANK, INFY, SBIN, RELIANCE)", key="watch_sym_input")
        new_watch_reason = w_col2.text_input("Watch Reason", value="Track automatically with Bull/Bear/Judge agents at Open & Close", key="watch_reason_input")
        if w_col3.button("➕ Add & Re-Check", type="primary") and new_watch_sym.strip():
            sym_clean = new_watch_sym.strip().upper()
            with st.spinner(f"Adding {sym_clean} and running 3-agent Bull/Bear/Judge debate..."):
                res = MarketDebateEngine(settings=settings).recheck_stock(sym_clean, add_to_watchlist=True, reason=new_watch_reason)
                st.session_state["last_rechecked_result"] = res
            st.success(f"Added and checked {sym_clean}!")
            st.rerun()

    with tab_mobile:
        st.markdown("### 📱 How to Use on Your Mobile Phone (Home & Travel)")
        st.markdown(
            """
            1. **Anywhere in the World (Even When Your Laptop is OFF — Recommended for Travel):**
               - This project includes `requirements.txt`, `Dockerfile`, `render.yaml`, and `.github/workflows/daily_nse_intelligence.yml`.
               - Upload this folder to a free **GitHub** account and connect it to **Streamlit Community Cloud** (`share.streamlit.io`) or **Render** (`render.com`).
               - You get a permanent private/public link (`https://your-app.streamlit.app`) that you can open on your phone's Chrome/Safari browser and save to your phone's Home Screen like a native app!
            2. **Instant Worldwide Mobile Link While Laptop is On (`Start_Mobile_Link.bat`):**
               - Double-click `Start_Mobile_Link.bat` in your project folder. It creates an instant `https://....lhr.life` or `https://....trycloudflare.com` link that works on 4G/5G mobile data anywhere.
            """
        )

st.subheader("NSE EOD synchronization")
try:
    sync_state = SyncStateStore(settings.sync_dir, daily_time=settings.nse_sync_daily_time).load()
except Exception:
    sync_state = {"status": "FAILED", "last_attempt": None,
                  "next_scheduled_sync": "Unavailable", "live_retrieval_enabled": False,
                  "agent_execution_status": "NOT CONFIGURED"}
sync_attempt = sync_state.get("last_attempt") or {}
sync_status = sync_state.get("status", "NOT CONFIGURED")
sync_run_manifest = {}
sync_manifest_path = sync_attempt.get("manifest_path")
if sync_manifest_path:
    try:
        sync_run_manifest = json.loads(Path(sync_manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sync_run_manifest = {}
sync_report_results = sync_run_manifest.get("reports", [])
parser_values = [row.get("parser_status") for row in sync_report_results if row.get("parser_status")]
if sync_status in {"BLOCKED", "NOT CONFIGURED"}:
    download_status = sync_status
elif sync_attempt.get("retrieved_reports", 0):
    download_status = "SUCCESS" if not sync_attempt.get("failed_reports", 0) else "WARNING"
else:
    download_status = sync_status
if not parser_values:
    parser_status = "NOT CONFIGURED" if sync_status not in {"BLOCKED", "NOT CONFIGURED"} else sync_status
elif "FAILED" in parser_values:
    parser_status = "FAILED"
elif "WARNING" in parser_values:
    parser_status = "WARNING"
else:
    parser_status = "SUCCESS"
if sync_status == "SUCCESS":
    st.success("NSE sync status: SUCCESS")
elif sync_status in {"WARNING", "BLOCKED"}:
    st.warning(f"NSE sync status: {sync_status}")
elif sync_status == "FAILED":
    st.error("NSE sync status: FAILED")
else:
    st.info(f"NSE sync status: {sync_status}")
sync_cols = st.columns(4)
sync_cols[0].metric("Last sync attempt", sync_attempt.get("finished_at", "—"))
sync_cols[1].metric("Last successful sync", sync_state.get("last_successful_sync") or "—")
sync_cols[2].metric("Latest available trading date", sync_state.get("latest_available_trading_date") or "—")
sync_cols[3].metric("Next scheduled sync", sync_state.get("next_scheduled_sync", "—"))
sync_detail_cols = st.columns(6)
sync_detail_cols[0].metric("Report discovery", sync_status)
sync_detail_cols[1].metric("Download", download_status)
sync_detail_cols[2].metric("Parser", parser_status)
sync_detail_cols[3].metric("Imported / rejected / duplicate",
                           f"{sync_attempt.get('imported_rows', 0)} / {sync_attempt.get('rejected_rows', 0)} / {sync_attempt.get('duplicate_rows', 0)}")
sync_detail_cols[4].metric("Agent execution", sync_state.get("agent_execution_status", "NOT CONFIGURED"))
sync_detail_cols[5].metric("Live retrieval", "Disabled — access not approved")
if sync_attempt.get("message"):
    st.caption(sync_attempt["message"])

with connect_read_only(settings.database_path) as conn:
    counts = conn.execute("SELECT COUNT(*) securities FROM securities").fetchone()["securities"]
    last_date = conn.execute("SELECT MAX(price_date) d FROM daily_prices WHERE close IS NOT NULL").fetchone()["d"]
    sample = conn.execute("SELECT 1 FROM source_records WHERE lower(source_name) LIKE '%sample%' OR lower(source_name) LIKE '%synthetic%' LIMIT 1").fetchone()
    q_count = conn.execute("SELECT COUNT(*) FROM data_quality_checks WHERE check_id IN (SELECT MAX(check_id) FROM data_quality_checks GROUP BY check_type, security_key, record_date)").fetchone()[0]
if sample:
    st.warning("SAMPLE DATA — SYNTHETIC; NOT EXCHANGE DATA")
summary_cols = st.columns(3)
summary_cols[0].metric("Securities", counts)
summary_cols[1].metric("Latest stored data", last_date or "—")
summary_cols[2].metric("Quality flags", q_count)

with st.expander("Import an official exchange file", expanded=not bool(counts)):
    st.write("Download an exchange file manually where its access conditions permit. Original file bytes are retained under `data/market_data/raw`.")
    exchange = st.selectbox("Exchange", ["Auto-detect", "NSE", "BSE"])
    uploaded = st.file_uploader("CSV, ZIP, XLSX, XLSM, TSV or delimited TXT", type=["csv", "zip", "xlsx", "xlsm", "tsv", "txt"])
    replace_existing = st.checkbox(
        "Apply an audited correction to an existing security/date price", value=False
    )
    correction_reason = st.text_input(
        "Correction reason (required for replacement)",
        disabled=not replace_existing,
    )
    if uploaded is not None and st.button("Import file", type="primary"):
        expected = None if exchange == "Auto-detect" else exchange
        provider = NSEProvider() if exchange == "NSE" else BSEProvider() if exchange == "BSE" else None
        importer = MarketImporter(settings=settings)
        try:
            if provider:
                result = provider.import_bytes(
                    uploaded.name, uploaded.getvalue(), importer,
                    replace_existing=replace_existing,
                    correction_reason=correction_reason or None,
                )
            else:
                result = importer.import_bytes(
                    uploaded.name, uploaded.getvalue(),
                    replace_existing=replace_existing,
                    correction_reason=correction_reason or None,
                )
            with connect_read_write(settings.database_path) as conn:
                run_screening(conn)
            st.success(f"Imported {result.rows_inserted} price records. Original file saved for audit.")
            st.json(result.to_dict())
        except Exception as exc:
            st.error(f"Import failed: {exc}")

with st.expander("Import sourced financial or macro evidence"):
    st.write("Use a manually obtained, permitted JSON evidence file. Each record must include its source URL, publication date, reporting period and timezone-aware retrieval timestamp. Set financial_basis to standalone or consolidated when known; unspecified records are kept separate from both.")
    evidence_file = st.file_uploader("Evidence JSON", type=["json"], key="evidence_json")
    if evidence_file is not None and st.button("Import evidence file"):
        try:
            result = ManualResearchProvider().import_fundamental_evidence_bytes(
                evidence_file.name, evidence_file.getvalue(), EvidenceStore(settings=settings))
            st.success(f"Imported {result['records_inserted']} sourced evidence records. Original bytes are retained for audit.")
            st.json(result)
        except Exception as exc:
            st.error(f"Evidence import failed: {exc}")

with st.expander("Import a manually obtained NSE stock master"):
    st.write("Upload a provenance-complete JSON listing file. Import is manual only; no provider is contacted and no price records are created.")
    master_file = st.file_uploader("NSE stock master JSON", type=["json"], key="stock_master_json")
    if master_file is not None and st.button("Import stock master"):
        try:
            result = ManualResearchProvider().import_stock_master_bytes(
                master_file.name, master_file.getvalue(), SecurityMasterStore(settings=settings))
            st.success(f"Imported {result['records_read']} master records; no market prices were written.")
            st.json(result)
        except Exception as exc:
            st.error(f"Stock master import failed: {exc}")

with connect_read_only(settings.database_path) as conn:
    latest_screen = conn.execute(
        """SELECT s.exchange, s.symbol, s.company_name, s.security_identifier, s.security_type,
                  p.close latest_close, p.price_date latest_price_date, r.return_20d_pct, r.return_60d_pct,
                  r.return_252d_pct, r.ma_50, r.ma_200, r.volatility_20d_pct, r.max_drawdown_252d_pct,
                  r.volume_change_20d_pct
           FROM screening_results r JOIN securities s ON s.security_key=r.security_key
           LEFT JOIN daily_prices p ON p.security_key=r.security_key
                AND p.price_date=(SELECT MAX(p2.price_date) FROM daily_prices p2
                                  WHERE p2.security_key=r.security_key AND p2.close IS NOT NULL AND p2.price_date<=r.data_date)
           WHERE r.data_date=(SELECT MAX(data_date) FROM screening_results)
           ORDER BY s.exchange, s.symbol""").fetchall()
    quality = conn.execute("SELECT check_type, severity, security_key, record_date, details, source_file FROM data_quality_checks ORDER BY check_id DESC LIMIT 200").fetchall()
    research_rows = conn.execute(
        """SELECT r.research_run_id, r.security_key, r.as_of_date, r.status, r.verdict,
                  r.risk_score, r.confidence_score, r.watchlist_action, r.evidence_count,
                  r.evidence_coverage_json, r.changes_json, r.sample_data,
                  s.exchange, s.symbol, s.company_name
           FROM research_runs r JOIN securities s ON s.security_key=r.security_key
           WHERE r.research_run_id=(SELECT r2.research_run_id FROM research_runs r2
                                    WHERE r2.security_key=r.security_key ORDER BY r2.started_at DESC LIMIT 1)
           ORDER BY s.exchange, s.symbol""").fetchall()
    sample_securities = conn.execute(
        """SELECT DISTINCT s.security_key, s.exchange, s.symbol, s.company_name
           FROM securities s WHERE EXISTS (SELECT 1 FROM source_records sr WHERE sr.security_key=s.security_key
             AND (lower(sr.source_name) LIKE '%sample%' OR lower(sr.source_name) LIKE '%synthetic%'))
           ORDER BY s.exchange, s.symbol""").fetchall()
    master_rows = SecurityMasterRepository.rows_from_connection(conn)

st.subheader("Latest screening snapshot")
if latest_screen:
    st.dataframe(pd.DataFrame([dict(row) for row in latest_screen]), width="stretch", hide_index=True)
else:
    st.info("Import price history to calculate returns and screening metrics. Missing history remains blank.")

st.subheader("Stock master and fundamental analysis")
if master_rows:
    master_frame = pd.DataFrame(master_rows)
    st.dataframe(master_frame[["exchange", "symbol", "company_name", "isin", "listing_segment",
                               "security_status", "source_name", "source_url", "source_retrieved_at"]],
                 width="stretch", hide_index=True,
                 column_config={"source_url": st.column_config.LinkColumn("Stock master source")})
    master_choices = {f"{row['exchange']}:{row['symbol']} — {row['company_name'] or ''} [{row['listing_segment']}]": row["security_key"]
                      for row in master_rows}
    selected_security = st.selectbox("Inspect deterministic fundamentals", list(master_choices))
    selected_key = master_choices[selected_security]
    try:
        bundle = EvidenceRepository(settings=settings).build_bundle(selected_key)
        snapshot = FundamentalAnalyzer().analyze(bundle)
        metric_frame = pd.DataFrame(snapshot.rows())
        st.dataframe(metric_frame[["metric_key", "label", "value", "unit", "reporting_period", "financial_basis",
                                   "data_status", "evidence_ids", "note"]],
                     width="stretch", hide_index=True)
        provenance_rows = []
        for metric in snapshot.metrics:
            for source in metric.sources:
                provenance_rows.append({"metric": metric.label, **source})
        if provenance_rows:
            st.markdown("**Metric source evidence**")
            st.dataframe(pd.DataFrame(provenance_rows), width="stretch", hide_index=True,
                         column_config={"source_url": st.column_config.LinkColumn("Source URL")})
        st.caption("Verified status is a local user-supplied label; this application does not authenticate source documents. Missing and conflicting values remain blank.")
        if st.button("Run local 3-agent evaluation on selected security"):
            try:
                result = ResearchOrchestrator(settings=settings, backend=LocalEvidenceAgentBackend()).run_security(selected_key)
                st.success(f"Completed 3-agent evaluation for {selected_key}: Verdict = {result.verdict}")
                st.json(result.__dict__)
                st.rerun()
            except Exception as exc:
                st.error(f"Local 3-agent evaluation failed: {exc}")
    except Exception as exc:
        st.error(f"Could not build the fundamental snapshot: {exc}")
else:
    st.info("Import a manually obtained stock master to view company identity and the fundamentals workspace. No live data retrieval is enabled.")

st.subheader("Data-quality flags")
if quality:
    st.dataframe(pd.DataFrame([dict(row) for row in quality]), width="stretch", hide_index=True)
else:
    st.caption("No checks have been recorded yet.")

st.subheader("Three-agent research")
if research_rows:
    research_frame = pd.DataFrame([dict(row) for row in research_rows])
    research_frame["evidence_coverage"] = research_frame["evidence_coverage_json"].map(
        lambda value: json.loads(value) if value else {})
    st.dataframe(research_frame[["exchange", "symbol", "company_name", "as_of_date", "status", "verdict",
                                 "risk_score", "confidence_score", "watchlist_action", "evidence_count", "sample_data"]],
                 width="stretch", hide_index=True)
    selected_run = st.selectbox(
        "Inspect a saved research run",
        [row["research_run_id"] for row in research_rows],
        format_func=lambda run_id: next(f"{row['exchange']}:{row['symbol']} — {row['verdict'] or row['status']} ({row['as_of_date']})"
                                        for row in research_rows if row["research_run_id"] == run_id),
    )
    with connect_read_only(settings.database_path) as conn:
        run_row = conn.execute("SELECT * FROM research_runs WHERE research_run_id=?", (selected_run,)).fetchone()
        agent_rows = conn.execute(
            """SELECT agent_id, attempt, status, output_json, error_message FROM agent_outputs
               WHERE research_run_id=? ORDER BY agent_id, attempt""", (selected_run,)).fetchall()
        evidence_rows = conn.execute(
            """SELECT evidence_id, source_type, source_name, source_url, publication_date,
                      reporting_period, retrieved_at, claim, excerpt FROM evidence_items
               WHERE (security_key=? OR security_key IS NULL) AND publication_date<=?
                 AND substr(retrieved_at,1,10)<=? ORDER BY publication_date DESC""",
            (run_row["security_key"], run_row["as_of_date"], run_row["as_of_date"])).fetchall()
    st.json({"coverage": json.loads(run_row["evidence_coverage_json"]),
             "changes_from_previous": json.loads(run_row["changes_json"]),
             "agent_attempts": [dict(row) | {"output_json": json.loads(row["output_json"]) if row["output_json"] else None}
                                for row in agent_rows]})
    if evidence_rows:
        st.markdown("**Cited source evidence**")
        st.dataframe(pd.DataFrame([dict(row) for row in evidence_rows]), width="stretch", hide_index=True,
                     column_config={"source_url": st.column_config.LinkColumn("Source URL")})
else:
    st.info("No agent research has been saved. Research reports remain inconclusive whenever evidence is missing or cannot be verified.")

if sample_securities:
    sample_options = {f"{row['exchange']}:{row['symbol']} — {row['company_name'] or ''}": row["security_key"]
                      for row in sample_securities}
    selected_sample = st.selectbox("Synthetic security for offline agent demo", list(sample_options))
    if st.button("Run offline synthetic demo"):
        try:
            result = ResearchOrchestrator(settings=settings, backend=SampleAgentBackend()).run_security(sample_options[selected_sample])
            report_result = ReportGenerator(settings=settings).generate(sample_data=True)
            st.success("Saved synthetic three-agent demo. It made no API calls and contains no company fundamentals.")
            st.json({"research": result.__dict__, "reports": report_result["files"]})
            st.rerun()
        except Exception as exc:
            st.error(f"Offline research demo failed: {exc}")

if st.button("Generate Excel, PDF and email-ready draft"):
    try:
        result = ReportGenerator(settings=settings).generate()
        st.success("Reports generated. Email remains a local draft and was not sent.")
        st.json(result)
        for kind in ("excel", "pdf", "email_draft", "manifest"):
            path = Path(result["files"][kind])
            if path.exists():
                st.download_button(f"Download {kind.replace('_', ' ').title()}", path.read_bytes(), file_name=path.name, key=f"download_{kind}")
    except Exception as exc:
        st.error(f"Report generation failed: {exc}")
