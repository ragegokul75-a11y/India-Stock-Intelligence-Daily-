"""Fail-closed entry point for the NSE CM UDiFF EOD parser.

The exact official catalogue version and accepted CM equity codes are not
present in this checkout. Keep this boundary disabled until a versioned,
officially verified profile is added; never fall back to generic aliases.
"""
from __future__ import annotations


class UnverifiedNseCmUdiffProfile(ValueError):
    """Raised until an exact official NSE CM UDiFF profile is verified."""


class NseCmUdiffParser:
    """Offline parser boundary, deliberately disabled without a verified profile."""

    parser_id = "nse_cm_udiff_eod"
    parser_version = "0.1.0-boundary"
    profile_id = "nse-cm-udiff-common-bhavcopy"
    supported_format_versions: frozenset[str] = frozenset()

    def parse(self, payload: bytes, *, format_version: str | None = None) -> list[dict]:
        """Reject parsing until a verified versioned schema is registered.

        This method has no filesystem, database, or network side effects. A
        future profile must define exact headers, date/number rules, the CM cash
        equity filter values, and ZIP member selection before mapping rows.
        """
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if not payload:
            raise ValueError("The supplied NSE CM UDiFF artifact is empty")
        if format_version not in self.supported_format_versions:
            requested = format_version if format_version is not None else "(none)"
            raise UnverifiedNseCmUdiffProfile(
                "No verified official NSE CM UDiFF profile is registered; "
                f"requested format version: {requested}. Supply the official "
                "versioned catalogue/profile, including exact CM cash-equity "
                "segment and instrument values, before parsing is enabled."
            )
        # No supported versions exist until official schema evidence is added.
        raise UnverifiedNseCmUdiffProfile(
            "The selected NSE CM UDiFF profile has no parser implementation."
        )