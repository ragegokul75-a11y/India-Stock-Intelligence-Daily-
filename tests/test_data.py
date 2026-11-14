import sqlite3

import pytest

from config import PROJECT_ROOT, Settings, get_settings
from providers.nse_provider import NSEProvider
from providers.news_provider import NewsProvider
from india_stock_intelligence.providers.base import ProviderNotConfigured
from scripts import migrate_layout


def test_default_paths_match_project_layout(monkeypatch):
    for name in ("INDIA_STOCK_DATA_DIR", "INDIA_STOCK_DATABASE_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = get_settings()
    assert settings.raw_dir == PROJECT_ROOT / "data" / "market_data" / "raw"
    assert settings.financial_raw_dir == PROJECT_ROOT / "data" / "financial_data" / "raw"
    assert settings.report_dir == PROJECT_ROOT / "data" / "reports"
    assert settings.database_path == PROJECT_ROOT / "database" / "stock_intelligence.db"


def test_temp_settings_keep_database_and_raw_files_isolated(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    assert settings.database_path == tmp_path / "database" / "stock_intelligence.db"
    assert settings.raw_dir.is_relative_to(tmp_path)


def test_provider_downloads_remain_disabled():
    with pytest.raises(ProviderNotConfigured):
        NSEProvider().download_eod()
    with pytest.raises(ProviderNotConfigured):
        NewsProvider().download()


def test_migration_copies_database_and_raw_bytes_without_deleting_source(tmp_path, monkeypatch):
    old_database = tmp_path / "legacy.sqlite3"
    new_database = tmp_path / "database" / "stock_intelligence.db"
    old_raw = tmp_path / "old_raw"
    new_raw = tmp_path / "data" / "market_data" / "raw"
    old_raw.mkdir()
    original_bytes = b"synthetic raw market file\x00\xff"
    (old_raw / "sample.csv").write_bytes(original_bytes)
    with sqlite3.connect(old_database) as conn:
        conn.execute("CREATE TABLE securities (security_key TEXT)")
        conn.execute("INSERT INTO securities VALUES ('NSE:SAMPLE')")

    monkeypatch.setattr(migrate_layout, "OLD_DATABASE", old_database)
    monkeypatch.setattr(migrate_layout, "NEW_DATABASE", new_database)
    monkeypatch.setattr(migrate_layout, "OLD_MARKET_RAW", old_raw)
    monkeypatch.setattr(migrate_layout, "NEW_MARKET_RAW", new_raw)
    copied, counts = migrate_layout.migrate_database()
    copied_files = migrate_layout.migrate_raw_market_files()

    assert copied is True
    assert counts["securities"] == 1
    assert copied_files == 1
    assert new_raw.joinpath("sample.csv").read_bytes() == original_bytes
    assert old_database.exists() and (old_raw / "sample.csv").exists()
    with pytest.raises(FileExistsError):
        migrate_layout.migrate_database()


def test_top_level_prompt_copies_match_the_agent_prompts():
    pairs = {
        "research_prompt.txt": "equity_research_analyst.md",
        "bull_bear_prompt.txt": "bull_bear_analyst.md",
        "judge_prompt.txt": "independent_judge.md",
    }
    for public_name, canonical_name in pairs.items():
        public_prompt = PROJECT_ROOT / "prompts" / public_name
        canonical_prompt = PROJECT_ROOT / "src" / "india_stock_intelligence" / "prompts" / canonical_name
        assert public_prompt.read_text(encoding="utf-8") == canonical_prompt.read_text(encoding="utf-8")


def test_packaged_config_facade_matches_repository_facade():
    repository_config = (PROJECT_ROOT / "config.py").read_text(encoding="utf-8")
    packaged_config = (PROJECT_ROOT / "src" / "config.py").read_text(encoding="utf-8")
    assert repository_config == packaged_config
