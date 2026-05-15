"""
tests/test_kenfrench_direct.py

Covers Sprint 6 cost optimisation: replacing pandas-datareader with
direct HTTP fetch from Ken French's Dartmouth page.

Tests exercise three things:
  1. The parser handles the real CSV format (preamble + monthly block +
     annual block — we synthesise this in-memory rather than hitting
     the network).
  2. The DB cache layer (_load_ff_factors_with_cache) is DB-first and
     only fetches when the cache is empty.
  3. Migration 005 imports cleanly with the correct revision chain.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


def _make_kenfrench_zip_bytes() -> bytes:
    """
    Build a zip containing a CSV that mirrors the actual Ken French
    file shape: copyright preamble, monthly block, then an Annual
    Factors block with a different schema. The parser must locate
    the monthly block and stop at the annual block boundary.
    """
    csv_content = (
        "This file was created by CMPT_ME_BEME_RETS using the 202412 CRSP database.\n"
        "The 1-, 3-, and 6-month risk-free rates used to construct the spread are\n"
        "the daily holding period returns of the CRSP T-bill files.\n"
        "\n"
        ",Mkt-RF,SMB,HML,RF\n"
        "192607, 2.96, -2.30, -2.87, 0.22\n"
        "192608, 2.64, -1.40, 4.19, 0.25\n"
        "200001, -4.74, 2.51, -1.45, 0.41\n"
        "200002, 2.45, 17.92, -10.16, 0.43\n"
        "202412, -2.13, -0.45, 0.66, 0.37\n"
        "\n"
        "Annual Factors: January-December\n"
        "\n"
        ",Mkt-RF,SMB,HML,RF\n"
        "1927, 29.05, -0.49, -3.91, 3.12\n"
        "1928, 35.13, 4.27, -5.59, 3.56\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("F-F_Research_Data_Factors.csv", csv_content)
    return buf.getvalue()


class TestKenFrenchDirectFetcher:
    """The direct HTTP path must replace pandas-datareader on output shape."""

    def test_parses_monthly_block_only_excludes_annual(self):
        """The CSV has a monthly block AND an annual block. Our parser
        must stop at the boundary — a YYYYMM-only index, not YYYY rows."""
        from tools.data_fetcher import _kenfrench_direct_fetch

        zip_bytes = _make_kenfrench_zip_bytes()
        mock_response = MagicMock()
        mock_response.content = zip_bytes
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("tools.data_fetcher.requests.get", return_value=mock_response):
            df = _kenfrench_direct_fetch()

        # 5 monthly rows in the synthetic CSV; the 2 annual rows are
        # filtered out by the YYYYMM range check.
        assert len(df) == 5
        # Annual years would be 1927/1928 — must not appear in the index
        assert 1927 not in df.index
        assert 1928 not in df.index

    def test_returns_yyyymm_integer_index(self):
        """The new shape is YYYYMM integers, not DatetimeIndex.
        _load_ff_factors_with_cache handles the conversion downstream."""
        from tools.data_fetcher import _kenfrench_direct_fetch

        mock_response = MagicMock()
        mock_response.content = _make_kenfrench_zip_bytes()
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("tools.data_fetcher.requests.get", return_value=mock_response):
            df = _kenfrench_direct_fetch()

        assert df.index.dtype == "int64"
        assert 192607 in df.index
        assert 200001 in df.index
        assert 202412 in df.index

    def test_returns_four_factor_columns(self):
        """Columns must be Mkt-RF, SMB, HML, RF — same names the OLS
        regression in chart_data.py looks up."""
        from tools.data_fetcher import _kenfrench_direct_fetch

        mock_response = MagicMock()
        mock_response.content = _make_kenfrench_zip_bytes()
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("tools.data_fetcher.requests.get", return_value=mock_response):
            df = _kenfrench_direct_fetch()

        assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RF"]

    def test_values_in_percent_form(self):
        """Ken French publishes percent values. The fetcher returns them
        as-is; _load_ff_factors_with_cache divides by 100 to get decimal
        returns. Test that we haven't accidentally double-scaled."""
        from tools.data_fetcher import _kenfrench_direct_fetch

        mock_response = MagicMock()
        mock_response.content = _make_kenfrench_zip_bytes()
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("tools.data_fetcher.requests.get", return_value=mock_response):
            df = _kenfrench_direct_fetch()

        # 200001 Mkt-RF = -4.74 in the synthetic CSV
        assert df.loc[200001, "Mkt-RF"] == pytest.approx(-4.74, abs=0.01)

    def test_raises_when_csv_has_no_monthly_rows(self):
        """Defence against an upstream format change that would silently
        return an empty DataFrame and break OLS regression downstream."""
        from tools.data_fetcher import _kenfrench_direct_fetch

        empty_csv = "Some preamble\nNo data here\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("empty.csv", empty_csv)

        mock_response = MagicMock()
        mock_response.content = buf.getvalue()
        mock_response.raise_for_status = MagicMock(return_value=None)

        with patch("tools.data_fetcher.requests.get", return_value=mock_response):
            with pytest.raises(ValueError, match="no monthly rows"):
                _kenfrench_direct_fetch()


class TestKenFrenchCacheLayer:
    """_load_ff_factors_with_cache should be DB-first when DATABASE_URL is set,
    HTTP-only when it isn't."""

    def test_falls_through_to_fetcher_when_db_unavailable(self, monkeypatch):
        """No DATABASE_URL → no DB read → direct HTTP path. Returned
        DataFrame is decimal-form with month-end DatetimeIndex."""
        from tools.data_fetcher import _load_ff_factors_with_cache

        mock_response = MagicMock()
        mock_response.content = _make_kenfrench_zip_bytes()
        mock_response.raise_for_status = MagicMock(return_value=None)

        # Force the DB-read path to return empty + skip the write side
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", lambda: [])
        with patch("tools.data_fetcher.requests.get", return_value=mock_response):
            df = _load_ff_factors_with_cache("2000-01-01", "2024-12-31")

        assert df is not None
        # After /100 scaling, Mkt-RF -4.74 → -0.0474
        assert df["Mkt-RF"].abs().max() < 1.0
        # DatetimeIndex with month-end stamps
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_reads_from_db_when_cache_populated(self, monkeypatch):
        """When the DB has rows AND none are stale, no HTTP fetch fires."""
        from tools.data_fetcher import _load_ff_factors_with_cache

        # Populate the mock DB with rows ending at the current month so
        # the staleness check (35 days since the next month started)
        # evaluates false. Use a recent yyyymm.
        from datetime import date
        today = date.today()
        recent_yyyymm = today.year * 100 + today.month
        # Build 24 months ending at "recent_yyyymm"
        rows = []
        year, month = today.year, today.month
        for _ in range(24):
            rows.append((year * 100 + month, 0.5, 0.1, 0.2, 0.02))
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        rows.sort()
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db",
                            lambda: rows)

        # Spy on the HTTP fetch — it must NOT be called
        fetch_calls = []
        def _spy_fetch(*args, **kwargs):
            fetch_calls.append(1)
            return pd.DataFrame()
        monkeypatch.setattr("tools.data_fetcher._kenfrench_direct_fetch", _spy_fetch)

        df = _load_ff_factors_with_cache("2000-01-01", "2030-12-31")

        assert df is not None
        assert len(fetch_calls) == 0, "Cache hit must not trigger HTTP fetch"
        assert recent_yyyymm in [int(d.strftime("%Y%m")) for d in df.index]


class TestLoadFFFactorsOptionalRange:
    """_load_ff_factors_with_cache accepts start=None / end=None so callers
    that don't know the date layout can still trigger an incremental fetch.
    The DB-first incremental decision is driven by the DB's last_yyyymm,
    not by the start/end arguments — those only slice the returned frame."""

    @staticmethod
    def _patch_fetch(monkeypatch):
        """Return a stub _kenfrench_direct_fetch that returns 24 months of
        plausible FF data and a counter so tests can assert call count."""
        calls: list[int] = []

        def _stub() -> pd.DataFrame:
            calls.append(1)
            from datetime import date
            today = date.today()
            year, month = today.year, today.month
            idx = []
            for _ in range(24):
                idx.append(year * 100 + month)
                month -= 1
                if month == 0:
                    month = 12
                    year -= 1
            idx.sort()
            return pd.DataFrame(
                {"Mkt-RF": [0.5] * 24, "SMB": [0.1] * 24, "HML": [0.2] * 24, "RF": [0.02] * 24},
                index=idx,
            )

        monkeypatch.setattr("tools.data_fetcher._kenfrench_direct_fetch", _stub)
        return calls

    def test_accepts_no_arguments_at_all(self, monkeypatch):
        """The headline contract: callers that don't know the date range
        can call _load_ff_factors_with_cache() with zero arguments."""
        from tools.data_fetcher import _load_ff_factors_with_cache
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", lambda: [])
        self._patch_fetch(monkeypatch)

        # No keyword args, no positional args — must not TypeError.
        df = _load_ff_factors_with_cache()

        assert df is not None
        assert isinstance(df.index, pd.DatetimeIndex)
        assert not df.empty

    def test_start_none_means_no_lower_bound(self, monkeypatch):
        """start=None must skip the >= filter so historical months survive."""
        from tools.data_fetcher import _load_ff_factors_with_cache
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", lambda: [])
        self._patch_fetch(monkeypatch)

        df_with_start = _load_ff_factors_with_cache(start="2100-01-01", end=None)
        # start in the future + no upper bound → empty slice
        assert df_with_start is not None and df_with_start.empty

        df_no_start = _load_ff_factors_with_cache(start=None, end=None)
        # No bounds → 24 rows survive
        assert df_no_start is not None and len(df_no_start) == 24

    def test_end_none_means_no_upper_bound(self, monkeypatch):
        """end=None must skip the <= filter so the most recent month
        always appears (the bug we're guarding against: someone passing
        end='2024-12-31' and silently losing 2025-onward rows)."""
        from tools.data_fetcher import _load_ff_factors_with_cache
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", lambda: [])
        self._patch_fetch(monkeypatch)

        df = _load_ff_factors_with_cache(start=None, end=None)
        assert df is not None
        # The stub's most recent row is the current month — must be present.
        from datetime import date
        today = date.today()
        recent_yyyymm = today.year * 100 + today.month
        seen_yyyymm = [int(d.strftime("%Y%m")) for d in df.index]
        assert recent_yyyymm in seen_yyyymm

    def test_existing_positional_call_still_works(self, monkeypatch):
        """Backwards compat: the existing call site
        `_load_ff_factors_with_cache(start, end)` with positional strings
        must continue to work unchanged."""
        from tools.data_fetcher import _load_ff_factors_with_cache
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", lambda: [])
        self._patch_fetch(monkeypatch)

        df = _load_ff_factors_with_cache("2000-01-01", "2024-12-31")
        assert df is not None
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_incremental_decision_uses_db_not_arguments(self, monkeypatch):
        """The 35-day staleness check looks at the DB's last_yyyymm, not
        at the caller's start/end. A caller asking for an old window
        should NOT force a fresh HTTP fetch when the DB is current."""
        from datetime import date
        from tools.data_fetcher import _load_ff_factors_with_cache

        # Populate DB with current-month rows → cache is fresh
        today = date.today()
        recent_yyyymm = today.year * 100 + today.month
        rows = [(recent_yyyymm, 0.5, 0.1, 0.2, 0.02)]
        monkeypatch.setattr("tools.data_fetcher._read_ff_factors_from_db", lambda: rows)
        calls = self._patch_fetch(monkeypatch)

        # Caller asks for an ancient slice; this must NOT trigger a refetch.
        _load_ff_factors_with_cache(start="1990-01-01", end="1995-12-31")

        assert len(calls) == 0, (
            "Caller's start/end window must not influence the incremental "
            "decision — the DB cache is the only authority on freshness"
        )


class TestRegistryDirectSource:
    """The provenance registry must reflect ken_french_direct so the
    Frontend Sources line displays the new label. We don't invoke
    _build_registry_entries directly because it requires a full
    `provided` dict with every Excel sheet loaded — instead we verify
    the URL constant that feeds into the static template inside it."""

    def test_zip_url_points_to_dartmouth_csv_archive(self):
        from tools.data_fetcher import _KEN_FRENCH_FF3_ZIP_URL
        assert _KEN_FRENCH_FF3_ZIP_URL.startswith("https://mba.tuck.dartmouth.edu/")
        assert _KEN_FRENCH_FF3_ZIP_URL.endswith("F-F_Research_Data_Factors_CSV.zip")

    def test_registry_source_type_string_present_in_module(self):
        """Grep-style sanity check: the new source_type string must be
        embedded in the data_fetcher source so the registry emits it."""
        import os, pathlib
        path = pathlib.Path(__file__).parent.parent / "backend" / "tools" / "data_fetcher.py"
        source = path.read_text(encoding="utf-8")
        # Two registry sites should both use the new tag
        assert source.count("ken_french_direct") >= 2, (
            "ken_french_direct should appear in both the runtime registry "
            "builder and the fallback static-registry function"
        )


class TestMigration005Importable:
    def test_migration_imports_cleanly(self):
        import importlib.util
        path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "migrations",
            "versions", "005_create_ff_factors_table.py",
        )
        spec = importlib.util.spec_from_file_location("m005", path)
        assert spec is not None and spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "005"
        assert m.down_revision == "004"
        assert callable(m.upgrade)
        assert callable(m.downgrade)
