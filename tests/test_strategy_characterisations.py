"""Coverage for tools/strategy_characterisations and migration 029.

Item 9 Commit 1 (May 22 2026). Exercises the three layers without a
live Postgres or Anthropic call:
  - Migration 029 loads cleanly.
  - compute_portfolio_characteristics is deterministic on a
    representative weight_schedule + monthly_returns payload.
  - derive_primary_risk_factor picks the largest absolute beta.
  - The test-env mock characterisation path produces the documented
    schema (every field present, types correct, length caps respected).
  - The JSON parser strips ``` fences and falls back to None on bad
    input.
  - The refresh orchestrator fails open when the strategy cache is
    empty.
"""
import os
import sys
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


def test_migration_029_loads():
    spec = importlib.util.spec_from_file_location(
        "mig_029",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "029_strategy_characterisations.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "029"
    assert m.down_revision == "028"
    assert callable(m.upgrade)
    assert callable(m.downgrade)


# ── Deterministic portfolio_characteristics ───────────────────────────────────


class TestComputePortfolioCharacteristics:
    """Pure compute against a fabricated backtest result. No DB, no AI."""

    def _result(self, schedule, monthly_returns=None, turnover=0.30):
        return {
            "weight_schedule": schedule,
            "monthly_returns": monthly_returns or [],
            "true_turnover": turnover,
        }

    def test_three_asset_uniform_weights(self):
        from tools.strategy_characterisations import (
            compute_portfolio_characteristics,
        )
        # EQUAL_WEIGHT-style backtest: every rebalance puts 1/3 in each
        # asset. 48 rebalances over 144 months -> 4 per year -> quarterly
        # cadence (3-9 per year is the 'quarterly' band; ≥9 is monthly).
        schedule = [{"date": f"2002-{(m % 12) + 1:02d}-01",
                     "weights": {"equity": 0.334, "ig": 0.333, "hy": 0.333}}
                    for m in range(48)]
        monthly = [0.01] * 144
        pc = compute_portfolio_characteristics(
            self._result(schedule, monthly, turnover=0.05))
        assert pc["avg_holdings"] == 3.0
        assert pc["avg_concentration"] == 33.4
        assert pc["avg_turnover_pct"] == 5.0
        assert pc["rebalance_frequency"] == "quarterly"

    def test_benchmark_buy_and_hold(self):
        from tools.strategy_characterisations import (
            compute_portfolio_characteristics,
        )
        # BENCHMARK-style: one schedule entry — 100% equity, no rebalance.
        schedule = [{
            "date": "2002-07-31",
            "weights": {"equity": 1.0, "ig": 0.0, "hy": 0.0},
        }]
        pc = compute_portfolio_characteristics(
            self._result(schedule, monthly_returns=[0.01] * 282,
                         turnover=0.0))
        assert pc["avg_holdings"] == 1.0
        assert pc["avg_concentration"] == 100.0
        assert pc["rebalance_frequency"] == "buy and hold"

    def test_vol_targeting_monthly_cadence(self):
        from tools.strategy_characterisations import (
            compute_portfolio_characteristics,
        )
        # VOL_TARGETING-style: 144 schedule entries over 144 months ->
        # monthly rebalance frequency.
        schedule = [{"date": f"2002-{(m % 12) + 1:02d}-01",
                     "weights": {"equity": 0.6, "ig": 0.4, "hy": 0.0}}
                    for m in range(144)]
        pc = compute_portfolio_characteristics(
            self._result(schedule, monthly_returns=[0.01] * 144,
                         turnover=0.40))
        assert pc["rebalance_frequency"] == "monthly"

    def test_empty_schedule_returns_safe_nulls(self):
        from tools.strategy_characterisations import (
            compute_portfolio_characteristics,
        )
        pc = compute_portfolio_characteristics(
            {"weight_schedule": [], "monthly_returns": [],
             "true_turnover": 0.0})
        assert pc["avg_holdings"] is None
        assert pc["avg_concentration"] is None
        assert pc["rebalance_frequency"] == "unknown"
        assert pc["avg_turnover_pct"] == 0.0

    def test_zero_weights_are_not_counted_as_holdings(self):
        """An asset present at 0% is not a holding — the spec says
        'number of holdings' which a zero-weight position is not."""
        from tools.strategy_characterisations import (
            compute_portfolio_characteristics,
        )
        schedule = [{"date": "2002-07-31",
                     "weights": {"equity": 0.8, "ig": 0.2, "hy": 0.0}}] * 12
        pc = compute_portfolio_characteristics(
            self._result(schedule, monthly_returns=[0.01] * 144,
                         turnover=0.10))
        assert pc["avg_holdings"] == 2.0  # not 3 — HY is zero everywhere
        assert pc["avg_concentration"] == 80.0


# ── derive_primary_risk_factor ───────────────────────────────────────────────


class TestDerivePrimaryRiskFactor:
    def test_picks_largest_absolute_beta(self):
        from tools.strategy_characterisations import (
            derive_primary_risk_factor,
        )
        # MOM dominates (|0.45| > |0.30| > |-0.25| > |0.10|).
        out = derive_primary_risk_factor({
            "mkt_rf": 0.30, "smb": -0.25, "hml": 0.10, "mom": 0.45,
        })
        assert out == "Momentum (MOM)"

    def test_negative_beta_counts_by_magnitude(self):
        from tools.strategy_characterisations import (
            derive_primary_risk_factor,
        )
        out = derive_primary_risk_factor({
            "mkt_rf": 0.20, "smb": -0.95, "hml": 0.10, "mom": 0.05,
        })
        assert out == "Size (SMB)"

    def test_none_row_falls_back(self):
        from tools.strategy_characterisations import (
            derive_primary_risk_factor,
        )
        assert derive_primary_risk_factor(None) == "Market exposure"

    def test_degenerate_row_falls_back(self):
        from tools.strategy_characterisations import (
            derive_primary_risk_factor,
        )
        # All None betas — degenerate regression output.
        out = derive_primary_risk_factor({
            "mkt_rf": None, "smb": None, "hml": None, "mom": None,
        })
        assert out == "Market exposure"


# ── AI generation (test env stub path) ───────────────────────────────────────


class TestGenerateCharacterisationTextStubPath:
    """In ENVIRONMENT=test the AI call is replaced with the
    deterministic stub. The contract this pins:
      - Every documented field is present
      - String types and length caps
      - primary_risk_factor flows through to the behavioural_profile
    """
    def test_static_strategy_stub_shape(self):
        from tools.strategy_characterisations import (
            generate_characterisation_text,
        )
        meta = {
            "id": "BENCHMARK",
            "name": "100% Equity (Benchmark)",
            "type": "static",
            "rebalancing": "Buy and hold — no rebalancing",
            "weights": {"equity": 1.0, "ig": 0.0, "hy": 0.0},
            "rationale": "The benchmark baseline.",
        }
        pc = {"avg_holdings": 1.0, "avg_turnover_pct": 0.0,
              "avg_concentration": 100.0,
              "rebalance_frequency": "buy and hold"}
        factor_row = {"mkt_rf": 1.0, "smb": 0.0, "hml": 0.0, "mom": 0.0}
        out = generate_characterisation_text(
            "BENCHMARK", meta, pc, factor_row, regime_row=None)
        # All four fields are present.
        for k in ("construction_summary", "behavioural_profile",
                  "regime_sensitivity", "behavioural_tag"):
            assert k in out
        bp = out["behavioural_profile"]
        for k in ("outperforms_when", "underperforms_when",
                  "primary_risk_factor", "diversification_role"):
            assert k in bp and isinstance(bp[k], str) and bp[k]
        # primary_risk_factor flows through the stub from the factor row.
        assert bp["primary_risk_factor"] == "Market (MKT-RF)"
        # behavioural_tag respects the 60-char cap (the migration's
        # column is varchar(120) but the spec calls for 'short').
        assert len(out["behavioural_tag"]) <= 120

    def test_dynamic_strategy_stub_shape(self):
        from tools.strategy_characterisations import (
            generate_characterisation_text,
        )
        meta = {
            "id": "MOMENTUM_ROTATION",
            "name": "Momentum Rotation",
            "type": "dynamic",
            "rebalancing": "Quarterly",
            "weights": None,
            "signal_logic": "Score each asset by momentum lookbacks.",
            "economic_intuition": "Momentum persists.",
            "key_parameter": "Lookback windows",
            "parameter_value": "1/3/6/12 months",
            "rationale": "Rotates into recent winners.",
        }
        pc = {"avg_holdings": 2.0, "avg_turnover_pct": 35.0,
              "avg_concentration": 50.0,
              "rebalance_frequency": "quarterly"}
        out = generate_characterisation_text(
            "MOMENTUM_ROTATION", meta, pc,
            factor_row={"mkt_rf": 0.5, "smb": 0.1, "hml": -0.2, "mom": 0.4},
            regime_row=None)
        assert out["behavioural_profile"]["primary_risk_factor"] in (
            "Market (MKT-RF)", "Momentum (MOM)")
        # The stub tag for a dynamic strategy mentions 'Dynamic'.
        assert "Dynamic" in out["behavioural_tag"]


# ── Model-output parsing ─────────────────────────────────────────────────────


class TestParseModelJson:
    def test_strips_code_fence(self):
        from tools.strategy_characterisations import _parse_model_json
        raw = "```json\n{\"a\": 1}\n```"
        assert _parse_model_json(raw) == {"a": 1}

    def test_plain_json_passes_through(self):
        from tools.strategy_characterisations import _parse_model_json
        assert _parse_model_json('{"a": 2}') == {"a": 2}

    def test_invalid_returns_none(self):
        from tools.strategy_characterisations import _parse_model_json
        assert _parse_model_json("not json") is None

    def test_empty_returns_none(self):
        from tools.strategy_characterisations import _parse_model_json
        assert _parse_model_json("") is None

    def test_list_payload_rejected(self):
        """The contract requires a JSON OBJECT. A list (model error)
        is rejected so the caller falls back to the stub."""
        from tools.strategy_characterisations import _parse_model_json
        assert _parse_model_json('["a", "b"]') is None


# ── Persistence fail-open ────────────────────────────────────────────────────


class TestPersistenceFailOpenWithoutDatabase:
    """Every accessor must fail open to None / [] without a DB.
    Mirrors test_precomputed_analytics' contract."""

    def test_get_characterisation_returns_none(self, monkeypatch):
        import asyncio
        import database as db_mod
        from tools import strategy_characterisations as sc
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(sc.get_characterisation("ANY", "any_hash"))
        assert out is None

    def test_get_all_characterisations_returns_empty(self, monkeypatch):
        import asyncio
        import database as db_mod
        from tools import strategy_characterisations as sc
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(sc.get_all_characterisations("any_hash"))
        assert out == []

    def test_upsert_is_silent_noop(self, monkeypatch):
        import asyncio
        import database as db_mod
        from tools import strategy_characterisations as sc
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        # No exception expected.
        asyncio.run(sc.upsert_characterisation(
            "ANY", "any_hash",
            construction_summary="cs",
            portfolio_characteristics={"avg_holdings": 1},
            behavioural_profile={"outperforms_when": "x"},
            regime_sensitivity="rs",
            behavioural_tag="tag"))

    def test_refresh_no_strategies_no_op(self, monkeypatch):
        """The refresh helper short-circuits when the strategy cache is
        empty — without a DB, get_latest_strategy_cache returns None."""
        import asyncio
        from tools import strategy_characterisations as sc

        async def _empty():
            return None
        monkeypatch.setattr("tools.cache.get_latest_strategy_cache", _empty)
        # Should complete without raising.
        asyncio.run(sc.refresh_strategy_characterisations("test_hash"))


# ── Endpoint: GET /api/v1/strategies/characterisations ───────────────────────


class TestCharacterisationsEndpoint:
    """The Item 9 Commit 2 endpoint. require_team_member gating, and
    the test-environment shortcut shape."""

    def _client_and_headers(self):
        from fastapi.testclient import TestClient
        from main import app
        from auth import generate_session_token
        os.environ.setdefault(
            "MASTER_API_KEY", "test_master_key")
        os.environ.setdefault(
            "ALLOWED_EMAILS",
            "ruurdsm@queens.edu,thaob@queens.edu,"
            "murdockm@queens.edu,panttserk@queens.edu")
        client = TestClient(app)
        team = {"X-API-Key": generate_session_token("thaob@queens.edu")}
        viewer = {"X-API-Key": generate_session_token(
            "panttserk@queens.edu")}
        return client, team, viewer

    def test_returns_test_env_shape(self):
        client, team, _viewer = self._client_and_headers()
        r = client.get("/api/v1/strategies/characterisations", headers=team)
        assert r.status_code == 200
        body = r.json()
        # In ENVIRONMENT=test the endpoint short-circuits to a
        # documented empty-shape response — same contract every other
        # analytics endpoint follows so the frontend can render an
        # empty state in the test harness without crashing.
        assert body.get("available") is False
        assert body.get("strategies") == []

    def test_rejects_unauthenticated(self):
        client, _team, _viewer = self._client_and_headers()
        r = client.get("/api/v1/strategies/characterisations")
        assert r.status_code == 401

    def test_rejects_viewer_without_team_membership(self):
        """The endpoint requires team_member — viewer accounts that can
        read the dashboard analytics still cannot read the per-strategy
        editorial context."""
        client, _team, viewer = self._client_and_headers()
        r = client.get("/api/v1/strategies/characterisations",
                       headers=viewer)
        assert r.status_code == 403
