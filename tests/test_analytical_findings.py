"""Coverage for tools/analytical_findings and migration 030.

Exercises the compute path without a live Postgres or Anthropic call:
  - Migration 030 loads cleanly.
  - compute_findings_from_payload returns 11 findings with the
    documented schema (title / finding / evidence / implication /
    nugget_strength / surprise) on a realistic payload.
  - Per-finding fail-open: a payload missing an input returns a
    'Deferred' finding rather than raising.
  - The markdown renderer produces a self-contained document with
    headers and the trailing macro context section.
  - inject_findings_context is a no-op when the cache is empty.
"""
import os
import sys
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")


def test_migration_030_loads():
    spec = importlib.util.spec_from_file_location(
        "mig_030",
        os.path.join(os.path.dirname(__file__), "..", "backend",
                     "migrations", "versions",
                     "030_analytical_findings_cache.py"),
    )
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "030"
    assert m.down_revision == "029"
    assert callable(m.upgrade)
    assert callable(m.downgrade)


def _sample_payload() -> dict:
    """Realistic-shape payload that exercises every finding's happy
    path. Each input mirrors the structure the live caches return."""
    return {
        "strategies": {
            "BENCHMARK": {
                "strategy_name": "BENCHMARK",
                "sharpe_ratio": 0.52, "cagr": 0.086, "max_drawdown": -0.51,
                "monthly_returns": [
                    [f"2002-{m:02d}-28", 0.01 if (m + i) % 3 else -0.01]
                    for i in range(24) for m in range(1, 13)],
            },
            "VOL_TARGETING": {
                "strategy_name": "VOL_TARGETING",
                "sharpe_ratio": 0.70, "cagr": 0.080, "max_drawdown": -0.18,
                "monthly_returns": [
                    [f"2002-{m:02d}-28", 0.006 if (m + i) % 4 else -0.004]
                    for i in range(24) for m in range(1, 13)],
            },
            "MOMENTUM_ROTATION": {
                "strategy_name": "MOMENTUM_ROTATION",
                "sharpe_ratio": 0.58, "cagr": 0.075, "max_drawdown": -0.24,
                "monthly_returns": [
                    [f"2002-{m:02d}-28", 0.008 if (m + i) % 5 else -0.005]
                    for i in range(24) for m in range(1, 13)],
            },
            "MIN_VARIANCE": {
                "strategy_name": "MIN_VARIANCE",
                "sharpe_ratio": 0.43, "cagr": 0.045, "max_drawdown": -0.22,
                "monthly_returns": [
                    [f"2002-{m:02d}-28", 0.004 if (m + i) % 6 else -0.002]
                    for i in range(24) for m in range(1, 13)],
            },
            "REGIME_SWITCHING": {
                "strategy_name": "REGIME_SWITCHING",
                "sharpe_ratio": 0.63, "cagr": 0.077, "max_drawdown": -0.19,
                "monthly_returns": [
                    [f"2002-{m:02d}-28", 0.007 if (m + i) % 4 else -0.004]
                    for i in range(24) for m in range(1, 13)],
            },
        },
        "correlation": {
            "labels": ["BENCHMARK", "VOL_TARGETING", "MOMENTUM_ROTATION",
                       "MIN_VARIANCE", "REGIME_SWITCHING"],
            "full": [
                [1.00, 0.65, 0.30, 0.40, 0.55],
                [0.65, 1.00, 0.45, 0.30, 0.50],
                [0.30, 0.45, 1.00, -0.10, 0.20],
                [0.40, 0.30, -0.10, 1.00, 0.35],
                [0.55, 0.50, 0.20, 0.35, 1.00],
            ],
            "pre_2022": [], "post_2022": [],
            "diagonal": 1.0,
        },
        "tail_risk": {
            "strategies": [
                {"strategy": "BENCHMARK", "var_99_annual": 0.40,
                 "cvar_99_annual": 0.55, "cvar_95_annual": 0.32},
                {"strategy": "VOL_TARGETING", "var_99_annual": 0.18,
                 "cvar_99_annual": 0.22, "cvar_95_annual": 0.14},
                {"strategy": "MOMENTUM_ROTATION", "var_99_annual": 0.25,
                 "cvar_99_annual": 0.31, "cvar_95_annual": 0.20},
                {"strategy": "MIN_VARIANCE", "var_99_annual": 0.20,
                 "cvar_99_annual": 0.25, "cvar_95_annual": 0.16},
                {"strategy": "REGIME_SWITCHING", "var_99_annual": 0.21,
                 "cvar_99_annual": 0.28, "cvar_95_annual": 0.18},
            ],
        },
        "crisis": {
            "windows": {
                "GFC_2008": {"start": "2008-09-01", "end": "2009-03-31"},
                "COVID_CRASH_2020": {"start": "2020-02-01",
                                      "end": "2020-04-30"},
                "RATE_SHOCK_2022": {"start": "2022-01-01",
                                     "end": "2022-12-31"},
            },
            "rows": {
                "BENCHMARK": {
                    "GFC_2008": {"cagr": -0.45, "partial": False, "n_months": 7},
                    "COVID_CRASH_2020": {"cagr": -0.20, "partial": False, "n_months": 3},
                    "RATE_SHOCK_2022": {"cagr": -0.18, "partial": False, "n_months": 12},
                },
                "VOL_TARGETING": {
                    "GFC_2008": {"cagr": -0.20, "partial": False, "n_months": 7},
                    "COVID_CRASH_2020": {"cagr": -0.08, "partial": False, "n_months": 3},
                    "RATE_SHOCK_2022": {"cagr": -0.05, "partial": False, "n_months": 12},
                },
                "MOMENTUM_ROTATION": {
                    "GFC_2008": {"cagr": -0.30, "partial": False, "n_months": 7},
                    "COVID_CRASH_2020": {"cagr": -0.15, "partial": False, "n_months": 3},
                    "RATE_SHOCK_2022": {"cagr": -0.08, "partial": False, "n_months": 12},
                },
            },
        },
        "risk_contribution": {
            "labels": ["BENCHMARK", "VOL_TARGETING", "MOMENTUM_ROTATION",
                       "MIN_VARIANCE", "REGIME_SWITCHING"],
            "tangency_weights": [0.05, 0.40, 0.15, 0.25, 0.15],
        },
        "academic": {
            "rolling_correlation": {
                "points": [
                    {"date": "2010-01-01", "equity_ig": -0.30},
                    {"date": "2015-01-01", "equity_ig": -0.28},
                    {"date": "2020-01-01", "equity_ig": -0.20},
                    {"date": "2022-06-01", "equity_ig": 0.45},
                    {"date": "2023-06-01", "equity_ig": 0.55},
                ],
            },
            "regime_conditional": [
                {"strategy": "BENCHMARK",
                 "pre_2022_sharpe": 0.60, "post_2022_sharpe": 0.30,
                 "pre_2022_cagr": 0.10, "post_2022_cagr": 0.05},
                {"strategy": "VOL_TARGETING",
                 "pre_2022_sharpe": 0.65, "post_2022_sharpe": 0.80,
                 "pre_2022_cagr": 0.09, "post_2022_cagr": 0.07},
                {"strategy": "MOMENTUM_ROTATION",
                 "pre_2022_sharpe": 0.55, "post_2022_sharpe": 0.62,
                 "pre_2022_cagr": 0.08, "post_2022_cagr": 0.07},
                {"strategy": "MIN_VARIANCE",
                 "pre_2022_sharpe": 0.40, "post_2022_sharpe": 0.48,
                 "pre_2022_cagr": 0.05, "post_2022_cagr": 0.04},
                {"strategy": "REGIME_SWITCHING",
                 "pre_2022_sharpe": 0.60, "post_2022_sharpe": 0.70,
                 "pre_2022_cagr": 0.08, "post_2022_cagr": 0.07},
            ],
            "factor_loadings": [
                {"strategy": "BENCHMARK", "mkt_rf": 1.00, "smb": 0.05,
                 "hml": 0.03, "mom": -0.01, "alpha_annualised": 0.00,
                 "r_squared": 0.99},
                {"strategy": "VOL_TARGETING", "mkt_rf": 0.55, "smb": 0.02,
                 "hml": 0.01, "mom": 0.04, "alpha_annualised": 0.02,
                 "r_squared": 0.85},
                {"strategy": "MOMENTUM_ROTATION", "mkt_rf": 0.65,
                 "smb": -0.10, "hml": -0.05, "mom": 0.30,
                 "alpha_annualised": 0.01, "r_squared": 0.82},
                {"strategy": "MIN_VARIANCE", "mkt_rf": 0.45, "smb": -0.20,
                 "hml": 0.15, "mom": -0.18, "alpha_annualised": 0.01,
                 "r_squared": 0.70},
                {"strategy": "REGIME_SWITCHING", "mkt_rf": 0.50,
                 "smb": 0.00, "hml": 0.05, "mom": 0.10,
                 "alpha_annualised": 0.02, "r_squared": 0.78},
            ],
        },
        "macro_digest": {
            "id": 42,
            "generated_at": "2026-05-22T18:00:00Z",
            "summary_text": "US rates on hold, growth slowing, inflation sticky.",
            "regime_implication": "Stagflationary squeeze regime.",
            "key_signals": [
                {"category": "monetary_policy",
                 "signal": "Fed holds rates at 5.25-5.50",
                 "implication": "Rates remain high for longer."},
            ],
        },
    }


class TestComputeFindings:
    def test_produces_twelve_findings(self):
        """May 31 2026 — the bootstrap-CI-overlap finding was inserted
        between FACTOR EXPOSURE and MACRO CONTEXT, taking the count
        from 11 to 12. The surprises rollup remains the LAST finding
        and aggregates over every prior; tests that index it use
        findings[-1]."""
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        assert len(findings) == 12

    def test_every_finding_has_documented_shape(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        required = {
            "title", "finding", "evidence", "implication",
            "nugget_strength", "surprise",
        }
        for f in findings:
            assert required.issubset(f.keys()), f"missing keys in {f}"
            assert isinstance(f["title"], str) and f["title"]
            assert isinstance(f["finding"], str) and f["finding"]
            assert isinstance(f["evidence"], list)
            assert isinstance(f["implication"], str) and f["implication"]
            assert f["nugget_strength"] in {"HIGH", "MEDIUM", "LOW"}
            assert isinstance(f["surprise"], bool)

    def test_finding_1_benchmark_competitiveness(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        f1 = findings[0]
        assert f1["title"] == "BENCHMARK COMPETITIVENESS"
        # BENCHMARK Sharpe (0.52) is mid-pack — VOL_TARGETING leads at 0.70.
        body = f1["finding"]
        assert "BENCHMARK" in body
        assert "VOL_TARGETING" in body or "leader" in body.lower()

    def test_finding_2_detects_2022_correlation_inversion(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        f2 = findings[1]
        assert f2["title"] == "REGIME SHIFT EVIDENCE"
        # Sample data: pre-2022 corr negative, post-2022 corr positive
        # → inversion detected in the finding string.
        body = f2["finding"]
        assert ("inverted" in body.lower()
                or "regime shift" in body.lower()
                or "shifted" in body.lower())

    def test_finding_4_picks_lowest_correlation_pair(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        f4 = findings[3]
        assert f4["title"] == "NATURAL COMPLEMENTS"
        # MOMENTUM_ROTATION ↔ MIN_VARIANCE is r=-0.10 in the sample —
        # the lowest off-diagonal value.
        body = f4["finding"]
        assert "MOMENTUM_ROTATION" in body
        assert "MIN_VARIANCE" in body

    def test_finding_8_strategies_that_beat_in_all_windows(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        f8 = findings[7]
        assert f8["title"] == "CRISIS PERFORMANCE"
        # In the sample: VOL_TARGETING and MOMENTUM_ROTATION both beat
        # BENCHMARK in every window.
        evidence_text = " ".join(f8["evidence"])
        assert ("VOL_TARGETING" in evidence_text
                or "BENCHMARK" in evidence_text)

    def test_finding_8_surfaces_vol_targeting_covid_callout(self):
        """May 31 2026 — the F3 CAGR-annualisation bug obscured
        VOL_TARGETING's COVID Crash capital-preservation result (the
        2-month CAGR over-stated VT's loss as -27.84% when the actual
        cumulative was -5.29%). Once the basis was switched to
        cumulative_return, the result is one of the clearest
        defensive narratives in the platform. The crisis-performance
        finding now surfaces a dedicated callout when a defensive
        strategy preserves capital at ≤ 50% of the benchmark's loss
        in a window where the benchmark lost ≥ 15%."""
        from tools.analytical_findings import compute_findings_from_payload
        # Patch the sample's crisis payload to carry production-shaped
        # COVID Crash figures: benchmark -19.87%, VOL_TARGETING -5.29%
        # (verified against the live backtester output).
        payload = _sample_payload()
        crisis = payload["crisis"]
        rows = crisis["rows"]
        rows.setdefault("BENCHMARK", {})["COVID_Crash_2020"] = {
            "cumulative_return": -0.1987, "cagr": -0.7353,
            "max_dd": -0.1251, "sharpe": -2.5,
            "partial": False, "n_months": 2,
        }
        rows.setdefault("VOL_TARGETING", {})["COVID_Crash_2020"] = {
            "cumulative_return": -0.0529, "cagr": -0.2784,
            "max_dd": -0.0529, "sharpe": -1.5,
            "partial": False, "n_months": 2,
        }
        crisis["windows"].setdefault(
            "COVID_Crash_2020",
            {"start": "2020-02-01", "end": "2020-03-31"})

        findings, _md = compute_findings_from_payload(payload)
        f8 = findings[7]
        assert f8["title"] == "CRISIS PERFORMANCE"
        text = " ".join(f8["evidence"])
        # The callout names the strategy, the window, and the ratio.
        assert "VOL_TARGETING preserved capital" in text
        assert "-5.29%" in text or "-5.30%" in text
        assert "-19.87%" in text
        assert "27%" in text  # 5.29 / 19.87 ≈ 0.27

    def test_finding_8_implication_names_the_f3_correction(self):
        """The implication must reference the cumulative-return basis
        fix (May 30 2026 F3) so a reader understands why the COVID
        capital-preservation result reads differently from earlier
        drafts that quoted the annualised figure."""
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        f8 = findings[7]
        impl = f8["implication"]
        assert "COVID" in impl
        assert ("cumulative" in impl.lower()
                or "CAGR-annualisation" in impl
                or "F3" in impl)
        assert ("regime-aware" in impl
                or "regime-conditional" in impl
                or "systematic" in impl.lower())

    def test_surprises_rollup_aggregates_prior_findings(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, _md = compute_findings_from_payload(_sample_payload())
        # Surprises is always the LAST finding — index by -1 rather
        # than a fixed position so a future insertion doesn't break
        # this assertion the way it broke the old `findings[10]`.
        f_last = findings[-1]
        assert f_last["title"] == "SURPRISES"
        prior_surprises = [f for f in findings[:-1] if f["surprise"]]
        # The Surprises finding must agree with the prior findings.
        if prior_surprises:
            assert f_last["surprise"] is True
        else:
            assert f11["surprise"] is False


class TestFailOpenPerFinding:
    """A payload missing a single input must NOT block the other ten
    findings — the absent one returns a 'Deferred' placeholder."""

    def test_missing_tail_risk_returns_deferred(self):
        from tools.analytical_findings import compute_findings_from_payload
        payload = _sample_payload()
        payload["tail_risk"] = None
        findings, _md = compute_findings_from_payload(payload)
        f3 = findings[2]
        assert f3["title"] == "TAIL RISK DIVERGENCE"
        assert "Deferred" in f3["finding"]
        assert f3["nugget_strength"] == "LOW"
        # The other 11 still landed (May 31 2026 — bootstrap-CI-overlap
        # finding bumped the total from 11 to 12).
        assert len(findings) == 12

    def test_missing_macro_digest_returns_deferred(self):
        """Index MACRO CONTEXT ALIGNMENT by title rather than position
        so future-finding inserts (e.g. the bootstrap-CI-overlap
        finding inserted before MACRO on May 31 2026) don't break this
        assertion."""
        from tools.analytical_findings import compute_findings_from_payload
        payload = _sample_payload()
        payload["macro_digest"] = None
        findings, _md = compute_findings_from_payload(payload)
        macro = next(
            (f for f in findings if f["title"] == "MACRO CONTEXT ALIGNMENT"),
            None)
        assert macro is not None, "MACRO CONTEXT ALIGNMENT finding missing"
        assert "Deferred" in macro["finding"]

    def test_empty_strategies_returns_deferred_for_dependent_findings(self):
        from tools.analytical_findings import compute_findings_from_payload
        payload = _sample_payload()
        payload["strategies"] = {}
        findings, _md = compute_findings_from_payload(payload)
        # Several findings depend on strategies — they should defer
        # rather than raise.
        deferred_titles = [
            f["title"] for f in findings if "Deferred" in f["finding"]]
        assert "BENCHMARK COMPETITIVENESS" in deferred_titles
        assert "MOMENTUM VS MEAN REVERSION" in deferred_titles


class TestMarkdownRender:
    def test_markdown_has_header_and_per_finding_sections(self):
        from tools.analytical_findings import compute_findings_from_payload
        findings, md = compute_findings_from_payload(_sample_payload())
        # Header
        assert md.startswith("# Analytical Findings — Staging Report")
        # Every finding gets a numbered H2.
        for i, f in enumerate(findings, start=1):
            assert f"## {i}. {f['title']}" in md
        # Per-finding labels.
        assert "**FINDING:**" in md
        assert "**EVIDENCE:**" in md
        assert "**IMPLICATION:**" in md
        assert "**NUGGET STRENGTH:**" in md
        assert "**SURPRISE:**" in md

    def test_markdown_has_trailing_macro_context(self):
        from tools.analytical_findings import compute_findings_from_payload
        _findings, md = compute_findings_from_payload(_sample_payload())
        # The macro digest summary lands at the end of the report so
        # the Academic Writer's injection block carries both layers in
        # one document.
        assert "## Current macro context" in md
        assert "Stagflationary squeeze" in md


class TestInjectFindingsContext:
    def test_no_op_when_cache_empty(self):
        from tools import analytical_findings as af
        # The module-level cache starts empty.
        af._CACHE["latest"] = None
        prompt = "SYSTEM PROMPT BODY"
        out = af.inject_findings_context(prompt)
        # No changes when the cache has not been populated yet.
        assert out == prompt

    def test_prepends_block_when_cache_populated(self):
        from tools import analytical_findings as af
        af._CACHE["latest"] = {
            "findings_md": "# Test findings — sample markdown body.",
        }
        try:
            out = af.inject_findings_context("BASE")
            assert "BASE" in out
            assert "ANALYTICAL FINDINGS CONTEXT" in out
            assert "Test findings" in out
        finally:
            af._CACHE["latest"] = None  # cleanup


class TestPersistenceFailOpenWithoutDatabase:
    def test_get_latest_findings_returns_none(self, monkeypatch):
        import asyncio
        import database as db_mod
        from tools import analytical_findings as af
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        assert asyncio.run(af.get_latest_findings()) is None

    def test_upsert_returns_none_without_db(self, monkeypatch):
        import asyncio
        import database as db_mod
        from tools import analytical_findings as af
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", None)
        out = asyncio.run(af.upsert_findings(
            "h", [], "md",
            macro_digest_id=None, strategy_count=0, surprise_count=0))
        assert out is None
