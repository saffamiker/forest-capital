export interface StressResult {
  return: number
  max_dd: number
  vs_benchmark: number
}

export interface StressResults {
  GFC_2008?: StressResult
  COVID_2020?: StressResult
  RATE_HIKE_2022?: StressResult
  DOTCOM_2000?: StressResult
  TAPER_TANTRUM?: StressResult
  note?: string
}

export interface SubperiodResult {
  sharpe: number
  p_value: number
  threshold_tier: 'tier2'
}

export interface SubperiodResults {
  period_2000_2008?: SubperiodResult
  period_2009_2018?: SubperiodResult
  period_2019_2024?: SubperiodResult
  n_subperiods_significant?: number
}

export interface CrossValidation {
  wf_oos_sharpe_mean: number
  wf_oos_sharpe_std: number
  wf_pct_folds_beating_bm: number
  wf_worst_fold_sharpe: number
  ew_oos_sharpe_mean: number
  ew_vs_wf_divergence: number
  pkf_oos_sharpe_mean: number
  pkf_oos_p_value: number
  cpcv_sharpe_mean: number
  cpcv_sharpe_std: number
  cpcv_sharpe_ci_95: [number, number]
  cpcv_pct_positive: number
  permutation_p_value: number
  permutation_passed: boolean
  cv_stability_score: number
  passes_all_cv: boolean
}

export interface StrategyResult {
  strategy_name: string
  strategy_type?: 'static' | 'dynamic'
  cagr: number
  total_return?: number
  monthly_returns?: number[]
  volatility: number
  max_drawdown: number
  drawdown_duration_days?: number
  drawdown_recovery_days?: number
  var_95?: number
  cvar_95?: number
  skewness?: number
  kurtosis?: number
  sharpe_ratio: number
  sortino_ratio?: number
  calmar_ratio?: number
  information_ratio?: number
  omega_ratio?: number
  alpha?: number
  alpha_bps?: number
  alpha_after_costs_bps?: number
  beta?: number
  r_squared?: number
  avg_monthly_turnover?: number
  true_turnover?: number
  avg_equity_weight?: number
  avg_bond_weight?: number
  is_economically_significant?: boolean
  min_viable_aum?: number
  p_value_ttest?: number
  p_value_sharpe_jk?: number
  p_value_alpha?: number
  p_value_corrected?: number
  p_value_bootstrap?: number
  normality_rejected?: boolean
  bootstrap_used?: boolean
  has_autocorrelation?: boolean
  is_stationary?: boolean
  is_adequately_powered?: boolean
  deflated_sharpe_ratio?: number
  dsr_p_value?: number
  probabilistic_sharpe_ratio?: number
  sharpe_ci_95?: [number, number]
  spa_p_value?: number
  passes_spa?: boolean
  cross_validation?: CrossValidation
  attribution?: Record<string, number>
  oos_sharpe?: number
  oos_cagr?: number
  oos_p_value?: number
  oos_significant?: boolean
  subperiod_results?: SubperiodResults
  stress_results?: StressResults
  tier1_gates_passed?: number
  is_significant?: boolean
  significance_summary?: string
  cv_stability_score?: number
}
