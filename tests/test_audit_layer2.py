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

  4. _run_group retry-with-higher-tokens (May 25 2026) — on a parse
     failure, retries once with _AUDITOR_MAX_TOKENS_RETRY (16000),
     mirroring the harness evaluator's 600 → 1500 escalation. The
     summary statistics (IG) group was the canonical trigger: seven
     metrics × step-by-step reasoning routinely overshot 8000 and
     truncated past the closing '}'.

  5. _summary_prompt — example schema now lists all seven metrics
     and instructs one-sentence reasoning, so the auditor emits one
     check per metric (not just cagr) without overshooting tokens.
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
        """The deterministic recompute path (May 25 2026 refactor) still
        splits factor loadings into two groups (A / B) covering disjoint
        strategy subsets — purely for finding provenance / audit history
        readability, since the recompute itself doesn't need chunking
        (no token cap). Verify the two recompute calls cover the full
        strategy set without overlap by monkeypatching the recompute
        function and capturing the subset_names argument."""
        import tools.audit_layer2_deterministic as det
        from tools import audit_layer2

        # Bypass the test-env skip; the deterministic path doesn't NEED
        # an API key but the orchestrator's gate still requires it.
        monkeypatch.setattr(audit_layer2, "_is_test_env", lambda: False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        captured_subsets: list[list[str]] = []

        def _capture_recompute(subset_names, payload):
            captured_subsets.append(list(subset_names))
            return {"strategy": "factor_loadings", "checks": []}

        monkeypatch.setattr(
            det, "recompute_factor_loadings", _capture_recompute)
        # The orchestrator imports recompute_factor_loadings INSIDE the
        # async function — patch the module attribute the import points
        # to AFTER it lands. Easier: also patch the module-level binding
        # the orchestrator looks up via from-import resolution.
        monkeypatch.setattr(
            "tools.audit_layer2_deterministic.recompute_factor_loadings",
            _capture_recompute,
        )
        # Stub the other recomputes so they don't error on the minimal
        # fixture; the orchestrator runs all six concurrently and any
        # raised exception aborts the gather.
        for fn_name in ("recompute_summary_statistics",
                        "recompute_efficient_frontier",
                        "recompute_regime_split",
                        "recompute_rolling_correlation"):
            monkeypatch.setattr(
                f"tools.audit_layer2_deterministic.{fn_name}",
                lambda *a, **kw: {"strategy": "stub", "checks": []},
            )

        result = asyncio.run(
            audit_layer2.layer_2_metric_audit(self._payload()))

        # Exactly two factor-loadings recomputes fired.
        assert len(captured_subsets) == 2, (
            f"expected 2 factor recompute calls, "
            f"got {len(captured_subsets)}"
        )
        # The two subsets together cover every strategy with no overlap
        # (5 + 5 = 10).
        set_a = set(captured_subsets[0])
        set_b = set(captured_subsets[1])
        assert set_a | set_b == {f"STRAT_{i}" for i in range(10)}
        assert set_a & set_b == set()
        assert result["status"] in ("pass", "warning", "warn", "fail", "skip")

    def test_max_tokens_raised_to_8000(self):
        # The cap was lifted from 4000 → 8000 to give every chunked
        # group ~4× the worst-case payload headroom.
        from tools.audit_layer2 import _AUDITOR_MAX_TOKENS
        assert _AUDITOR_MAX_TOKENS == 8000

    def test_retry_max_tokens_doubles_for_truncation_fallback(self):
        # Parse-failure retry uses a larger budget so a truncated
        # initial response can complete the JSON on the second pass.
        # Mirrors the harness evaluator's 600 → 1500 escalation.
        from tools.audit_layer2 import (
            _AUDITOR_MAX_TOKENS, _AUDITOR_MAX_TOKENS_RETRY,
        )
        assert _AUDITOR_MAX_TOKENS_RETRY == 16000
        assert _AUDITOR_MAX_TOKENS_RETRY > _AUDITOR_MAX_TOKENS


# ── _run_group retry-with-higher-tokens (May 25 2026) ────────────────────────


class TestRunGroupRetryOnParseFailure:
    """When the first auditor response truncates past the closing '}',
    _run_group retries ONCE with _AUDITOR_MAX_TOKENS_RETRY. If the
    retry parses, the findings reflect the retry's JSON. If both
    fail, the group degrades to one WARN finding noting the retry."""

    _TRUNCATED = (
        '{"strategy": "IG", "checks": [{"metric": "cagr", '
        '"platform_value": 0.05, "auditor_value": 0.05, '
        '"status": "pass", "discrepancy_pct": 0.0, '
        '"reasoning": "Recomputed value matches.", "flag": ""},'
        '{"metric": "volatility", "platform_va'  # truncates here
    )

    _VALID_FULL = (
        '{"strategy": "IG", "checks": ['
        '{"metric": "cagr", "platform_value": 0.05, '
        '"auditor_value": 0.05, "status": "pass", '
        '"discrepancy_pct": 0.0, "reasoning": "x", "flag": ""},'
        '{"metric": "volatility", "platform_value": 0.06, '
        '"auditor_value": 0.06, "status": "pass", '
        '"discrepancy_pct": 0.0, "reasoning": "x", "flag": ""}'
        ']}'
    )

    def test_first_truncated_response_triggers_retry(self, monkeypatch):
        from tools import audit_layer2

        calls: list[tuple[str, int]] = []

        def _fake_call(prompt: str,
                       max_tokens: int = audit_layer2._AUDITOR_MAX_TOKENS) -> str:
            calls.append((prompt[:50], max_tokens))
            if len(calls) == 1:
                return self._TRUNCATED
            return self._VALID_FULL

        monkeypatch.setattr(audit_layer2, "_call_auditor", _fake_call)

        findings = audit_layer2._run_group(
            "summary statistics (IG)",
            "stub prompt",
            "raw_hash_abc",
            "cagr formula",
        )

        # Exactly two calls: first at default cap, second at the retry cap.
        assert len(calls) == 2
        assert calls[0][1] == audit_layer2._AUDITOR_MAX_TOKENS
        assert calls[1][1] == audit_layer2._AUDITOR_MAX_TOKENS_RETRY
        # The retry's JSON parsed; the two checks landed as findings.
        assert len(findings) == 2
        metrics = {f["metric"] for f in findings}
        assert metrics == {"cagr", "volatility"}

    def test_successful_first_attempt_does_not_retry(self, monkeypatch):
        from tools import audit_layer2

        calls: list[int] = []

        def _fake_call(prompt: str,
                       max_tokens: int = audit_layer2._AUDITOR_MAX_TOKENS) -> str:
            calls.append(max_tokens)
            return self._VALID_FULL

        monkeypatch.setattr(audit_layer2, "_call_auditor", _fake_call)

        findings = audit_layer2._run_group(
            "summary statistics (IG)", "p", "h", "cagr")

        # No retry — first call succeeded.
        assert len(calls) == 1
        assert calls[0] == audit_layer2._AUDITOR_MAX_TOKENS
        assert len(findings) == 2

    def test_both_attempts_truncated_yields_warn_finding(self, monkeypatch):
        from tools import audit_layer2

        def _fake_call(prompt: str,
                       max_tokens: int = audit_layer2._AUDITOR_MAX_TOKENS) -> str:
            return self._TRUNCATED

        monkeypatch.setattr(audit_layer2, "_call_auditor", _fake_call)

        findings = audit_layer2._run_group(
            "summary statistics (IG)", "p", "h", "cagr")

        # One WARN finding referencing the retry path.
        assert len(findings) == 1
        f = findings[0]
        assert f["status"] == "warning"
        # The auditor_reasoning explicitly mentions the retry — so an
        # operator scanning Render logs knows the higher-tokens fallback
        # was exercised before the warning fired.
        assert "retry" in f["auditor_reasoning"]


# ── _summary_prompt all-seven-metrics expansion (May 25 2026) ────────────────


class TestSummaryPromptListsAllSevenMetrics:
    """The example schema spells out every metric the auditor must
    cover, so the auditor doesn't emit a single 'cagr' check and call
    it a day. The expansion was the upstream cause of the IG parse
    failure: an auditor that emits seven step-by-step checks
    overshoots the token cap; an example that asks for one terse
    line per check fits comfortably under it."""

    def _payload(self) -> dict:
        return {
            "raw_data": {
                "asset_returns": {
                    "equity": [0.01, 0.02, 0.03],
                    "ig":     [0.005, 0.006, 0.007],
                    "hy":     [0.008, 0.009, 0.010],
                    "rf":     [0.001, 0.001, 0.001],
                    "dates":  ["2022-01-31", "2022-02-28", "2022-03-31"],
                },
            },
            "metadata": {"risk_free_rate": {"value": 0.025}},
            "formula_specifications": {
                "cagr": "...", "volatility": "...", "sharpe": "...",
                "max_drawdown": "...", "skewness": "...",
                "excess_return": "...", "information_ratio": "...",
            },
        }

    def test_prompt_lists_all_seven_metrics_in_schema(self):
        from tools.audit_layer2 import _summary_prompt

        prompt = _summary_prompt(
            "IG", self._payload(), {"cagr": 0.05})
        # Every metric appears as a `"metric": "<name>"` line in the
        # JSON schema example — the auditor knows to emit one per.
        for metric in ("cagr", "volatility", "sharpe", "max_drawdown",
                       "skewness", "excess_return", "information_ratio"):
            assert f'"metric": "{metric}"' in prompt

    def test_prompt_uses_ig_series_for_ig_asset(self):
        # Asset routing — IG audits the IG series, not the equity one.
        from tools.audit_layer2 import _summary_prompt

        prompt = _summary_prompt(
            "IG", self._payload(), {"cagr": 0.05})
        # The IG series numbers appear once as the "Series to audit"
        # block. The equity numbers appear under the
        # "Benchmark monthly returns (equity)" line, but the IG line
        # must reference the IG series specifically.
        assert "[0.005, 0.006, 0.007]" in prompt
        assert "Series to audit (IG)" in prompt

    def test_prompt_uses_hy_series_for_hy_asset(self):
        from tools.audit_layer2 import _summary_prompt
        prompt = _summary_prompt(
            "HY", self._payload(), {"cagr": 0.05})
        # Python's repr drops the trailing zero from 0.010 → 0.01.
        assert "[0.008, 0.009, 0.01]" in prompt
        assert "Series to audit (HY)" in prompt

    def test_prompt_instructs_one_sentence_reasoning(self):
        # Verbose reasoning was the root cause of the IG truncation.
        # The new prompt explicitly limits reasoning to one sentence.
        from tools.audit_layer2 import _summary_prompt
        prompt = _summary_prompt(
            "IG", self._payload(), {"cagr": 0.05})
        assert "ONE sentence per check" in prompt
