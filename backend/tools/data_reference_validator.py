"""tools/data_reference_validator.py -- cross-reference validator
for the Data Reference Sheet.

PURPOSE

Bob and Molly use the Data Reference Sheet to confirm every value
in the submission documents matches its underlying source. Until
now that check was visual -- a human had to compare each token
value against the analytics endpoint or the strategy cache. This
module automates that comparison.

ARCHITECTURE

  validate_reference_sheet(rendered_categories, sources)
    The top-level entry point. Walks the rendered reference sheet
    (the response the existing /data-reference-sheet endpoint
    produces), dispatches each token to the strategy registered
    for its token name pattern, and aggregates results into a
    ValidationReport.

  ValidationStrategy
    Per-category validation logic. Each strategy:
      - Knows which tokens it owns (matched by regex / explicit
        prefix list).
      - Knows which source row + field carries the authoritative
        value.
      - Knows the tolerance / comparison rule for its value type.
      - Returns ValidationResult with status pass/fail/warning/
        skipped + delta + cache_freshness.

  SOURCES (pre-loaded once per request to avoid re-querying):
    strategy_cache         -- get_latest_strategy_cache()
    cio_row                -- get_latest_recommendation()
    implied_alloc          -- compute_implied_asset_allocation
                              from cio blend_weights
    live_signals           -- detect_current_regime()
    academic_analytics     -- get_latest_metric("academic_analytics")
    oos_cost_sensitivity   -- get_latest_metric("oos_cost_sensitivity")
    monthly                -- get_monthly_returns() (for derived
                              correlation reproduction)

TOLERANCES

  Sharpe / blend ratios:    +/- 0.01
  Factor loadings:          +/- 0.0001
  Percentages:              +/- 0.005 (0.5 pp)
  Correlation:              +/- 0.01
  Integers / strings:       exact
  Months:                   exact

  Locked constants (academic_deck.py / OOS_*_LOCKED) are
  marked skipped with cache_freshness=None and note "locked
  at submission".

STALENESS

  A pass with the source row's computed_at older than 24 hours
  is downgraded to "warning" with note "source row N hours
  old". The frontend renders amber.

DESIGN NOTES

  - Zero LLM calls. Strictly numeric / string comparison.
  - Fail-open per token: a strategy raising returns skipped
    with note "validator_error: <msg>"; the report still
    completes for the other 152 tokens.
  - The strategies hold NO global state -- they take a Sources
    dataclass + reference_value and return a ValidationResult.
    Unit-testable without touching the DB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import structlog

log = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────


_SHARPE_TOL = 0.01
_FACTOR_TOL = 0.0001
_PCT_TOL = 0.005       # 0.5 percentage points expressed as fraction
_CORR_TOL = 0.01
_STALE_HOURS = 24

_LOCKED_NOTE = "locked at submission"
_VALIDATOR_ERROR_PREFIX = "validator_error: "
_MISSING_SOURCE_NOTE = "source unavailable"
_NO_STRATEGY_NOTE = "no validator registered for this token"


Status = Literal["pass", "fail", "warning", "skipped"]


# ── Data shapes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    """One row of the validation report. Mirrors what the frontend
    panel renders alongside each token: status pill, optional
    expanded diff for failures, freshness timestamp."""
    token: str
    label: str
    reference_value: str | None
    source_value: str | None
    source_endpoint: str
    status: Status
    delta: str | None = None
    note: str | None = None
    cache_freshness: str | None = None

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "label": self.label,
            "reference_value": self.reference_value,
            "source_value": self.source_value,
            "source_endpoint": self.source_endpoint,
            "status": self.status,
            "delta": self.delta,
            "note": self.note,
            "cache_freshness": self.cache_freshness,
        }


@dataclass
class Sources:
    """Pre-loaded source data for one validation pass. Strategies
    read from this rather than re-querying the DB per token --
    one DB hit per source for the whole 153-token report."""
    strategy_cache: dict = field(default_factory=dict)
    cio_row: dict | None = None
    implied_alloc: dict | None = None
    live_signals: dict | None = None
    academic_analytics: dict | None = None
    oos_cost_sensitivity: dict | None = None
    n_monthly_months: int | None = None
    # Per-source computed_at timestamps for cache_freshness.
    strategy_cache_computed_at: str | None = None
    academic_analytics_computed_at: str | None = None
    oos_cost_sensitivity_computed_at: str | None = None
    cio_computed_at: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    data_hash: str
    validated_at: str
    summary: dict
    results: list[ValidationResult]

    def to_dict(self) -> dict:
        return {
            "data_hash": self.data_hash,
            "validated_at": self.validated_at,
            "summary": self.summary,
            "results": [r.to_dict() for r in self.results],
        }


# ── Value parsing ────────────────────────────────────────────────────


def _parse_decimal(formatted: str | None) -> float | None:
    """Parse a Sharpe/correlation/factor-loading display string
    back to a float. Handles leading sign, leading/trailing
    whitespace. None / em-dash / empty -> None."""
    if not formatted or formatted in ("—", "-", ""):
        return None
    s = str(formatted).strip().lstrip("+")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_pct(formatted: str | None) -> float | None:
    """Parse a "62.0%" or "-22.0%" display string back to a
    decimal fraction (0.62 / -0.22). None / em-dash -> None."""
    if not formatted or formatted in ("—", "-", ""):
        return None
    s = str(formatted).strip().lstrip("+")
    had_pct = s.endswith("%")
    if had_pct:
        s = s[:-1]
    try:
        v = float(s)
        return v / 100.0 if had_pct else v
    except (TypeError, ValueError):
        return None


def _parse_months(formatted: str | None) -> int | None:
    """Parse "8 months" or "8" back to int. The bug from
    June 21 -- "37 months months" -- means we may see double-
    suffix junk; strip both."""
    if not formatted or formatted in ("—", "-", ""):
        return None
    s = str(formatted).strip()
    s = re.sub(r"\s*months?\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*months?\s*$", "", s, flags=re.IGNORECASE)
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _format_sharpe(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def _format_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _format_corr(v: float | None) -> str:
    if v is None:
        return "—"
    return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"


def _format_factor(v: float | None) -> str:
    return "—" if v is None else f"{v:.4f}"


# ── Freshness check ─────────────────────────────────────────────────


def _is_stale(computed_at: str | None) -> bool:
    """True when the source row was written more than
    _STALE_HOURS ago. Comparison is timezone-aware -- assumes
    naive ISO strings are UTC."""
    if not computed_at:
        return False
    try:
        s = computed_at.replace("Z", "+00:00")
        ts = datetime.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts > timedelta(
            hours=_STALE_HOURS)
    except (TypeError, ValueError):
        return False


# ── Strategy helpers ────────────────────────────────────────────────


def _skipped(
    token: str, label: str, reference_value: str | None,
    source_endpoint: str, note: str = _LOCKED_NOTE,
) -> ValidationResult:
    return ValidationResult(
        token=token, label=label,
        reference_value=reference_value,
        source_value=reference_value,
        source_endpoint=source_endpoint,
        status="skipped", note=note,
        cache_freshness=None)


def _missing_source(
    token: str, label: str, reference_value: str | None,
    source_endpoint: str,
) -> ValidationResult:
    return ValidationResult(
        token=token, label=label,
        reference_value=reference_value, source_value=None,
        source_endpoint=source_endpoint,
        status="skipped", note=_MISSING_SOURCE_NOTE,
        cache_freshness=None)


def _compare_floats(
    token: str, label: str,
    reference_value: str | None,
    source_float: float | None,
    formatted_source: str,
    source_endpoint: str,
    tolerance: float,
    parser: Callable[[str | None], float | None],
    cache_freshness: str | None,
) -> ValidationResult:
    """Common shape for any numeric token validator. Parses the
    reference value via `parser`, compares to source_float within
    tolerance, and downgrades pass-with-stale-source to warning."""
    ref_float = parser(reference_value)
    if source_float is None:
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=None,
            source_endpoint=source_endpoint,
            status="skipped", note=_MISSING_SOURCE_NOTE,
            cache_freshness=cache_freshness)
    if ref_float is None:
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=formatted_source,
            source_endpoint=source_endpoint,
            status="fail",
            delta=(
                f"reference is em-dash; source has "
                f"{formatted_source}"),
            cache_freshness=cache_freshness)
    delta = abs(ref_float - source_float)
    if delta > tolerance:
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=formatted_source,
            source_endpoint=source_endpoint,
            status="fail",
            delta=f"|Δ| = {delta:.4f} (tolerance {tolerance})",
            cache_freshness=cache_freshness)
    if _is_stale(cache_freshness):
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=formatted_source,
            source_endpoint=source_endpoint,
            status="warning",
            note=(
                "source row > "
                f"{_STALE_HOURS}h old; rerun warm pipeline"),
            cache_freshness=cache_freshness)
    return ValidationResult(
        token=token, label=label,
        reference_value=reference_value,
        source_value=formatted_source,
        source_endpoint=source_endpoint,
        status="pass",
        cache_freshness=cache_freshness)


# ── Per-category strategies ─────────────────────────────────────────


def _validate_locked(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """Locked academic constants -- defended at panel, not
    validated at runtime. Skip with the canonical note."""
    return _skipped(
        token, label, reference_value,
        "academic_deck.py (locked)")


def _validate_strategy_metric(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{<STRATEGY>_SHARPE}}, {{<STRATEGY>_MAX_DD}},
    {{<STRATEGY>_CAGR}}, {{<STRATEGY>_VOLATILITY}},
    {{<STRATEGY>_RECOVERY_MONTHS}} -- read from
    strategy_results_cache via the cache key."""
    m = re.match(
        r"\{\{([A-Z_0-9]+)_"
        r"(SHARPE|MAX_DD|CAGR|VOLATILITY|RECOVERY_MONTHS)\}\}",
        token)
    if not m:
        return _skipped(
            token, label, reference_value,
            "strategy_cache", note=_NO_STRATEGY_NOTE)
    strategy, metric_suffix = m.groups()
    entry = sources.strategy_cache.get(strategy)
    if not isinstance(entry, dict):
        return _missing_source(
            token, label, reference_value,
            f"strategy_cache[{strategy}]")
    field_map = {
        "SHARPE": ("sharpe_ratio", _format_sharpe,
                   _parse_decimal, _SHARPE_TOL),
        "MAX_DD": ("max_drawdown", _format_pct,
                   _parse_pct, _PCT_TOL),
        "CAGR": ("cagr", _format_pct,
                 _parse_pct, _PCT_TOL),
        "VOLATILITY": ("volatility", _format_pct,
                       _parse_pct, _PCT_TOL),
        "RECOVERY_MONTHS": ("drawdown_recovery_days", None,
                            None, None),
    }
    cache_field, formatter, parser, tol = field_map[metric_suffix]
    if metric_suffix == "RECOVERY_MONTHS":
        # Recovery is stored as DAYS; reference renders as
        # "<n> months" via /21 trading-day convention.
        days = entry.get("drawdown_recovery_days")
        if days is None:
            return _missing_source(
                token, label, reference_value,
                f"strategy_cache[{strategy}].drawdown_recovery_days")
        try:
            source_months = int(round(float(days) / 21.0))
        except (TypeError, ValueError):
            return _missing_source(
                token, label, reference_value,
                f"strategy_cache[{strategy}].drawdown_recovery_days")
        ref_months = _parse_months(reference_value)
        if ref_months is None:
            return ValidationResult(
                token=token, label=label,
                reference_value=reference_value,
                source_value=f"{source_months} months",
                source_endpoint=(
                    f"strategy_cache[{strategy}]"
                    ".drawdown_recovery_days / 21"),
                status="fail",
                delta=(
                    f"reference is em-dash; source has "
                    f"{source_months} months"),
                cache_freshness=sources.strategy_cache_computed_at)
        if ref_months != source_months:
            return ValidationResult(
                token=token, label=label,
                reference_value=reference_value,
                source_value=f"{source_months} months",
                source_endpoint=(
                    f"strategy_cache[{strategy}]"
                    ".drawdown_recovery_days / 21"),
                status="fail",
                delta=f"Δ = {ref_months - source_months} months",
                cache_freshness=sources.strategy_cache_computed_at)
        if _is_stale(sources.strategy_cache_computed_at):
            return ValidationResult(
                token=token, label=label,
                reference_value=reference_value,
                source_value=f"{source_months} months",
                source_endpoint=(
                    f"strategy_cache[{strategy}]"
                    ".drawdown_recovery_days / 21"),
                status="warning",
                note=f"source row > {_STALE_HOURS}h old",
                cache_freshness=sources.strategy_cache_computed_at)
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=f"{source_months} months",
            source_endpoint=(
                f"strategy_cache[{strategy}]"
                ".drawdown_recovery_days / 21"),
            status="pass",
            cache_freshness=sources.strategy_cache_computed_at)
    raw = entry.get(cache_field)
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=formatter(source_float),
        source_endpoint=f"strategy_cache[{strategy}].{cache_field}",
        tolerance=tol,
        parser=parser,
        cache_freshness=sources.strategy_cache_computed_at)


def _validate_regime_conditional_sharpe(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{<STRATEGY>_POST2022_SHARPE}} and
    {{<STRATEGY>_PRE2022_SHARPE}} -- read from
    analytics_metrics_cache[academic_analytics].regime_conditional.
    """
    m = re.match(
        r"\{\{([A-Z_0-9]+)_(PRE2022|POST2022)_SHARPE\}\}", token)
    if not m:
        return _skipped(
            token, label, reference_value,
            "analytics_metrics_cache[academic_analytics]",
            note=_NO_STRATEGY_NOTE)
    strategy, regime = m.groups()
    aa = sources.academic_analytics or {}
    rc_rows = aa.get("regime_conditional") or []
    row = None
    for r in rc_rows:
        if isinstance(r, dict) and (
                r.get("strategy") == strategy
                or r.get("strategy_name") == strategy):
            row = r
            break
    if row is None:
        return _missing_source(
            token, label, reference_value,
            "analytics_metrics_cache"
            f"[academic_analytics].regime_conditional[{strategy}]")
    field = "pre_2022_sharpe" if regime == "PRE2022" else "post_2022_sharpe"
    raw = row.get(field)
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=_format_sharpe(source_float),
        source_endpoint=(
            "analytics_metrics_cache"
            f"[academic_analytics].regime_conditional[{strategy}]"
            f".{field}"),
        tolerance=_SHARPE_TOL,
        parser=_parse_decimal,
        cache_freshness=sources.academic_analytics_computed_at)


def _validate_factor_loading(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{<STRATEGY>_ALPHA}}, {{<STRATEGY>_BETA}},
    {{<STRATEGY>_SMB_BETA}}, {{<STRATEGY>_HML_BETA}},
    {{<STRATEGY>_R_SQUARED}}, plus the catalog-internal
    {{factor_loadings.<STRATEGY>.<field>}} shape -- read from
    analytics_metrics_cache[academic_analytics].factor_loadings.
    Maps the conceptual suffixes to the raw statsmodels fields
    (alpha_annualized / mkt_rf / smb / hml / r_squared)."""
    suffix_to_field = {
        "ALPHA": "alpha_annualized",
        "BETA": "mkt_rf",
        "SMB_BETA": "smb",
        "HML_BETA": "hml",
        "R_SQUARED": "r_squared",
    }
    # Non-greedy strategy capture + longest suffix alternative FIRST.
    # A greedy [A-Z_0-9]+ matches the longest sequence then backtracks
    # by one char looking for any valid suffix match -- so
    # "BENCHMARK_SMB_BETA" routes to strategy="BENCHMARK_SMB",
    # suffix="BETA" (BETA appears as an alternative and matches at the
    # first backtrack point). Non-greedy expands one char at a time
    # from minimum, taking the FIRST overall match -- combined with
    # SMB_BETA/HML_BETA listed before BETA, the regex correctly
    # consumes "BENCHMARK" as strategy and "SMB_BETA" as suffix.
    m = re.match(
        r"\{\{(.+?)_"
        r"(SMB_BETA|HML_BETA|R_SQUARED|ALPHA|BETA)\}\}", token)
    if not m:
        return _skipped(
            token, label, reference_value,
            "analytics_metrics_cache[academic_analytics]",
            note=_NO_STRATEGY_NOTE)
    strategy, suffix = m.groups()
    field = suffix_to_field[suffix]
    aa = sources.academic_analytics or {}
    fl_rows = aa.get("factor_loadings") or []
    row = None
    for r in fl_rows:
        if isinstance(r, dict) and (
                r.get("strategy") == strategy
                or r.get("strategy_name") == strategy):
            row = r
            break
    if row is None:
        return _missing_source(
            token, label, reference_value,
            "analytics_metrics_cache"
            f"[academic_analytics].factor_loadings[{strategy}]")
    raw = row.get(field)
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=_format_factor(source_float),
        source_endpoint=(
            "analytics_metrics_cache"
            f"[academic_analytics].factor_loadings[{strategy}]"
            f".{field}"),
        tolerance=_FACTOR_TOL,
        parser=_parse_decimal,
        cache_freshness=sources.academic_analytics_computed_at)


def _validate_net_sharpe(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{NET_SHARPE_10BP}}, {{NET_SHARPE_15BP}},
    {{NET_SHARPE_20BP}} -- read from
    analytics_metrics_cache[oos_cost_sensitivity].scenarios."""
    m = re.match(r"\{\{NET_SHARPE_(\d+)BP\}\}", token)
    if not m:
        return _skipped(
            token, label, reference_value,
            "analytics_metrics_cache[oos_cost_sensitivity]",
            note=_NO_STRATEGY_NOTE)
    bps = int(m.group(1))
    cs = sources.oos_cost_sensitivity or {}
    scenarios = cs.get("scenarios") or []
    row = None
    for s in scenarios:
        if isinstance(s, dict) and s.get("bps") == bps:
            row = s
            break
    if row is None:
        return _missing_source(
            token, label, reference_value,
            "analytics_metrics_cache"
            f"[oos_cost_sensitivity].scenarios[bps={bps}]")
    raw = row.get("net_sharpe")
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=_format_sharpe(source_float),
        source_endpoint=(
            "analytics_metrics_cache"
            f"[oos_cost_sensitivity].scenarios[bps={bps}]"
            ".net_sharpe"),
        tolerance=_SHARPE_TOL,
        parser=_parse_decimal,
        cache_freshness=sources.oos_cost_sensitivity_computed_at)


def _validate_live_signal(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{VIX_CURRENT}}, {{YIELD_CURVE_CURRENT}},
    {{CREDIT_SPREAD_CURRENT}}, {{EQUITY_TREND_CURRENT}} -- read
    from detect_current_regime() result."""
    field_map = {
        "{{VIX_CURRENT}}": ("vix_level", _format_sharpe,
                            _parse_decimal),
        "{{YIELD_CURVE_CURRENT}}": ("yield_curve_slope",
                                    _format_sharpe, _parse_decimal),
        "{{CREDIT_SPREAD_CURRENT}}": ("credit_spread",
                                      _format_sharpe, _parse_decimal),
        "{{EQUITY_TREND_CURRENT}}": ("equity_trend", _format_pct,
                                     _parse_pct),
    }
    if token not in field_map:
        return _skipped(
            token, label, reference_value,
            "detect_current_regime()", note=_NO_STRATEGY_NOTE)
    field, formatter, parser = field_map[token]
    ls = sources.live_signals or {}
    raw = ls.get(field)
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=formatter(source_float),
        source_endpoint=f"detect_current_regime().{field}",
        tolerance=_PCT_TOL if formatter is _format_pct else _SHARPE_TOL,
        parser=parser,
        cache_freshness=None)


def _validate_blend_weight(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{BLEND_<STRATEGY>_WT}} -- read from cio_row.blend_weights.
    Strategy slug in the token maps to the cio_row key."""
    m = re.match(r"\{\{BLEND_([A-Z_0-9]+)_WT\}\}", token)
    if not m:
        return _skipped(
            token, label, reference_value,
            "cio_recommendation.blend_weights",
            note=_NO_STRATEGY_NOTE)
    slug = m.group(1)
    # Token uses "REGIME_SWITCHING" / "BENCHMARK" / "CLASSIC_6040"
    # for the three brief-side strategies. The cio_row stores
    # blend_weights with cache-key names that mostly match, except
    # CLASSIC_6040 -> CLASSIC_60_40.
    cio_key_map = {"CLASSIC_6040": "CLASSIC_60_40"}
    cio_key = cio_key_map.get(slug, slug)
    cio = sources.cio_row or {}
    bw = cio.get("blend_weights") or {}
    raw = bw.get(cio_key)
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    if source_float is None:
        return _missing_source(
            token, label, reference_value,
            f"cio_recommendation.blend_weights[{cio_key}]")
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=_format_pct(source_float),
        source_endpoint=(
            f"cio_recommendation.blend_weights[{cio_key}]"),
        tolerance=_PCT_TOL,
        parser=_parse_pct,
        cache_freshness=sources.cio_computed_at)


def _validate_current_asset_pct(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{CURRENT_EQUITY_PCT}}, {{CURRENT_IG_PCT}},
    {{CURRENT_HY_PCT}} -- read from implied_alloc computed from
    the cio blend_weights via compute_implied_asset_allocation."""
    field_map = {
        "{{CURRENT_EQUITY_PCT}}": "equity_pct",
        "{{CURRENT_IG_PCT}}": "ig_bond_pct",
        "{{CURRENT_HY_PCT}}": "hy_bond_pct",
    }
    if token not in field_map:
        return _skipped(
            token, label, reference_value,
            "implied_asset_allocation", note=_NO_STRATEGY_NOTE)
    field = field_map[token]
    ia = sources.implied_alloc or {}
    raw = ia.get(field)
    try:
        source_float = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        source_float = None
    if source_float is None:
        return _missing_source(
            token, label, reference_value,
            f"implied_asset_allocation.{field}")
    return _compare_floats(
        token=token, label=label,
        reference_value=reference_value,
        source_float=source_float,
        formatted_source=_format_pct(source_float),
        source_endpoint=f"implied_asset_allocation.{field}",
        tolerance=_PCT_TOL,
        parser=_parse_pct,
        cache_freshness=sources.cio_computed_at)


def _validate_study_months(
    token: str, label: str, reference_value: str | None,
    sources: Sources,
) -> ValidationResult:
    """{{STUDY_MONTHS}} -- read from monthly returns count."""
    if token != "{{STUDY_MONTHS}}":
        return _skipped(
            token, label, reference_value, "monthly_returns",
            note=_NO_STRATEGY_NOTE)
    source_int = sources.n_monthly_months
    if source_int is None:
        return _missing_source(
            token, label, reference_value, "monthly_returns.length")
    try:
        ref_int = int(str(reference_value).strip())
    except (TypeError, ValueError):
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=str(source_int),
            source_endpoint="monthly_returns.length",
            status="fail",
            delta=f"reference {reference_value!r} not an integer",
            cache_freshness=None)
    if ref_int != source_int:
        return ValidationResult(
            token=token, label=label,
            reference_value=reference_value,
            source_value=str(source_int),
            source_endpoint="monthly_returns.length",
            status="fail",
            delta=f"Δ = {ref_int - source_int} months",
            cache_freshness=None)
    return ValidationResult(
        token=token, label=label,
        reference_value=reference_value,
        source_value=str(source_int),
        source_endpoint="monthly_returns.length",
        status="pass", cache_freshness=None)


# ── Token -> strategy dispatch ──────────────────────────────────────


_LOCKED_TOKENS: frozenset[str] = frozenset({
    # OOS / correlation locked constants (academic_deck.py)
    "{{OOS_SHARPE_BLEND}}",
    "{{OOS_SHARPE_BENCHMARK}}",
    "{{OOS_WINDOW_MONTHS}}",
    "{{OOS_WINDOW_PCT_OF_STUDY}}",
    "{{OOS_WINDOW}}",
    "{{PRE_2022_EQ_IG_CORR}}",
    "{{POST_2022_EQ_IG_CORR}}",
    "{{PLAY_BY_PLAY_VALUE_ADD}}",
    "{{STUDY_START}}", "{{STUDY_END}}",
    # Per-strategy locked academic figures
    # (academic_deck._LOCKED_PER_STRATEGY)
    "{{REGIME_SWITCHING_MAX_DD}}",
    "{{BENCHMARK_MAX_DD}}",
    "{{CLASSIC_6040_MAX_DD}}",
    "{{EQUAL_WEIGHT_MAX_DD}}",
    "{{RISK_PARITY_MAX_DD}}",
    "{{MIN_VARIANCE_MAX_DD}}",
    "{{VOL_TARGETING_MAX_DD}}",
    "{{BLACK_LITTERMAN_MAX_DD}}",
    "{{MOMENTUM_ROTATION_MAX_DD}}",
    "{{MAX_SHARPE_ROLLING_MAX_DD}}",
})


def dispatch_strategy(token: str) -> Callable[
    [str, str, str | None, Sources], ValidationResult,
]:
    """Return the validation strategy for `token`. Locked tokens
    short-circuit to _validate_locked; everything else routes by
    pattern. The unknown-token fallback marks skipped with a
    note so an unrecognised token shows up in the report
    explicitly rather than silently passing."""
    if token in _LOCKED_TOKENS:
        return _validate_locked
    if token == "{{STUDY_MONTHS}}":
        return _validate_study_months
    if re.match(r"\{\{[A-Z_0-9]+_(PRE2022|POST2022)_SHARPE\}\}",
                token):
        return _validate_regime_conditional_sharpe
    if re.match(
            r"\{\{.+?_"
            r"(SMB_BETA|HML_BETA|R_SQUARED|ALPHA|BETA)\}\}",
            token):
        return _validate_factor_loading
    if re.match(r"\{\{NET_SHARPE_\d+BP\}\}", token):
        return _validate_net_sharpe
    if token in (
            "{{VIX_CURRENT}}", "{{YIELD_CURVE_CURRENT}}",
            "{{CREDIT_SPREAD_CURRENT}}",
            "{{EQUITY_TREND_CURRENT}}"):
        return _validate_live_signal
    if re.match(r"\{\{BLEND_[A-Z_0-9]+_WT\}\}", token):
        return _validate_blend_weight
    if token in (
            "{{CURRENT_EQUITY_PCT}}", "{{CURRENT_IG_PCT}}",
            "{{CURRENT_HY_PCT}}"):
        return _validate_current_asset_pct
    if re.match(
            r"\{\{[A-Z_0-9]+_"
            r"(SHARPE|MAX_DD|CAGR|VOLATILITY|RECOVERY_MONTHS)\}\}",
            token):
        return _validate_strategy_metric
    # Catalog-internal tokens (data.factor_loadings.X.Y; the
    # category catalog doesn't go through the substitution table
    # for these -- they're read by main._resolve_value directly)
    # and the {{DATA_HASH}} / {{CURRENT_REGIME}} / similar
    # text tokens that don't have a numeric source to compare
    # against. Default to skipped.
    return lambda t, l, r, s: _skipped(  # noqa: E731
        t, l, r, "—", note=_NO_STRATEGY_NOTE)


# ── Top-level entry point ───────────────────────────────────────────


def validate_reference_sheet(
    rendered_categories: dict,
    sources: Sources,
    data_hash: str,
) -> ValidationReport:
    """Walk the rendered Data Reference Sheet and produce a
    ValidationReport. Each row dispatches to its strategy; a
    raising strategy is caught and reported as skipped with
    note='validator_error: <msg>' so the report always completes.

    `rendered_categories` -- the `categories` dict from the
    existing /data-reference-sheet endpoint response. Each
    category is `{label, entries: [{token, label, value,
    source, is_locked, last_verified, document_locations}]}`.

    `sources` -- pre-loaded source data (see load_sources()
    helper on the endpoint side).

    Returns ValidationReport. Use .to_dict() for JSON
    serialisation."""
    results: list[ValidationResult] = []
    for category in rendered_categories.values():
        for entry in (category.get("entries") or []):
            token = entry.get("token")
            label = entry.get("label")
            reference_value = entry.get("value")
            if not isinstance(token, str):
                continue
            try:
                strategy = dispatch_strategy(token)
                result = strategy(
                    token, label, reference_value, sources)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "validator_strategy_raised",
                    token=token, error=str(exc))
                result = _skipped(
                    token, label, reference_value, "—",
                    note=(
                        _VALIDATOR_ERROR_PREFIX
                        + type(exc).__name__ + ": " + str(exc)))
            results.append(result)
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == "pass"),
        "failed": sum(1 for r in results if r.status == "fail"),
        "warning": sum(1 for r in results if r.status == "warning"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
    }
    return ValidationReport(
        data_hash=data_hash,
        validated_at=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        results=results)
