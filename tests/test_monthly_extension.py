"""
tests/test_monthly_extension.py

The monthly data pipeline auto-extension — extend_market_data() and its
helpers. The Excel file seeds the historical series (ending 2025-12);
once a calendar month closes, SPY/BND/HYG total returns from yfinance and
DTB3 from FRED extend the series forward.

No database or network in the test environment, so the fetch wrappers are
monkeypatched and the persistence path is exercised only for its
fail-open behaviour.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")

os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

import tools.data_fetcher as df  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from auth import generate_session_token  # noqa: E402

_client = TestClient(app)
_SYSADMIN = {"X-API-Key": generate_session_token("ruurdsm@queens.edu")}
_VIEWER = {"X-API-Key": generate_session_token("panttserk@queens.edu")}


# ── _most_recent_complete_month ───────────────────────────────────────────────

class TestMostRecentCompleteMonth:
    def test_returns_a_month_end_in_the_past(self):
        m = df._most_recent_complete_month()
        # It is a month-end date.
        assert m == m + pd.offsets.MonthEnd(0)
        # And it is strictly before the current (still-open) month.
        today = pd.Timestamp.now()
        assert m < today.replace(day=1)


# ── _fetch_monthly_total_returns ──────────────────────────────────────────────

class TestFetchMonthlyTotalReturns:
    def test_compounds_daily_prices_to_a_monthly_return(self, monkeypatch):
        # Three Jan-2026 trading days: 100 → 101 → 102.
        idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        prices = pd.DataFrame({"SPY": [100.0, 101.0, 102.0]}, index=idx)
        monkeypatch.setattr(df, "_yfinance_fetch",
                            lambda t, s, e: prices)
        out = df._fetch_monthly_total_returns("SPY", "2026-01-01", "2026-02-01")
        # (1.01)(1.00990099) - 1 = 0.02 exactly.
        assert abs(out.loc["2026-01-31"] - 0.02) < 1e-9

    def test_empty_yfinance_result_yields_empty_series(self, monkeypatch):
        monkeypatch.setattr(df, "_yfinance_fetch",
                            lambda t, s, e: pd.DataFrame())
        out = df._fetch_monthly_total_returns("SPY", "2026-01-01", "2026-02-01")
        assert out.empty


# ── _validate_extension_months (Part 4 — splice validation) ───────────────────

def _cand(months: dict[str, tuple[float, float, float]]) -> pd.DataFrame:
    """Build a candidate frame: {month-end: (equity, ig, hy)}."""
    idx = pd.to_datetime(list(months.keys()))
    return pd.DataFrame({
        "equity_return": [v[0] for v in months.values()],
        "ig_return": [v[1] for v in months.values()],
        "hy_return": [v[2] for v in months.values()],
        "risk_free_rate": [0.003] * len(months),
    }, index=idx)


class TestValidateExtensionMonths:
    def test_clean_contiguous_data_all_valid(self):
        cand = _cand({
            "2026-01-31": (0.02, 0.005, 0.01),
            "2026-02-28": (-0.01, 0.004, 0.008),
            "2026-03-31": (0.03, 0.006, 0.012),
        })
        valid, skipped = df._validate_extension_months(
            cand, pd.Timestamp("2025-12-31"), pd.Timestamp("2026-03-31"))
        assert len(valid) == 3
        assert skipped == []

    def test_out_of_bounds_month_stops_the_run(self):
        cand = _cand({
            "2026-01-31": (0.02, 0.005, 0.01),
            "2026-02-28": (0.70, 0.004, 0.008),   # +70% — implausible
            "2026-03-31": (0.03, 0.006, 0.012),
        })
        valid, skipped = df._validate_extension_months(
            cand, pd.Timestamp("2025-12-31"), pd.Timestamp("2026-03-31"))
        # Only the valid contiguous prefix is kept — March is not stored
        # because storing it would leave a February-shaped gap.
        assert list(valid.index) == [pd.Timestamp("2026-01-31")]
        assert any("2026-02" in s for s in skipped)

    def test_missing_month_stops_the_run(self):
        cand = _cand({
            "2026-01-31": (0.02, 0.005, 0.01),
            "2026-03-31": (0.03, 0.006, 0.012),   # February absent
        })
        valid, skipped = df._validate_extension_months(
            cand, pd.Timestamp("2025-12-31"), pd.Timestamp("2026-03-31"))
        assert list(valid.index) == [pd.Timestamp("2026-01-31")]
        assert any("2026-02" in s and "missing" in s for s in skipped)


# ── _extension_registry_entries ───────────────────────────────────────────────

class TestExtensionRegistryEntries:
    def test_four_entries_with_documented_hyg_source_change(self):
        entries = df._extension_registry_entries("2026-01-31", "2026-04-30", 4)
        ids = {e["series_id"] for e in entries}
        assert ids == {"equity_monthly_yf", "ig_monthly_bnd_yf",
                       "hy_monthly_hyg_yf", "risk_free_dtb3_fred"}
        hyg = next(e for e in entries if e["series_id"] == "hy_monthly_hyg_yf")
        # The HYG → BAMLHYH source change is documented in the registry,
        # not just a code comment.
        assert "BAMLHYH" in hyg["source_detail"]["proxy_for"]
        assert "source_change_note" in hyg["source_detail"]


# ── extend_market_data — fail-open ────────────────────────────────────────────

class TestExtendMarketData:
    def test_no_database_is_a_clean_status_not_a_raise(self, monkeypatch):
        import database
        monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
        out = df.extend_market_data()
        assert out["status"] == "no_database"
        assert out["monthly_rows_added"] == 0

    def test_returns_the_full_result_shape(self, monkeypatch):
        import database
        monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
        out = df.extend_market_data()
        for key in ("monthly_rows_added", "monthly_new_max",
                    "monthly_skipped", "status"):
            assert key in out


# ── POST /api/v1/admin/refresh-monthly-data ───────────────────────────────────

class TestRefreshMonthlyDataEndpoint:
    def test_requires_manage_users(self):
        # A viewer is refused — the endpoint is sysadmin-only.
        resp = _client.post("/api/v1/admin/refresh-monthly-data",
                            headers=_VIEWER)
        assert resp.status_code == 403

    def test_unauthenticated_is_401(self):
        assert _client.post(
            "/api/v1/admin/refresh-monthly-data").status_code == 401

    def test_sysadmin_gets_the_result_shape(self):
        # No database in the test env → extend_market_data reports a
        # status; the endpoint contract is a 200 with the result fields.
        resp = _client.post("/api/v1/admin/refresh-monthly-data",
                            headers=_SYSADMIN)
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body and "monthly_rows_added" in body
