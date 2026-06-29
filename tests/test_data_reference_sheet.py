"""tests/test_data_reference_sheet.py -- GET /api/v1/export/
data-reference-sheet endpoint + the underlying catalog
(June 22 2026).

Contract pinned by these tests:
  1. Endpoint returns 200 + the documented response shape.
  2. Every expected category present in the response.
  3. Every catalog token has all required fields (token,
     label, value, source, is_locked, last_verified,
     document_locations).
  4. is_locked classification is correct -- academic_deck
     constants are locked; cache/cio reads are not.
  5. last_verified carries the locked-sentinel for locked
     entries and a cache timestamp (or 'cache miss') for
     live entries.
  6. Per-strategy expansion produces 50 appendix rows and
     50 factor-loading rows (10 strategies x 5 each).
  7. Both hashes (strategy_hash, platform_fingerprint) are
     surfaced.
  8. Endpoint accessible to a non-admin team session.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from fastapi.testclient import TestClient


# ── Catalog -- static structure tests ────────────────────────────────────


class TestCatalogStructure:
    """The catalog is the source of truth for which tokens appear
    in the reference sheet. These tests pin the categorisation +
    the per-strategy expansion so a refactor can't silently drop
    a token."""

    def test_every_catalog_entry_has_required_fields(self):
        from tools.data_reference_catalog import CATALOG
        for category_key, category_label, entries in CATALOG:
            assert isinstance(category_key, str)
            assert isinstance(category_label, str)
            assert len(entries) >= 1, (
                f"category {category_key} has no entries")
            for entry in entries:
                assert entry.token.startswith("{{")
                assert entry.token.endswith("}}")
                assert entry.label
                assert entry.source
                assert isinstance(entry.is_locked, bool)
                assert (isinstance(entry.document_locations, tuple)
                        and len(entry.document_locations) >= 1)

    def test_expected_categories_present(self):
        from tools.data_reference_catalog import CATALOG
        category_keys = {k for k, _, _ in CATALOG}
        for expected in (
            "study_period", "oos_window",
            "full_period_performance", "pre_post_2022",
            "drawdown_recovery", "correlation",
            "live_regime", "cost_sensitivity",
            "play_by_play",
        ):
            assert expected in category_keys

    def test_locked_vs_live_classification_is_correct(self):
        """academic_deck constants are locked; strategy_cache /
        cio_recommendation / regime_signals_cache reads are not.
        Pin specific examples so the classification can't drift."""
        from tools.data_reference_catalog import CATALOG
        flat = {
            e.token: e
            for _, _, entries in CATALOG for e in entries
        }
        # Locked academic constants.
        assert flat["{{OOS_SHARPE_BLEND}}"].is_locked is True
        assert flat["{{OOS_SHARPE_BENCHMARK}}"].is_locked is True
        assert flat["{{REGIME_SWITCHING_MAX_DD}}"].is_locked is True
        assert flat["{{BENCHMARK_MAX_DD}}"].is_locked is True
        assert flat["{{PRE_2022_EQ_IG_CORR}}"].is_locked is True
        assert flat["{{POST_2022_EQ_IG_CORR}}"].is_locked is True
        assert flat["{{PLAY_BY_PLAY_VALUE_ADD}}"].is_locked is True
        assert flat["{{OOS_WINDOW_MONTHS}}"].is_locked is True
        # Live cache reads.
        assert flat["{{REGIME_SWITCHING_SHARPE}}"].is_locked is False
        assert flat["{{BENCHMARK_SHARPE}}"].is_locked is False
        assert flat["{{CURRENT_REGIME}}"].is_locked is False
        assert flat["{{VIX_CURRENT}}"].is_locked is False
        assert flat["{{NET_SHARPE_10BP}}"].is_locked is False
        assert flat["{{CURRENT_EQUITY_PCT}}"].is_locked is False

    def test_per_strategy_appendix_expansion_15_rows(self):
        """3 submission-scope strategies x 5 metrics = 15 rows.

        June 29 2026 (token-registry PR) -- restricted from the
        prior 10-strategy expansion (50 rows) to the submission
        scope (BENCHMARK + CLASSIC_60_40 + REGIME_SWITCHING) so
        the data reference sheet doesn't surface tokens for the
        7 out-of-scope strategies that the brief / appendix / deck
        no longer reference. Test name + assertion updated to
        match the new shape; the row-content pins below remain
        unchanged."""
        from tools.data_reference_catalog import (
            expand_per_strategy_appendix_metrics,
        )
        rows = expand_per_strategy_appendix_metrics()
        assert len(rows) == 15
        # All rows mark is_locked=False since they read from the
        # live strategy cache.
        for row in rows:
            assert row.is_locked is False
            assert row.source.startswith("strategy_cache.")
        # Out-of-scope leak guard.
        for row in rows:
            for prefix in (
                "EQUAL_WEIGHT_", "RISK_PARITY_", "MIN_VARIANCE_",
                "VOL_TARGETING_", "BLACK_LITTERMAN_",
                "MOMENTUM_ROTATION_", "MAX_SHARPE_ROLLING_",
            ):
                assert prefix not in row.token, (
                    f"out-of-scope token leaked: {row.token}")

    def test_per_strategy_factor_loadings_expansion_15_rows(self):
        """3 submission-scope strategies x 5 columns = 15 rows.
        June 29 2026 (token-registry PR) -- same scope
        restriction as appendix-metrics expansion."""
        from tools.data_reference_catalog import (
            expand_per_strategy_factor_loadings,
        )
        rows = expand_per_strategy_factor_loadings()
        assert len(rows) == 15
        # Capture the metric segment of every source string so
        # we can pin which field keys the resolver will read.
        metric_keys: set[str] = set()
        for row in rows:
            assert row.is_locked is False
            assert row.source.startswith("data.factor_loadings.")
            # Source shape: data.factor_loadings.<STRATEGY>.<metric>
            parts = row.source.split(".")
            assert len(parts) == 4, (
                f"unexpected source shape: {row.source}")
            metric_keys.add(parts[-1])
        # The five metric keys MUST be exactly what
        # tools.analytics.factor_loadings writes per row.
        assert metric_keys == {
            "alpha_annualized",
            "mkt_rf",
            "smb",
            "hml",
            "r_squared",
        }, f"unexpected metric keys: {metric_keys}"

    def test_factor_loading_metrics_match_analytics_output_shape(self):
        """FACTOR_LOADING_METRICS keys MUST be exactly the field
        names tools.analytics.factor_loadings actually writes.
        Hard-pin them so a future rename in either direction
        breaks this test rather than silently rendering em-dashes
        across the data reference sheet's factor-loadings
        category."""
        from tools.data_reference_catalog import (
            FACTOR_LOADING_METRICS,
        )
        keys = {k for k, _label in FACTOR_LOADING_METRICS}
        assert keys == {
            "alpha_annualized",
            "mkt_rf",
            "smb",
            "hml",
            "r_squared",
        }

    def test_oos_window_constants_in_catalog(self):
        """The three new constants from PR A must appear in the
        catalog so the reference sheet surfaces them."""
        from tools.data_reference_catalog import CATALOG
        flat = {
            e.token: e
            for _, _, entries in CATALOG for e in entries
        }
        assert "{{OOS_WINDOW_MONTHS}}" in flat
        assert "{{OOS_WINDOW_PCT_OF_STUDY}}" in flat
        # CURRENT_EQUITY_WEIGHT is a constant that doesn't
        # have a substitution token; it's exposed via
        # validated_constants for the story plan. Its live
        # counterpart {{CURRENT_EQUITY_PCT}} (implied from
        # the live blend) IS in the live_regime category.
        assert "{{CURRENT_EQUITY_PCT}}" in flat


# ── Endpoint integration tests ───────────────────────────────────────────


class TestEndpointBasics:

    def _client(self):
        from auth import generate_session_token
        from main import app
        client = TestClient(app)
        # Use a non-admin team email to verify the endpoint isn't
        # admin-gated. ruurdsm is admin but the other members
        # (thaob, murdockm) are regular team accounts.
        return client, generate_session_token("thaob@queens.edu")

    def test_endpoint_returns_200(self):
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        assert r.status_code == 200

    def test_response_shape(self):
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        data = r.json()
        assert "data_hash" in data
        assert "platform_fingerprint" in data
        assert "generated_at" in data
        assert "categories" in data
        assert isinstance(data["categories"], dict)
        # Generated-at is ISO-8601.
        assert "T" in data["generated_at"]

    def test_expected_categories_in_response(self):
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        data = r.json()
        for key in (
            "study_period", "oos_window",
            "full_period_performance", "pre_post_2022",
            "drawdown_recovery", "correlation",
            "live_regime", "cost_sensitivity",
            "play_by_play",
            "per_strategy_appendix", "factor_loadings",
        ):
            assert key in data["categories"], (
                f"missing category {key}")

    def test_per_strategy_appendix_category_carries_15_rows(self):
        # June 29 2026 (token-registry PR) -- restricted to the
        # 3 submission-scope strategies.
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        cat = r.json()["categories"]["per_strategy_appendix"]
        assert len(cat["entries"]) == 15

    def test_factor_loadings_category_carries_15_rows(self):
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        cat = r.json()["categories"]["factor_loadings"]
        assert len(cat["entries"]) == 15

    def test_every_response_entry_has_required_fields(self):
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        data = r.json()
        for cat in data["categories"].values():
            for entry in cat["entries"]:
                for field in (
                    "token", "label", "value", "source",
                    "is_locked", "last_verified",
                    "document_locations",
                ):
                    assert field in entry, (
                        f"entry {entry.get('token')} missing "
                        f"field {field}")

    def test_locked_entries_carry_locked_sentinel(self):
        """is_locked=True rows render their last_verified as
        the 'locked at submission' sentinel rather than a
        cache timestamp."""
        client, token = self._client()
        r = client.get(
            "/api/v1/export/data-reference-sheet",
            headers={"X-API-Key": token})
        data = r.json()
        for cat in data["categories"].values():
            for entry in cat["entries"]:
                if entry["is_locked"]:
                    assert "locked at submission" in (
                        entry["last_verified"])

    def test_accessible_to_non_admin_team_member(self):
        """Bob, Molly, and Mike are all team members. The
        endpoint must work for any of them -- no admin gate."""
        from auth import generate_session_token
        from main import app
        client = TestClient(app)
        for email in (
            "thaob@queens.edu",
            "murdockm@queens.edu",
            "ruurdsm@queens.edu",
        ):
            token = generate_session_token(email)
            r = client.get(
                "/api/v1/export/data-reference-sheet",
                headers={"X-API-Key": token})
            assert r.status_code == 200, (
                f"endpoint denied for {email}")

    def test_unauthenticated_request_blocked(self):
        from main import app
        client = TestClient(app)
        r = client.get("/api/v1/export/data-reference-sheet")
        assert r.status_code in (401, 403)
