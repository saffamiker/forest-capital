"""
tests/test_database_writes.py

Verifies that get_full_history() populates all four PostgreSQL tables with
correct source_type values and non-zero row counts.

These tests require a live Postgres connection and the Excel data file.
Both prerequisites are checked up-front — the full suite is skipped rather
than failing with an opaque connection error when the DB is not reachable.

Source-type assertions mirror the CLAUDE.md mandatory provenance test:
  - BND / BAMLHYH series must be excel_provided
  - SPY equity daily must be yfinance
  - VIXCLS / DGS2 must be fred_api
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

# ── Prerequisites ─────────────────────────────────────────────────────────────

_EXCEL_PATH = Path(__file__).parent.parent / "backend" / "data" / "FNA_670_Project_Sources.xlsx"

_db_url: str | None = None


def _get_db_url() -> str | None:
    """Return the connection URL if DATABASE_URL is set and Postgres responds."""
    global _db_url
    if _db_url is not None:
        return _db_url

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        return None

    # asyncpg uses postgres:// not postgresql+asyncpg://
    url = raw.replace("postgresql+asyncpg://", "postgresql://")

    try:
        import asyncpg

        async def _ping() -> bool:
            conn = await asyncpg.connect(url, timeout=5)
            await conn.close()
            return True

        asyncio.run(_ping())
        _db_url = url
        return url
    except Exception:
        return None


def _skip_if_no_db() -> None:
    if _get_db_url() is None:
        pytest.skip("Postgres not reachable — skipping DB write tests")


def _skip_if_no_excel() -> None:
    if not _EXCEL_PATH.exists():
        pytest.skip("Excel data file not present — skipping DB write tests")


# ── DB query helper ───────────────────────────────────────────────────────────

def _query(sql: str, *args: Any) -> list[dict]:
    """Open a fresh connection, execute a query, close, return rows as dicts."""
    import asyncpg

    url = _get_db_url()
    if url is None:
        pytest.skip("Postgres not reachable")

    async def _run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(url, timeout=10)
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(_run())


def _scalar(sql: str, *args: Any) -> Any:
    """Execute a query and return the first column of the first row."""
    import asyncpg

    url = _get_db_url()
    if url is None:
        pytest.skip("Postgres not reachable")

    async def _run() -> Any:
        conn = await asyncpg.connect(url, timeout=10)
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(_run())


# ── Pipeline fixture (module-scoped, runs once) ───────────────────────────────

@pytest.fixture(scope="module")
def pipeline_ran():
    """
    Run the full data pipeline once per test module.
    All dependent tests are skipped if DB or Excel is absent.
    """
    _skip_if_no_db()
    _skip_if_no_excel()

    from tools.data_fetcher import get_full_history
    get_full_history()
    return True


# ── data_series_registry tests ────────────────────────────────────────────────

def test_registry_populated(pipeline_ran):
    """data_series_registry must contain at least 13 series after pipeline run."""
    count = _scalar("SELECT COUNT(*) FROM data_series_registry")
    assert count >= 13, (
        f"Expected ≥13 rows in data_series_registry, got {count}"
    )


def test_registry_bnd_is_excel_provided(pipeline_ran):
    """
    BND monthly and daily entries must be excel_provided — the Excel file is
    authoritative for bond data. yfinance must never appear for BND.
    """
    rows = _query(
        "SELECT series_id, source_type FROM data_series_registry "
        "WHERE series_id = ANY($1)",
        ["ig_monthly_bnd", "ig_daily_bnd"],
    )
    assert len(rows) == 2, (
        f"Expected 2 BND rows, got {len(rows)}: {[r['series_id'] for r in rows]}"
    )
    for row in rows:
        assert row["source_type"] == "excel_provided", (
            f"'{row['series_id']}' has source_type='{row['source_type']}' — expected excel_provided"
        )


def test_registry_bamlhyh_is_excel_provided(pipeline_ran):
    """BAMLHYH total return index (monthly and daily) must be excel_provided."""
    rows = _query(
        "SELECT series_id, source_type FROM data_series_registry "
        "WHERE series_id = ANY($1)",
        ["hy_monthly_baml", "hy_daily_baml"],
    )
    assert len(rows) == 2, (
        f"Expected 2 BAMLHYH rows, got {len(rows)}: {[r['series_id'] for r in rows]}"
    )
    for row in rows:
        assert row["source_type"] == "excel_provided", (
            f"'{row['series_id']}' has source_type='{row['source_type']}' — expected excel_provided"
        )


def test_registry_spy_is_yfinance(pipeline_ran):
    """SPY daily equity series must be sourced from yfinance — not Excel, not FRED."""
    rows = _query(
        "SELECT source_type FROM data_series_registry WHERE series_id = 'equity_daily_spy'"
    )
    assert len(rows) == 1, "equity_daily_spy not found in data_series_registry"
    assert rows[0]["source_type"] == "yfinance", (
        f"equity_daily_spy has source_type='{rows[0]['source_type']}' — expected yfinance"
    )


def test_registry_vix_is_fred_api(pipeline_ran):
    """VIX (VIXCLS) must come from FRED — it is not in the Excel file."""
    rows = _query(
        "SELECT source_type FROM data_series_registry WHERE series_id = 'vix_daily'"
    )
    assert len(rows) == 1, "vix_daily not found in data_series_registry"
    assert rows[0]["source_type"] == "fred_api", (
        f"vix_daily has source_type='{rows[0]['source_type']}' — expected fred_api"
    )


def test_registry_dgs2_is_fred_api(pipeline_ran):
    """DGS2 must come from FRED. DGS10 is in Excel; DGS2 is the supplemental gap."""
    rows = _query(
        "SELECT source_type FROM data_series_registry WHERE series_id = 'dgs2_daily'"
    )
    assert len(rows) == 1, "dgs2_daily not found in data_series_registry"
    assert rows[0]["source_type"] == "fred_api", (
        f"dgs2_daily has source_type='{rows[0]['source_type']}' — expected fred_api"
    )


def test_registry_dgs10_not_in_fred_api(pipeline_ran):
    """
    DGS10 must NOT appear as a fred_api series — it comes from Excel.
    Fetching it from FRED would duplicate and potentially contradict the
    authoritative Excel source.
    """
    rows = _query(
        "SELECT series_id, source_detail FROM data_series_registry "
        "WHERE source_type = 'fred_api'"
    )
    import json
    for row in rows:
        detail = json.loads(row["source_detail"]) if isinstance(row["source_detail"], str) else row["source_detail"]
        sid = detail.get("series_id", "")
        assert sid != "DGS10", (
            f"DGS10 found as a fred_api series (series_id='{row['series_id']}') — "
            "DGS10 must come from Excel"
        )


# ── market_data_monthly tests ─────────────────────────────────────────────────

def test_monthly_table_has_rows(pipeline_ran):
    """market_data_monthly must have data — BND starts April 2007 so ≥100 months expected."""
    count = _scalar("SELECT COUNT(*) FROM market_data_monthly")
    assert count > 0, "market_data_monthly is empty after pipeline run"
    assert count >= 100, f"Expected ≥100 monthly rows, got {count}"


def test_monthly_source_columns_populated(pipeline_ran):
    """
    Every non-null equity/ig/hy return must have a matching source column.
    An orphaned return with no source would violate the provenance contract.
    """
    rows = _query(
        "SELECT COUNT(*) AS total, "
        "  COUNT(equity_source) AS eq_src, "
        "  COUNT(ig_source) AS ig_src, "
        "  COUNT(hy_source) AS hy_src "
        "FROM market_data_monthly "
        "WHERE equity_return IS NOT NULL"
    )
    row = rows[0]
    assert row["total"] > 0, "No rows with equity_return in market_data_monthly"
    assert row["eq_src"] == row["total"], "equity_source missing on some rows"
    assert row["ig_src"] == row["total"], "ig_source missing on some rows"
    assert row["hy_src"] == row["total"], "hy_source missing on some rows"


def test_monthly_equity_source_is_a_known_equity_series(pipeline_ran):
    """
    Every equity return in market_data_monthly must cite a known equity
    series: 'equity_monthly' for the Excel-seeded history, or
    'equity_monthly_yf' for the months auto-extended from yfinance beyond
    the Excel period (see MONTHLY DATA AUTO-EXTENSION). No other value —
    a stray source would mean the provenance tagging is broken.
    """
    bad = _scalar(
        "SELECT COUNT(*) FROM market_data_monthly "
        "WHERE equity_source NOT IN ('equity_monthly', 'equity_monthly_yf')"
    )
    assert bad == 0, f"{bad} rows have an unknown equity_source"


# ── market_data_daily tests ───────────────────────────────────────────────────

def test_daily_table_has_rows(pipeline_ran):
    """market_data_daily must contain daily observations across the backtest window."""
    count = _scalar("SELECT COUNT(*) FROM market_data_daily")
    assert count > 0, "market_data_daily is empty after pipeline run"
    assert count >= 1000, f"Expected ≥1000 daily rows, got {count}"


def test_daily_ig_source_is_excel_provided(pipeline_ran):
    """
    Daily IG returns must cite ig_daily_bnd — an excel_provided series.
    This verifies that the pipeline did not fall back to yfinance BND.
    """
    rows = _query(
        "SELECT DISTINCT ig_source FROM market_data_daily WHERE ig_source IS NOT NULL"
    )
    sources = {r["ig_source"] for r in rows}
    assert sources == {"ig_daily_bnd"}, (
        f"Unexpected ig_source values in market_data_daily: {sources}"
    )


def test_daily_hy_source_is_excel_provided(pipeline_ran):
    """Daily HY returns must cite hy_daily_baml (excel_provided), not HYG from yfinance."""
    rows = _query(
        "SELECT DISTINCT hy_source FROM market_data_daily WHERE hy_source IS NOT NULL"
    )
    sources = {r["hy_source"] for r in rows}
    assert sources == {"hy_daily_baml"}, (
        f"Unexpected hy_source values in market_data_daily: {sources}"
    )


def test_daily_equity_source_is_yfinance(pipeline_ran):
    """Daily equity returns must cite equity_daily_spy (yfinance)."""
    rows = _query(
        "SELECT DISTINCT equity_source FROM market_data_daily WHERE equity_source IS NOT NULL"
    )
    sources = {r["equity_source"] for r in rows}
    assert sources == {"equity_daily_spy"}, (
        f"Unexpected equity_source values in market_data_daily: {sources}"
    )


# ── data_validation_log tests ─────────────────────────────────────────────────

def test_validation_log_has_entry(pipeline_ran):
    """data_validation_log must contain at least one row after pipeline run."""
    count = _scalar("SELECT COUNT(*) FROM data_validation_log")
    assert count > 0, "data_validation_log is empty after pipeline run"


def test_validation_log_cross_validate_equity(pipeline_ran):
    """
    cross_validate_equity result must be logged with a valid status.
    The status tells future pipeline runs whether the equity series agree.
    """
    import json

    rows = _query(
        "SELECT status, detail FROM data_validation_log "
        "WHERE check_name = 'cross_validate_equity' "
        "ORDER BY timestamp DESC LIMIT 1"
    )
    assert len(rows) == 1, "No cross_validate_equity row in data_validation_log"
    row = rows[0]
    assert row["status"] in ("pass", "warn", "fail"), (
        f"Unexpected status '{row['status']}' in validation log"
    )
    detail = json.loads(row["detail"]) if isinstance(row["detail"], str) else row["detail"]
    assert "n_months_compared" in detail, "detail missing n_months_compared"
    assert detail["n_months_compared"] > 0, "n_months_compared is 0"


# ── Idempotency test ──────────────────────────────────────────────────────────

def test_persist_is_idempotent(pipeline_ran):
    """
    Running get_full_history() a second time must not raise an error and must
    not change the row count. All writes use INSERT ... ON CONFLICT DO UPDATE
    so re-runs are safe. This guards against regressions where someone adds
    a non-idempotent INSERT.
    """
    _skip_if_no_excel()

    count_before = _scalar("SELECT COUNT(*) FROM market_data_monthly")

    from tools.data_fetcher import get_full_history
    get_full_history()

    count_after = _scalar("SELECT COUNT(*) FROM market_data_monthly")
    assert count_after == count_before, (
        f"Row count changed on second run: {count_before} → {count_after}. "
        "Likely a missing ON CONFLICT clause."
    )
