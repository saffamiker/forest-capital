"""
strategy_metadata.py

The authoritative, source-controlled record of each strategy's rules —
its construction logic and, for the dynamic strategies, the signal,
economic intuition, and key parameter.

No structured strategy metadata existed before: the strategy definitions
lived only as a tuple list + code docstrings in tools/backtester.py. This
file is the single place that articulates the rules in plain English,
serving the Part II requirement to explain the logic and economic
intuition behind each dynamic rule.

Uniform schema per entry:
  id, name, type ("static" | "dynamic"), rebalancing, rationale
  weights            — fixed equity/IG/HY weights, or None when the
                       allocation is optimised or dynamic
  signal_logic       — dynamic only: how the allocation signal is formed
  economic_intuition — dynamic only: one sentence on why it should work
  key_parameter      — dynamic only: the tunable parameter name
  parameter_value    — dynamic only: its current setting

`type` mirrors the strategy_type the backtester stamps on each result.
Min Variance, Risk Parity and Black-Litterman are OPTIMISED — their
weights are solved, not fixed, so `weights` is None and the rationale
says so explicitly.
"""
from __future__ import annotations

STRATEGY_METADATA: list[dict] = [
    {
        "id": "BENCHMARK",
        "name": "100% Equity (Benchmark)",
        "type": "static",
        "rebalancing": "Buy and hold — no rebalancing",
        "weights": {"equity": 1.00, "ig": 0.00, "hy": 0.00},
        "signal_logic": None,
        "economic_intuition": None,
        "key_parameter": None,
        "parameter_value": None,
        "rationale": "The 100% S&P 500 baseline required by the brief — "
                     "every other strategy is judged against it.",
    },
    {
        "id": "CLASSIC_60_40",
        "name": "Classic 60/40",
        "type": "static",
        "rebalancing": "Quarterly, to fixed target weights",
        "weights": {"equity": 0.60, "ig": 0.40, "hy": 0.00},
        "signal_logic": None,
        "economic_intuition": None,
        "key_parameter": None,
        "parameter_value": None,
        "rationale": "The canonical balanced policy allocation — equities "
                     "for growth, investment-grade bonds for ballast.",
    },
    {
        "id": "RISK_PARITY",
        "name": "Risk Parity",
        "type": "static",
        "rebalancing": "Quarterly, to optimised target weights",
        "weights": None,
        "signal_logic": None,
        "economic_intuition": None,
        "key_parameter": None,
        "parameter_value": None,
        "rationale": "Weights are OPTIMISED (not fixed) so each of equity, "
                     "IG and HY contributes an equal share of portfolio "
                     "risk — no single sleeve dominates drawdowns.",
    },
    {
        "id": "MIN_VARIANCE",
        "name": "Minimum Variance",
        "type": "static",
        "rebalancing": "Quarterly, rolling 36-month covariance window",
        "weights": None,
        "signal_logic": None,
        "economic_intuition": None,
        "key_parameter": None,
        "parameter_value": None,
        "rationale": "Weights are OPTIMISED to minimise portfolio variance "
                     "over a rolling 36-month window — covariance is "
                     "estimable with far less error than expected return.",
    },
    {
        "id": "EQUAL_WEIGHT",
        "name": "Equal Weight",
        "type": "static",
        "rebalancing": "Quarterly, to fixed target weights",
        "weights": {"equity": 1 / 3, "ig": 1 / 3, "hy": 1 / 3},
        "signal_logic": None,
        "economic_intuition": None,
        "key_parameter": None,
        "parameter_value": None,
        "rationale": "Naive 1/N diversification across the three asset "
                     "classes — a hard-to-beat baseline (DeMiguel et al. 2009).",
    },
    {
        "id": "MOMENTUM_ROTATION",
        "name": "Momentum Rotation",
        "type": "dynamic",
        "rebalancing": "Quarterly",
        "weights": None,
        "signal_logic": "Each quarter, score equity, IG and HY by a "
                        "composite momentum signal over 1-, 3-, 6- and "
                        "12-month lookbacks (weighted toward 12 months); "
                        "hold the top two at 50% each.",
        "economic_intuition": "Asset classes that have outperformed "
                              "recently tend to keep outperforming over "
                              "the following months (Jegadeesh & Titman 1993).",
        "key_parameter": "Lookback windows",
        "parameter_value": "1 / 3 / 6 / 12 months, weighted 0.10 / 0.20 / "
                           "0.30 / 0.40",
        "rationale": "Rotates into recent winners while excluding the "
                     "weakest of the three sleeves.",
    },
    {
        "id": "REGIME_SWITCHING",
        "name": "Regime Switching",
        "type": "dynamic",
        "rebalancing": "Quarterly",
        "weights": None,
        "signal_logic": "Classify the market each quarter as BULL, BEAR or "
                        "TRANSITION from the trailing 3-month equity trend, "
                        "then allocate per regime — BULL 80/20 equity/IG, "
                        "BEAR 20/60/20, TRANSITION 50/40/10.",
        "economic_intuition": "Equity drawdowns cluster; cutting equity and "
                              "adding bonds when momentum turns down limits "
                              "participation in bear markets.",
        "key_parameter": "Regime-assessment window",
        "parameter_value": "3 months",
        "rationale": "A small, transparent set of regime allocations driven "
                     "by one robust signal — the equity trend.",
    },
    {
        "id": "VOL_TARGETING",
        "name": "Volatility Targeting",
        "type": "dynamic",
        "rebalancing": "Monthly",
        "weights": None,
        "signal_logic": "Each month, scale the equity weight so the "
                        "portfolio targets 10% annualised volatility, using "
                        "the trailing 21-day realised volatility of equity; "
                        "the remainder goes to IG bonds.",
        "economic_intuition": "Volatility is persistent — targeting constant "
                              "risk de-risks into turbulent periods and "
                              "re-risks into calm ones (Moreira & Muir 2017).",
        "key_parameter": "Target volatility",
        "parameter_value": "10% annualised",
        "rationale": "Holds portfolio risk roughly constant rather than "
                     "letting it swing with the market.",
    },
    {
        "id": "BLACK_LITTERMAN",
        "name": "Black-Litterman",
        "type": "dynamic",
        "rebalancing": "Quarterly, rolling 36-month window",
        "weights": None,
        "signal_logic": "Each quarter, form the Black-Litterman posterior "
                        "from an equal-weight equilibrium prior over a "
                        "rolling 36-month window, then solve a mean-variance "
                        "optimisation on the posterior.",
        "economic_intuition": "Anchoring to an equilibrium prior before "
                              "tilting prevents the extreme corner "
                              "portfolios raw mean-variance produces on "
                              "noisy 36-month estimates.",
        "key_parameter": "Rolling window",
        "parameter_value": "36 months",
        "rationale": "Weights are OPTIMISED from the BL posterior — the "
                     "covariance regularisation is the main benefit at "
                     "this stage (no external views yet).",
    },
    {
        "id": "MAX_SHARPE_ROLLING",
        "name": "Max Sharpe Rolling",
        "type": "dynamic",
        "rebalancing": "Quarterly, rolling 36-month window",
        "weights": None,
        "signal_logic": "Each quarter, solve for the maximum-Sharpe "
                        "portfolio over the trailing 36 months under the "
                        "long-only weight bounds.",
        "economic_intuition": "Continuously re-estimating the best "
                              "risk-adjusted mix adapts the allocation as "
                              "the covariance and return structure shifts.",
        "key_parameter": "Rolling window",
        "parameter_value": "36 months",
        "rationale": "Weights are OPTIMISED for Sharpe each quarter; the "
                     "36-month window trades estimation error against "
                     "regime staleness.",
    },
]
