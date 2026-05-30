"""tools/invariant_checks.py — analytics invariant assertion framework.

May 30 2026. A permanent quality gate that runs at the end of every
analytics warm. Catches the class of error the F3 crisis-window
incident surfaced (CAGR annualisation on a 2-month window turning a
-19.87% loss into a -73.53% displayed figure) and a broader set of
mathematical, basis-consistency, external-reference, directional and
temporal invariants.

ARCHITECTURE — five categories, two severity tiers:

  CATEGORY 1 — MATHEMATICAL IMPOSSIBILITIES (HARD)
    Things that cannot be true if the math is correct: window return
    > full-period max drawdown, Sharpe inconsistent with components,
    CVaR99 less negative than CVaR95, weights not summing to 1,
    non-PSD correlation matrix, returns outside (-1, +2), full-period
    max DD weaker than any crisis cumulative.
  CATEGORY 2 — TIME BASIS CONSISTENCY (HARD)
    Within a table all figures use the same basis: crisis windows use
    cumulative not CAGR, full-period Sharpes use sqrt(12) annualisation,
    factor betas use the same estimation window, a metric appearing in
    two tables uses the same basis in both.
  CATEGORY 3 — EXTERNAL REFERENCE CHECKS (SOFT)
    Plausibility ranges from published references (S&P 500 crisis
    returns, macro series bounds). Failure logs a warning; the warm
    proceeds.
  CATEGORY 4 — DIRECTIONAL LOGIC (SOFT)
    Defensive strategies should protect capital in drawdowns, higher
    Sharpe correlates with lower CVaR, the tangency Sharpe dominates
    every individual Sharpe, bootstrap CI brackets the point estimate,
    defensive strategies outperform in their designed environments.
  CATEGORY 5 — TEMPORAL INTEGRITY (HARD on gaps, SOFT on ordering)
    No gaps in the monthly series, initialisation periods produce no
    results, no future-data leakage, crisis windows fall within the
    data range, OOS split sits well after the lookback windows.

HARD FAILURE BEHAVIOUR
  A hard-tier assertion failure aborts the warm — the caller (set_
  strategy_cache + refresh_all_analytics) MUST preserve the previous
  cache row rather than overwriting it with the new bad data. A log
  line `invariant_hard_failure` carries the failing assertion code
  (e.g. "1a", "2a") and the values that triggered it.

SOFT FAILURE BEHAVIOUR
  Logged as `invariant_soft_warning` with the assertion code, then
  attached to the result summary so the admin view can surface them.

DOCUMENTATION — every assertion (the WHAT / WHY / class of error it
catches / motivating example) is recorded in docs/INVARIANTS.md.

────────────────────────────────────────────────────────────────────────
ARCHITECTURE CONSTRAINT — DETERMINISTIC DETECTION ONLY
────────────────────────────────────────────────────────────────────────
Every assertion in this file is a PURE MATHEMATICAL COMPARISON. No
LLM is involved in any detection or interpretation path. The seven
rules a contributor MUST honor before adding a new check_* function:

  1. Pure comparison: `abs(x) <= threshold`, `lo <= x <= hi`,
     `abs(computed - displayed) < tolerance`. No interpretation, no
     "looks suspicious."
  2. Expected ranges are hardcoded module-level constants (see
     `_BENCHMARK_CRISIS_PLAUSIBILITY`, `_MACRO_PLAUSIBILITY`),
     reviewed and committed by a human. Never generated at runtime.
  3. Layer 4 fixture expected values are computed inline in the
     test file using basic arithmetic (`(1+r1)*(1+r2)-1`). Never
     call a platform helper for the expected side — a bug in the
     helper would pass both checks.
  4. Hard failure is a deterministic boolean. No probabilities, no
     scoring, no LLM verdicts.
  5. Log / error messages are static f-string templates with values
     substituted — no LLM formatting.
  6. Soft warnings use the same deterministic comparison logic as
     hard ones; only `severity` differs.
  7. Any randomness in fixtures (bootstrap, synthetic series) uses
     a fixed seed so the same input produces the same output across
     CI runs.

The 30-second readability test: a junior analyst should be able to
verify the logic of each check_* by reading 2-3 lines. If a check
requires more, decompose it into smaller checks. The validation
layer is the thing we trust when everything else is wrong.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)


# ── Result types ───────────────────────────────────────────────────────────


@dataclass
class InvariantViolation:
    """One failing check. `severity` is "hard" or "soft"; "hard" aborts
    the warm, "soft" logs and continues. `code` is the assertion ID
    from INVARIANTS.md (e.g. "1a"); `entity` names the strategy or
    window the violation applies to (or "" for global checks)."""
    code: str
    severity: str
    category: int
    entity: str
    metric: str
    expected: str
    actual: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code":     self.code,
            "severity": self.severity,
            "category": self.category,
            "entity":   self.entity,
            "metric":   self.metric,
            "expected": self.expected,
            "actual":   self.actual,
            "detail":   self.detail,
        }


@dataclass
class InvariantResult:
    """Aggregate summary returned from run_all_invariants()."""
    violations: list[InvariantViolation] = field(default_factory=list)
    checks_run: int = 0

    @property
    def hard_failures(self) -> list[InvariantViolation]:
        return [v for v in self.violations if v.severity == "hard"]

    @property
    def soft_warnings(self) -> list[InvariantViolation]:
        return [v for v in self.violations if v.severity == "soft"]

    @property
    def passed(self) -> bool:
        """True iff no HARD failure landed. Soft warnings do not block."""
        return not self.hard_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed":         self.passed,
            "checks_run":     self.checks_run,
            "hard_failures":  len(self.hard_failures),
            "soft_warnings":  len(self.soft_warnings),
            "violations":     [v.to_dict() for v in self.violations],
        }

    def summary_log_payload(self) -> dict[str, Any]:
        """Compact one-line log payload — for invariant_check_summary."""
        return {
            "hard_failures":  len(self.hard_failures),
            "soft_warnings":  len(self.soft_warnings),
            "checks_passed":
                self.checks_run - len(self.violations),
            "total_checks":   self.checks_run,
        }


# ── Helpers ────────────────────────────────────────────────────────────────


def _monthly_series(result: dict[str, Any]) -> pd.Series:
    """Extract monthly_returns from a strategy result as a date-indexed
    Series. Empty if absent or malformed."""
    mr = result.get("monthly_returns") or []
    if not mr:
        return pd.Series(dtype=float)
    if isinstance(mr[0], dict):
        try:
            df = pd.DataFrame(mr)
            return pd.Series(
                df["return"].astype(float).values,
                index=pd.to_datetime(df["date"]))
        except Exception:  # noqa: BLE001
            return pd.Series(dtype=float)
    try:
        return pd.Series({pd.to_datetime(x[0]): float(x[1]) for x in mr})
    except Exception:  # noqa: BLE001
        return pd.Series(dtype=float)


def _max_drawdown_from_returns(r: pd.Series) -> float:
    """Same convention as tools/analytics._max_drawdown."""
    if len(r) == 0:
        return 0.0
    curve = (1.0 + r).cumprod()
    return float((curve / curve.cummax() - 1.0).min())


def _annual_vol(r: pd.Series) -> float:
    return float(r.std(ddof=1) * math.sqrt(12)) if len(r) > 1 else 0.0


def _cagr(r: pd.Series) -> float:
    if len(r) == 0:
        return 0.0
    g = float((1.0 + r).prod())
    if g <= 0.0:
        return -1.0
    return g ** (12.0 / len(r)) - 1.0


# ── CATEGORY 1 — MATHEMATICAL IMPOSSIBILITIES (HARD) ───────────────────────


def check_1a_window_return_le_full_max_dd(
    strategy_results: dict[str, dict],
    crisis_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """1a — no single crisis-window cumulative loss can be larger
    in absolute terms than the strategy's full-period max drawdown.
    Motivating example: F3 incident. COVID Crash -73.53% (CAGR) on
    a strategy whose full-period max DD was -52.56% — mathematically
    impossible, the assertion would have caught it automatically."""
    vios: list[InvariantViolation] = []
    n = 0
    if not crisis_payload:
        return vios, n
    rows = crisis_payload.get("rows") or {}
    eps = 1e-6
    for name, cells in rows.items():
        res = strategy_results.get(name) or {}
        if not res:
            # Match by display name if the dict key differs.
            for r in strategy_results.values():
                if (r or {}).get("strategy_name") == name:
                    res = r
                    break
        s = _monthly_series(res)
        if len(s) < 2:
            continue
        full_dd = _max_drawdown_from_returns(s)
        for crisis, cell in (cells or {}).items():
            n += 1
            cum = cell.get("cumulative_return")
            if cum is None or cum >= 0:
                continue
            if abs(cum) > abs(full_dd) + eps:
                vios.append(InvariantViolation(
                    code="1a", severity="hard", category=1,
                    entity=f"{name}/{crisis}",
                    metric="cumulative_return",
                    expected=f"|return| ≤ |full-period max DD| = "
                             f"{abs(full_dd):.4f}",
                    actual=f"|{cum:.4f}| = {abs(cum):.4f}",
                    detail=("A crisis-window cumulative loss cannot "
                            "exceed the strategy's worst-ever loss "
                            "across the full sample.")))
    return vios, n


def check_1b_sharpe_consistent_with_components(
    strategy_results: dict[str, dict],
    *,
    risk_free_rate: float | None = None,
    tolerance: float = 0.02,
) -> tuple[list[InvariantViolation], int]:
    """1b — Sharpe ≈ (CAGR - rf) / annualised_vol within 0.02 tolerance.
    The Sharpe stored on a strategy result must be reconstructible
    from the components also stored on the same result."""
    vios: list[InvariantViolation] = []
    n = 0
    for name, res in (strategy_results or {}).items():
        s = _monthly_series(res)
        if len(s) < 24:
            continue
        n += 1
        stored = res.get("sharpe_ratio")
        if stored is None:
            continue
        cagr = res.get("cagr")
        if cagr is None:
            cagr = _cagr(s)
        vol = res.get("volatility")
        if vol is None or vol <= 0:
            vol = _annual_vol(s)
            if vol <= 0:
                continue
        rf = risk_free_rate if risk_free_rate is not None else 0.0
        implied = (float(cagr) - float(rf)) / float(vol)
        if abs(float(stored) - implied) > tolerance:
            vios.append(InvariantViolation(
                code="1b", severity="hard", category=1,
                entity=name, metric="sharpe_ratio",
                expected=f"(CAGR - rf)/vol = "
                         f"({cagr:.4f} - {rf:.4f})/{vol:.4f} = "
                         f"{implied:.4f}",
                actual=f"{float(stored):.4f}",
                detail=("Stored Sharpe drifted from a recompute "
                        "via its own components beyond the "
                        f"{tolerance:g} tolerance.")))
    return vios, n


def check_1c_max_drawdown_le_min_monthly(
    strategy_results: dict[str, dict],
) -> tuple[list[InvariantViolation], int]:
    """1c — max drawdown must be at least as negative as the worst
    single monthly return. A drawdown is a sum of consecutive losses;
    it cannot be less negative than any one month within it."""
    vios: list[InvariantViolation] = []
    n = 0
    eps = 1e-6
    for name, res in (strategy_results or {}).items():
        s = _monthly_series(res)
        if len(s) < 2:
            continue
        n += 1
        min_month = float(s.min())
        if min_month >= 0:
            continue
        # Recompute max DD if not stored.
        dd = res.get("max_drawdown")
        if dd is None:
            dd = _max_drawdown_from_returns(s)
        if float(dd) > min_month + eps:
            vios.append(InvariantViolation(
                code="1c", severity="hard", category=1,
                entity=name, metric="max_drawdown",
                expected=f"max_dd ≤ worst monthly return = "
                         f"{min_month:.4f}",
                actual=f"{float(dd):.4f}",
                detail=("Max drawdown is less negative than the "
                        "single worst monthly return, which is "
                        "impossible — a drawdown contains at least "
                        "that month.")))
    return vios, n


def check_1d_cvar99_le_cvar95(
    strategy_results: dict[str, dict],
) -> tuple[list[InvariantViolation], int]:
    """1d — CVaR99 (worst 1%) must be at least as negative as CVaR95
    (worst 5%). By definition the 1% tail is a subset of the 5% tail."""
    vios: list[InvariantViolation] = []
    n = 0
    eps = 1e-6
    for name, res in (strategy_results or {}).items():
        c95 = res.get("cvar_95")
        c99 = res.get("cvar_99")
        if c95 is None or c99 is None:
            continue
        n += 1
        if float(c99) > float(c95) + eps:
            vios.append(InvariantViolation(
                code="1d", severity="hard", category=1,
                entity=name, metric="cvar",
                expected=f"CVaR99 ≤ CVaR95 = {float(c95):.4f}",
                actual=f"CVaR99 = {float(c99):.4f}",
                detail=("CVaR99 averages the worst 1% of months; that "
                        "set is a strict subset of the worst 5% (CVaR95) "
                        "and its mean cannot be less negative.")))
    return vios, n


def check_1e_weight_schedule_sums_to_one(
    strategy_results: dict[str, dict],
    *, tolerance: float = 0.001,
) -> tuple[list[InvariantViolation], int]:
    """1e — every weight schedule entry sums to 1 ± 0.001."""
    vios: list[InvariantViolation] = []
    n = 0
    for name, res in (strategy_results or {}).items():
        sched = res.get("weight_schedule") or []
        for entry in sched:
            n += 1
            w = (entry or {}).get("weights") or {}
            total = sum(float(v) for v in w.values())
            if abs(total - 1.0) > tolerance:
                vios.append(InvariantViolation(
                    code="1e", severity="hard", category=1,
                    entity=f"{name}@{entry.get('date')}",
                    metric="weights",
                    expected=f"sum(weights) = 1 ± {tolerance:g}",
                    actual=f"{total:.4f}",
                    detail="A fully-invested portfolio's weights must "
                           "sum to one at every rebalance date."))
                # One violation per schedule is enough — the upstream
                # bug, not every row, is the signal. Stop scanning this
                # strategy to keep the report compact.
                break
    return vios, n


def check_1f_correlation_matrix_psd(
    correlation_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """1f — a correlation matrix must be positive semi-definite.
    Allows a small numerical slack (-0.001) since sample correlation
    matrices have eigenvalues that drift slightly below zero from
    floating-point precision alone."""
    vios: list[InvariantViolation] = []
    n = 0
    if not correlation_payload:
        return vios, n
    eps = 1e-3
    # Several payload shapes are possible — full / pre / post / labels.
    for label in ("full", "pre_2022", "post_2022"):
        matrix = (correlation_payload or {}).get(label)
        if not matrix:
            continue
        n += 1
        try:
            arr = np.asarray(matrix, dtype=float)
        except Exception:  # noqa: BLE001
            continue
        if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
            continue
        try:
            eigs = np.linalg.eigvalsh(arr)
        except Exception:  # noqa: BLE001
            continue
        min_eig = float(eigs.min())
        if min_eig < -eps:
            vios.append(InvariantViolation(
                code="1f", severity="hard", category=1,
                entity=label, metric="correlation_matrix",
                expected=f"min eigenvalue ≥ -{eps:g}",
                actual=f"{min_eig:.6f}",
                detail=("Correlation matrix is not positive semi-definite "
                        "— some eigenvalue is materially negative, which "
                        "indicates a data or alignment bug upstream.")))
    return vios, n


def check_1g_monthly_return_bounds(
    strategy_results: dict[str, dict],
    asset_returns: dict[str, list[float]] | None = None,
) -> tuple[list[InvariantViolation], int]:
    """1g — no monthly return < -100% (would mean a strategy lost more
    than 100% — only possible with leverage / shorting) or > +200%
    (well outside any historically observed monthly print)."""
    vios: list[InvariantViolation] = []
    n = 0
    LOWER, UPPER = -1.0, 2.0
    for name, res in (strategy_results or {}).items():
        s = _monthly_series(res)
        if s.empty:
            continue
        n += 1
        if s.min() < LOWER or s.max() > UPPER:
            vios.append(InvariantViolation(
                code="1g", severity="hard", category=1,
                entity=name, metric="monthly_return",
                expected=f"every month in ({LOWER:.0%}, {UPPER:.0%}]",
                actual=f"min={float(s.min()):.4f}, "
                       f"max={float(s.max()):.4f}",
                detail="Monthly return outside the (-100%, +200%) band "
                       "indicates a data error or an unintended "
                       "leverage / short."))
    for asset, series in (asset_returns or {}).items():
        if not series:
            continue
        n += 1
        arr = np.asarray(series, dtype=float)
        if arr.size == 0:
            continue
        if arr.min() < LOWER or arr.max() > UPPER:
            vios.append(InvariantViolation(
                code="1g", severity="hard", category=1,
                entity=asset, metric="monthly_return",
                expected=f"every month in ({LOWER:.0%}, {UPPER:.0%}]",
                actual=f"min={float(arr.min()):.4f}, "
                       f"max={float(arr.max()):.4f}",
                detail="Asset monthly return outside the (-100%, +200%) "
                       "band indicates a data error."))
    return vios, n


def check_1h_full_period_dd_dominates_crisis(
    strategy_results: dict[str, dict],
    crisis_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """1h — the strategy's full-period max drawdown must be more
    negative than any single crisis-window cumulative loss for that
    strategy. A direct restatement of 1a but framed from the DD side
    — gives a separate finding code so post-mortem reports can
    distinguish a 'bad return basis' bug from a 'bad DD computation'
    bug. Both fired by the same condition; the second framing
    proves the subset inequality from the drawdown angle."""
    vios: list[InvariantViolation] = []
    n = 0
    if not crisis_payload:
        return vios, n
    rows = crisis_payload.get("rows") or {}
    eps = 1e-6
    for name, cells in rows.items():
        res = strategy_results.get(name) or {}
        if not res:
            for r in strategy_results.values():
                if (r or {}).get("strategy_name") == name:
                    res = r
                    break
        s = _monthly_series(res)
        if len(s) < 2:
            continue
        full_dd = _max_drawdown_from_returns(s)
        for crisis, cell in (cells or {}).items():
            n += 1
            cum = cell.get("cumulative_return")
            if cum is None or cum >= 0:
                continue
            if cum < full_dd - eps:
                vios.append(InvariantViolation(
                    code="1h", severity="hard", category=1,
                    entity=f"{name}/{crisis}",
                    metric="max_drawdown vs crisis_return",
                    expected=f"full-period max DD ≤ crisis return = "
                             f"{cum:.4f}",
                    actual=f"full-period max DD = {full_dd:.4f}",
                    detail=("Full-period max DD is less negative than "
                            "a crisis-window cumulative loss inside it. "
                            "Impossible by the subset argument — the "
                            "crisis is part of the full period.")))
    return vios, n


# ── CATEGORY 2 — TIME BASIS CONSISTENCY (HARD) ─────────────────────────────


def check_2a_crisis_uses_cumulative_basis(
    crisis_payload: dict[str, Any] | None,
    strategy_results: dict[str, dict],
    *, tolerance: float = 0.005,
) -> tuple[list[InvariantViolation], int]:
    """2a — every crisis-window cell's displayed cumulative_return
    must match a fresh recompute from the monthly series within
    0.5%. Anything else means a different basis (CAGR, annualised
    rate, max DD) leaked into the column."""
    vios: list[InvariantViolation] = []
    n = 0
    if not crisis_payload:
        return vios, n
    windows = crisis_payload.get("windows") or {}
    rows = crisis_payload.get("rows") or {}
    for name, cells in rows.items():
        res = strategy_results.get(name) or {}
        if not res:
            for r in strategy_results.values():
                if (r or {}).get("strategy_name") == name:
                    res = r
                    break
        s = _monthly_series(res)
        if s.empty:
            continue
        for crisis, cell in (cells or {}).items():
            displayed = cell.get("cumulative_return")
            if displayed is None:
                continue
            w = windows.get(crisis) or {}
            start = pd.Timestamp(w.get("start"))
            end = pd.Timestamp(w.get("end"))
            if pd.isna(start) or pd.isna(end):
                continue
            window = s[(s.index >= start) & (s.index <= end)]
            if len(window) < 2:
                continue
            n += 1
            expected = float((1.0 + window).prod() - 1.0)
            if abs(float(displayed) - expected) > tolerance:
                vios.append(InvariantViolation(
                    code="2a", severity="hard", category=2,
                    entity=f"{name}/{crisis}",
                    metric="cumulative_return",
                    expected=f"{expected:.4f} (recomputed from monthly)",
                    actual=f"{float(displayed):.4f}",
                    detail=("Displayed crisis return drifted from the "
                            "cumulative-of-monthly recompute beyond the "
                            f"{tolerance:g} tolerance. A different basis "
                            "(CAGR, annualised rate, max DD) leaked into "
                            "the column — the F3 class of bug.")))
    return vios, n


def check_2b_full_period_sharpe_annualised(
    strategy_results: dict[str, dict],
    *, tolerance: float = 0.05,
) -> tuple[list[InvariantViolation], int]:
    """2b — every full-period Sharpe must be annualised by sqrt(12).
    Recomputed: mean(monthly_excess)/std(monthly_excess) * sqrt(12).
    A monthly Sharpe accidentally stored without the sqrt(12) factor
    would be ~3.46× smaller — easy to spot at this tolerance."""
    vios: list[InvariantViolation] = []
    n = 0
    for name, res in (strategy_results or {}).items():
        s = _monthly_series(res)
        if len(s) < 24:
            continue
        stored = res.get("sharpe_ratio")
        if stored is None:
            continue
        n += 1
        # Use zero risk-free for the basis check — the absolute value
        # could differ but the magnitude relative to monthly-Sharpe is
        # what matters here.
        sd = float(s.std(ddof=1))
        if sd < 1e-12:
            continue
        annualised = float(s.mean()) / sd * math.sqrt(12)
        monthly = float(s.mean()) / sd
        # The stored Sharpe should be near `annualised`. If it's near
        # `monthly` (no sqrt(12)), basis is wrong.
        if (abs(float(stored) - annualised) > tolerance
                and abs(float(stored) - monthly) < tolerance):
            vios.append(InvariantViolation(
                code="2b", severity="hard", category=2,
                entity=name, metric="sharpe_ratio",
                expected=f"annualised Sharpe ≈ {annualised:.4f}",
                actual=f"{float(stored):.4f} "
                       f"(closer to monthly Sharpe {monthly:.4f})",
                detail=("Stored Sharpe appears to be the monthly "
                        "Sharpe (no sqrt(12) annualisation). The "
                        "full-period Sharpe must be annualised.")))
    return vios, n


def check_2c_factor_betas_same_window(
    factor_loadings_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """2c — every strategy's factor betas should be estimated on
    consistent windows. Surfaces only an obvious mismatch (different
    period_start / period_end across rows in the same table)."""
    vios: list[InvariantViolation] = []
    n = 0
    if not factor_loadings_payload:
        return vios, n
    rows = factor_loadings_payload.get("rows") \
        or factor_loadings_payload.get("strategies") or []
    starts, ends = set(), set()
    for row in rows:
        n += 1
        s, e = row.get("period_start"), row.get("period_end")
        if s is not None:
            starts.add(s)
        if e is not None:
            ends.add(e)
    # Two distinct window endpoints across rows is acceptable when
    # short-history strategies legitimately have shorter samples; a
    # check of length > 4 is the signal for a real bug (more distinct
    # windows than strategy cohorts).
    if len(starts) > 4 or len(ends) > 4:
        vios.append(InvariantViolation(
            code="2c", severity="hard", category=2,
            entity="factor_loadings", metric="estimation_window",
            expected="2-4 distinct start/end pairs "
                     "(per strategy-cohort)",
            actual=f"{len(starts)} distinct starts, "
                   f"{len(ends)} distinct ends",
            detail=("Factor-loadings rows use too many distinct "
                    "estimation windows — comparisons across rows "
                    "are no longer apples-to-apples.")))
    return vios, n


def check_2d_metric_basis_consistent_across_tables(
    strategy_results: dict[str, dict],
    crisis_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """2d — a metric appearing in more than one table uses the same
    basis. Spot-check: a strategy's full-period Sharpe stored on the
    result must match the Sharpe stored in any other table that
    repeats it (today only the strategy_results table holds Sharpe,
    so this is a reservation for future cross-table cases). For
    now, verify that a strategy's `cagr` field is consistent with a
    fresh recompute from monthly_returns — a sanity self-check that
    catches a quiet stored-value drift."""
    vios: list[InvariantViolation] = []
    n = 0
    for name, res in (strategy_results or {}).items():
        s = _monthly_series(res)
        if len(s) < 12:
            continue
        stored_cagr = res.get("cagr")
        if stored_cagr is None:
            continue
        n += 1
        recomp = _cagr(s)
        if abs(float(stored_cagr) - recomp) > 0.005:
            vios.append(InvariantViolation(
                code="2d", severity="hard", category=2,
                entity=name, metric="cagr",
                expected=f"recompute from monthly: {recomp:.4f}",
                actual=f"{float(stored_cagr):.4f}",
                detail=("Stored CAGR drifted from a fresh recompute "
                        "off the monthly series beyond 0.5% — basis "
                        "drift between the storage and the underlying "
                        "series.")))
    return vios, n


# ── CATEGORY 3 — EXTERNAL REFERENCE CHECKS (SOFT) ──────────────────────────


_BENCHMARK_CRISIS_PLAUSIBILITY: dict[str, tuple[float, float]] = {
    "GFC_2008-2009":    (-0.50, -0.38),
    "EU_Debt_2011":     (-0.10, -0.02),
    "COVID_Crash_2020": (-0.25, -0.15),
    "COVID_Recovery":   (+0.70, +0.95),
    "Rate_Shock_2022":  (-0.22, -0.16),
}


def check_3_benchmark_crisis_plausibility(
    crisis_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """3 — benchmark crisis cumulative returns must fall within
    published-reference ranges (S&P 500 price-return)."""
    vios: list[InvariantViolation] = []
    n = 0
    if not crisis_payload:
        return vios, n
    rows = crisis_payload.get("rows") or {}
    bench = rows.get("BENCHMARK") or {}
    for crisis, (lo, hi) in _BENCHMARK_CRISIS_PLAUSIBILITY.items():
        cell = bench.get(crisis) or {}
        cum = cell.get("cumulative_return")
        if cum is None:
            continue
        n += 1
        if not (lo <= float(cum) <= hi):
            vios.append(InvariantViolation(
                code="3-benchmark", severity="soft", category=3,
                entity=f"BENCHMARK/{crisis}",
                metric="cumulative_return",
                expected=f"[{lo:+.2%}, {hi:+.2%}] "
                         "(S&P 500 price-return reference)",
                actual=f"{float(cum):+.4f}",
                detail=("Benchmark crisis return outside the published "
                        "reference range — investigate the underlying "
                        "monthly series for that window.")))
    return vios, n


_MACRO_PLAUSIBILITY: dict[str, tuple[float, float]] = {
    "dgs10":  (0.005, 0.08),    # 10Y yield: 0.5% – 8.0%
    "vix":    (9.0, 85.0),      # VIX level
    "hy_oas": (200.0, 2500.0),  # BAMLH0A0HYM2 OAS in bps
    "dtb3":   (0.0, 0.06),      # T-bill: 0% – 6%
}


def check_3_macro_series_plausibility(
    macro_series: dict[str, Iterable[float]] | None,
) -> tuple[list[InvariantViolation], int]:
    """3 — each macro series stays within its plausibility band."""
    vios: list[InvariantViolation] = []
    n = 0
    if not macro_series:
        return vios, n
    for series_id, (lo, hi) in _MACRO_PLAUSIBILITY.items():
        values = list(macro_series.get(series_id) or [])
        if not values:
            continue
        n += 1
        mn, mx = float(min(values)), float(max(values))
        if mn < lo or mx > hi:
            vios.append(InvariantViolation(
                code="3-macro", severity="soft", category=3,
                entity=series_id, metric="series_range",
                expected=f"[{lo}, {hi}]",
                actual=f"observed [{mn:.4f}, {mx:.4f}]",
                detail=("Macro series outside the plausibility band — "
                        "verify the upstream fetch and any unit "
                        "conversions.")))
    return vios, n


# ── CATEGORY 4 — DIRECTIONAL LOGIC (SOFT) ──────────────────────────────────


def check_4a_defensive_protects_in_crash(
    strategy_results: dict[str, dict],
    crisis_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """4a — in each loss window, VOL_TARGETING should beat BENCHMARK.
    The lowest-beta strategy should protect capital best."""
    vios: list[InvariantViolation] = []
    n = 0
    if not crisis_payload:
        return vios, n
    rows = crisis_payload.get("rows") or {}
    bench = rows.get("BENCHMARK") or {}
    vt = rows.get("VOL_TARGETING") or {}
    for crisis, bcell in bench.items():
        bret = bcell.get("cumulative_return")
        vcell = vt.get(crisis) or {}
        vret = vcell.get("cumulative_return")
        if bret is None or vret is None or float(bret) >= 0:
            continue
        n += 1
        if float(vret) < float(bret):
            vios.append(InvariantViolation(
                code="4a", severity="soft", category=4,
                entity=f"VOL_TARGETING/{crisis}",
                metric="cumulative_return",
                expected=f"VOL_TARGETING return > benchmark "
                         f"{float(bret):.4f}",
                actual=f"{float(vret):.4f}",
                detail=("A volatility-targeting strategy lost more "
                        "than the benchmark in a crash window — "
                        "investigate the regime-detection / scaling "
                        "logic for this window.")))
    return vios, n


def check_4b_higher_sharpe_lower_cvar(
    strategy_results: dict[str, dict],
) -> tuple[list[InvariantViolation], int]:
    """4b — across strategies, higher Sharpe should generally imply
    less-negative CVaR. Flag the pair with the worst inversion."""
    vios: list[InvariantViolation] = []
    n = 0
    pairs = [
        (name, float(r.get("sharpe_ratio")), float(r.get("cvar_95")))
        for name, r in (strategy_results or {}).items()
        if (r.get("sharpe_ratio") is not None
            and r.get("cvar_95") is not None)
    ]
    if len(pairs) < 4:
        return vios, n
    n += 1
    # Sort by Sharpe descending; check that CVaR doesn't get
    # systematically more negative with higher Sharpe.
    pairs.sort(key=lambda x: x[1], reverse=True)
    top = pairs[0]
    bottom = pairs[-1]
    if top[2] < bottom[2] - 0.02:
        vios.append(InvariantViolation(
            code="4b", severity="soft", category=4,
            entity=f"{top[0]} vs {bottom[0]}",
            metric="sharpe / cvar inversion",
            expected="higher Sharpe → less negative CVaR",
            actual=f"top-Sharpe {top[0]} CVaR95={top[2]:.4f}, "
                   f"bottom-Sharpe {bottom[0]} CVaR95={bottom[2]:.4f}",
            detail=("The strategy with the highest Sharpe has worse "
                    "tail risk than the lowest-Sharpe strategy — "
                    "unusual; investigate.")))
    return vios, n


def check_4c_tangency_sharpe_dominates(
    strategy_results: dict[str, dict],
    tangency_sharpe: float | None,
) -> tuple[list[InvariantViolation], int]:
    """4c — the tangency portfolio sits on the efficient frontier;
    its Sharpe must equal or exceed every individual-strategy
    Sharpe. (Slack on equality because the tangency optimiser is
    sample-noisy.)"""
    vios: list[InvariantViolation] = []
    n = 0
    if tangency_sharpe is None:
        return vios, n
    n += 1
    best = max(
        (float(r.get("sharpe_ratio"))
         for r in (strategy_results or {}).values()
         if r.get("sharpe_ratio") is not None),
        default=None)
    if best is None:
        return vios, n
    if float(tangency_sharpe) < best - 0.02:
        vios.append(InvariantViolation(
            code="4c", severity="soft", category=4,
            entity="tangency_portfolio", metric="sharpe_ratio",
            expected=f"≥ best individual Sharpe = {best:.4f}",
            actual=f"{float(tangency_sharpe):.4f}",
            detail=("Tangency Sharpe below the best individual "
                    "strategy's Sharpe — implies the tangency "
                    "optimiser did not find the frontier's apex "
                    "(SLSQP fallback / constraint binding).")))
    return vios, n


def check_4d_bootstrap_ci_brackets_point(
    bootstrap_payload: dict[str, Any] | None,
) -> tuple[list[InvariantViolation], int]:
    """4d — every bootstrap 95% CI must bracket its point estimate."""
    vios: list[InvariantViolation] = []
    n = 0
    if not bootstrap_payload:
        return vios, n
    rows = bootstrap_payload.get("rows") or bootstrap_payload.get("strategies") or []
    eps = 1e-6
    for row in rows:
        name = row.get("strategy") or row.get("name") or "?"
        point = row.get("sharpe_ratio") or row.get("point")
        lo = row.get("ci_low")
        hi = row.get("ci_high")
        if point is None or lo is None or hi is None:
            continue
        n += 1
        if float(lo) > float(point) + eps or float(hi) < float(point) - eps:
            vios.append(InvariantViolation(
                code="4d", severity="soft", category=4,
                entity=name, metric="bootstrap_ci",
                expected=f"lo ≤ point ≤ hi (point={float(point):.4f})",
                actual=f"[{float(lo):.4f}, {float(hi):.4f}]",
                detail=("Bootstrap CI does not contain the point "
                        "estimate — verify the resample/aggregation.")))
    return vios, n


def check_4e_defensive_outperforms_post_2022(
    strategy_results: dict[str, dict],
) -> tuple[list[InvariantViolation], int]:
    """4e — in the post-2022 regime, the defensive strategies
    (MIN_VARIANCE, RISK_PARITY, VOL_TARGETING) should outperform
    BENCHMARK on Sharpe. Read from `subperiod_results.post_2022` if
    present; the check is gated on the field being available."""
    vios: list[InvariantViolation] = []
    n = 0
    bench = (strategy_results or {}).get("BENCHMARK") or {}
    bsub = bench.get("subperiod_results") or {}
    bpost = (bsub.get("post_2022") or {}).get("sharpe")
    if bpost is None:
        return vios, n
    for defender in ("MIN_VARIANCE", "RISK_PARITY", "VOL_TARGETING"):
        res = (strategy_results or {}).get(defender) or {}
        dsub = res.get("subperiod_results") or {}
        dpost = (dsub.get("post_2022") or {}).get("sharpe")
        if dpost is None:
            continue
        n += 1
        if float(dpost) < float(bpost):
            vios.append(InvariantViolation(
                code="4e", severity="soft", category=4,
                entity=defender, metric="post_2022_sharpe",
                expected=f"≥ benchmark post-2022 Sharpe = "
                         f"{float(bpost):.4f}",
                actual=f"{float(dpost):.4f}",
                detail=("Defensive strategy underperforms benchmark "
                        "post-2022 — investigate whether the regime "
                        "logic is firing as designed.")))
    return vios, n


# ── CATEGORY 5 — TEMPORAL INTEGRITY ────────────────────────────────────────


def check_5a_no_monthly_gaps(
    strategy_results: dict[str, dict],
) -> tuple[list[InvariantViolation], int]:
    """5a (HARD) — no gaps in the monthly series for any strategy
    between its first and last observation. The pipeline aligns
    every series to month-end; a gap is a real data integrity bug."""
    vios: list[InvariantViolation] = []
    n = 0
    for name, res in (strategy_results or {}).items():
        s = _monthly_series(res)
        if len(s) < 3:
            continue
        n += 1
        # Compare in Period-of-month space — month-end snapping
        # makes a Timestamp comparison brittle (Feb has different
        # month-end dates across years), but Period('YYYY-MM') is
        # canonical and equality works cleanly.
        expected_p = pd.period_range(
            s.index[0].to_period("M"), s.index[-1].to_period("M"),
            freq="M")
        present = {ts.to_period("M") for ts in s.index}
        missing = [p for p in expected_p if p not in present]
        if missing:
            vios.append(InvariantViolation(
                code="5a", severity="hard", category=5,
                entity=name, metric="monthly_series",
                expected=f"contiguous months {s.index[0].date()} "
                         f"→ {s.index[-1].date()}",
                actual=f"{len(missing)} missing month(s); "
                       f"first gap at {missing[0]}",
                detail="Gap in the monthly return series — data "
                       "integrity bug upstream."))
    return vios, n


def check_5b_initialisation_period_excluded(
    strategy_results: dict[str, dict],
    *,
    expected_lookback_months: dict[str, int] | None = None,
) -> tuple[list[InvariantViolation], int]:
    """5b (HARD) — strategies with a stated lookback window must NOT
    produce results before the lookback has rolled. Read the
    project's expected_lookback_months map; flag a strategy whose
    series starts more than ~1 month earlier than the map allows."""
    vios: list[InvariantViolation] = []
    n = 0
    if expected_lookback_months is None:
        expected_lookback_months = {
            "REGIME_SWITCHING":   3,
            "MOMENTUM_ROTATION":  12,
            "MIN_VARIANCE":       36,
            "BLACK_LITTERMAN":    36,
            "MAX_SHARPE_ROLLING": 36,
        }
    # Reference anchor — the BENCHMARK (or earliest asset) is the
    # data start. Use whichever full-history strategy is shortest.
    anchor = None
    for ref in ("BENCHMARK", "CLASSIC_60_40", "EQUAL_WEIGHT"):
        s = _monthly_series((strategy_results or {}).get(ref) or {})
        if not s.empty:
            anchor = s.index[0]
            break
    if anchor is None:
        return vios, n
    for name, lookback in expected_lookback_months.items():
        res = (strategy_results or {}).get(name) or {}
        s = _monthly_series(res)
        if s.empty:
            continue
        n += 1
        expected_start = anchor + pd.DateOffset(months=lookback - 1)
        # Allow 1 month slack for alignment differences.
        if s.index[0] < expected_start - pd.DateOffset(months=1):
            vios.append(InvariantViolation(
                code="5b", severity="hard", category=5,
                entity=name, metric="series_start",
                expected=f"≥ {expected_start.date()} "
                         f"(anchor + {lookback}m lookback)",
                actual=f"{s.index[0].date()}",
                detail=("Strategy returns appear before its lookback "
                        "window has completed — initialisation period "
                        "not excluded; possible look-ahead.")))
    return vios, n


def check_5d_crisis_windows_within_data_range(
    crisis_payload: dict[str, Any] | None,
    strategy_results: dict[str, dict],
) -> tuple[list[InvariantViolation], int]:
    """5d (SOFT) — every crisis window's [start, end] sits inside the
    BENCHMARK data range. A window referencing a date past the data
    end produces an empty cell — visible to a reader, but worth
    flagging in the invariant report."""
    vios: list[InvariantViolation] = []
    n = 0
    if not crisis_payload:
        return vios, n
    s = _monthly_series(
        (strategy_results or {}).get("BENCHMARK") or {})
    if s.empty:
        return vios, n
    data_start, data_end = s.index[0], s.index[-1]
    for crisis, w in (crisis_payload.get("windows") or {}).items():
        n += 1
        start = pd.Timestamp(w.get("start"))
        end = pd.Timestamp(w.get("end"))
        if pd.isna(start) or pd.isna(end):
            continue
        if end < data_start or start > data_end:
            vios.append(InvariantViolation(
                code="5d", severity="soft", category=5,
                entity=crisis, metric="window_range",
                expected=f"within data range "
                         f"[{data_start.date()}, {data_end.date()}]",
                actual=f"[{start.date()}, {end.date()}]",
                detail=("Crisis window falls outside the available "
                        "data range — the cell will be empty in the "
                        "table.")))
    return vios, n


def check_5e_oos_split_after_lookbacks(
    *,
    oos_split: pd.Timestamp | str = "2022-01-01",
    data_start: pd.Timestamp | str | None = None,
    min_months_after_start: int = 36,
) -> tuple[list[InvariantViolation], int]:
    """5e (SOFT) — the OOS split must sit ≥36 months after data start
    so initialisation periods of the longest-lookback strategies
    fall entirely within the in-sample window."""
    vios: list[InvariantViolation] = []
    n = 0
    if data_start is None:
        return vios, n
    n += 1
    ds = pd.Timestamp(data_start)
    sp = pd.Timestamp(oos_split)
    gap_months = (sp.year - ds.year) * 12 + (sp.month - ds.month)
    if gap_months < min_months_after_start:
        vios.append(InvariantViolation(
            code="5e", severity="soft", category=5,
            entity="oos_split", metric="months_after_start",
            expected=f"≥ {min_months_after_start} months "
                     f"after data start ({ds.date()})",
            actual=f"{gap_months} months",
            detail=("OOS split is too close to the data start; "
                    "the longest-lookback strategies have not "
                    "rolled their initialisation period in.")))
    return vios, n


# ── Top-level runner ──────────────────────────────────────────────────────


# Module-level cache of the most recent run so the admin endpoint
# can surface it without re-running. Updated by run_all_invariants on
# every call (whether the result passed or failed).
_latest_result: dict[str, Any] | None = None
_latest_timestamp: str | None = None


def get_latest_result() -> dict[str, Any] | None:
    """Returns the most recent run summary (or None). Backs the
    /api/v1/admin/invariants surface."""
    if _latest_result is None:
        return None
    return {**_latest_result, "ran_at": _latest_timestamp}


def run_all_invariants(
    strategy_results: dict[str, dict] | None,
    *,
    crisis_payload: dict[str, Any] | None = None,
    correlation_payload: dict[str, Any] | None = None,
    factor_loadings_payload: dict[str, Any] | None = None,
    bootstrap_payload: dict[str, Any] | None = None,
    asset_returns: dict[str, list[float]] | None = None,
    macro_series: dict[str, Iterable[float]] | None = None,
    risk_free_rate: float | None = None,
    tangency_sharpe: float | None = None,
    oos_split: str = "2022-01-01",
    data_start: str | None = None,
) -> InvariantResult:
    """Runs every check, aggregates the result, and logs the summary.

    Hard failures are also logged individually under
    `invariant_hard_failure` so an upstream alert system (Render log
    drain, etc.) can fire on the code alone. Soft warnings are
    logged under `invariant_soft_warning`. The caller is responsible
    for honouring `passed` — typically by refusing to write the new
    cache row when it is False."""
    strategy_results = strategy_results or {}
    res = InvariantResult()

    # The check_* functions are intentionally homogeneous — each
    # returns (violations, n_checks_run). The runner stitches them.
    suites: list[tuple[Iterable[InvariantViolation], int]] = []
    suites.append(check_1a_window_return_le_full_max_dd(
        strategy_results, crisis_payload))
    suites.append(check_1b_sharpe_consistent_with_components(
        strategy_results, risk_free_rate=risk_free_rate))
    suites.append(check_1c_max_drawdown_le_min_monthly(strategy_results))
    suites.append(check_1d_cvar99_le_cvar95(strategy_results))
    suites.append(check_1e_weight_schedule_sums_to_one(strategy_results))
    suites.append(check_1f_correlation_matrix_psd(correlation_payload))
    suites.append(check_1g_monthly_return_bounds(
        strategy_results, asset_returns))
    suites.append(check_1h_full_period_dd_dominates_crisis(
        strategy_results, crisis_payload))

    suites.append(check_2a_crisis_uses_cumulative_basis(
        crisis_payload, strategy_results))
    suites.append(check_2b_full_period_sharpe_annualised(strategy_results))
    suites.append(check_2c_factor_betas_same_window(factor_loadings_payload))
    suites.append(check_2d_metric_basis_consistent_across_tables(
        strategy_results, crisis_payload))

    suites.append(check_3_benchmark_crisis_plausibility(crisis_payload))
    suites.append(check_3_macro_series_plausibility(macro_series))

    suites.append(check_4a_defensive_protects_in_crash(
        strategy_results, crisis_payload))
    suites.append(check_4b_higher_sharpe_lower_cvar(strategy_results))
    suites.append(check_4c_tangency_sharpe_dominates(
        strategy_results, tangency_sharpe))
    suites.append(check_4d_bootstrap_ci_brackets_point(bootstrap_payload))
    suites.append(check_4e_defensive_outperforms_post_2022(strategy_results))

    suites.append(check_5a_no_monthly_gaps(strategy_results))
    suites.append(check_5b_initialisation_period_excluded(strategy_results))
    suites.append(check_5d_crisis_windows_within_data_range(
        crisis_payload, strategy_results))
    suites.append(check_5e_oos_split_after_lookbacks(
        oos_split=oos_split, data_start=data_start))

    for vios, n in suites:
        res.violations.extend(vios)
        res.checks_run += n

    # Per-violation logging, then the summary line.
    for v in res.hard_failures:
        log.warning("invariant_hard_failure", **v.to_dict())
    for v in res.soft_warnings:
        log.info("invariant_soft_warning", **v.to_dict())
    log.info("invariant_check_summary", **res.summary_log_payload())

    # Update the module-level cache so the admin surface and the
    # next /api/v1/admin/invariants poll read the freshest result.
    global _latest_result, _latest_timestamp
    from datetime import datetime, timezone
    _latest_result = res.to_dict()
    _latest_timestamp = datetime.now(timezone.utc).isoformat()

    return res
