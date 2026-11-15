from __future__ import annotations

import base64
import io
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def export_cloud_seed() -> Path:
    db_path = ROOT / "database" / "stock_intelligence.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    latest_date = conn.execute("SELECT MAX(price_date) FROM daily_prices WHERE close IS NOT NULL").fetchone()[0]
    recent_dates = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT price_date FROM daily_prices WHERE close IS NOT NULL ORDER BY price_date DESC LIMIT 5"
        ).fetchall()
    ]
    min_5d_date = recent_dates[-1] if recent_dates else latest_date

    # Fetch 5-day history for all NSE securities
    hist_rows = conn.execute(
        """SELECT security_key, price_date, open, high, low, close, volume
           FROM daily_prices
           WHERE price_date >= ? AND close IS NOT NULL
           ORDER BY security_key, price_date ASC""",
        (min_5d_date,),
    ).fetchall()
    by_sec: dict[str, list[dict]] = {}
    for hr in hist_rows:
        by_sec.setdefault(hr["security_key"], []).append(dict(hr))

    rows = conn.execute(
        """SELECT s.security_key AS k, s.symbol AS s, s.company_name AS n,
                  p.open AS o, p.high AS h, p.low AS l, p.close AS c, p.volume AS v,
                  r.return_1d_pct AS r1
           FROM securities s
           LEFT JOIN daily_prices p ON p.security_key = s.security_key AND p.price_date = ?
           LEFT JOIN screening_results r ON r.security_key = s.security_key AND r.data_date = ?
           WHERE s.security_key NOT LIKE '%SYNTH%'
           ORDER BY s.symbol""",
        (latest_date, latest_date),
    ).fetchall()
    conn.close()

    stock_list = []
    for r in rows:
        item = dict(r)
        k = item["k"]
        series = by_sec.get(k, [])
        if series:
            first_c = float(series[0]["close"] or 0.0)
            last_c = float(series[-1]["close"] or 0.0)
            h5 = max(float(x["high"] or x["close"]) for x in series)
            l5 = min(float(x["low"] or x["close"]) for x in series)
            vols = [float(x["volume"] or 0.0) for x in series if (x["volume"] or 0) > 0]
            av5 = sum(vols) / len(vols) if vols else 0.0
            cur_v = float(item.get("v") or series[-1].get("volume") or 0.0)
            vr = round(cur_v / av5, 2) if av5 > 0 else 1.0
            r5 = round(((last_c / first_c) - 1.0) * 100.0, 2) if first_c > 0 and len(series) > 1 else None
            up5 = 0
            for idx in range(len(series)):
                if idx == 0:
                    if float(series[idx]["close"]) >= float(series[idx]["open"] or series[idx]["close"]):
                        up5 += 1
                else:
                    if float(series[idx]["close"]) >= float(series[idx - 1]["close"]):
                        up5 += 1
            item["r5"] = r5
            item["h5"] = round(h5, 2)
            item["l5"] = round(l5, 2)
            item["av5"] = int(round(av5))
            item["vr"] = vr
            item["up5"] = up5
            item["nd"] = len(series)
        stock_list.append(item)

    # Compute turnover rank across traded stocks
    traded_sorted = sorted(
        [x for x in stock_list if x.get("c") and x.get("v")],
        key=lambda x: float(x["c"]) * float(x["v"]),
        reverse=True,
    )
    total_traded = max(1, len(traded_sorted))
    rank_map = {x["k"]: idx + 1 for idx, x in enumerate(traded_sorted)}
    for x in stock_list:
        rk = rank_map.get(x["k"])
        if rk:
            x["rk"] = rk
            x["tp"] = round((rk / total_traded) * 100.0, 1)

    seed_payload = {
        "latest_date": latest_date,
        "trading_dates_5d": recent_dates,
        "total_traded": total_traded,
        "stocks": stock_list,
    }
    seed_path = ROOT / "data" / "nse_cloud_seed.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(seed_payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(stock_list)} NSE stocks (with 5D multi-session history) to {seed_path.name} ({round(seed_path.stat().st_size / 1024, 1)} KB)")
    return seed_path


def build_bundle() -> None:
    export_cloud_seed()
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    marker = "# === END_CLOUD_SELF_EXTRACT_BOOTSTRAP ===\n"
    if marker in app_source:
        app_source = app_source.split(marker, 1)[1]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in (ROOT / "src").rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and ".egg-info" not in str(p):
                zf.write(p, p.relative_to(ROOT).as_posix())
        for rel in (
            "stage4d/inputs/stage4d_financial_evidence.json",
            "data/latest_market_briefing.json",
            "data/watchlist.json",
            "data/nse_cloud_seed.json",
            "data/google_news_cache.json",
        ):
            target = ROOT / rel
            if target.exists():
                zf.write(target, rel)

    b64_payload = base64.b64encode(buf.getvalue()).decode("ascii")

    bootstrap_code = f'''from __future__ import annotations

import base64
import hashlib
import io
import sys
import zipfile
from pathlib import Path

_BOOT_ROOT = Path(__file__).resolve().parent
_EMBEDDED_BUNDLE_B64 = "{b64_payload}"
_BUNDLE_HASH = hashlib.sha256(_EMBEDDED_BUNDLE_B64.encode("ascii")).hexdigest()[:16]


def _ensure_cloud_files_extracted() -> None:
    """Automatically unpacks src/ and initial 3,500+ NSE stock seed and hot-reloads package modules on Streamlit Cloud."""
    if getattr(sys, "_isi_bundle_hash", None) == _BUNDLE_HASH:
        return
    raw_zip = base64.b64decode(_EMBEDDED_BUNDLE_B64.encode("ascii"))
    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        for member in zf.namelist():
            dest = _BOOT_ROOT / member
            if member.startswith("src/") or member == "data/nse_cloud_seed.json" or not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
    for mod_name in [k for k in list(sys.modules) if k == "india_stock_intelligence" or k.startswith("india_stock_intelligence.")]:
        sys.modules.pop(mod_name, None)
    setattr(sys, "_isi_bundle_hash", _BUNDLE_HASH)


_ensure_cloud_files_extracted()
# === END_CLOUD_SELF_EXTRACT_BOOTSTRAP ===
'''
    app_body = app_source.replace("from __future__ import annotations\n", "", 1)
    combined = bootstrap_code + app_body

    (ROOT / "app.py").write_text(combined, encoding="utf-8")
    (ROOT / "streamlit_app.py").write_text(combined, encoding="utf-8")

    upload_dir = ROOT / "UPLOAD_THESE_2_FILES_TO_GITHUB"
    if upload_dir.exists():
        shutil.rmtree(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "app.py").write_text(combined, encoding="utf-8")
    (upload_dir / "requirements.txt").write_bytes((ROOT / "requirements.txt").read_bytes())
    print("Built self-extracting cloud app.py and UPLOAD_THESE_2_FILES_TO_GITHUB successfully!")
    print("app.py size KB:", round(len(combined.encode("utf-8")) / 1024, 1))


if __name__ == "__main__":
    build_bundle()
