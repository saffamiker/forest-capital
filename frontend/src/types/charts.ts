/**
 * Type definitions for the /api/v1/charts/data payload.
 * Mirrors the shape returned by backend/tools/chart_data.py compute_chart_data.
 */

export interface CPCVStats {
  sharpe_mean: number
  sharpe_std: number
  sharpe_min: number
  sharpe_max: number
  sharpe_q1: number
  sharpe_q3: number
  sharpe_median: number
  pct_positive: number
  n_paths: number
}

export interface CVRadarPoint {
  walk_forward: number
  cpcv: number
  permutation: number
  regime: number
  oos: number
  stability: number
}

export interface WalkForwardWindow {
  window_end: string
  oos_sharpe: number
}

export interface RegimeConditionalStats {
  mean_return: number
  sharpe: number
  n_months: number
}

export type Regime = 'BULL' | 'BEAR' | 'TRANSITION'

export interface RegimeConditional {
  BULL: RegimeConditionalStats
  BEAR: RegimeConditionalStats
  TRANSITION: RegimeConditionalStats
}

export interface RegimeTimelinePoint {
  date: string
  regime: Regime
}

export interface CorrelationPoint {
  date: string
  rolling_12m: number
}

export interface FactorLoadings {
  mkt_rf: number
  smb: number
  hml: number
  alpha: number
  r_squared: number
  n_obs: number
}

export interface AttributionResult {
  allocation: number
  selection: number
  interaction: number
  total_active: number
}

export type TransitionMatrix = Record<Regime, Record<Regime, number>>

export interface ChartDataPayload {
  cpcv:                  Record<string, CPCVStats>
  cv_radar:              Record<string, CVRadarPoint>
  walk_forward:          Record<string, WalkForwardWindow[]>
  regime_conditional:    Record<string, RegimeConditional>
  regime_timeline:       RegimeTimelinePoint[]
  correlation_breakdown: CorrelationPoint[]
  factor_loadings:       Record<string, FactorLoadings>
  attribution:           Record<string, AttributionResult>
  transition_matrix:     TransitionMatrix
  n_strategies:          number
  n_months:              number
  strategy_hash?:        string
  error?:                string
}
