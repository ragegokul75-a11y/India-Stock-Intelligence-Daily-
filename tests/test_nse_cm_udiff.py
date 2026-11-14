from __future__ import annotations

import pytest

from india_stock_intelligence.nse_cm_udiff import (
    NseCmUdiffParser,
    UnverifiedNseCmUdiffProfile,
)


def test_parser_boundary_rejects_missing_profile_without_guessing():
    parser = NseCmUdiffParser()

    with pytest.raises(UnverifiedNseCmUdiffProfile, match="No verified official"):
        parser.parse(b"synthetic fixture; no market data")


def test_parser_boundary_rejects_unverified_version_without_side_effects(tmp_path):
    parser = NseCmUdiffParser()
    temp_database = tmp_path / "temporary-only.sqlite3"
    temp_raw_dir = tmp_path / "raw"

    with pytest.raises(UnverifiedNseCmUdiffProfile, match="cash-equity"):
        parser.parse(
            b"synthetic fixture; no market data",
            format_version="synthetic-unverified-v1",
        )

    assert not temp_database.exists()
    assert not temp_raw_dir.exists()


def test_empty_artifact_fails_before_profile_selection():
    parser = NseCmUdiffParser()

    with pytest.raises(ValueError, match="artifact is empty"):
        parser.parse(b"")


def test_non_bytes_input_is_rejected():
    parser = NseCmUdiffParser()

    with pytest.raises(TypeError, match="payload must be bytes"):
        parser.parse("synthetic fixture")  # type: ignore[arg-type]


def test_no_format_versions_are_enabled_until_verified():
    assert NseCmUdiffParser.supported_format_versions == frozenset()