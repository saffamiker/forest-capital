"""
tests/test_audit_layer2.py — May 28 2026.

Layer 2 statistical-audit task-group robustness:

  1. _extract_json — two-pass JSON extraction. Pass 1 (find/rfind) for
     the happy path, pass 2 (strategy-anchored regex) for responses
     where the auditor emitted prose containing a stray '{' or '}'
     before/after the JSON object.

  2. _factor_prompt — chunked over a subset of strategies. Mirrors
     the regime-split pattern so the response stays under the
     auditor's output cap.

  3. layer_2_metric_audit orchestrator — emits TWO factor-loadings
     jobs covering disjoint subsets of strategies, same chunking
     pattern as the existing regime-split jobs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MASTER_API_KEY", "test-master-key")


# ── _extract_json two-pass extraction ────────────────────────────────────────

class TestExtractJsonPassOne:
    """Pass 1 (find/rfind) covers the common cases: response IS the
    JSON, JSON wrapped in plain prose, JSON wrapped in markdown."""

    def test_returns_dict_for_bare_json(self):
        from tools.audit_layer2 import _extract_json

        out = _extract_json('{"strategy": "x", "checks": []}')
        assert out == {"strategy": "x", "checks": []}

    def test_strips_leading_prose(self):
        from tools.audit_layer2 import _extract_json

        out = _extract_json(
            'Here is the result:\n\n{"strategy": "x", "checks": []}')
        assert out == {"strategy": "x", "checks": []}

    def test_strips_markdown_fences(self):
        from tools.audit_layer2 import _extract_json

        out = _extract_json(
            '```json\n{"strategy": "x", "checks": []}\n```')
        assert out == {"strategy": "x", "checks": []}

    def test_returns_none_for_empty_input(self):
        from tools.audit_layer2 import _extract_json

        assert _extract_json("") is None
        assert _extract_json(None) is None  # type: ignore[arg-type]

    def test_returns_none_when_no_braces(self):
        from tools.audit_layer2 import _extract_json

        assert _extract_json("plain prose with no JSON") is None


class TestExtractJsonPassTwo:
    """Pass 2 (strategy-anchored regex) salvages responses where pass
    1's find/rfind picks up a stray '{' or '}' inside prose."""

    def test_recovers_from_stray_open_brace_in_leading_prose(self):
        # Pass 1 finds the first '{' (in the prose's "{sharpe}") and
        # the last '}' (closing the JSON). The slice is invalid JSON.
        # Pass 2 anchors on '{"strategy"' and recovers the real JSON.
        from tools.audit_layer2 import _extract_json

        text = (
            'Computing {sharpe} via OLS now. Here is the JSON:\n'
            '{"strategy": "factor_loadings", "checks": '
            '[{"metric": "EQUITY.mkt_rf", "platform_value": 1.0}]}'
        )
        out = _extract_json(text)
        assert out is not None
        assert out["strategy"] == "factor_loadings"
        assert len(out["checks"]) == 1
        assert out["checks"][0]["metric"] == "EQUITY.mkt_rf"

    def test_recovers_from_stray_open_brace_in_inline_remark(self):
        from tools.audit_layer2 import _extract_json

        # A stray reference like "the {auditor} model" before the JSON
        # taints pass 1; pass 2 still finds the strategy-anchored span.
        text = (
            'I reviewed the {auditor} comparison thresholds first. '
            '{"strategy": "regime_split", "checks": []}'
        )
        out = _extract_json(text)
        assert out == {"strategy": "regime_split", "checks": []}

    def test_returns_none_when_both_passes_fail(self):
        from tools.audit_layer2 import _extract_json

        # No JSON at all and no `"strategy"` anchor — both passes fail.
        text = 'No JSON here. {unbalanced'
        assert _extract_json(text) is None


# ── _factor_prompt chunking ──────────────────────────────────────────────────

class TestFactorPromptSubset:
    """The factor-loadings prompt now accepts a subset of strategies
    so the orchestrator can chunk the call into 5+5 strategies."""

    def _payload(self) -> dict:
        return {
            "raw_data": {
                "strategy_returns": {
                    "STRAT_A": [0.01, 0.02],
                    "STRAT_B": [0.02, 0.03],
                    "STRAT_C": [0.03, 0.04],
                },
                "ff_factors": {"mkt_rf": [0.5, 0.6]},
                "asset_returns": {"rf": [0.001, 0.001]},
            },
            "platform_computed": {
                "factor_loadings": {
                    "STRAT_A": {"mkt_rf": 1.0, "alpha": 0.0},
                    "STRAT_B": {"mkt_rf": 1.1, "alpha": 0.01},
                    "STRAT_C": {"mkt_rf": 1.2, "alpha": 0.02},
                },
            },
            "formula_specifications": {
                "factor_regression": "OLS regression of excess returns "
                                     "on Mkt-RF, SMB, HML, MOM",
            },
        }

    def test_subset_only_includes_named_strategies_in_returns(self):
        from tools.audit_layer2 import _factor_prompt

        prompt = _factor_prompt(self._payload(), ["STRAT_A", "STRAT_B"])
        # The subset's returns are in the prompt; STRAT_C is not.
        assert "STRAT_A" in prompt
        assert "STRAT_B" in prompt
        assert "STRAT_C" not in prompt

    def test_subset_only_includes_named_strategies_in_loadings(self):
        from tools.audit_layer2 import _factor_prompt

        prompt = _factor_prompt(self._payload(), ["STRAT_A"])
        # Single-strategy subset — STRAT_B/C platform loadings absent.
        # The subset_loadings dict appears verbatim in the prompt; the
        # serialised dict contains only the chosen strategy.
        # Extract the platform-loadings JSON block from the prompt.
        # We assert the absence directly.
        assert "STRAT_B" not in prompt
        assert "STRAT_C" not in prompt
        assert "STRAT_A" in prompt

    def test_legacy_signature_includes_every_strategy(self):
        from tools.audit_layer2 import _factor_prompt

        prompt = _factor_prompt(self._payload())
        for strat in ("STRAT_A", "STRAT_B", "STRAT_C"):
            assert strat in prompt

    def test_subset_names_appear_in_prompt_header(self):
        from tools.audit_layer2 import _factor_prompt

        prompt = _factor_prompt(self._payload(), ["STRAT_A", "STRAT_B"])
        # The header line names the strategies being audited so the
        # auditor knows which subset it is being asked about.
        assert "STRAT_A, STRAT_B" in prompt


# ── Orchestrator chunking ────────────────────────────────────────────────────

class TestLayer2OrchestratorChunking:
    """The orchestrator emits two factor-loadings jobs covering disjoint
    strategy subsets — mirror of the existing regime-split chunking.

    The test environment normally returns 'skip' from
    layer_2_metric_audit, so we monkeypatch _is_test_env to False AND
    set ANTHROPIC_API_KEY locally, then capture the auditor calls."""

    def _payload(self) -> dict:
        return {
            "available": True,
            "raw_inputs_hash": "testhash",
            "metadata": {"risk_free_rate": {"value": 0.025,
                                            "source": "FRED DTB3"}},
            "formula_specifications": {
                "sharpe": "...", "cagr": "...", "factor_regression": "...",
                "efficient_frontier": "...", "regime_split": "...",
                "rolling_correlation": "...",
            },
            "raw_data": {
                "strategy_returns": {
                    f"STRAT_{i}": [0.01] * 12 for i in range(10)
                },
                "ff_factors": {"mkt_rf": [0.5] * 12},
                "asset_returns": {"rf": [0.001] * 12, "dates": []},
            },
            "platform_computed": {
                "summary_statistics": {},
                "factor_loadings": {
                    f"STRAT_{i}": {"mkt_rf": 1.0} for i in range(10)
                },
                "regime_conditional": {
                    f"STRAT_{i}": {"pre_2022_sharpe": 0.5,
                                   "post_2022_sharpe": 0.6}
                    for i in range(10)
                },
                "efficient_frontier": {},
                "rolling_correlation": {"pre_2022": {}, "post_2022": {}},
            },
        }

    def test_orchestrator_emits_two_factor_loadings_jobs(
        self, monkeypatch,
    ):
        from tools import audit_layer2

        # Bypass the test-env skip without an API key.
        monkeypatch.setattr(audit_layer2, "_is_test_env", lambda: False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        captured: list[tuple[str, str]] = []

        def _capture(prompt: str) -> str:
            # Find which group's prompt this is by inspecting the body.
            # Return a valid empty JSON shape so _run_group succeeds.
            captured.append(("call", prompt))
            return '{"strategy": "stub", "checks": []}'

        monkeypatch.setattr(audit_layer2, "_call_auditor", _capture)

        result = asyncio.run(audit_layer2.layer_2_metric_audit(self._payload()))
        # The result is built from at least the chunked factor calls and
        # the other groups; what we pin here is that TWO of the captured
        # prompts are factor-loadings prompts and they cover the strategies.
        factor_prompts = [p for _, p in captured
                          if "Carhart four-factor loadings" in p]
        assert len(factor_prompts) == 2, (
            f"expected 2 factor-loadings calls, got {len(factor_prompts)}"
        )
        # The two subsets together cover every strategy and they do not
        # overlap (5 + 5 = 10 with no duplicates).
        subsets: list[set[str]] = []
        for p in factor_prompts:
            strats = {
                f"STRAT_{i}" for i in range(10) if f"STRAT_{i}" in p
            }
            subsets.append(strats)
        union = subsets[0] | subsets[1]
        overlap = subsets[0] & subsets[1]
        assert union == {f"STRAT_{i}" for i in range(10)}
        assert len(overlap) == 0
        assert result["status"] in ("pass", "warning", "warn", "fail", "skip")

    def test_max_tokens_raised_to_8000(self):
        # The cap was lifted from 4000 → 8000 to give every chunked
        # group ~4× the worst-case payload headroom.
        from tools.audit_layer2 import _AUDITOR_MAX_TOKENS
        assert _AUDITOR_MAX_TOKENS == 8000
