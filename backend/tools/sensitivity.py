"""
tools/sensitivity.py

Parameter sensitivity analysis for the four dynamic strategies — how the
Sharpe ratio responds as each strategy's key parameter is swept around
its current setting.

This re-runs each dynamic strategy 5-7 times (one backtest per parameter
value) — a ~23-backtest computation. The result is memoised in-process,
keyed by the history length: the first call after a restart pays the
cost once, every later call is a dict lookup. It is deliberately NOT run
on the light /api/v1/analytics/academic path — sensitivity has its own
endpoint with its own loading state.

The four dynamic strategies and their swept parameter:
  - Momentum Rotation   — a scale factor applied uniformly to all four
                          lookbacks (0.5x .. 1.5x)
  - Regime Switching    — the regime-assessment window in months
  - Volatility Targeting — the annualised volatility target
  - Max Sharpe Rolling  — the rolling optimisation window in months
"""
from __future__ import annotations

import logging

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    log = logging.getLogger(__name__)  # type: ignore[assignment]

# Memo keyed by history length — the data is fixed within a deployment,
# so one entry is all that is ever stored.
_sensitivity_cache: dict[int, dict] = {}


def _sensitivity_clear() -> None:
    """Drops the in-process sensitivity memo (used by tests)."""
    _sensitivity_cache.clear()


def _sweeps() -> list[dict]:
    """Per-strategy sweep config. Imported lazily so importing this module
    never drags in the backtester unless sensitivity is actually run."""
    from config import TARGET_VOLATILITY, OPTIMIZATION_WINDOW
    from tools.backtester import (
        run_momentum_rotation, run_regime_switching,
        run_vol_targeting, run_max_sharpe_rolling, _REGIME_WINDOW_M,
    )
    return [
        {
            "strategy": "Momentum Rotation",
            "parameter": "Lookback scale (x)",
            "current_value": 1.0,
            "values": [0.5, 0.75, 1.0, 1.25, 1.5],
            "runner": lambda h, v: run_momentum_rotation(h, lookback_scale=v),
        },
        {
            "strategy": "Regime Switching",
            "parameter": "Regime window (months)",
            "current_value": _REGIME_WINDOW_M,
            "values": [1, 2, 3, 4, 5, 6],
            "runner": lambda h, v: run_regime_switching(h, regime_window_m=v),
        },
        {
            "strategy": "Volatility Targeting",
            "parameter": "Target volatility",
            "current_value": TARGET_VOLATILITY,
            "values": [0.05, 0.075, 0.10, 0.125, 0.15],
            "runner": lambda h, v: run_vol_targeting(h, target_volatility=v),
        },
        {
            "strategy": "Max Sharpe Rolling",
            "parameter": "Rolling window (months)",
            "current_value": OPTIMIZATION_WINDOW,
            "values": [18, 24, 30, 36, 42, 48, 54],
            "runner": lambda h, v: run_max_sharpe_rolling(h, optimization_window=v),
        },
    ]


def compute_sensitivity(history: dict) -> dict:
    """
    Runs the parameter sweeps and returns, per dynamic strategy, the
    Sharpe ratio at each parameter value plus the current setting.

    Memoised in-process by history length. A parameter value whose
    backtest errors (e.g. a rolling window longer than the data) records
    sharpe=None rather than failing the whole sweep.
    """
    n_months = len(history.get("equity_monthly", []))
    if n_months in _sensitivity_cache:
        log.info("sensitivity_cache_hit", n_months=n_months)
        return _sensitivity_cache[n_months]

    strategies: list[dict] = []
    for sw in _sweeps():
        points: list[dict] = []
        for v in sw["values"]:
            sharpe = None
            try:
                res = sw["runner"](history, v)
                if isinstance(res, dict) and "error" not in res:
                    sharpe = res.get("sharpe_ratio")
            except Exception as exc:  # noqa: BLE001
                log.warning("sensitivity_run_failed", strategy=sw["strategy"],
                            value=v, error=str(exc))
            points.append({"value": v, "sharpe": sharpe})
        strategies.append({
            "strategy": sw["strategy"],
            "parameter": sw["parameter"],
            "current_value": sw["current_value"],
            "points": points,
        })
        log.info("sensitivity_strategy_complete",
                 strategy=sw["strategy"], n_points=len(points))

    out = {"strategies": strategies}
    _sensitivity_cache[n_months] = out
    log.info("sensitivity_computed", n_months=n_months, n_strategies=len(strategies))
    return out
