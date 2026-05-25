"""tools/precomputed_analytics.py — write/read for analytics_metrics_cache.

May 22 2026 — item 7 in the sprint queue. Backs the migration 028
analytics_metrics_cache table. The endpoint code reads a row by
(data_hash, metric_kind) and returns the payload verbatim; the
refresh hook computes the payload once when the strategy cache is
written and upserts the row.

PATTERN — every metric_kind has its own compute function in this
module, called by refresh_all_analytics on data-hash change. Adding
a new metric is two changes: a compute function here + an additional
entry in the refresh_all_analytics dispatch.

FAIL-OPEN EVERYWHERE. A database read miss returns None and the
endpoint computes inline. A compute failure during the refresh logs
and proceeds to the next metric (one bad metric does not block the
others). Mirrors the academic_context / macro_context fail-open
contract.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def get_metric(data_hash: str, metric_kind: str) -> dict[str, Any] | None:
    """Reads a pre-computed metric payload from the cache.

    Returns the payload dict on hit. Returns None on:
      - row not yet written for this (data_hash, metric_kind)
      - data_hash does not match any row (the latest data has not
        been refreshed yet — the cold-deploy case)
      - database unavailable

    The endpoint's fallback path runs the inline compute when this
    returns None, so the user always sees data — just slower on the
    first request after a fresh ingestion.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT payload, computed_at FROM analytics_metrics_cache "
                "WHERE data_hash = :h AND metric_kind = :k LIMIT 1"
            ), {"h": data_hash, "k": metric_kind})
            found = row.fetchone()
            if not found:
                return None
            payload = found[0]
            computed_at = found[1]
            if isinstance(payload, str):
                # asyncpg may return JSONB as a string in some setups.
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            if not isinstance(payload, dict):
                return None
            # Attach the computed_at so the endpoint can surface
            # freshness on the response.
            payload["_computed_at"] = (
                computed_at.isoformat() if computed_at else None)
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_read_failed",
                    metric_kind=metric_kind, error=str(exc))
        return None


async def set_metric(
    data_hash: str,
    metric_kind: str,
    payload: dict[str, Any],
    *,
    source: str | None = None,
) -> None:
    """Upserts a pre-computed metric payload into the cache.

    Idempotent — ON CONFLICT (data_hash, metric_kind) DO UPDATE so
    a re-fire of the refresh hook within the same data_hash window
    refreshes the row rather than failing.

    Strips internal underscore-prefixed keys before writing so a
    payload that was already read once (with _computed_at attached)
    can round-trip cleanly.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return
        clean = {k: v for k, v in payload.items() if not k.startswith("_")}
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "INSERT INTO analytics_metrics_cache "
                "(data_hash, metric_kind, payload, source) "
                "VALUES (:h, :k, :p, :s) "
                "ON CONFLICT (data_hash, metric_kind) DO UPDATE SET "
                " payload = EXCLUDED.payload, "
                " computed_at = now(), "
                " source = EXCLUDED.source"
            ), {
                "h": data_hash, "k": metric_kind,
                "p": json.dumps(clean, default=str),
                "s": source,
            })
            await session.commit()
        log.info("precomputed_analytics_written",
                 metric_kind=metric_kind, data_hash=data_hash[:8],
                 source=source)
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_write_failed",
                    metric_kind=metric_kind, error=str(exc))


async def get_latest_metric(metric_kind: str) -> dict[str, Any] | None:
    """Returns the most-recently-written row for a metric_kind,
    regardless of data_hash. Used by the cold-deploy fallback when
    the current data_hash has not been refreshed yet — serving the
    last refresh is better than serving nothing.

    The endpoint surfaces _stale=True on the response when this
    fallback path is taken so the user knows the analytics are
    from a previous data ingestion.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as session:
            row = await session.execute(text(
                "SELECT payload, computed_at, data_hash "
                "FROM analytics_metrics_cache "
                "WHERE metric_kind = :k "
                "ORDER BY computed_at DESC LIMIT 1"
            ), {"k": metric_kind})
            found = row.fetchone()
            if not found:
                return None
            payload = found[0]
            computed_at = found[1]
            row_hash = found[2]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            if not isinstance(payload, dict):
                return None
            payload["_computed_at"] = (
                computed_at.isoformat() if computed_at else None)
            payload["_data_hash"] = row_hash
            payload["_stale"] = True
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_latest_failed",
                    metric_kind=metric_kind, error=str(exc))
        return None


# ── Refresh dispatch ──────────────────────────────────────────────────────────


# ── Completeness validation ───────────────────────────────────────────────────
#
# May 24 2026 — the QA audit was reporting AN01 (Carhart loadings) and
# AN04 (regime split / transition matrix) as WARN because the
# deterministic check ran against per-strategy result dicts that don't
# carry these analytic tables — the data lives here, in the
# analytics_metrics_cache. The fix is two-sided: (a) validate
# completeness HERE so a missing row is loud, not silent, and (b) read
# the validated payload from the QA audit's pre-flight (see
# ensure_qa_data_complete below). The cache row is now self-describing —
# every payload carries a `_completeness` block listing which structural
# fields are present so a downstream consumer doesn't have to guess.

_FACTOR_LOADING_REQUIRED_FIELDS: tuple[str, ...] = (
    "strategy", "mkt_rf", "smb", "hml", "mom",
    "alpha_annualized", "r_squared",
    "mkt_rf_significant", "smb_significant", "hml_significant",
    "alpha_significant",
)
_REGIME_CONDITIONAL_REQUIRED_FIELDS: tuple[str, ...] = (
    "strategy", "pre_2022_sharpe", "post_2022_sharpe",
    "pre_2022_cagr", "post_2022_cagr",
    "pre_2022_months", "post_2022_months",
)


def _validate_factor_loadings(rows: list[dict]) -> dict[str, Any]:
    """Verifies every factor_loadings row carries the four Carhart betas
    (MKT-RF / SMB / HML / MOM), annualised alpha, R-squared in [0, 1],
    and per-coefficient significance flags. MOM is nullable for strategies
    whose history predates the momentum-factor backfill — that row falls
    back to a three-factor fit (`model: ff_3factor`), and the MOM fields
    are recorded as `null` rather than missing.

    Returns a structured verdict: `complete` is True only when every row
    populates the required fields with the right types AND R-squared is
    in the valid [0, 1] range. The verdict carries the per-row gaps so
    a sysadmin can pinpoint which strategy's regression went bad.
    """
    if not rows:
        return {
            "complete": False, "n_rows": 0,
            "missing_fields": ["entire factor_loadings table"],
            "invalid_rows": [],
        }
    invalid: list[dict] = []
    for r in rows:
        missing = [f for f in _FACTOR_LOADING_REQUIRED_FIELDS if f not in r]
        # MOM may be None (three-factor fallback), but the field itself
        # must exist alongside its significance flag.
        if "mom" not in r or "mom_significant" not in r:
            missing.append("mom_or_mom_significant")
        r2 = r.get("r_squared")
        if not isinstance(r2, (int, float)) or not 0.0 <= r2 <= 1.0:
            missing.append(f"r_squared_out_of_range:{r2}")
        if missing:
            invalid.append({"strategy": r.get("strategy"), "missing": missing})
    return {
        "complete": not invalid, "n_rows": len(rows),
        "invalid_rows": invalid,
        "missing_fields": [] if not invalid else
            [f for row in invalid for f in row["missing"]],
    }


def _validate_regime_conditional(rows: list[dict]) -> dict[str, Any]:
    """Verifies every regime_conditional row carries pre/post-2022 Sharpe
    and CAGR. A None Sharpe is permissible only when the corresponding
    months count is < 2 (insufficient data) — the row IS complete in
    that case because the analytic correctly reported the gap.
    """
    if not rows:
        return {
            "complete": False, "n_rows": 0,
            "missing_fields": ["entire regime_conditional table"],
            "invalid_rows": [],
        }
    invalid: list[dict] = []
    for r in rows:
        missing = [f for f in _REGIME_CONDITIONAL_REQUIRED_FIELDS if f not in r]
        # A None Sharpe is legitimate ONLY when months < 2.
        for period in ("pre_2022", "post_2022"):
            sharpe = r.get(f"{period}_sharpe")
            months = r.get(f"{period}_months", 0)
            if sharpe is None and isinstance(months, int) and months >= 2:
                missing.append(f"{period}_sharpe_unexpectedly_null")
        if missing:
            invalid.append({"strategy": r.get("strategy"), "missing": missing})
    return {
        "complete": not invalid, "n_rows": len(rows),
        "invalid_rows": invalid,
        "missing_fields": [] if not invalid else
            [f for row in invalid for f in row["missing"]],
    }


def _validate_transition_matrix(matrix: dict) -> dict[str, Any]:
    """Verifies a regime transition matrix carries three originating
    regimes (BULL/BEAR/TRANSITION) and that each non-empty row sums to
    1.0 (within 1e-3 — rounding at four decimals carries a small drift).

    A row with every probability == 0.0 is the "this regime never
    occurred in the historical window" case; it counts as complete-by-
    construction. A non-empty row that does NOT sum to 1.0 is a data
    error and is flagged.
    """
    states = ("BULL", "BEAR", "TRANSITION")
    if not isinstance(matrix, dict):
        return {
            "complete": False, "missing_regimes": list(states),
            "row_sums": {}, "invalid_rows": [],
        }
    missing = [s for s in states if s not in matrix]
    invalid: list[dict] = []
    row_sums: dict[str, float] = {}
    for s in states:
        row = matrix.get(s) or {}
        if not isinstance(row, dict):
            invalid.append({"regime": s, "reason": "row_is_not_a_dict"})
            row_sums[s] = float("nan")
            continue
        # The row must enumerate every destination state, even with 0.
        if any(t not in row for t in states):
            invalid.append({"regime": s, "reason": "missing_destination_state"})
        s_sum = sum(float(row.get(t, 0.0)) for t in states)
        row_sums[s] = round(s_sum, 6)
        # 0 is legitimate (regime never observed); 1 ± epsilon is
        # legitimate (probabilities are well-formed). Anything else
        # flags an arithmetic error.
        if not (abs(s_sum) < 1e-6 or abs(s_sum - 1.0) < 1e-3):
            invalid.append({"regime": s, "reason": f"row_sum_{s_sum:.6f}"})
    return {
        "complete": not missing and not invalid,
        "missing_regimes": missing, "row_sums": row_sums,
        "invalid_rows": invalid,
    }


def validate_analytics_payload(payload: dict) -> dict[str, Any]:
    """Top-level validator. Inspects an academic_analytics payload and
    returns a structured `_completeness` block:

      { complete: bool,
        factor_loadings: {complete, n_rows, missing_fields, invalid_rows},
        regime_conditional: {complete, n_rows, missing_fields, invalid_rows},
        validated_at: iso timestamp }

    `complete` is the AND of the two sub-validators. This block is
    attached to every cached payload so a downstream consumer (the QA
    audit, the operator dashboard, anything else) can detect a partial
    refresh without parsing the full payload.
    """
    from datetime import datetime, timezone
    fl = _validate_factor_loadings(payload.get("factor_loadings") or [])
    rc = _validate_regime_conditional(payload.get("regime_conditional") or [])
    return {
        "complete": bool(fl["complete"] and rc["complete"]),
        "factor_loadings": fl,
        "regime_conditional": rc,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


def _log_table_rows_diagnostic(
    table_kind: str, rows: list[dict], required_fields: tuple[str, ...],
    data_hash: str | None,
) -> dict[str, Any]:
    """Per-row diagnostic on what was written for factor_loadings /
    regime_conditional. Emits one log line PER ROW so a Render log scan
    after the warm can answer 'did this strategy land' field-by-field
    without parsing the full payload. Returns an aggregate summary
    dict for the parent log line.

    For each row:
      - present: fields that are present AND non-null
      - null: fields present but value is None
      - missing: fields the validator requires but the row does not have

    INFO level on a clean row, WARNING on a row with any missing or null
    field, so a `level >= warning` alert filter catches incomplete rows
    without flooding on healthy refreshes.
    """
    # Rebind locally so structlog.testing.capture_logs() in tests can
    # intercept (May 25 2026 CI fix). The module-level `log` is a
    # cached BoundLoggerLazyProxy that binds to whatever processors
    # were configured at first-use time — under structlog's default
    # cache_logger_on_first_use=True (set in logger.configure_logging),
    # a logger bound before capture_logs() takes effect cannot be
    # re-captured. A fresh proxy inside the function binds to the
    # CURRENT config (LogCapture under capture_logs), so events flow.
    log = structlog.get_logger(__name__)
    summary: dict[str, int] = {
        "n_rows": len(rows), "n_complete": 0, "n_with_nulls": 0,
        "n_with_missing": 0,
    }
    short_hash = data_hash[:8] if data_hash else None
    for row in rows:
        if not isinstance(row, dict):
            log.warning(f"precomputed_{table_kind}_row_invalid_type",
                        data_hash=short_hash, row_type=type(row).__name__)
            continue
        strategy = row.get("strategy", "<unnamed>")
        present: list[str] = []
        nulls: list[str] = []
        missing: list[str] = []
        for f in required_fields:
            if f not in row:
                missing.append(f)
            elif row[f] is None:
                nulls.append(f)
            else:
                present.append(f)
        if missing:
            summary["n_with_missing"] += 1
        elif nulls:
            summary["n_with_nulls"] += 1
        else:
            summary["n_complete"] += 1
        level = "warning" if (missing or nulls) else "info"
        getattr(log, level)(
            f"precomputed_{table_kind}_row_written",
            data_hash=short_hash, strategy=strategy,
            n_present=len(present), n_null=len(nulls),
            n_missing=len(missing),
            # Field names — explicit so the log scan can grep for
            # 'mkt_rf_significant missing' without payload inspection.
            present=present if level == "warning" else None,
            null_fields=nulls or None,
            missing_fields=missing or None,
        )
    return summary


async def refresh_academic_analytics(data_hash: str) -> None:
    """Computes the /api/v1/analytics/academic payload and writes it
    to analytics_metrics_cache under metric_kind='academic_analytics'.

    Mirrors the inline compute path in main.get_academic_analytics
    exactly — same series prep, same 7 reductions — so the cached
    payload is bit-identical to what the endpoint would have
    produced inline. The endpoint then reads the cached row and
    returns it verbatim.

    May 24 2026 — completeness validation. After the seven reductions
    run, validate_analytics_payload inspects factor_loadings and
    regime_conditional and attaches a `_completeness` block. A
    structurally-incomplete payload logs precomputed_academic_analytics
    _incomplete at WARNING level so a Render alert can fire; the row
    is still written so the next QA audit has SOMETHING to read, with
    the gap clearly documented in `_completeness`.

    May 25 2026 — per-row write diagnostics. The factor_loadings and
    regime_conditional tables drive AN01 / AN04; when those checks
    stay WARN after a warm cycle, a Render log scan needs to answer
    'which strategies landed, which fields were null, which were
    missing' without inspecting the JSONB payload by hand. Each refresh
    now emits one log line per table-row (level upgrades to WARNING
    on any null/missing field) plus a single aggregate summary line
    per refresh, so the trail of a degraded AN01/AN04 verdict reads
    end-to-end in the Render log.
    """
    # See _log_table_rows_diagnostic for the rebind rationale —
    # capture_logs() in tests can't intercept a pre-cached proxy.
    log = structlog.get_logger(__name__)
    short_hash = data_hash[:8] if data_hash else None
    log.info("precomputed_academic_analytics_started",
             data_hash=short_hash)
    try:
        import pandas as pd
        from tools.cache import (
            get_monthly_returns, get_latest_strategy_cache, get_ff_factors,
        )
        from tools import analytics as an
        from strategy_metadata import STRATEGY_METADATA

        monthly = await get_monthly_returns()
        strategies = await get_latest_strategy_cache()
        ff = await get_ff_factors()

        # Upstream-input visibility — explicit before we abort or
        # compute. A zero strategies count is the canonical cause of
        # empty AN01/AN04 outputs; logging it here ties the refresh
        # to the strategy cache state on the SAME line.
        log.info(
            "precomputed_academic_analytics_inputs",
            data_hash=short_hash,
            n_monthly_dates=len(monthly.get("dates") or []) if monthly else 0,
            n_strategies=len(strategies or {}),
            n_ff_rows=len(ff or []),
        )
        if not monthly or not strategies:
            log.warning(
                "precomputed_academic_analytics_skipped_empty_inputs",
                data_hash=short_hash,
                monthly_present=bool(monthly),
                strategies_present=bool(strategies),
            )
            return

        idx = pd.to_datetime(monthly["dates"])
        equity = pd.Series(monthly["equity"], index=idx)
        ig = pd.Series(monthly["ig"], index=idx)
        hy = pd.Series(monthly["hy"], index=idx)
        rf = pd.Series(monthly["rf"], index=idx)

        benchmark = strategies.get("BENCHMARK", {})
        bench_series = an._pairs_to_series(
            benchmark.get("monthly_returns") or [])

        asset_series = {"EQUITY": equity, "IG": ig, "HY": hy}
        if not bench_series.empty:
            asset_series["BENCHMARK"] = bench_series

        # Compute each AN01/AN04 reduction in its own try/except so a
        # downstream failure (regression solver, regime split) is
        # caught with explicit context rather than swallowed by the
        # outer handler — the parent log can't say WHICH reduction
        # crashed without this finer grain.
        factor_loadings_rows: list[dict] = []
        try:
            factor_loadings_rows = an.factor_loadings(strategies, ff or [])
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "precomputed_factor_loadings_compute_failed",
                data_hash=short_hash, exc_type=type(exc).__name__,
                error=str(exc),
            )
        regime_conditional_rows: list[dict] = []
        try:
            regime_conditional_rows = an.regime_conditional_performance(
                strategies, rf)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "precomputed_regime_conditional_compute_failed",
                data_hash=short_hash, exc_type=type(exc).__name__,
                error=str(exc),
            )

        # Per-row diagnostics for the two AN01/AN04 tables — explicit
        # field-by-field visibility so a Render log scan can answer
        # 'which strategies landed' without inspecting the payload.
        fl_summary = _log_table_rows_diagnostic(
            "factor_loadings", factor_loadings_rows,
            _FACTOR_LOADING_REQUIRED_FIELDS, data_hash,
        )
        rc_summary = _log_table_rows_diagnostic(
            "regime_conditional", regime_conditional_rows,
            _REGIME_CONDITIONAL_REQUIRED_FIELDS, data_hash,
        )

        payload = {
            "available": True,
            "study_period": {
                "start": str(idx[0].date()),
                "end": str(idx[-1].date()),
                "n_months": len(idx),
            },
            "summary_statistics": an.summary_statistics(asset_series, rf),
            "cumulative_returns": an.cumulative_returns(strategies),
            "rolling_correlation": an.rolling_correlation(
                equity, ig, hy, window=12),
            "rolling_excess_return": an.rolling_excess_return(
                strategies, window=12),
            "regime_conditional": regime_conditional_rows,
            "drawdown_comparison": an.drawdown_comparison(strategies),
            "factor_loadings": factor_loadings_rows,
            "strategy_metadata": STRATEGY_METADATA,
        }
        # Validate BEFORE writing — the verdict travels with the row.
        completeness = validate_analytics_payload(payload)
        payload["_completeness"] = completeness
        if not completeness["complete"]:
            log.warning(
                "precomputed_academic_analytics_incomplete",
                data_hash=short_hash,
                factor_loadings_complete=completeness["factor_loadings"]["complete"],
                regime_conditional_complete=completeness["regime_conditional"]["complete"],
                factor_loadings_gaps=completeness["factor_loadings"]["invalid_rows"][:3],
                regime_conditional_gaps=completeness["regime_conditional"]["invalid_rows"][:3],
            )
        await set_metric(data_hash, "academic_analytics", payload,
                         source="refresh_academic_analytics")
        # Final write summary — the single line a log scan grep can use
        # to confirm a refresh COMPLETED and how clean the output was.
        log.info(
            "precomputed_academic_analytics_written",
            data_hash=short_hash,
            complete=completeness["complete"],
            factor_loadings=fl_summary,
            regime_conditional=rc_summary,
        )
    except Exception as exc:  # noqa: BLE001
        # Include exc_type + the module/function context so a Render
        # log scan can locate the failure source from the line alone
        # (the prior single-line warning logged only str(exc), which
        # was often ambiguous between solver and write-side errors).
        log.warning("precomputed_academic_analytics_failed",
                    data_hash=short_hash,
                    exc_type=type(exc).__name__,
                    error=str(exc))


async def refresh_transition_matrix(data_hash: str) -> None:
    """Computes the 3x3 regime transition matrix from the HMM-labelled
    historical regime series and writes it to analytics_metrics_cache
    under metric_kind='transition_matrix'. The matrix counts consecutive
    month-pairs in the regime history and normalises per originating
    regime — every non-empty row must sum to 1.0 by construction.

    Each row also carries `_completeness` with the per-row sums so a
    consumer (the AN04 deterministic check) can verify the invariant
    without recomputing.

    May 25 2026 — per-stage diagnostics. AN04 reads BOTH the
    regime_conditional table (factor analytics) AND this transition
    matrix; logging the HMM fit, the matrix shape, and the per-regime
    row sums on every refresh makes a degraded AN04 verdict
    debuggable from the Render log alone.
    """
    # See _log_table_rows_diagnostic for the rebind rationale —
    # capture_logs() in tests can't intercept a pre-cached proxy.
    log = structlog.get_logger(__name__)
    short_hash = data_hash[:8] if data_hash else None
    log.info("precomputed_transition_matrix_started",
             data_hash=short_hash)
    try:
        import pandas as pd
        from tools.cache import get_monthly_returns
        from tools.regime_detector import classify_hmm_regime
        from tools.chart_data import _compute_transition_matrix

        monthly = await get_monthly_returns()
        if not monthly:
            log.warning(
                "precomputed_transition_matrix_skipped_empty_monthly",
                data_hash=short_hash,
            )
            return
        idx = pd.to_datetime(monthly["dates"])
        equity = pd.Series(monthly["equity"], index=idx)
        # The HMM classifier emits a labelled_series (BULL/BEAR/TRANSITION
        # per month); _compute_transition_matrix counts the transitions.
        try:
            hmm = classify_hmm_regime(equity)
            labelled = hmm.get("labelled_series") if isinstance(hmm, dict) else None
        except Exception as exc:  # noqa: BLE001
            log.warning("transition_matrix_hmm_failed",
                        data_hash=short_hash,
                        exc_type=type(exc).__name__, error=str(exc))
            labelled = None
        if labelled is None or (hasattr(labelled, "empty") and labelled.empty):
            log.warning(
                "precomputed_transition_matrix_skipped_empty_hmm",
                data_hash=short_hash,
                labelled_is_none=labelled is None,
            )
            return
        if not isinstance(labelled, pd.Series):
            labelled = pd.Series(labelled)
        matrix = _compute_transition_matrix(labelled)
        completeness = _validate_transition_matrix(matrix)
        payload = {
            "available": True,
            "matrix": matrix,
            "regime_break_date": "2022-01-01",
            "n_months": int(len(labelled)),
            "_completeness": completeness,
        }
        if not completeness["complete"]:
            log.warning(
                "precomputed_transition_matrix_incomplete",
                data_hash=short_hash,
                row_sums=completeness["row_sums"],
                invalid_rows=completeness["invalid_rows"],
            )
        await set_metric(data_hash, "transition_matrix", payload,
                         source="refresh_transition_matrix")
        # Final write summary — explicit per-regime row sums so a
        # log scan can confirm the matrix landed AND that every
        # originating regime has a well-formed (sums-to-1.0 or 0.0)
        # row without inspecting the JSONB payload.
        log.info(
            "precomputed_transition_matrix_written",
            data_hash=short_hash,
            complete=completeness["complete"],
            n_months=int(len(labelled)),
            row_sums=completeness["row_sums"],
            missing_regimes=completeness.get("missing_regimes") or None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_transition_matrix_failed",
                    data_hash=short_hash,
                    exc_type=type(exc).__name__,
                    error=str(exc))


async def refresh_efficient_frontier(data_hash: str) -> None:
    """Pre-computes the long-only efficient frontier curve (100-point
    target-return sweep) once per data_hash and caches it under
    metric_kind='efficient_frontier'.

    Hotfix (May 23 2026): the /api/optimize/weights endpoint runs
    `efficient_frontier(returns, n_points=100)` on the request thread
    — 100 SLSQP solves with maxiter=1000 per solve. On Render's
    shared CPU this routinely exceeds the frontend's 30s timeout,
    blanking the Efficient Frontier chart on the dashboard.

    The curve depends ONLY on the equity / IG / HY monthly returns —
    same input → same output. Cache it here and the endpoint reads
    it in ~50ms instead of running the full sweep per request. The
    per-method `weights` are still computed inline (a single solve
    is fast), but the 100-point curve is shared across every method
    so the cache hit serves every request.

    Computed once per strategy data_hash; the same hash that gates
    every other analytics cache row keeps this row in step.

    Cached payload shape:
      {
        "frontier_points":  [...],  # 100-ish points
        "rf_annual":        float,  # mean monthly DTB3 × 12
        "tickers":          ["EQUITY", "IG", "HY"],
        "n_obs":            int,
      }
    """
    try:
        import pandas as pd
        from tools.cache import get_monthly_returns
        from tools.optimizer import efficient_frontier as _frontier

        monthly = await get_monthly_returns()
        if not monthly or len(monthly.get("dates", [])) < 24:
            log.info("efficient_frontier_refresh_skipped_short")
            return

        returns = pd.DataFrame(
            {
                "EQUITY": monthly["equity"],
                "IG":     monthly["ig"],
                "HY":     monthly["hy"],
            },
            index=pd.to_datetime(monthly["dates"]),
        ).dropna()

        rf_monthly = monthly.get("rf") or []
        rf_annual = (sum(rf_monthly) / len(rf_monthly) * 12) \
            if rf_monthly else 0.0

        # The slow part — runs ~10-30s on Render shared CPU. By
        # running it inside this background refresh task instead of
        # the request thread, the user never sees the cost.
        raw_frontier = _frontier(
            returns, n_points=100,
            periods_per_year=12, risk_free=rf_annual,
        )

        payload = {
            "frontier_points": raw_frontier,
            "rf_annual":       round(float(rf_annual), 6),
            "tickers":         list(returns.columns),
            "n_obs":           len(returns),
        }
        await set_metric(data_hash, "efficient_frontier", payload,
                         source="refresh_efficient_frontier")
        log.info("precomputed_efficient_frontier_complete",
                 data_hash=data_hash[:8] if data_hash else None,
                 n_points=len(raw_frontier),
                 n_obs=len(returns))
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_efficient_frontier_failed",
                    error=str(exc))


async def refresh_diversification_metrics(data_hash: str) -> None:
    """Item 8 — the seven diversification suite metrics. Pure NumPy /
    pandas / scipy; same data sources as refresh_academic_analytics.
    Each metric is its own metric_kind row in analytics_metrics_cache
    so the corresponding endpoint reads exactly one indexed row.

    Fail-open per metric — one bad compute does not block the others.
    """
    try:
        from tools.cache import get_latest_strategy_cache
        from tools import diversification_analytics as div

        strategies = await get_latest_strategy_cache()
        if not strategies:
            return

        # Each metric is independently try/except'd so one failure
        # doesn't sink the rest. The cache row is left absent on a
        # failure; the endpoint falls back to inline compute.
        computations = [
            ("correlation_matrices",
             lambda: div.correlation_matrices(strategies)),
            ("tail_risk",
             lambda: {"strategies": div.tail_risk(strategies)}),
            ("capture_ratios",
             lambda: {"strategies": div.capture_ratios(strategies)}),
            ("drawdown_duration",
             lambda: {"strategies": div.drawdown_duration(strategies)}),
            ("crisis_performance",
             lambda: div.crisis_performance(strategies)),
            ("marginal_contribution_to_risk",
             lambda: div.marginal_contribution_to_risk(
                 strategies, tangency_weights=None)),
            ("return_distribution",
             lambda: {"strategies": div.return_distribution(strategies)}),
        ]
        for metric_kind, fn in computations:
            try:
                payload = fn()
                await set_metric(
                    data_hash, metric_kind, payload,
                    source="refresh_diversification_metrics")
            except Exception as exc:  # noqa: BLE001
                log.warning("diversification_metric_failed",
                            metric_kind=metric_kind, error=str(exc))
        # After all metric rows land, refresh the agent-context cache
        # so the council / academic_review / explainer prompts see
        # the new diversification numbers on the next agent call.
        try:
            from tools.diversification_context import (
                refresh_diversification_context,
            )
            await refresh_diversification_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("diversification_context_refresh_failed",
                        error=str(exc))
        # Item 5 (May 23 2026 — analytics narrative context). The
        # narrative layer rides the same refresh tick as the
        # structured diversification block so the three context
        # layers stay in step.
        try:
            from tools.analytics_context import refresh_analytics_context
            await refresh_analytics_context()
        except Exception as exc:  # noqa: BLE001
            log.warning("analytics_context_refresh_failed",
                        error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("diversification_refresh_failed", error=str(exc))


async def refresh_sensitivity(data_hash: str) -> None:
    """Parameter sensitivity (F1, May 22 2026). Runs compute_sensitivity
    on the FULL pipeline history — the same ~23-backtest computation
    the endpoint used to do inline on the request thread. By caching
    the result here, the endpoint becomes a single-row DB read on the
    hot path (get_full_history + compute_sensitivity never run inside
    a request handler).

    The compute_sensitivity helper has its own worker-local memo, so
    a refresh that re-runs on the same history is also cheap. The
    expensive work is the cold-deploy first call after each data
    ingestion.
    """
    try:
        from tools.data_fetcher import get_full_history
        from tools.sensitivity import compute_sensitivity

        history = get_full_history()
        if not history:
            log.info("precomputed_sensitivity_no_history")
            return
        # `not series` raises ValueError on a populated pd.Series
        # ("The truth value of a Series is ambiguous"). The old gate
        # `not history.get("equity_monthly")` had this exact shape and
        # blew up on every cold startup, blanking the sensitivity
        # precompute until the next data ingestion's downstream refresh
        # masked it. len() is unambiguous on pd.Series AND on the list
        # shape some tests stub the history with, so it handles every
        # caller cleanly. (May 28 2026.)
        equity_monthly = history.get("equity_monthly")
        if equity_monthly is None or len(equity_monthly) == 0:
            log.info("precomputed_sensitivity_no_history")
            return
        result = compute_sensitivity(history)
        payload = {"available": True, **result}
        await set_metric(data_hash, "sensitivity", payload,
                         source="refresh_sensitivity")
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_sensitivity_failed", error=str(exc))


async def refresh_risk_free_rate_config(data_hash: str) -> None:
    """Risk-free rate config (F4, May 22 2026). The /api/v1/analytics/
    config endpoint computes the mean monthly DTB3 rate × 12 inline
    on every request. The value depends solely on the data hash, so
    cache it alongside the other metrics.

    Single scalar plus its source label; the cached row is tiny but
    eliminates the per-request 280-row sum + multiply on the
    request thread.
    """
    try:
        from tools.cache import get_monthly_returns

        monthly = await get_monthly_returns()
        rf_list = (monthly or {}).get("rf") or []
        rf_annual = (sum(rf_list) / len(rf_list) * 12) if rf_list else None
        payload = {
            "available": rf_annual is not None,
            "risk_free_rate":
                round(rf_annual, 4) if rf_annual is not None else None,
            "risk_free_source": (
                "FRED DTB3 (3-month T-bill, mean monthly rate, annualised)"),
        }
        await set_metric(data_hash, "risk_free_rate_config", payload,
                         source="refresh_risk_free_rate_config")
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_risk_free_rate_config_failed",
                    error=str(exc))


async def refresh_all_analytics(data_hash: str) -> None:
    """Top-level dispatch — calls every refresh function. Fires from
    tools/cache.set_strategy_cache after a successful strategy write.
    Fail-open per-metric so one bad compute does not block the others.

    May 24 2026 P0 hotfix — accepts empty string / None data_hash and
    substitutes a "BOOT-WARM" sentinel so the cold-deploy path (no
    strategy_results_cache rows yet) still produces a row in
    analytics_metrics_cache. get_latest_metric is hash-agnostic so
    the stale-cache fallback path served by both endpoints will then
    find it.
    """
    if not data_hash:
        data_hash = "BOOT-WARM"
        log.info("precomputed_analytics_refresh_sentinel_hash",
                 note="no strategy hash available; using BOOT-WARM")
    log.info("precomputed_analytics_refresh_started",
             data_hash=data_hash[:8] if data_hash else None)
    await refresh_academic_analytics(data_hash)
    # AN04 (May 24 2026) — the regime transition matrix is its own
    # metric_kind so the QA audit can read it as a single indexed
    # row. The matrix lives logically with chart_data, but caching
    # it here lets the audit pre-flight check completeness without
    # touching the full chart_data payload.
    await refresh_transition_matrix(data_hash)
    # Hotfix (May 23 2026): the 100-point efficient frontier sweep
    # now runs once here at ingestion time instead of on every
    # /api/optimize/weights request. Read precomputed inside the
    # endpoint; the slow SLSQP path never hits a request thread.
    await refresh_efficient_frontier(data_hash)
    await refresh_diversification_metrics(data_hash)
    # F1 + F4 (May 22 2026) — sensitivity and the risk-free rate
    # config are the last two endpoints that did inline compute on
    # the request thread. Both now refresh through this dispatch and
    # serve from analytics_metrics_cache on the hot path.
    await refresh_sensitivity(data_hash)
    await refresh_risk_free_rate_config(data_hash)
    # Item 9 (May 22 2026) — strategy_characterisations is its own
    # table (one row per strategy) rather than another row in
    # analytics_metrics_cache, but it refreshes on the same data-
    # hash signal. Fail-open per strategy inside the refresh.
    try:
        from tools.strategy_characterisations import (
            refresh_strategy_characterisations,
        )
        await refresh_strategy_characterisations(data_hash)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_characterisation_dispatch_failed",
                    error=str(exc))
    log.info("precomputed_analytics_refresh_complete",
             data_hash=data_hash[:8] if data_hash else None)


def trigger_refresh_async(data_hash: str) -> None:
    """Fire-and-forget spawn of refresh_all_analytics. Mirrors the
    audit_engine / research_engine async-trigger pattern: spawn on
    the running event loop when available, else spawn a daemon
    thread. The strategy_cache write hook uses this so the refresh
    runs in the background and doesn't block the write returning."""
    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(refresh_all_analytics(data_hash))
            _refresh_tasks.add(task)
            task.add_done_callback(_refresh_tasks.discard)
        else:
            import threading
            threading.Thread(
                target=lambda: asyncio.run(refresh_all_analytics(data_hash)),
                daemon=True, name="analytics-refresh",
            ).start()
    except Exception as exc:  # noqa: BLE001
        log.warning("precomputed_analytics_spawn_failed",
                    error=str(exc))


# Module-level strong references so spawned tasks are not GC'd before
# they complete. Mirrors the research_engine pattern.
_refresh_tasks: set = set()


# ── QA audit pre-flight ───────────────────────────────────────────────────────
#
# The QA audit's AN01 (Carhart) and AN04 (regime / transition matrix)
# checks need data that lives in analytics_metrics_cache rather than on
# the per-strategy result dicts. Reading the cache asynchronously from
# the sync deterministic-check path is awkward, so the audit endpoint
# does the read ONCE before calling qa.run_audit and passes the data
# in. ensure_qa_data_complete is that single pre-flight: it fetches both
# rows, validates completeness, and triggers a refresh if either row is
# missing or stale (the cold-deploy case).
#
# The behaviour is fail-open with full diagnostic logging — the audit
# never blocks on a degraded cache. AN01 / AN04 will report INCOMPLETE
# in the unlikely event the refresh also fails; that is the honest
# signal (no examination took place) per the four-tier verdict system.

async def ensure_qa_data_complete(
    data_hash: str | None,
) -> dict[str, Any]:
    """Returns the AN01 / AN04 cache rows the QA audit needs, triggering
    a refresh on miss or incomplete. Output shape:

      { academic_analytics:   payload dict | None,
        transition_matrix:    payload dict | None,
        refresh_triggered:    list[str],   # which refreshes fired
        completeness: {factor_loadings: bool,
                       regime_conditional: bool,
                       transition_matrix: bool} }

    On cold deploy the academic_analytics row may not exist yet for the
    current data_hash — the function falls through to get_latest_metric
    so the audit sees the most recent refresh, even if it predates the
    current data ingestion. A stale row is still better than no row;
    the QA audit's deterministic check surfaces the stale flag if it
    matters for the analytical conclusion.
    """
    refresh_triggered: list[str] = []
    academic: dict[str, Any] | None = None
    transition: dict[str, Any] | None = None

    def _needs_refresh(payload: dict | None, key: str) -> bool:
        if not payload:
            return True
        completeness = payload.get("_completeness")
        if not isinstance(completeness, dict):
            # Old payload written before completeness validation shipped.
            # Treat as needing a refresh so the new validator runs.
            return True
        sub = completeness.get(key)
        if isinstance(sub, dict):
            return not sub.get("complete", False)
        return not completeness.get("complete", False)

    try:
        if data_hash:
            academic = await get_metric(data_hash, "academic_analytics")
            transition = await get_metric(data_hash, "transition_matrix")
        # Fall back to the most recent row when current-hash miss.
        if not academic:
            academic = await get_latest_metric("academic_analytics")
        if not transition:
            transition = await get_latest_metric("transition_matrix")

        # Refresh on miss or incomplete. We trigger on the current
        # data_hash so a successful refresh lands on the same row the
        # endpoint will see next.
        if data_hash:
            need_academic = (
                _needs_refresh(academic, "factor_loadings") or
                _needs_refresh(academic, "regime_conditional")
            )
            if need_academic:
                log.warning(
                    "qa_preflight_refreshing_academic",
                    data_hash=data_hash[:8],
                    had_payload=bool(academic),
                )
                await refresh_academic_analytics(data_hash)
                refresh_triggered.append("academic_analytics")
                academic = await get_metric(
                    data_hash, "academic_analytics") or academic
            if not transition or not (transition.get("_completeness") or {}).get(
                    "complete", False):
                log.warning(
                    "qa_preflight_refreshing_transition_matrix",
                    data_hash=data_hash[:8],
                    had_payload=bool(transition),
                )
                await refresh_transition_matrix(data_hash)
                refresh_triggered.append("transition_matrix")
                transition = await get_metric(
                    data_hash, "transition_matrix") or transition
    except Exception as exc:  # noqa: BLE001
        log.warning("qa_preflight_failed", error=str(exc))

    # Surface a flattened completeness summary for the caller.
    def _is_complete(payload: dict | None, key: str) -> bool:
        if not payload:
            return False
        completeness = payload.get("_completeness") or {}
        sub = completeness.get(key)
        if isinstance(sub, dict):
            return bool(sub.get("complete", False))
        return bool(completeness.get("complete", False))

    return {
        "academic_analytics":   academic,
        "transition_matrix":    transition,
        "refresh_triggered":    refresh_triggered,
        "completeness": {
            "factor_loadings":    _is_complete(academic, "factor_loadings"),
            "regime_conditional": _is_complete(academic, "regime_conditional"),
            "transition_matrix":  _is_complete(
                transition, "complete") if transition else False,
        },
    }


# Silence the structlog import-only false positive
_ = logging
