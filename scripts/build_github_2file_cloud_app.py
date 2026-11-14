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
    seed_payload = {
        "latest_date": latest_date,
        "stocks": [dict(r) for r in rows],
    }
    seed_path = ROOT / "data" / "nse_cloud_seed.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(json.dumps(seed_payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(rows)} NSE stocks to {seed_path.name} ({round(seed_path.stat().st_size / 1024, 1)} KB)")
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
        ):
            target = ROOT / rel
            if target.exists():
                zf.write(target, rel)

    b64_payload = base64.b64encode(buf.getvalue()).decode("ascii")

    bootstrap_code = f'''from __future__ import annotations

import base64
import io
import sys
import zipfile
from pathlib import Path

_BOOT_ROOT = Path(__file__).resolve().parent
_EMBEDDED_BUNDLE_B64 = "{b64_payload}"


def _ensure_cloud_files_extracted() -> None:
    """Automatically unpacks src/ and initial 3,500+ NSE stock seed if deployed via GitHub."""
    pkg_init = _BOOT_ROOT / "src" / "india_stock_intelligence" / "__init__.py"
    briefing = _BOOT_ROOT / "data" / "latest_market_briefing.json"
    seed_file = _BOOT_ROOT / "data" / "nse_cloud_seed.json"
    raw_zip = base64.b64decode(_EMBEDDED_BUNDLE_B64.encode("ascii"))
    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        for member in zf.namelist():
            dest = _BOOT_ROOT / member
            if member.startswith("src/") or not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))


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
