"""
Pydantic schemas and Sprint-1 mock data.
"""
from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, EmailStr


# ── Auth ──────────────────────────────────────────────────────────────────────

class MagicLinkRequest(BaseModel):
    email: str


class MagicLinkResponse(BaseModel):
    message: str
    # "sent"    → email is on the approved list; link was sent (or printed in dev mode)
    # "pending" → email is NOT on the approved list; no link sent
    # Both return HTTP 200 to prevent email enumeration — the frontend uses this
    # field to decide which confirmation UI to show.
    status: str = "pending"
    dev_mode: bool = False


class SessionResponse(BaseModel):
    session_token: str
    email: str
    expires_in_hours: int


class LogoutRequest(BaseModel):
    session_token: str


# ── Council ───────────────────────────────────────────────────────────────────

class CouncilQueryRequest(BaseModel):
    query: str
    include_agents: Optional[list[str]] = None


class AgentMessage(BaseModel):
    agent: str
    role: str
    content: str
    model: str
    is_final: bool = False


class CouncilDebateResponse(BaseModel):
    query: str
    messages: list[AgentMessage]
    final_recommendation: str
    consensus_reached: bool


# ── Strategy / Backtest ───────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str
    params: Optional[dict[str, Any]] = None
    start: Optional[str] = None
    end: Optional[str] = None


class QAQueryRequest(BaseModel):
    question: str


class OptimizeRequest(BaseModel):
    method: str
    assets: Optional[list[str]] = None
    constraints: Optional[dict[str, Any]] = None


class UIUXReviewRequest(BaseModel):
    component_name: str
    jsx_code: str
    screenshot: Optional[str] = None


# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_REGIME = {
    "threshold_regime": "BULL",
    "hmm_regime": 0,
    "hmm_probabilities": [0.82, 0.12, 0.06],
    "regimes_agree": True,
    "vix_level": 14.3,
    "yield_curve_slope": 0.42,
    "credit_spread": 3.21,
    "equity_trend": 0.08,
    "as_of": "2024-12-31",
}

MOCK_STRATEGIES = [
    {
        "strategy_name": "BENCHMARK",
        "strategy_type": "static",
        "cagr": 0.1023,
        "total_return": 2.4521,
        "volatility": 0.1891,
        "max_drawdown": -0.5084,
        "drawdown_duration_days": 517,
        "drawdown_recovery_days": 1089,
        "sharpe_ratio": 0.61,
        "sharpe_ci_95": [0.45, 0.77],
        "sortino_ratio": 0.89,
        "calmar_ratio": 0.20,
        "information_ratio": 0.0,
        "omega_ratio": 1.18,
        "var_95": -0.0189,
        "cvar_95": -0.0287,
        "skewness": -0.71,
        "kurtosis": 2.34,
        "alpha": 0.0,
        "alpha_bps": 0,
        "alpha_after_costs_bps": 0,
        "beta": 1.0,
        "r_squared": 1.0,
        "avg_monthly_turnover": 0.0,
        "avg_equity_weight": 1.0,
        "avg_bond_weight": 0.0,
        "is_economically_significant": False,
        "min_viable_aum": 0,
        "p_value_ttest": 1.0,
        "p_value_sharpe_jk": 1.0,
        "p_value_alpha": 1.0,
        "p_value_corrected": 1.0,
        "p_value_bootstrap": 1.0,
        "normality_rejected": True,
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.52,
        "dsr_p_value": 1.0,
        "probabilistic_sharpe_ratio": 0.0,
        "spa_p_value": 1.0,
        "passes_spa": True,
        "oos_sharpe": 0.58,
        "oos_cagr": 0.094,
        "oos_p_value": 1.0,
        "oos_significant": False,
        "tier1_gates_passed": 0,
        "is_significant": False,
        "significance_summary": "Reference portfolio — no significance test applied",
        "cv_stability_score": 0.0,
        "stress_results": {
            "GFC_2008":       {"return": -0.468, "max_dd": -0.508, "vs_benchmark": 0.0},
            "COVID_2020":     {"return": -0.197, "max_dd": -0.338, "vs_benchmark": 0.0},
            "RATE_HIKE_2022": {"return": -0.181, "max_dd": -0.241, "vs_benchmark": 0.0},
            "DOTCOM_2000":    {"return": -0.441, "max_dd": -0.491, "vs_benchmark": 0.0},
            "TAPER_TANTRUM":  {"return": -0.058, "max_dd": -0.077, "vs_benchmark": 0.0},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "CLASSIC_60_40",
        "strategy_type": "static",
        "cagr": 0.0876,
        "total_return": 1.9234,
        "volatility": 0.1102,
        "max_drawdown": -0.3027,
        "drawdown_duration_days": 284,
        "drawdown_recovery_days": 452,
        "sharpe_ratio": 0.79,
        "sharpe_ci_95": [0.65, 0.93],
        "sortino_ratio": 1.14,
        "calmar_ratio": 0.29,
        "information_ratio": 0.18,
        "omega_ratio": 1.31,
        "var_95": -0.0118,
        "cvar_95": -0.0189,
        "skewness": -0.43,
        "kurtosis": 1.87,
        "alpha": 0.0012,
        "alpha_bps": 12,
        "alpha_after_costs_bps": 2,
        "beta": 0.59,
        "r_squared": 0.91,
        "avg_monthly_turnover": 0.034,
        "avg_equity_weight": 0.60,
        "avg_bond_weight": 0.40,
        "is_economically_significant": False,
        "min_viable_aum": 6_000_000,
        "p_value_ttest": 0.042,
        "p_value_sharpe_jk": 0.038,
        "p_value_alpha": 0.071,
        "p_value_corrected": 0.124,
        "p_value_bootstrap": 0.058,
        "normality_rejected": True,
        "bootstrap_used": True,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.67,
        "dsr_p_value": 0.052,
        "probabilistic_sharpe_ratio": 0.89,
        "spa_p_value": 0.089,
        "passes_spa": True,
        "oos_sharpe": 0.71,
        "oos_cagr": 0.081,
        "oos_p_value": 0.063,
        "oos_significant": False,
        "tier1_gates_passed": 2,
        "is_significant": False,
        "significance_summary": "Passes t-test, JK. Fails alpha, FDR, DSR at p<0.005 (Tier 1). Sub-period results mixed.",
        "cv_stability_score": 0.62,
        "stress_results": {
            "GFC_2008":       {"return": -0.261, "max_dd": -0.303, "vs_benchmark": 0.207},
            "COVID_2020":     {"return": -0.112, "max_dd": -0.201, "vs_benchmark": 0.085},
            "RATE_HIKE_2022": {"return": -0.158, "max_dd": -0.213, "vs_benchmark": 0.023},
            "DOTCOM_2000":    {"return": -0.198, "max_dd": -0.241, "vs_benchmark": 0.243},
            "TAPER_TANTRUM":  {"return": -0.031, "max_dd": -0.044, "vs_benchmark": 0.027},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "RISK_PARITY",
        "strategy_type": "static",
        "cagr": 0.0921,
        "total_return": 2.0847,
        "volatility": 0.0871,
        "max_drawdown": -0.2213,
        "drawdown_duration_days": 198,
        "drawdown_recovery_days": 321,
        "sharpe_ratio": 0.91,
        "sharpe_ci_95": [0.79, 1.03],
        "sortino_ratio": 1.34,
        "calmar_ratio": 0.42,
        "information_ratio": 0.31,
        "omega_ratio": 1.44,
        "var_95": -0.0089,
        "cvar_95": -0.0143,
        "skewness": -0.22,
        "kurtosis": 1.21,
        "alpha": 0.0031,
        "alpha_bps": 31,
        "alpha_after_costs_bps": 18,
        "beta": 0.44,
        "r_squared": 0.82,
        "avg_monthly_turnover": 0.051,
        "avg_equity_weight": 0.33,
        "avg_bond_weight": 0.50,
        "is_economically_significant": False,
        "min_viable_aum": 3_600_000,
        "p_value_ttest": 0.018,
        "p_value_sharpe_jk": 0.021,
        "p_value_alpha": 0.029,
        "p_value_corrected": 0.058,
        "p_value_bootstrap": 0.024,
        "normality_rejected": False,
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.78,
        "dsr_p_value": 0.022,
        "probabilistic_sharpe_ratio": 0.94,
        "spa_p_value": 0.041,
        "passes_spa": True,
        "oos_sharpe": 0.84,
        "oos_cagr": 0.086,
        "oos_p_value": 0.031,
        "oos_significant": False,
        "tier1_gates_passed": 3,
        "is_significant": False,
        "significance_summary": "Passes t-test, JK, alpha. Fails FDR, DSR at p<0.005 (Tier 1).",
        "cv_stability_score": 0.71,
        "stress_results": {
            "GFC_2008":       {"return": -0.181, "max_dd": -0.221, "vs_benchmark": 0.287},
            "COVID_2020":     {"return": -0.089, "max_dd": -0.158, "vs_benchmark": 0.108},
            "RATE_HIKE_2022": {"return": -0.141, "max_dd": -0.193, "vs_benchmark": 0.040},
            "DOTCOM_2000":    {"return": -0.121, "max_dd": -0.161, "vs_benchmark": 0.320},
            "TAPER_TANTRUM":  {"return": -0.022, "max_dd": -0.031, "vs_benchmark": 0.036},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "MIN_VARIANCE",
        "strategy_type": "static",
        "cagr": 0.0812,
        "total_return": 1.7891,
        "volatility": 0.0798,
        "max_drawdown": -0.2487,
        "drawdown_duration_days": 221,
        "drawdown_recovery_days": 389,
        "sharpe_ratio": 0.85,
        "sharpe_ci_95": [0.72, 0.98],
        "sortino_ratio": 1.24,
        "calmar_ratio": 0.33,
        "information_ratio": 0.24,
        "omega_ratio": 1.38,
        "var_95": -0.0081,
        "cvar_95": -0.0131,
        "skewness": -0.18,
        "kurtosis": 1.09,
        "alpha": 0.0021,
        "alpha_bps": 21,
        "alpha_after_costs_bps": 9,
        "beta": 0.38,
        "r_squared": 0.78,
        "avg_monthly_turnover": 0.062,
        "avg_equity_weight": 0.35,
        "avg_bond_weight": 0.55,
        "is_economically_significant": False,
        "min_viable_aum": 4_700_000,
        "p_value_ttest": 0.029,
        "p_value_sharpe_jk": 0.034,
        "p_value_alpha": 0.048,
        "p_value_corrected": 0.089,
        "p_value_bootstrap": 0.038,
        "normality_rejected": False,
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.72,
        "dsr_p_value": 0.038,
        "probabilistic_sharpe_ratio": 0.92,
        "spa_p_value": 0.067,
        "passes_spa": True,
        "oos_sharpe": 0.77,
        "oos_cagr": 0.074,
        "oos_p_value": 0.044,
        "oos_significant": False,
        "tier1_gates_passed": 2,
        "is_significant": False,
        "significance_summary": "Passes t-test, JK. Fails alpha, FDR, DSR at p<0.005 (Tier 1).",
        "cv_stability_score": 0.68,
        "stress_results": {
            "GFC_2008":       {"return": -0.158, "max_dd": -0.249, "vs_benchmark": 0.310},
            "COVID_2020":     {"return": -0.071, "max_dd": -0.141, "vs_benchmark": 0.126},
            "RATE_HIKE_2022": {"return": -0.128, "max_dd": -0.178, "vs_benchmark": 0.053},
            "DOTCOM_2000":    {"return": -0.098, "max_dd": -0.138, "vs_benchmark": 0.343},
            "TAPER_TANTRUM":  {"return": -0.018, "max_dd": -0.027, "vs_benchmark": 0.040},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "EQUAL_WEIGHT",
        "strategy_type": "static",
        "cagr": 0.0978,
        "total_return": 2.2341,
        "volatility": 0.1398,
        "max_drawdown": -0.3812,
        "drawdown_duration_days": 391,
        "drawdown_recovery_days": 612,
        "sharpe_ratio": 0.77,
        "sharpe_ci_95": [0.62, 0.92],
        "sortino_ratio": 1.09,
        "calmar_ratio": 0.26,
        "information_ratio": 0.16,
        "omega_ratio": 1.27,
        "var_95": -0.0141,
        "cvar_95": -0.0228,
        "skewness": -0.54,
        "kurtosis": 1.98,
        "alpha": 0.0009,
        "alpha_bps": 9,
        "alpha_after_costs_bps": -1,
        "beta": 0.71,
        "r_squared": 0.94,
        "avg_monthly_turnover": 0.029,
        "avg_equity_weight": 0.50,
        "avg_bond_weight": 0.25,
        "is_economically_significant": False,
        "min_viable_aum": 0,
        "p_value_ttest": 0.081,
        "p_value_sharpe_jk": 0.074,
        "p_value_alpha": 0.148,
        "p_value_corrected": 0.214,
        "p_value_bootstrap": 0.091,
        "normality_rejected": True,
        "bootstrap_used": True,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.65,
        "dsr_p_value": 0.089,
        "probabilistic_sharpe_ratio": 0.86,
        "spa_p_value": 0.142,
        "passes_spa": True,
        "oos_sharpe": 0.69,
        "oos_cagr": 0.088,
        "oos_p_value": 0.102,
        "oos_significant": False,
        "tier1_gates_passed": 0,
        "is_significant": False,
        "significance_summary": "Fails all Tier 1 gates. Net alpha after costs is negative.",
        "cv_stability_score": 0.54,
        "stress_results": {
            "GFC_2008":       {"return": -0.312, "max_dd": -0.381, "vs_benchmark": 0.156},
            "COVID_2020":     {"return": -0.152, "max_dd": -0.272, "vs_benchmark": 0.045},
            "RATE_HIKE_2022": {"return": -0.162, "max_dd": -0.221, "vs_benchmark": 0.019},
            "DOTCOM_2000":    {"return": -0.271, "max_dd": -0.341, "vs_benchmark": 0.170},
            "TAPER_TANTRUM":  {"return": -0.041, "max_dd": -0.056, "vs_benchmark": 0.017},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "MOMENTUM_ROTATION",
        "strategy_type": "dynamic",
        "cagr": 0.1213,
        "total_return": 2.8912,
        "volatility": 0.1542,
        "max_drawdown": -0.2841,
        "drawdown_duration_days": 248,
        "drawdown_recovery_days": 401,
        "sharpe_ratio": 0.88,
        "sharpe_ci_95": [0.74, 1.02],
        "sortino_ratio": 1.28,
        "calmar_ratio": 0.43,
        "information_ratio": 0.29,
        "omega_ratio": 1.41,
        "var_95": -0.0155,
        "cvar_95": -0.0249,
        "skewness": -0.33,
        "kurtosis": 1.54,
        "alpha": 0.0043,
        "alpha_bps": 43,
        "alpha_after_costs_bps": 27,
        "beta": 0.78,
        "r_squared": 0.88,
        "avg_monthly_turnover": 0.148,
        "avg_equity_weight": 0.72,
        "avg_bond_weight": 0.21,
        "is_economically_significant": False,
        "min_viable_aum": 1_900_000,
        "p_value_ttest": 0.009,
        "p_value_sharpe_jk": 0.011,
        "p_value_alpha": 0.014,
        "p_value_corrected": 0.031,
        "p_value_bootstrap": 0.012,
        "normality_rejected": True,
        "bootstrap_used": True,
        "has_autocorrelation": True,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.74,
        "dsr_p_value": 0.016,
        "probabilistic_sharpe_ratio": 0.95,
        "spa_p_value": 0.028,
        "passes_spa": True,
        "oos_sharpe": 0.81,
        "oos_cagr": 0.111,
        "oos_p_value": 0.019,
        "oos_significant": False,
        "tier1_gates_passed": 4,
        "is_significant": False,
        "significance_summary": "Passes t-test, JK, alpha, OOS. Fails FDR at p<0.005 (q=0.031). Close to significance.",
        "cv_stability_score": 0.73,
        "stress_results": {
            "GFC_2008":       {"return": -0.221, "max_dd": -0.284, "vs_benchmark": 0.247},
            "COVID_2020":     {"return": -0.134, "max_dd": -0.231, "vs_benchmark": 0.063},
            "RATE_HIKE_2022": {"return": -0.089, "max_dd": -0.148, "vs_benchmark": 0.092},
            "DOTCOM_2000":    {"return": -0.178, "max_dd": -0.231, "vs_benchmark": 0.263},
            "TAPER_TANTRUM":  {"return": 0.021,  "max_dd": -0.038, "vs_benchmark": 0.079},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "REGIME_SWITCHING",
        "strategy_type": "dynamic",
        "cagr": 0.1081,
        "total_return": 2.5487,
        "volatility": 0.1124,
        "max_drawdown": -0.2018,
        "drawdown_duration_days": 187,
        "drawdown_recovery_days": 298,
        "sharpe_ratio": 0.94,
        "sharpe_ci_95": [0.81, 1.07],
        "sortino_ratio": 1.41,
        "calmar_ratio": 0.54,
        "information_ratio": 0.38,
        "omega_ratio": 1.52,
        "var_95": -0.0112,
        "cvar_95": -0.0181,
        "skewness": -0.14,
        "kurtosis": 0.98,
        "alpha": 0.0051,
        "alpha_bps": 51,
        "alpha_after_costs_bps": 37,
        "beta": 0.56,
        "r_squared": 0.83,
        "avg_monthly_turnover": 0.089,
        "avg_equity_weight": 0.58,
        "avg_bond_weight": 0.34,
        "is_economically_significant": True,
        "min_viable_aum": 1_350_000,
        "p_value_ttest": 0.004,
        "p_value_sharpe_jk": 0.004,
        "p_value_alpha": 0.003,
        "p_value_corrected": 0.004,
        "p_value_bootstrap": 0.004,
        "normality_rejected": False,
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.82,
        "dsr_p_value": 0.004,
        "probabilistic_sharpe_ratio": 0.97,
        "spa_p_value": 0.003,
        "passes_spa": True,
        "oos_sharpe": 0.89,
        "oos_cagr": 0.099,
        "oos_p_value": 0.004,
        "oos_significant": True,
        "tier1_gates_passed": 5,
        "is_significant": True,
        "significance_summary": "SIGNIFICANT — All 5 Tier 1 gates pass at p<0.005. CV Stability 0.78 > 0.60 threshold.",
        "cv_stability_score": 0.78,
        "stress_results": {
            "GFC_2008":       {"return": -0.089, "max_dd": -0.202, "vs_benchmark": 0.379},
            "COVID_2020":     {"return": -0.062, "max_dd": -0.121, "vs_benchmark": 0.135},
            "RATE_HIKE_2022": {"return": -0.071, "max_dd": -0.118, "vs_benchmark": 0.110},
            "DOTCOM_2000":    {"return": -0.081, "max_dd": -0.141, "vs_benchmark": 0.360},
            "TAPER_TANTRUM":  {"return": 0.038,  "max_dd": -0.021, "vs_benchmark": 0.096},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "VOL_TARGETING",
        "strategy_type": "dynamic",
        "cagr": 0.0951,
        "total_return": 2.1678,
        "volatility": 0.0921,
        "max_drawdown": -0.1834,
        "drawdown_duration_days": 164,
        "drawdown_recovery_days": 267,
        "sharpe_ratio": 1.02,
        "sharpe_ci_95": [0.89, 1.15],
        "sortino_ratio": 1.58,
        "calmar_ratio": 0.52,
        "information_ratio": 0.41,
        "omega_ratio": 1.61,
        "var_95": -0.0093,
        "cvar_95": -0.0149,
        "skewness": -0.09,
        "kurtosis": 0.71,
        "alpha": 0.0047,
        "alpha_bps": 47,
        "alpha_after_costs_bps": 32,
        "beta": 0.45,
        "r_squared": 0.79,
        "avg_monthly_turnover": 0.104,
        "avg_equity_weight": 0.49,
        "avg_bond_weight": 0.51,
        "is_economically_significant": False,
        "min_viable_aum": 1_563_000,
        "p_value_ttest": 0.003,
        "p_value_sharpe_jk": 0.003,
        "p_value_alpha": 0.004,
        "p_value_corrected": 0.003,
        "p_value_bootstrap": 0.004,
        "normality_rejected": False,
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.88,
        "dsr_p_value": 0.003,
        "probabilistic_sharpe_ratio": 0.98,
        "spa_p_value": 0.002,
        "passes_spa": True,
        "oos_sharpe": 0.96,
        "oos_cagr": 0.088,
        "oos_p_value": 0.003,
        "oos_significant": True,
        "tier1_gates_passed": 5,
        "is_significant": True,
        "significance_summary": "SIGNIFICANT — All 5 Tier 1 gates pass at p<0.005. Alpha 32bps after costs < 50bps threshold.",
        "cv_stability_score": 0.81,
        "stress_results": {
            "GFC_2008":       {"return": -0.071, "max_dd": -0.183, "vs_benchmark": 0.397},
            "COVID_2020":     {"return": -0.051, "max_dd": -0.108, "vs_benchmark": 0.146},
            "RATE_HIKE_2022": {"return": -0.058, "max_dd": -0.101, "vs_benchmark": 0.123},
            "DOTCOM_2000":    {"return": -0.061, "max_dd": -0.118, "vs_benchmark": 0.380},
            "TAPER_TANTRUM":  {"return": 0.028,  "max_dd": -0.019, "vs_benchmark": 0.086},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "BLACK_LITTERMAN",
        "strategy_type": "dynamic",
        "cagr": 0.1038,
        "total_return": 2.4108,
        "volatility": 0.1218,
        "max_drawdown": -0.2412,
        "drawdown_duration_days": 211,
        "drawdown_recovery_days": 342,
        "sharpe_ratio": 0.96,
        "sharpe_ci_95": [0.83, 1.09],
        "sortino_ratio": 1.44,
        "calmar_ratio": 0.43,
        "information_ratio": 0.35,
        "omega_ratio": 1.49,
        "var_95": -0.0122,
        "cvar_95": -0.0198,
        "skewness": -0.21,
        "kurtosis": 1.18,
        "alpha": 0.0048,
        "alpha_bps": 48,
        "alpha_after_costs_bps": 33,
        "beta": 0.61,
        "r_squared": 0.86,
        "avg_monthly_turnover": 0.071,
        "avg_equity_weight": 0.62,
        "avg_bond_weight": 0.31,
        "is_economically_significant": False,
        "min_viable_aum": 1_515_000,
        "p_value_ttest": 0.004,
        "p_value_sharpe_jk": 0.004,
        "p_value_alpha": 0.004,
        "p_value_corrected": 0.004,
        "p_value_bootstrap": 0.004,
        "normality_rejected": False,
        "bootstrap_used": False,
        "has_autocorrelation": False,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.83,
        "dsr_p_value": 0.004,
        "probabilistic_sharpe_ratio": 0.97,
        "spa_p_value": 0.004,
        "passes_spa": True,
        "oos_sharpe": 0.91,
        "oos_cagr": 0.096,
        "oos_p_value": 0.004,
        "oos_significant": True,
        "tier1_gates_passed": 5,
        "is_significant": True,
        "significance_summary": "SIGNIFICANT — All 5 Tier 1 gates pass at p<0.005. Alpha 33bps after costs < 50bps threshold.",
        "cv_stability_score": 0.76,
        "stress_results": {
            "GFC_2008":       {"return": -0.121, "max_dd": -0.241, "vs_benchmark": 0.347},
            "COVID_2020":     {"return": -0.081, "max_dd": -0.161, "vs_benchmark": 0.116},
            "RATE_HIKE_2022": {"return": -0.091, "max_dd": -0.148, "vs_benchmark": 0.090},
            "DOTCOM_2000":    {"return": -0.101, "max_dd": -0.161, "vs_benchmark": 0.340},
            "TAPER_TANTRUM":  {"return": 0.019,  "max_dd": -0.028, "vs_benchmark": 0.077},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
    {
        "strategy_name": "MAX_SHARPE_ROLLING",
        "strategy_type": "dynamic",
        "cagr": 0.1134,
        "total_return": 2.6789,
        "volatility": 0.1289,
        "max_drawdown": -0.2589,
        "drawdown_duration_days": 228,
        "drawdown_recovery_days": 371,
        "sharpe_ratio": 0.98,
        "sharpe_ci_95": [0.84, 1.12],
        "sortino_ratio": 1.47,
        "calmar_ratio": 0.44,
        "information_ratio": 0.37,
        "omega_ratio": 1.54,
        "var_95": -0.0131,
        "cvar_95": -0.0211,
        "skewness": -0.28,
        "kurtosis": 1.34,
        "alpha": 0.0058,
        "alpha_bps": 58,
        "alpha_after_costs_bps": 41,
        "beta": 0.64,
        "r_squared": 0.87,
        "avg_monthly_turnover": 0.128,
        "avg_equity_weight": 0.65,
        "avg_bond_weight": 0.28,
        "is_economically_significant": False,
        "min_viable_aum": 1_220_000,
        "p_value_ttest": 0.004,
        "p_value_sharpe_jk": 0.003,
        "p_value_alpha": 0.003,
        "p_value_corrected": 0.004,
        "p_value_bootstrap": 0.004,
        "normality_rejected": False,
        "bootstrap_used": False,
        "has_autocorrelation": True,
        "is_stationary": True,
        "is_adequately_powered": True,
        "deflated_sharpe_ratio": 0.84,
        "dsr_p_value": 0.003,
        "probabilistic_sharpe_ratio": 0.97,
        "spa_p_value": 0.003,
        "passes_spa": True,
        "oos_sharpe": 0.92,
        "oos_cagr": 0.104,
        "oos_p_value": 0.003,
        "oos_significant": True,
        "tier1_gates_passed": 5,
        "is_significant": True,
        "significance_summary": "SIGNIFICANT — All 5 Tier 1 gates pass at p<0.005. Alpha 41bps after costs < 50bps threshold.",
        "cv_stability_score": 0.74,
        "stress_results": {
            "GFC_2008":       {"return": -0.148, "max_dd": -0.259, "vs_benchmark": 0.320},
            "COVID_2020":     {"return": -0.098, "max_dd": -0.181, "vs_benchmark": 0.099},
            "RATE_HIKE_2022": {"return": -0.081, "max_dd": -0.141, "vs_benchmark": 0.100},
            "DOTCOM_2000":    {"return": -0.118, "max_dd": -0.181, "vs_benchmark": 0.323},
            "TAPER_TANTRUM":  {"return": 0.024,  "max_dd": -0.033, "vs_benchmark": 0.082},
            "note": "No p-values reported — insufficient observations for valid testing",
        },
    },
]

MOCK_COUNCIL_RESPONSE = {
    "query": "Does fixed income diversification improve risk-adjusted performance?",
    "messages": [
        {
            "agent": "Equity Analyst",
            "role": "specialist",
            "model": "claude-sonnet-4-6",
            "content": (
                "Equity regime analysis complete. SPY momentum composite scores: 21d +0.41, 63d +0.38, 252d +0.31. "
                "HMM and threshold both classify current regime as BULL (agreement confirmed). "
                "Factor exposure: growth tilt (β=0.82), quality positive (t=3.1). "
                "Significance test: paired t-test vs benchmark p=0.008 at Tier 1 threshold — "
                "note this is portfolio-level, tool-returned value only."
            ),
            "is_final": False,
        },
        {
            "agent": "Fixed Income Analyst",
            "role": "specialist",
            "model": "claude-sonnet-4-6",
            "content": (
                "CRITICAL FINDING — equity-bond correlation breakdown confirmed. "
                "Pre-2022 rolling 252d correlation: −0.31. Post-2022 correlation: +0.48. "
                "breakdown_detected: True. diversification_effective: False in 2022 hiking cycle. "
                "Yield curve: 10Y−2Y spread +0.42%, RISING rate regime. "
                "This is the most important finding: the diversification benefit that drives 60/40 "
                "disappeared precisely when investors needed it most. Dynamic allocation is required."
            ),
            "is_final": False,
        },
        {
            "agent": "Risk Manager",
            "role": "specialist",
            "model": "claude-sonnet-4-6",
            "content": (
                "FDR correction applied across all 10 strategies (Benjamini-Hochberg, q<0.005). "
                "4 strategies survive FDR: REGIME_SWITCHING, VOL_TARGETING, BLACK_LITTERMAN, MAX_SHARPE_ROLLING. "
                "SPA test p=0.003 — data snooping risk is contained. "
                "Power confirmed: n=288 monthly obs, all Tier 1 tests adequately powered. "
                "WARN: 2022 stress test shows all strategies underperformed vs history — "
                "correlation breakdown reduced diversification benefit in that period."
            ),
            "is_final": False,
        },
        {
            "agent": "Quant Backtester",
            "role": "specialist",
            "model": "claude-sonnet-4-6",
            "content": (
                "Walk-forward OOS results (rolling 36m train / 12m test): "
                "REGIME_SWITCHING OOS Sharpe 0.89, p=0.004. VOL_TARGETING OOS Sharpe 0.96, p=0.003. "
                "Transaction costs applied at 10bps both ways. No look-ahead bias detected. "
                "CPCV confirms: Sharpe distribution is right-skewed, 74% of paths positive. "
                "Expanding vs rolling divergence: 0.08 — well below 0.30 threshold. Robust."
            ),
            "is_final": False,
        },
        {
            "agent": "Independent Analyst (Gemini)",
            "role": "dissenter",
            "model": "gemini-2.0-flash",
            "content": (
                "I challenge the consensus on three points. "
                "First, the 2022 correlation breakdown is more severe than acknowledged — "
                "if rate-hiking cycles are the new normal, the diversification benefit may be "
                "structurally impaired, not temporarily. "
                "Second, REGIME_SWITCHING depends on accurate regime classification. "
                "Both HMM and threshold methods show 15–20% disagreement in transition periods — "
                "strategy performance in those periods may be worse than the backtest implies. "
                "Third, 41bps alpha after costs for MAX_SHARPE_ROLLING is below the 50bps "
                "economic significance threshold. The council should not recommend it as economically viable."
            ),
            "is_final": False,
        },
        {
            "agent": "CIO",
            "role": "cio",
            "model": "claude-opus-4-6",
            "content": (
                "FINAL RECOMMENDATION — after full council deliberation including Gemini's challenge. "
                "\n\n"
                "The answer to the research question is nuanced: diversification via fixed income "
                "does improve risk-adjusted performance, but only through dynamic allocation. "
                "Static 60/40 does not pass all Tier 1 gates. "
                "\n\n"
                "Strategies that pass ALL 5 Tier 1 gates (p<0.005): "
                "REGIME_SWITCHING (Sharpe 0.94, CV 0.78), "
                "VOL_TARGETING (Sharpe 1.02, CV 0.81), "
                "BLACK_LITTERMAN (Sharpe 0.96, CV 0.76). "
                "\n\n"
                "I accept Gemini's point on the 2022 correlation breakdown — this is disclosed prominently. "
                "I accept the MAX_SHARPE_ROLLING economic significance concern — 41bps < 50bps threshold, "
                "it is flagged as statistically but not economically significant. "
                "I do not accept the regime uncertainty objection as a disqualifier — "
                "both signals are used in ensemble, and the HMM/threshold disagreement is already "
                "disclosed as an uncertainty flag in the output."
            ),
            "is_final": True,
        },
    ],
    "final_recommendation": "Dynamic allocation improves risk-adjusted performance. Three strategies pass all Tier 1 gates. Static 60/40 does not.",
    "consensus_reached": True,
}

MOCK_QA_AUDIT = {
    "sprint": "4",
    "checks_total": 30,
    "checks_passed": 22,
    "checks_warned": 5,
    "checks_failed": 3,
    "verdict": "WARN",
    "items": [
        # DATA INTEGRITY (7)
        {"check_id": "D01", "category": "DATA_INTEGRITY",      "check": "Total returns verified",        "description": "Total returns used (adjusted close, auto_adjust=True)",             "status": "PASS", "evidence": "yfinance auto_adjust=True confirmed on all fetches.",          "fix": None},
        {"check_id": "D02", "category": "DATA_INTEGRITY",      "check": "No survivorship bias",          "description": "No survivorship bias — all assets existed at backtest start",        "status": "PASS", "evidence": "All tickers verified present from 2000-01-01.",                 "fix": None},
        {"check_id": "D03", "category": "DATA_INTEGRITY",      "check": "Missing data policy",           "description": "Missing data policy applied (forward-fill max 5 days)",              "status": "PASS", "evidence": "Max gap 3 days observed; policy enforced.",                     "fix": None},
        {"check_id": "D04", "category": "DATA_INTEGRITY",      "check": "Full period data",              "description": "All assets have data for full backtest period",                       "status": "PASS", "evidence": "All assets validated 2000–2024.",                               "fix": None},
        {"check_id": "D05", "category": "DATA_INTEGRITY",      "check": "Time-varying risk-free rate",   "description": "Time-varying risk-free rate used (not fixed 4.5%)",                  "status": "WARN", "evidence": "Sprint 2: DTB3 from Excel integrated.",                         "fix": None},
        {"check_id": "D06", "category": "DATA_INTEGRITY",      "check": "Return consistency",            "description": "Returns computed consistently — simple not log",                     "status": "PASS", "evidence": "Simple returns (pct_change) used throughout.",                  "fix": None},
        {"check_id": "D07", "category": "DATA_INTEGRITY",      "check": "Annualisation factor",          "description": "Annualisation factor is sqrt(252) throughout",                       "status": "PASS", "evidence": "ANNUALIZATION_FACTOR=252 enforced at module level.",            "fix": None},
        # PORTFOLIO MECHANICS (5)
        {"check_id": "P01", "category": "PORTFOLIO_MECHANICS", "check": "Weights sum to 1",             "description": "Weights sum to 1.0 on every rebalance date (|sum - 1| < 1e-6)",      "status": "PASS", "evidence": "Assertion confirmed in backtester.",                             "fix": None},
        {"check_id": "P02", "category": "PORTFOLIO_MECHANICS", "check": "No short positions",           "description": "No negative weights (long-only enforced)",                           "status": "PASS", "evidence": "MIN_WEIGHT=0.0 constraint active.",                             "fix": None},
        {"check_id": "P03", "category": "PORTFOLIO_MECHANICS", "check": "Transaction costs applied",    "description": "Transaction costs applied both ways on every trade",                  "status": "PASS", "evidence": "10bps applied to every weight delta.",                          "fix": None},
        {"check_id": "P04", "category": "PORTFOLIO_MECHANICS", "check": "Rebalance timing",             "description": "Rebalancing at next-day open, not same-day close",                   "status": "WARN", "evidence": "Verify look-ahead in live backtester.",                         "fix": "Confirm signal date is strictly before execution date."},
        {"check_id": "P05", "category": "PORTFOLIO_MECHANICS", "check": "No test leakage",              "description": "TEST window (2022-2024) never used during optimisation",              "status": "PASS", "evidence": "Date partitions enforced in config.",                            "fix": None},
        # STATISTICAL INTEGRITY (10)
        {"check_id": "S01", "category": "STATISTICAL_INTEGRITY","check": "Power analysis",              "description": "Power analysis run before applying any threshold",                    "status": "PASS", "evidence": "n=282 > MIN_OBSERVATIONS_FOR_POWER=220 for all full-period tests.","fix": None},
        {"check_id": "S02", "category": "STATISTICAL_INTEGRITY","check": "Threshold tier disclosed",    "description": "Threshold tier explicitly disclosed alongside every p-value",          "status": "PASS", "evidence": "significance_summary field includes tier label.",              "fix": None},
        {"check_id": "S03", "category": "STATISTICAL_INTEGRITY","check": "All gates required",          "description": "is_significant = True requires ALL 5 Tier 1 gates passed",            "status": "PASS", "evidence": "Logic confirmed in schema validation.",                         "fix": None},
        {"check_id": "S04", "category": "STATISTICAL_INTEGRITY","check": "Sub-period narrative only",   "description": "Sub-period / regime results not used as hard significance gates",     "status": "PASS", "evidence": "Tier 2 results are narrative only.",                            "fix": None},
        {"check_id": "S05", "category": "STATISTICAL_INTEGRITY","check": "FDR correction applied",      "description": "FDR correction (q<0.005) applied across all Tier 1 tests",           "status": "PASS", "evidence": "Benjamini-Hochberg applied across 10 strategies.",             "fix": None},
        {"check_id": "S06", "category": "STATISTICAL_INTEGRITY","check": "Autocorrelation check",       "description": "Autocorrelation tested — Newey-West SE used if detected",             "status": "WARN", "evidence": "Autocorrelation flagged for MOMENTUM_ROTATION; Newey-West applied.","fix": None},
        {"check_id": "S07", "category": "STATISTICAL_INTEGRITY","check": "Normality tested",            "description": "Normality tested — block bootstrap used if rejected",                 "status": "PASS", "evidence": "Jarque-Bera applied; bootstrap used where normality rejected.", "fix": None},
        {"check_id": "S08", "category": "STATISTICAL_INTEGRITY","check": "DSR computed",               "description": "Deflated Sharpe Ratio computed (corrects for n_trials=10)",           "status": "PASS", "evidence": "DSR computed with n_trials=10 per Lopez de Prado.",             "fix": None},
        {"check_id": "S09", "category": "STATISTICAL_INTEGRITY","check": "PSR computed",               "description": "Probabilistic Sharpe Ratio computed (CI on Sharpe reported)",         "status": "PASS", "evidence": "probabilistic_sharpe_ratio and sharpe_ci_95 populated.",       "fix": None},
        {"check_id": "S10", "category": "STATISTICAL_INTEGRITY","check": "In-sample and OOS p-values",  "description": "Both in-sample AND out-of-sample p-values reported",                  "status": "PASS", "evidence": "oos_p_value and oos_significant fields populated.",             "fix": None},
        # CROSS-VALIDATION (4)
        {"check_id": "C01", "category": "CROSS_VALIDATION",    "check": "Walk-forward compared",        "description": "Walk-forward: rolling AND expanding window compared",                 "status": "PASS", "evidence": "Both methods implemented; divergence 0.08 < 0.30.",             "fix": None},
        {"check_id": "C02", "category": "CROSS_VALIDATION",    "check": "Purged K-fold applied",        "description": "Purged K-Fold with embargo = CV_EMBARGO_PERIODS applied",            "status": "PASS", "evidence": "Embargo=252 (momentum lookback) applied.",                      "fix": None},
        {"check_id": "C03", "category": "CROSS_VALIDATION",    "check": "CPCV distribution",            "description": "CPCV run — Sharpe distribution reported, not just point estimate",   "status": "PASS", "evidence": "CPCV C(6,2)=15 paths run; distribution reported.",             "fix": None},
        {"check_id": "C04", "category": "CROSS_VALIDATION",    "check": "CV stability >= 0.60",         "description": "CV Stability Score >= 0.60 for all recommended strategies",          "status": "PASS", "evidence": "REGIME_SWITCHING=0.78, VOL_TARGETING=0.81, BL=0.76 all pass.", "fix": None},
        # OVERFITTING (2)
        {"check_id": "O01", "category": "OVERFITTING",         "check": "SPA test passed",              "description": "SPA test passed across full strategy universe",                      "status": "PASS", "evidence": "SPA p=0.003 — data snooping risk contained.",                  "fix": None},
        {"check_id": "O02", "category": "OVERFITTING",         "check": "Parameter sensitivity",        "description": "Parameter sensitivity: ±20% on key params, results stable",         "status": "WARN", "evidence": "Sensitivity analysis pending Sprint 5.",                        "fix": "Run ±20% sensitivity sweep before Sprint 6."},
        # ECONOMIC SIGNIFICANCE (1)
        {"check_id": "E01", "category": "ECONOMIC_SIGNIFICANCE","check": "Alpha after costs >= 50bps",  "description": "Alpha after transaction costs >= 50bps for recommended strategies",  "status": "WARN", "evidence": "Alpha after costs is below 50bps threshold for some strategies.","fix": "Disclose economic significance threshold alongside statistical results."},
        # PRESENTATION (1)
        {"check_id": "PR01","category": "PRESENTATION",        "check": "2022 breakdown disclosed",     "description": "2022 correlation breakdown disclosed prominently",                   "status": "PASS", "evidence": "Amber warning callout present on Dashboard, always visible.",   "fix": None},
    ],
}

MOCK_EFFICIENT_FRONTIER = {
    "frontier_points": [
        {"volatility": 0.068, "expected_return": 0.062, "sharpe": 0.61},
        {"volatility": 0.074, "expected_return": 0.071, "sharpe": 0.73},
        {"volatility": 0.082, "expected_return": 0.081, "sharpe": 0.83},
        {"volatility": 0.091, "expected_return": 0.091, "sharpe": 0.91},
        {"volatility": 0.101, "expected_return": 0.100, "sharpe": 0.96},
        {"volatility": 0.112, "expected_return": 0.108, "sharpe": 0.98},
        {"volatility": 0.124, "expected_return": 0.115, "sharpe": 0.95},
        {"volatility": 0.138, "expected_return": 0.121, "sharpe": 0.90},
        {"volatility": 0.154, "expected_return": 0.126, "sharpe": 0.84},
        {"volatility": 0.172, "expected_return": 0.130, "sharpe": 0.78},
        {"volatility": 0.189, "expected_return": 0.102, "sharpe": 0.61},  # Benchmark
    ],
    "portfolio_points": [
        {"strategy": "BENCHMARK",         "volatility": 0.189, "expected_return": 0.102, "sharpe": 0.61},
        {"strategy": "CLASSIC_60_40",     "volatility": 0.110, "expected_return": 0.088, "sharpe": 0.79},
        {"strategy": "RISK_PARITY",       "volatility": 0.087, "expected_return": 0.092, "sharpe": 0.91},
        {"strategy": "MIN_VARIANCE",      "volatility": 0.080, "expected_return": 0.081, "sharpe": 0.85},
        {"strategy": "EQUAL_WEIGHT",      "volatility": 0.140, "expected_return": 0.098, "sharpe": 0.77},
        {"strategy": "MOMENTUM_ROTATION", "volatility": 0.154, "expected_return": 0.121, "sharpe": 0.88},
        {"strategy": "REGIME_SWITCHING",  "volatility": 0.112, "expected_return": 0.108, "sharpe": 0.94},
        {"strategy": "VOL_TARGETING",     "volatility": 0.092, "expected_return": 0.095, "sharpe": 1.02},
        {"strategy": "BLACK_LITTERMAN",   "volatility": 0.122, "expected_return": 0.104, "sharpe": 0.96},
        {"strategy": "MAX_SHARPE_ROLLING","volatility": 0.129, "expected_return": 0.113, "sharpe": 0.98},
    ],
    "min_variance_point": {"volatility": 0.068, "expected_return": 0.062},
    "max_sharpe_point":   {"volatility": 0.112, "expected_return": 0.108},
}
