"""Coverage for the AN01 / AN04 completeness pipeline (May 24 2026).

Three layers exercised here:

  1. precomputed_analytics validators —
     _validate_factor_loadings, _validate_regime_conditional,
     _validate_transition_matrix. Pure functions: given a payload shape,
     produce a structured verdict.

  2. ensure_qa_data_complete pre-flight —
     fetches academic_analytics + transition_matrix from
     analytics_metrics_cache, triggers refresh on miss/incomplete,
     re-reads. Fail-open: a database miss returns a completeness map
     of False rather than raising.

  3. QAAgent._evaluate_carhart_completeness +
     _evaluate_regime_completeness —
     consume the pre-flight output and produce (status, evidence)
     pairs for the deterministic AN01 / AN04 checks.

Behaviour-focused: the validators are pure so they're exercised against
crafted payload shapes without a live Postgres. The pre-flight and the
agent helpers are exercised against in-memory fixtures rather than the
database, mirroring the broader test suite's fail-open contract.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)


# ── Layer 1: validators ───────────────────────────────────────────────────────

class TestFactorLoadingsValidator:
    """_validate_factor_loadings verifies the Carhart table is structurally
    complete: every row carries MKT-RF / SMB / HML / MOM betas, annualised
    alpha, R-squared in [0, 1], and per-coefficient significance flags."""

    def test_complete_payload_passes(self):
        from tools.precomputed_analytics import _validate_factor_loadings
        rows = [{
            "strategy": "BENCHMARK", "model": "carhart_4factor",
            "mkt_rf": 0.97, "mkt_rf_significant": True,
            "smb": 0.02, "smb_significant": False,
            "hml": -0.01, "hml_significant": False,
            "mom": 0.05, "mom_significant": True,
            "alpha_annualized": 0.012, "alpha_significant": True,
            "r_squared": 0.94,
        }]
        verdict = _validate_factor_loadings(rows)
        assert verdict["complete"] is True
        assert verdict["n_rows"] == 1
        assert verdict["invalid_rows"] == []

    def test_empty_table_fails_completeness(self):
        from tools.precomputed_analytics import _validate_factor_loadings
        verdict = _validate_factor_loadings([])
        assert verdict["complete"] is False
        assert "entire factor_loadings table" in verdict["missing_fields"]

    def test_r_squared_out_of_range_flagged(self):
        from tools.precomputed_analytics import _validate_factor_loadings
        rows = [{
            "strategy": "BAD", "model": "carhart_4factor",
            "mkt_rf": 0.97, "mkt_rf_significant": True,
            "smb": 0.0, "smb_significant": False,
            "hml": 0.0, "hml_significant": False,
            "mom": 0.0, "mom_significant": False,
            "alpha_annualized": 0.0, "alpha_significant": False,
            "r_squared": 1.5,  # invalid — R-squared must be in [0, 1]
        }]
        verdict = _validate_factor_loadings(rows)
        assert verdict["complete"] is False
        invalid = verdict["invalid_rows"][0]
        assert invalid["strategy"] == "BAD"
        assert any("r_squared_out_of_range" in m for m in invalid["missing"])

    def test_missing_mom_field_flagged(self):
        from tools.precomputed_analytics import _validate_factor_loadings
        # mom may be None for pre-backfill histories, but the field
        # itself must be present alongside its significance flag.
        rows = [{
            "strategy": "OLD", "model": "ff_3factor",
            "mkt_rf": 0.97, "mkt_rf_significant": True,
            "smb": 0.0, "smb_significant": False,
            "hml": 0.0, "hml_significant": False,
            "alpha_annualized": 0.0, "alpha_significant": False,
            "r_squared": 0.9,
            # mom + mom_significant intentionally absent
        }]
        verdict = _validate_factor_loadings(rows)
        assert verdict["complete"] is False
        invalid = verdict["invalid_rows"][0]
        assert any("mom" in m for m in invalid["missing"])

    def test_mom_present_as_none_passes(self):
        from tools.precomputed_analytics import _validate_factor_loadings
        # Three-factor fallback row: mom is None but the FIELD exists.
        rows = [{
            "strategy": "OLD", "model": "ff_3factor",
            "mkt_rf": 0.97, "mkt_rf_significant": True,
            "smb": 0.0, "smb_significant": False,
            "hml": 0.0, "hml_significant": False,
            "mom": None, "mom_significant": False,
            "alpha_annualized": 0.0, "alpha_significant": False,
            "r_squared": 0.9,
        }]
        verdict = _validate_factor_loadings(rows)
        assert verdict["complete"] is True


class TestRegimeConditionalValidator:
    """_validate_regime_conditional verifies pre/post-2022 Sharpe + CAGR
    are present. A None Sharpe is legitimate ONLY when months < 2."""

    def test_complete_payload_passes(self):
        from tools.precomputed_analytics import _validate_regime_conditional
        rows = [{
            "strategy": "BENCHMARK",
            "pre_2022_sharpe": 0.42, "post_2022_sharpe": 0.28,
            "pre_2022_cagr": 0.08, "post_2022_cagr": 0.05,
            "pre_2022_months": 240, "post_2022_months": 36,
        }]
        verdict = _validate_regime_conditional(rows)
        assert verdict["complete"] is True

    def test_none_sharpe_with_no_months_is_acceptable(self):
        from tools.precomputed_analytics import _validate_regime_conditional
        # A strategy with no pre-2022 history correctly reports
        # pre_2022_sharpe=None and pre_2022_months=0.
        rows = [{
            "strategy": "LATE_START",
            "pre_2022_sharpe": None, "post_2022_sharpe": 0.5,
            "pre_2022_cagr": None, "post_2022_cagr": 0.07,
            "pre_2022_months": 0, "post_2022_months": 36,
        }]
        verdict = _validate_regime_conditional(rows)
        assert verdict["complete"] is True

    def test_none_sharpe_with_months_is_flagged(self):
        from tools.precomputed_analytics import _validate_regime_conditional
        # A None Sharpe with 36 months of data is a computation gap.
        rows = [{
            "strategy": "BUG",
            "pre_2022_sharpe": None, "post_2022_sharpe": 0.5,
            "pre_2022_cagr": 0.08, "post_2022_cagr": 0.07,
            "pre_2022_months": 36, "post_2022_months": 36,
        }]
        verdict = _validate_regime_conditional(rows)
        assert verdict["complete"] is False
        assert any("pre_2022_sharpe_unexpectedly_null" in m
                   for m in verdict["invalid_rows"][0]["missing"])


class TestTransitionMatrixValidator:
    """_validate_transition_matrix verifies the 3x3 matrix carries
    BULL/BEAR/TRANSITION as originating regimes and every non-empty
    row sums to 1.0 within 1e-3 tolerance."""

    def test_complete_matrix_passes(self):
        from tools.precomputed_analytics import _validate_transition_matrix
        matrix = {
            "BULL":       {"BULL": 0.92, "BEAR": 0.03, "TRANSITION": 0.05},
            "BEAR":       {"BULL": 0.10, "BEAR": 0.80, "TRANSITION": 0.10},
            "TRANSITION": {"BULL": 0.30, "BEAR": 0.20, "TRANSITION": 0.50},
        }
        verdict = _validate_transition_matrix(matrix)
        assert verdict["complete"] is True
        assert all(abs(v - 1.0) < 1e-3
                   for v in verdict["row_sums"].values())

    def test_empty_row_is_complete_by_construction(self):
        from tools.precomputed_analytics import _validate_transition_matrix
        # A regime that never occurred has all-zero row — that is the
        # "no transitions observed" case, not a bug.
        matrix = {
            "BULL":       {"BULL": 1.0, "BEAR": 0.0, "TRANSITION": 0.0},
            "BEAR":       {"BULL": 0.0, "BEAR": 0.0, "TRANSITION": 0.0},
            "TRANSITION": {"BULL": 0.0, "BEAR": 0.0, "TRANSITION": 0.0},
        }
        verdict = _validate_transition_matrix(matrix)
        assert verdict["complete"] is True

    def test_row_sum_not_one_fails(self):
        from tools.precomputed_analytics import _validate_transition_matrix
        matrix = {
            "BULL":       {"BULL": 0.5, "BEAR": 0.3, "TRANSITION": 0.5},  # 1.3
            "BEAR":       {"BULL": 0.0, "BEAR": 1.0, "TRANSITION": 0.0},
            "TRANSITION": {"BULL": 0.5, "BEAR": 0.5, "TRANSITION": 0.0},
        }
        verdict = _validate_transition_matrix(matrix)
        assert verdict["complete"] is False
        assert any(r["regime"] == "BULL" for r in verdict["invalid_rows"])

    def test_missing_regime_fails(self):
        from tools.precomputed_analytics import _validate_transition_matrix
        matrix = {
            "BULL": {"BULL": 1.0, "BEAR": 0.0, "TRANSITION": 0.0},
            # BEAR + TRANSITION absent
        }
        verdict = _validate_transition_matrix(matrix)
        assert verdict["complete"] is False
        assert "BEAR" in verdict["missing_regimes"]
        assert "TRANSITION" in verdict["missing_regimes"]

    def test_non_dict_input_fails(self):
        from tools.precomputed_analytics import _validate_transition_matrix
        verdict = _validate_transition_matrix("not a dict")  # type: ignore[arg-type]
        assert verdict["complete"] is False


# ── Layer 2: validate_analytics_payload ───────────────────────────────────────

class TestPayloadValidator:
    """validate_analytics_payload wraps the two table validators and
    produces the top-level _completeness block attached to every cached
    academic_analytics row."""

    def test_complete_payload(self):
        from tools.precomputed_analytics import validate_analytics_payload
        payload = {
            "factor_loadings": [{
                "strategy": "BENCHMARK", "model": "carhart_4factor",
                "mkt_rf": 1.0, "mkt_rf_significant": True,
                "smb": 0.0, "smb_significant": False,
                "hml": 0.0, "hml_significant": False,
                "mom": 0.0, "mom_significant": False,
                "alpha_annualized": 0.01, "alpha_significant": True,
                "r_squared": 0.95,
            }],
            "regime_conditional": [{
                "strategy": "BENCHMARK",
                "pre_2022_sharpe": 0.5, "post_2022_sharpe": 0.3,
                "pre_2022_cagr": 0.08, "post_2022_cagr": 0.06,
                "pre_2022_months": 240, "post_2022_months": 36,
            }],
        }
        verdict = validate_analytics_payload(payload)
        assert verdict["complete"] is True
        assert verdict["factor_loadings"]["complete"] is True
        assert verdict["regime_conditional"]["complete"] is True
        assert "validated_at" in verdict

    def test_one_table_incomplete_fails_overall(self):
        from tools.precomputed_analytics import validate_analytics_payload
        payload = {
            "factor_loadings": [],  # empty — fails
            "regime_conditional": [{
                "strategy": "BENCHMARK",
                "pre_2022_sharpe": 0.5, "post_2022_sharpe": 0.3,
                "pre_2022_cagr": 0.08, "post_2022_cagr": 0.06,
                "pre_2022_months": 240, "post_2022_months": 36,
            }],
        }
        verdict = validate_analytics_payload(payload)
        assert verdict["complete"] is False
        assert verdict["factor_loadings"]["complete"] is False
        assert verdict["regime_conditional"]["complete"] is True


# ── Layer 3: QAAgent helpers ──────────────────────────────────────────────────

class TestCarhartEvaluation:
    """_evaluate_carhart_completeness consumes the pre-flight output
    (analytics_cache.completeness.factor_loadings) and produces a
    (status, evidence) verdict for the AN01 deterministic check."""

    def test_validated_complete_cache_passes(self):
        from agents.qa_agent import QAAgent
        analytics_cache = {
            "academic_analytics": {
                "factor_loadings": [
                    {"strategy": "BENCHMARK", "model": "carhart_4factor"},
                    {"strategy": "60/40", "model": "carhart_4factor"},
                ],
                "_completeness": {
                    "factor_loadings": {"complete": True, "n_rows": 2},
                },
            },
            "completeness": {"factor_loadings": True},
        }
        status, evidence = QAAgent._evaluate_carhart_completeness(
            strategy_results={}, analytics_cache=analytics_cache,
        )
        assert status == "PASS"
        assert "2 strategies" in evidence
        assert "carhart_4factor" in evidence

    def test_cache_incomplete_surfaces_gap(self):
        from agents.qa_agent import QAAgent
        analytics_cache = {
            "academic_analytics": {
                "factor_loadings": [
                    {"strategy": "BUG", "model": "carhart_4factor"},
                ],
                "_completeness": {
                    "factor_loadings": {
                        "complete": False, "n_rows": 1,
                        "invalid_rows": [
                            {"strategy": "BUG", "missing": ["r_squared_out_of_range:1.5"]}
                        ],
                    },
                },
            },
            "completeness": {"factor_loadings": False},
        }
        status, evidence = QAAgent._evaluate_carhart_completeness(
            strategy_results={}, analytics_cache=analytics_cache,
        )
        assert status == "WARN"
        assert "BUG" in evidence
        assert "r_squared_out_of_range" in evidence

    def test_no_cache_falls_back_to_per_strategy(self):
        from agents.qa_agent import QAAgent
        strategy_results = {
            "S1": {"factor_loadings": {
                "mkt_rf": 0.9, "smb": 0.0, "hml": 0.0, "mom": 0.0,
                "alpha": 0.01, "r_squared": 0.9,
            }},
        }
        status, evidence = QAAgent._evaluate_carhart_completeness(
            strategy_results=strategy_results, analytics_cache=None,
        )
        assert status == "PASS"
        assert "per-strategy inline" in evidence

    def test_no_cache_no_per_strategy_warns_with_remediation(self):
        from agents.qa_agent import QAAgent
        status, evidence = QAAgent._evaluate_carhart_completeness(
            strategy_results={}, analytics_cache=None,
        )
        assert status == "WARN"
        assert "ensure_qa_data_complete" in evidence


class TestRegimeEvaluation:
    """_evaluate_regime_completeness requires BOTH the regime_conditional
    table AND the transition_matrix row sums to be complete for PASS."""

    def _good_cache(self) -> dict:
        return {
            "academic_analytics": {
                "regime_conditional": [
                    {"strategy": "BENCHMARK"},
                ],
                "_completeness": {
                    "regime_conditional": {"complete": True, "n_rows": 1},
                },
            },
            "transition_matrix": {
                "matrix": {
                    "BULL":       {"BULL": 0.9, "BEAR": 0.05, "TRANSITION": 0.05},
                    "BEAR":       {"BULL": 0.1, "BEAR": 0.8, "TRANSITION": 0.1},
                    "TRANSITION": {"BULL": 0.3, "BEAR": 0.2, "TRANSITION": 0.5},
                },
                "_completeness": {
                    "complete": True,
                    "row_sums": {"BULL": 1.0, "BEAR": 1.0, "TRANSITION": 1.0},
                },
            },
            "completeness": {
                "regime_conditional": True,
                "transition_matrix": True,
            },
        }

    def test_both_complete_passes(self):
        from agents.qa_agent import QAAgent
        status, evidence = QAAgent._evaluate_regime_completeness(
            strategy_results={}, analytics_cache=self._good_cache(),
        )
        assert status == "PASS"
        assert "BULL=1.000" in evidence
        assert "BEAR=1.000" in evidence
        assert "TRANSITION=1.000" in evidence

    def test_regime_incomplete_warns(self):
        from agents.qa_agent import QAAgent
        cache = self._good_cache()
        cache["completeness"]["regime_conditional"] = False
        cache["academic_analytics"]["_completeness"]["regime_conditional"] = {
            "complete": False, "n_rows": 1,
            "invalid_rows": [{"strategy": "BUG",
                              "missing": ["pre_2022_sharpe_unexpectedly_null"]}],
        }
        status, evidence = QAAgent._evaluate_regime_completeness(
            strategy_results={}, analytics_cache=cache,
        )
        assert status == "WARN"
        assert "BUG" in evidence
        assert "pre_2022_sharpe_unexpectedly_null" in evidence

    def test_transition_matrix_incomplete_warns(self):
        from agents.qa_agent import QAAgent
        cache = self._good_cache()
        cache["completeness"]["transition_matrix"] = False
        cache["transition_matrix"]["_completeness"] = {
            "complete": False,
            "row_sums": {"BULL": 1.3, "BEAR": 1.0, "TRANSITION": 1.0},
            "invalid_rows": [{"regime": "BULL", "reason": "row_sum_1.300000"}],
        }
        status, evidence = QAAgent._evaluate_regime_completeness(
            strategy_results={}, analytics_cache=cache,
        )
        assert status == "WARN"
        assert "BULL" in evidence
        assert "row_sum" in evidence


# ── Layer 4: ensure_qa_data_complete (fail-open contract) ─────────────────────

class TestPreflightFailOpen:
    """The pre-flight must never raise. A database miss / outage returns
    a completeness map of False rather than crashing the audit."""

    @pytest.mark.asyncio
    async def test_returns_dict_even_on_full_failure(self, monkeypatch):
        from tools import precomputed_analytics as pa
        # Force every helper to return None — simulates an empty
        # analytics_metrics_cache.

        async def _none(*args, **kwargs):
            return None

        monkeypatch.setattr(pa, "get_metric", _none)
        monkeypatch.setattr(pa, "get_latest_metric", _none)
        monkeypatch.setattr(pa, "refresh_academic_analytics", _none)
        monkeypatch.setattr(pa, "refresh_transition_matrix", _none)

        result = await pa.ensure_qa_data_complete("test_hash")
        assert isinstance(result, dict)
        assert "completeness" in result
        # Every completeness field is False because nothing was fetched.
        assert result["completeness"]["factor_loadings"] is False
        assert result["completeness"]["regime_conditional"] is False
        assert result["completeness"]["transition_matrix"] is False

    @pytest.mark.asyncio
    async def test_complete_cache_short_circuits_no_refresh(self, monkeypatch):
        from tools import precomputed_analytics as pa

        good_academic = {
            "_completeness": {
                "factor_loadings": {"complete": True},
                "regime_conditional": {"complete": True},
            },
        }
        good_transition = {
            "_completeness": {"complete": True},
        }

        async def _get_metric(data_hash, kind):
            if kind == "academic_analytics":
                return good_academic
            if kind == "transition_matrix":
                return good_transition
            return None

        async def _get_latest(kind):
            return None

        refresh_calls: list[str] = []

        async def _track_academic(_):
            refresh_calls.append("academic")

        async def _track_transition(_):
            refresh_calls.append("transition")

        monkeypatch.setattr(pa, "get_metric", _get_metric)
        monkeypatch.setattr(pa, "get_latest_metric", _get_latest)
        monkeypatch.setattr(pa, "refresh_academic_analytics", _track_academic)
        monkeypatch.setattr(pa, "refresh_transition_matrix", _track_transition)

        result = await pa.ensure_qa_data_complete("hash")
        assert result["completeness"]["factor_loadings"] is True
        assert result["completeness"]["regime_conditional"] is True
        assert result["completeness"]["transition_matrix"] is True
        assert refresh_calls == []  # no refresh on a complete cache

    @pytest.mark.asyncio
    async def test_incomplete_cache_triggers_refresh(self, monkeypatch):
        from tools import precomputed_analytics as pa

        # First read: incomplete. After refresh: still missing (the
        # refresh is a no-op in the test). We're checking the trigger
        # path, not the refresh implementation.
        incomplete_academic = {
            "_completeness": {
                "factor_loadings": {"complete": False, "invalid_rows": []},
                "regime_conditional": {"complete": True},
            },
        }

        reads: list[str] = []

        async def _get_metric(data_hash, kind):
            reads.append(kind)
            if kind == "academic_analytics":
                return incomplete_academic
            return None

        async def _get_latest(kind):
            return None

        refresh_calls: list[str] = []

        async def _track_academic(_):
            refresh_calls.append("academic")

        async def _track_transition(_):
            refresh_calls.append("transition")

        monkeypatch.setattr(pa, "get_metric", _get_metric)
        monkeypatch.setattr(pa, "get_latest_metric", _get_latest)
        monkeypatch.setattr(pa, "refresh_academic_analytics", _track_academic)
        monkeypatch.setattr(pa, "refresh_transition_matrix", _track_transition)

        result = await pa.ensure_qa_data_complete("hash")
        assert "academic" in refresh_calls
        assert "transition" in refresh_calls
        assert "academic_analytics" in result["refresh_triggered"]
        assert "transition_matrix" in result["refresh_triggered"]


# ── Layer 5: disclosure text exists ───────────────────────────────────────────

class TestDisclosuresExposed:
    """analytical_appendix_disclosures returns pre-drafted paragraphs for
    AN01 + AN04. The academic writer's write_methodology() pulls these
    into its prompt context."""

    def test_disclosures_present(self):
        from agents.qa_agent import analytical_appendix_disclosures
        d = analytical_appendix_disclosures()
        assert "AN01_carhart" in d
        assert "AN04_regime_split" in d

    def test_carhart_disclosure_names_required_fields(self):
        from agents.qa_agent import analytical_appendix_disclosures
        text = analytical_appendix_disclosures()["AN01_carhart"]
        for factor in ("MKT-RF", "SMB", "HML", "MOM"):
            assert factor in text
        assert "Carhart" in text
        assert "p < 0.05" in text

    def test_regime_disclosure_names_breakpoint_and_tolerance(self):
        from agents.qa_agent import analytical_appendix_disclosures
        text = analytical_appendix_disclosures()["AN04_regime_split"]
        assert "2022-01-01" in text
        assert "1e-3" in text or "1.0 within" in text
        assert "BULL" in text and "BEAR" in text and "TRANSITION" in text
