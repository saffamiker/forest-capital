"""
tests/test_provenance_justification.py

Verifies GET /api/v1/provenance/justification returns structured metadata
describing why each supplemental data source is included.

Each supplemental source must carry:
  - strategies_enabled: which strategies depend on it
  - key_reason: one-sentence methodological justification
  - months_added: additional historical observations provided (0 if none)
  - statistical_impact: concrete impact on the study
  - without_this_source: what breaks if the source is removed
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-justification-tests")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

REQUIRED_FIELDS = {"strategies_enabled", "key_reason", "months_added", "statistical_impact", "without_this_source"}
REQUIRED_SOURCES = {"spy_daily", "vixcls", "dgs2", "lqd_bridge"}


def _client() -> TestClient:
    from main import app  # type: ignore[import]
    return TestClient(app)


class TestProvenanceJustificationEndpoint:
    """GET /api/v1/provenance/justification returns 200 with all four supplemental sources."""

    def test_endpoint_returns_200(self):
        resp = _client().get("/api/v1/provenance/justification")
        assert resp.status_code == 200, (
            f"Expected 200 from /api/v1/provenance/justification, got {resp.status_code}"
        )

    def test_all_four_sources_present(self):
        data = _client().get("/api/v1/provenance/justification").json()
        missing = REQUIRED_SOURCES - set(data.keys())
        assert not missing, f"Missing supplemental sources from justification response: {missing}"

    def test_spy_daily_has_required_fields(self):
        data = _client().get("/api/v1/provenance/justification").json()
        spy = data["spy_daily"]
        missing = REQUIRED_FIELDS - set(spy.keys())
        assert not missing, f"spy_daily missing fields: {missing}"

    def test_vixcls_has_required_fields(self):
        data = _client().get("/api/v1/provenance/justification").json()
        vix = data["vixcls"]
        missing = REQUIRED_FIELDS - set(vix.keys())
        assert not missing, f"vixcls missing fields: {missing}"

    def test_dgs2_has_required_fields(self):
        data = _client().get("/api/v1/provenance/justification").json()
        dgs2 = data["dgs2"]
        missing = REQUIRED_FIELDS - set(dgs2.keys())
        assert not missing, f"dgs2 missing fields: {missing}"

    def test_lqd_bridge_has_required_fields(self):
        data = _client().get("/api/v1/provenance/justification").json()
        lqd = data["lqd_bridge"]
        missing = REQUIRED_FIELDS - set(lqd.keys())
        assert not missing, f"lqd_bridge missing fields: {missing}"


class TestProvenanceJustificationContent:
    """Validates the semantic content of each supplemental source justification."""

    def test_lqd_bridge_adds_months(self):
        """LQD bridge extends IG history by ~57-58 months — this is the key power argument."""
        data = _client().get("/api/v1/provenance/justification").json()
        months = data["lqd_bridge"]["months_added"]
        assert isinstance(months, int), "months_added must be an integer"
        assert months >= 50, (
            f"lqd_bridge months_added={months} — expected ≥50 (BND starts April 2007, "
            f"LQD bridge extends back to July 2002)"
        )

    def test_spy_daily_adds_no_months(self):
        """SPY daily data enables strategies but doesn't extend the date range."""
        data = _client().get("/api/v1/provenance/justification").json()
        assert data["spy_daily"]["months_added"] == 0, (
            "spy_daily should not add months — it enables intramonth signals, not extend history"
        )

    def test_vol_targeting_in_spy_strategies(self):
        """VOL_TARGETING requires SPY daily data to compute 21-day rolling volatility."""
        data = _client().get("/api/v1/provenance/justification").json()
        strategies = data["spy_daily"]["strategies_enabled"]
        assert any("VOL" in s.upper() or "VOLATILITY" in s.upper() for s in strategies), (
            f"VOL_TARGETING must appear in spy_daily.strategies_enabled. Got: {strategies}"
        )

    def test_momentum_rotation_in_spy_strategies(self):
        """MOMENTUM_ROTATION requires SPY daily for month-end signal computation."""
        data = _client().get("/api/v1/provenance/justification").json()
        strategies = data["spy_daily"]["strategies_enabled"]
        assert any("MOMENTUM" in s.upper() for s in strategies), (
            f"MOMENTUM_ROTATION must appear in spy_daily.strategies_enabled. Got: {strategies}"
        )

    def test_regime_switching_in_vixcls_strategies(self):
        """REGIME_SWITCHING uses VIX as a forward-looking fear signal for threshold classification."""
        data = _client().get("/api/v1/provenance/justification").json()
        strategies = data["vixcls"]["strategies_enabled"]
        assert any("REGIME" in s.upper() for s in strategies), (
            f"REGIME_SWITCHING must appear in vixcls.strategies_enabled. Got: {strategies}"
        )

    def test_regime_switching_in_dgs2_strategies(self):
        """REGIME_SWITCHING uses 10Y-2Y yield curve (DGS10 from Excel minus DGS2 from FRED)."""
        data = _client().get("/api/v1/provenance/justification").json()
        strategies = data["dgs2"]["strategies_enabled"]
        assert any("REGIME" in s.upper() for s in strategies), (
            f"REGIME_SWITCHING must appear in dgs2.strategies_enabled. Got: {strategies}"
        )

    def test_lqd_bridge_enables_all_strategies(self):
        """LQD bridge extends the dataset — all 10 strategies benefit."""
        data = _client().get("/api/v1/provenance/justification").json()
        strategies = data["lqd_bridge"]["strategies_enabled"]
        assert len(strategies) >= 5, (
            f"lqd_bridge should enable ≥5 strategies (or indicate 'All 10'). Got: {strategies}"
        )

    def test_key_reason_is_non_empty_string(self):
        """Each source must carry a non-trivial methodological justification."""
        data = _client().get("/api/v1/provenance/justification").json()
        for source_key, source in data.items():
            reason = source.get("key_reason", "")
            assert isinstance(reason, str) and len(reason) > 20, (
                f"{source_key}.key_reason is too short or missing: {reason!r}"
            )

    def test_without_this_source_is_non_empty_string(self):
        """Each source must describe what breaks if removed — motivates inclusion."""
        data = _client().get("/api/v1/provenance/justification").json()
        for source_key, source in data.items():
            without = source.get("without_this_source", "")
            assert isinstance(without, str) and len(without) > 10, (
                f"{source_key}.without_this_source is too short or missing: {without!r}"
            )

    def test_strategies_enabled_are_lists(self):
        """strategies_enabled must be a list (not a string) for all sources."""
        data = _client().get("/api/v1/provenance/justification").json()
        for source_key, source in data.items():
            strategies = source.get("strategies_enabled")
            assert isinstance(strategies, list), (
                f"{source_key}.strategies_enabled must be a list, got {type(strategies)}"
            )

    def test_statistical_impact_is_non_empty_string(self):
        """Each source must quantify or describe its impact on the analysis."""
        data = _client().get("/api/v1/provenance/justification").json()
        for source_key, source in data.items():
            impact = source.get("statistical_impact", "")
            assert isinstance(impact, str) and len(impact) > 10, (
                f"{source_key}.statistical_impact is too short or missing: {impact!r}"
            )
