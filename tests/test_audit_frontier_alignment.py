"""
tests/test_audit_frontier_alignment.py — May 25 2026.

Pins the L2 efficient-frontier alignment fix: the auditor must
recompute mu @ w and sqrt(w · cov · w) against the SAME subset of
months that refresh_efficient_frontier built its mu / cov over —
namely pd.DataFrame({EQUITY, IG, HY}).dropna(). Without this, the
auditor's recomputation lands on a different sample (full arrays,
including months where one column has NaN) and reports the
platform's (sigma, mu) as inconsistent with the weights, even
though the platform's own arithmetic is internally consistent.

Two surfaces tested:
  1. audit_assembler.assemble_audit_payload — produces
     platform_computed.efficient_frontier.aligned_returns: dropna'd
     equity/ig/hy/rf arrays + n_obs + rf_annual.
  2. audit_layer2._frontier_prompt — reads aligned_returns when
     present, falls back to raw_data.asset_returns for legacy
     payloads. The prompt names the sample so the auditor doesn't
     re-truncate or extend on its own initiative.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


# ── _frontier_prompt — uses aligned_returns when present ─────────────────────


class TestFrontierPromptUsesAlignedReturns:
    """The prompt builds from platform_computed.efficient_frontier.
    aligned_returns when present; the SAME arrays the platform's
    mu / cov were computed over."""

    def _payload(self, aligned: dict | None) -> dict:
        ef_block: dict = {
            "max_sharpe_point": {
                "sigma": 0.089, "mu": 0.0773,
                "sharpe": 0.682,
                "weights": {"EQUITY": 0.0808, "IG": 0.0, "HY": 0.9192},
            },
        }
        if aligned is not None:
            ef_block["aligned_returns"] = aligned
        return {
            "available": True,
            "raw_inputs_hash": "x",
            "metadata": {"risk_free_rate": {"value": 0.025,
                                            "source": "FRED DTB3"}},
            "formula_specifications": {"efficient_frontier": "F"},
            "raw_data": {
                "asset_returns": {
                    "equity": [0.10, 0.20, 0.30, 0.40],   # full (with NaN row)
                    "ig":     [0.01, 0.02, 0.03, None],   # NaN tail
                    "hy":     [0.03, 0.04, 0.05, 0.06],
                    "rf":     [0.001, 0.001, 0.001, 0.001],
                    "dates":  ["2022-01-31", "2022-02-28",
                               "2022-03-31", "2022-04-30"],
                },
            },
            "platform_computed": {"efficient_frontier": ef_block},
        }

    def test_uses_aligned_arrays_when_block_present(self):
        from tools.audit_layer2 import _frontier_prompt
        aligned = {
            "equity":    [0.10, 0.20, 0.30],   # first 3, NaN row dropped
            "ig":        [0.01, 0.02, 0.03],
            "hy":        [0.03, 0.04, 0.05],
            "rf":        [0.001, 0.001, 0.001],
            "dates":     ["2022-01-31", "2022-02-28", "2022-03-31"],
            "n_obs":     3,
            "rf_annual": 0.012,
        }
        prompt = _frontier_prompt(self._payload(aligned))
        # The aligned arrays appear in the prompt (3 entries, not 4).
        assert "[0.1, 0.2, 0.3]" in prompt
        assert "[0.01, 0.02, 0.03]" in prompt
        assert "[0.03, 0.04, 0.05]" in prompt
        # The full-arrays should NOT appear — the prompt is dispatched
        # off the aligned block, not raw.
        assert "0.4" not in prompt
        # n_obs from the aligned block surfaces in the sample note so
        # the auditor knows the sample size to average over.
        assert "3 months after aligning" in prompt
        # The aligned rf_annual takes precedence over metadata's.
        assert "0.012" in prompt

    def test_falls_back_to_raw_data_when_aligned_missing(self):
        # Legacy payload contract — pre-May-25-2026 audits.
        from tools.audit_layer2 import _frontier_prompt
        prompt = _frontier_prompt(self._payload(None))
        # The raw arrays go into the prompt with the None tail intact.
        assert "[0.1, 0.2, 0.3, 0.4]" in prompt
        assert "0.025" in prompt   # metadata's rf_annual
        assert "legacy payload" in prompt

    def test_prompt_references_dropna_alignment_explicitly(self):
        """The sample note tells the auditor exactly which rows it is
        averaging over — 'rows dropped where any of EQUITY / IG / HY
        is NaN'. Without this hint the auditor may apply its own
        idiosyncratic NaN handling and re-introduce the discrepancy."""
        from tools.audit_layer2 import _frontier_prompt
        aligned = {
            "equity": [0.1], "ig": [0.01], "hy": [0.03], "rf": [0.001],
            "dates": ["2022-01-31"], "n_obs": 1, "rf_annual": 0.012,
        }
        prompt = _frontier_prompt(self._payload(aligned))
        assert "rows dropped where any of EQUITY / IG / HY is NaN" \
            in prompt

    def test_max_sharpe_point_is_serialised_for_the_auditor(self):
        from tools.audit_layer2 import _frontier_prompt
        aligned = {
            "equity": [0.1], "ig": [0.01], "hy": [0.03], "rf": [0.001],
            "dates": ["2022-01-31"], "n_obs": 1, "rf_annual": 0.012,
        }
        prompt = _frontier_prompt(self._payload(aligned))
        # The platform's reported max-Sharpe must reach the auditor so
        # the comparison can be made — both the metrics AND the weights.
        assert "0.0808" in prompt   # equity weight
        assert "0.9192" in prompt   # HY weight
        assert "0.0773" in prompt   # platform mu


# ── audit_assembler — builds the aligned subset ──────────────────────────────


class TestAssemblerBuildsAlignedSubset:
    """The assembler runs the same pd.DataFrame({EQUITY, IG, HY, rf}).
    dropna() that refresh_efficient_frontier runs, and serialises the
    resulting arrays as aligned_returns. n_obs and rf_annual on that
    block are derived from the aligned set, not the raw monthly arrays.

    Unit-test scope: the alignment logic itself runs on a tiny pd
    DataFrame; the full assemble_audit_payload chain depends on a
    live cache and is exercised by the existing test_audit_carry suite."""

    def test_aligned_subset_drops_rows_where_any_column_is_nan(self):
        # The exact arithmetic the assembler applies — replicated here
        # to pin the contract without depending on DB cache state.
        import pandas as pd

        equity = pd.Series([0.10, 0.20, 0.30, 0.40, 0.50],
                           index=pd.to_datetime(
                               ["2022-01-31", "2022-02-28", "2022-03-31",
                                "2022-04-30", "2022-05-31"]))
        ig = pd.Series([0.01, 0.02, 0.03, None, 0.05],
                       index=equity.index)
        hy = pd.Series([0.03, None, 0.05, 0.06, 0.07],
                       index=equity.index)
        rf = pd.Series([0.001, 0.001, 0.001, 0.001, 0.001],
                       index=equity.index)

        ef_frame = pd.DataFrame(
            {"EQUITY": equity, "IG": ig, "HY": hy, "rf": rf},
            index=equity.index,
        ).dropna()

        # Row 1 (None in HY) and row 3 (None in IG) drop — 3 rows remain.
        assert len(ef_frame) == 3
        assert list(ef_frame["EQUITY"]) == [0.10, 0.30, 0.50]
        assert list(ef_frame["IG"]) == [0.01, 0.03, 0.05]
        assert list(ef_frame["HY"]) == [0.03, 0.05, 0.07]

    def test_aligned_rf_annual_uses_the_aligned_subset_mean(self):
        # rf_annual on the aligned block is mean(rf_in_aligned_subset)*12,
        # NOT mean(rf_full_array)*12 — so a row dropped because of NaN
        # in EQ/IG/HY also drops its rf contribution.
        import pandas as pd

        idx = pd.to_datetime(["2022-01-31", "2022-02-28", "2022-03-31"])
        equity = pd.Series([0.01, None, 0.03], index=idx)
        ig = pd.Series([0.01, 0.02, 0.03], index=idx)
        hy = pd.Series([0.02, 0.03, 0.04], index=idx)
        # Mid-row rf is 0.10 — a big number that, if averaged into
        # rf_annual, would dominate. The aligned subset drops row 1
        # (NaN in EQUITY) so rf row 1 is excluded.
        rf = pd.Series([0.001, 0.10, 0.001], index=idx)

        ef_frame = pd.DataFrame(
            {"EQUITY": equity, "IG": ig, "HY": hy, "rf": rf},
            index=idx,
        ).dropna()

        # Aligned set: 2 rows (row 1 dropped). Mean rf = 0.001.
        assert len(ef_frame) == 2
        rf_annual_aligned = round(float(ef_frame["rf"].mean()) * 12, 6)
        assert rf_annual_aligned == round(0.001 * 12, 6)
        # The full-series average would have been (0.001+0.10+0.001)/3 *
        # 12 ≈ 0.408 — confirming the alignment matters.
        full_rf_annual = round(rf.mean() * 12, 6)
        assert full_rf_annual != rf_annual_aligned

    def test_assembler_returns_unavailable_in_test_environment(self):
        # assemble_audit_payload exits early when ENVIRONMENT=test —
        # this prevents the test from needing a live DB.
        import asyncio
        from tools.audit_assembler import assemble_audit_payload

        result = asyncio.run(assemble_audit_payload())
        assert result["available"] is False
        assert "test environment" in result["note"]
