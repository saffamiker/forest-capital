"""
tests/test_momentum_factor.py

Tests for the Ken French momentum (MOM) factor fetch — the Carhart
fourth factor added to ff_factors_monthly.

The CSV parser is tested against a synthetic fixture (no network); the
backfill function is tested for graceful behaviour without a database.
The "mom populated, no nulls" assertion is an integration check that
runs against a populated database in deployment, not the test env.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")


# A synthetic momentum CSV mirroring Ken French's format — prose preamble,
# a header line, the monthly block, then the annual block.
_SAMPLE_MOM_CSV = """This file was created using the 100 Portfolios ...
Missing data are indicated by -99.99.

,Mom
192701,    0.56
192702,   -0.93
192703,    1.40
202411,    2.15
202412,   -0.40

Annual Factors:

,Mom
1927,    20.81
1928,    23.74
"""


class TestMomentumParser:
    def test_parses_monthly_block(self):
        from tools.data_fetcher import _parse_kenfrench_momentum_csv
        df = _parse_kenfrench_momentum_csv(_SAMPLE_MOM_CSV)
        # Five monthly rows, indexed by YYYYMM, one Mom column.
        assert list(df.columns) == ["Mom"]
        assert len(df) == 5
        assert 192701 in df.index
        assert 202412 in df.index

    def test_stops_at_annual_block(self):
        """The 4-digit annual rows (1927, 1928) must not be captured."""
        from tools.data_fetcher import _parse_kenfrench_momentum_csv
        df = _parse_kenfrench_momentum_csv(_SAMPLE_MOM_CSV)
        assert 1927 not in df.index
        assert 1928 not in df.index
        assert df.index.max() == 202412

    def test_values_preserved_in_percent_form(self):
        from tools.data_fetcher import _parse_kenfrench_momentum_csv
        df = _parse_kenfrench_momentum_csv(_SAMPLE_MOM_CSV)
        # Values are kept in percent form (the caller divides by 100).
        assert abs(float(df.loc[192701, "Mom"]) - 0.56) < 1e-9
        assert abs(float(df.loc[202412, "Mom"]) - (-0.40)) < 1e-9

    def test_no_monthly_rows_raises(self):
        from tools.data_fetcher import _parse_kenfrench_momentum_csv
        with pytest.raises(ValueError):
            _parse_kenfrench_momentum_csv("preamble only\nno data here\n")


class TestMomentumBackfill:
    def test_backfill_graceful_without_database(self, monkeypatch):
        """With no DATABASE_URL the backfill is a no-op summary, never an
        error — the pipeline must not break when the DB is absent."""
        import database
        monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
        from tools.data_fetcher import backfill_momentum_factor
        summary = backfill_momentum_factor()
        assert summary["rows_updated"] == 0
        assert summary["gaps"] == []

    def test_count_null_mom_graceful_without_database(self, monkeypatch):
        import database
        monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
        from tools.data_fetcher import _count_null_mom
        assert _count_null_mom() == 0
