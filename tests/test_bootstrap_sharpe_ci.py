"""Bootstrap CI on Sharpe — helper + analytics-reduction contract.

The helper is block bootstrap (length 12, 10,000 resamples, seed=42)
on monthly returns. The Sharpe formula mirrors tools/backtester._m_
sharpe exactly: arithmetic mean of (r - rf) / std(excess, ddof=1) *
sqrt(12).

These tests pin the WIRE CONTRACT — point + CI fields, deterministic
seed, bracket-the-point invariant, table reduction shape — and a
realistic-fixture round-trip so a future change can't drift the
methodology silently.

The Layer 4 fixture in test_display_layer_fixtures.py (Fixture 5)
also exercises the bracket invariant against the bootstrap helper.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("MASTER_API_KEY", "test_master_key")
os.environ.setdefault(
    "ALLOWED_EMAILS",
    "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
)

from tools.analytics import (  # noqa: E402
    bootstrap_sharpe_ci,
    bootstrap_ci_table,
)


def _synthetic(n: int = 120, mean: float = 0.006, std: float = 0.04,
               seed: int = 17) -> pd.Series:
    rng = np.random.default_rng(seed=seed)
    return pd.Series(
        rng.normal(mean, std, n),
        index=pd.date_range("2010-01-31", periods=n, freq="ME"))


# ── helper — wire-contract ────────────────────────────────────────────────


def test_helper_returns_required_fields():
    out = bootstrap_sharpe_ci(_synthetic())
    assert isinstance(out, dict)
    for key in (
        "point", "ci_low", "ci_high",
        "n_resamples", "block_size", "n_observations",
        "confidence", "samples",
    ):
        assert key in out, f"missing key: {key}"
    assert out["n_resamples"] == 10_000
    assert out["block_size"] == 12
    assert out["confidence"] == 0.95


def test_helper_returns_none_on_too_short_series():
    s = _synthetic(n=10)
    assert bootstrap_sharpe_ci(s) is None


def test_helper_is_deterministic_with_seed():
    """Same input + same seed must produce bit-identical CI bounds.
    Reproducibility is the project-wide RANDOM_SEED contract."""
    s = _synthetic()
    a = bootstrap_sharpe_ci(s, seed=42)
    b = bootstrap_sharpe_ci(s, seed=42)
    assert a is not None and b is not None
    assert a["ci_low"] == b["ci_low"]
    assert a["ci_high"] == b["ci_high"]
    assert a["samples"][:10] == b["samples"][:10]


def test_helper_brackets_point_estimate():
    """The 95% CI must contain the point Sharpe — the bracket-the-
    point invariant. Layer 4 Fixture 5 pins this as a fixture-driven
    cross-check; this test pins it at the helper boundary."""
    out = bootstrap_sharpe_ci(_synthetic())
    assert out is not None
    assert out["ci_low"] <= out["point"] <= out["ci_high"]


def test_helper_uses_backtester_sharpe_formula():
    """The point Sharpe must match the backtester's arithmetic-
    monthly formula `mean(excess)/std(excess, ddof=1)*sqrt(12)`
    exactly — recompute inline (no platform helper) and compare."""
    s = _synthetic(n=240, seed=99)
    rf_s = pd.Series(0.0015, index=s.index)
    out = bootstrap_sharpe_ci(s, rf=rf_s)
    assert out is not None
    excess = s.values - rf_s.values
    expected = float(
        np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(12))
    assert abs(out["point"] - expected) < 1e-9


def test_helper_handles_scalar_rf():
    """A scalar rf is broadcast as a constant — same as the
    backtester's rf alignment for the constant-rf case."""
    s = _synthetic(n=120, seed=5)
    out_scalar = bootstrap_sharpe_ci(s, rf=0.002)
    out_series = bootstrap_sharpe_ci(
        s, rf=pd.Series(0.002, index=s.index))
    assert out_scalar is not None and out_series is not None
    # Identical inputs (constant rf) → identical point + CI bounds.
    assert abs(out_scalar["point"] - out_series["point"]) < 1e-9


def test_ci_width_shrinks_with_more_observations():
    """A larger sample tightens the CI — a basic sanity check on the
    methodology. Compare 24-month and 240-month series with the same
    mean/std; the longer series should yield a narrower CI."""
    rng = np.random.default_rng(seed=2026)
    short = pd.Series(
        rng.normal(0.005, 0.04, 24),
        index=pd.date_range("2020-01-31", periods=24, freq="ME"))
    long = pd.Series(
        rng.normal(0.005, 0.04, 240),
        index=pd.date_range("2000-01-31", periods=240, freq="ME"))
    s_short = bootstrap_sharpe_ci(short)
    s_long = bootstrap_sharpe_ci(long)
    assert s_short is not None and s_long is not None
    width_short = s_short["ci_high"] - s_short["ci_low"]
    width_long = s_long["ci_high"] - s_long["ci_low"]
    assert width_long < width_short


# ── table reduction — shape + sorting ─────────────────────────────────────


def _strategy_result(name: str, returns: pd.Series) -> dict:
    pairs = [[d.strftime("%Y-%m-%d"), float(v)]
             for d, v in returns.items()]
    return {"strategy_name": name, "monthly_returns": pairs}


def test_table_returns_one_row_per_strategy_sorted_by_sharpe():
    rng = np.random.default_rng(seed=11)
    strategies = {
        "ALPHA":   _strategy_result(
            "ALPHA", pd.Series(
                rng.normal(0.010, 0.03, 60),
                index=pd.date_range("2018-01-31", periods=60, freq="ME"))),
        "BETA":    _strategy_result(
            "BETA", pd.Series(
                rng.normal(0.005, 0.04, 60),
                index=pd.date_range("2018-01-31", periods=60, freq="ME"))),
        "GAMMA":   _strategy_result(
            "GAMMA", pd.Series(
                rng.normal(0.000, 0.02, 60),
                index=pd.date_range("2018-01-31", periods=60, freq="ME"))),
    }
    rows = bootstrap_ci_table(strategies)
    assert len(rows) == 3
    # Sorted by Sharpe descending.
    assert rows[0]["sharpe"] >= rows[1]["sharpe"] >= rows[2]["sharpe"]
    for row in rows:
        for key in ("strategy", "sharpe", "ci_low", "ci_high",
                    "n_resamples", "block_size", "n_observations"):
            assert key in row
        # CI bracket invariant on each row.
        assert row["ci_low"] <= row["sharpe"] <= row["ci_high"]
        # samples is OFF by default.
        assert "samples" not in row


def test_table_with_include_samples_emits_distribution():
    strategies = {
        "ALPHA": _strategy_result(
            "ALPHA", _synthetic(n=60, seed=3)),
    }
    rows = bootstrap_ci_table(strategies, include_samples=True)
    assert len(rows) == 1
    row = rows[0]
    assert "samples" in row
    # Down-sampled to at most 1000.
    assert 0 < len(row["samples"]) <= 1000
    # Samples cover the CI bounds — sorted ascending so the first
    # element is at or below the CI low, the last at or above the high.
    assert row["samples"][0] <= row["ci_low"] + 1e-6
    assert row["samples"][-1] >= row["ci_high"] - 1e-6


def test_table_skips_strategies_with_insufficient_observations():
    """A strategy with < 24 months returns None from the helper and
    is silently skipped from the table — the table is a positive
    surface, not a per-strategy diagnostic."""
    long_series = _synthetic(n=60, seed=1)
    short_series = _synthetic(n=10, seed=2)
    strategies = {
        "LONG":  _strategy_result("LONG", long_series),
        "SHORT": _strategy_result("SHORT", short_series),
    }
    rows = bootstrap_ci_table(strategies)
    names = [r["strategy"] for r in rows]
    assert "LONG" in names
    assert "SHORT" not in names


# ── Limitation copy — embedded in the analytical_findings output ──────────


def test_finding_carries_the_user_spec_limitation_copy():
    """The bootstrap-CI finding embeds the verbatim limitation copy
    the user named in the spec. The Academic Writer reads this
    directly into the midpoint paper's / brief's limitations
    section."""
    from tools.analytical_findings import _finding_bootstrap_ci_overlap

    academic = {
        "bootstrap_ci_sharpe": [
            {"strategy": "A", "sharpe": 0.70, "ci_low": 0.40,
             "ci_high": 1.00, "n_observations": 286,
             "n_resamples": 10_000, "block_size": 12},
            {"strategy": "B", "sharpe": 0.50, "ci_low": 0.20,
             "ci_high": 0.80, "n_observations": 286,
             "n_resamples": 10_000, "block_size": 12},
        ],
    }
    finding = _finding_bootstrap_ci_overlap(academic)
    impl = finding["implication"]
    assert "Bootstrap 95% confidence intervals on Sharpe ratios" in impl
    assert ("Static strategy selection cannot be made with "
            "statistical confidence from historical averages alone") \
        in impl
    assert ("the empirical motivation for regime-conditional "
            "construction") in impl
    assert "current regime signals" in impl


def test_finding_defers_when_payload_missing():
    from tools.analytical_findings import _finding_bootstrap_ci_overlap
    out = _finding_bootstrap_ci_overlap(None)
    assert out.get("strength") == "DEFERRED" or "cache miss" in str(out)
