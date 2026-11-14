"""Copy legacy Stage 1 database and market archives into the current folders."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parents[1]
OLD_DATABASE = ROOT / "data" / "india_stocks.sqlite3"
NEW_DATABASE = ROOT / "database" / "stock_intelligence.db"
OLD_MARKET_RAW = ROOT / "data" / "raw"
NEW_MARKET_RAW = ROOT / "data" / "market_data" / "raw"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_database() -> tuple[bool, dict[str, int]]:
    if not OLD_DATABASE.exists():
        return False, {}
    if NEW_DATABASE.exists():
        raise FileExistsError(f"Target database already exists; refusing to overwrite it: {NEW_DATABASE}")
    NEW_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(OLD_DATABASE)
    destination = sqlite3.connect(NEW_DATABASE)
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Migrated database failed integrity check: {integrity}")
        table_names = ("securities", "daily_prices", "evidence_items", "research_runs", "agent_outputs")
        counts = {}
        existing = {row[0] for row in destination.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in table_names:
            counts[table] = destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in existing else 0
        destination.commit()
        return True, counts
    except Exception:
        destination.close()
        source.close()
        NEW_DATABASE.unlink(missing_ok=True)
        raise
    finally:
        try:
            destination.close()
        except sqlite3.Error:
            pass
        source.close()


def migrate_raw_market_files() -> int:
    NEW_MARKET_RAW.mkdir(parents=True, exist_ok=True)
    if not OLD_MARKET_RAW.exists():
        return 0
    copied = 0
    for old_file in OLD_MARKET_RAW.iterdir():
        if not old_file.is_file() or old_file.name == ".gitkeep":
            continue
        new_file = NEW_MARKET_RAW / old_file.name
        if new_file.exists():
            if sha256_file(old_file) != sha256_file(new_file):
                raise FileExistsError(f"Raw-file hash mismatch; refusing to overwrite: {new_file}")
            continue
        copy2(old_file, new_file)
        if sha256_file(old_file) != sha256_file(new_file):
            new_file.unlink(missing_ok=True)
            raise IOError(f"Raw file verification failed after copying: {old_file.name}")
        copied += 1
    return copied


def main() -> int:
    try:
        copied, counts = migrate_database()
        raw_files = migrate_raw_market_files()
    except (FileExistsError, OSError, sqlite3.Error) as exc:
        print(f"Migration stopped safely: {exc}", file=sys.stderr)
        return 2
    print(f"Database copied: {copied}; counts: {counts}")
    print(f"Raw market files copied and hash-verified: {raw_files}")
    print("The legacy database and raw files were left in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
