/**
 * frontend/src/types/diversification.ts
 *
 * Type contracts for the seven /api/v1/analytics/* endpoints shipped
 * in `a239843` (item 8 backend). The backend Python source of truth
 * is backend/tools/diversification_analytics.py — keep these types
 * in sync when the metric shapes change.
 *
 * Every endpoint reads through the analytics_metrics_cache (migration
 * 028); the response carries `_computed_at` from the cache row and
 * (when serving from a stale fallback) `_stale=true`. Both are
 * underscore-prefixed because the cache helper strips them on write
 * and only attaches them on read.
 */

export interface CachedFields {
  _computed_at?: string | null
  _data_hash?: string | null
  _stale?: boolean
}

// 1. Correlation matrices ────────────────────────────────────────────────────
export interface CorrelationMatrixPayload extends CachedFields {
  labels: string[]
  /** N x N. Diagonal is always 1.0. Off-diagonal cells may be null
   *  when a sub-period had < 2 observations. */
  full: Array<Array<number | null>>
  pre_2022: Array<Array<number | null>>
  post_2022: Array<Array<number | null>>
  diagonal: number
}

// 2. Tail risk ───────────────────────────────────────────────────────────────
export interface TailRiskRow {
  strategy: string
  var_95_monthly: number
  var_99_monthly: number
  cvar_95_monthly: number
  cvar_99_monthly: number
  var_95_annual: number
  var_99_annual: number
  cvar_95_annual: number
  cvar_99_annual: number
}
export interface TailRiskPayload extends CachedFields {
  strategies: TailRiskRow[]
}

// 3. Capture ratios ──────────────────────────────────────────────────────────
export interface CaptureWindow {
  up_capture: number | null
  down_capture: number | null
  capture_score: number | null
}
export interface CaptureRow {
  strategy: string
  full: CaptureWindow
  pre_2022: CaptureWindow
  post_2022: CaptureWindow
}
export interface CapturePayload extends CachedFields {
  strategies: CaptureRow[]
}

// 4. Drawdown duration ───────────────────────────────────────────────────────
export interface DrawdownDurationRow {
  strategy: string
  avg_duration_months: number
  max_duration_months: number
  avg_recovery_months: number
  longest_recovery_months: number
  currently_in_drawdown: boolean
  current_drawdown_months: number
}
export interface DrawdownDurationPayload extends CachedFields {
  strategies: DrawdownDurationRow[]
}

// 5. Crisis performance ──────────────────────────────────────────────────────
//
// May 30 2026 — added `cumulative_return` after the F3 incident. The
// crisis-window headline figure is now the cumulative return through
// the window, not the annualised CAGR (which over-stated COVID Crash
// 6× on a 2-month window). `cagr` is still emitted by the backend for
// callers that want the annualised rate (COVID Recovery readers
// asking "what was the annualised pace of the recovery?"); display
// layers MUST use `cumulative_return` for the "loss/gain during the
// event" framing. The field is nullable so a legacy payload still
// parses; cells should fall back to `cagr` only when
// `cumulative_return` is genuinely absent.
export interface CrisisCell {
  cumulative_return: number | null
  cagr: number | null
  max_dd: number | null
  sharpe: number | null
  partial: boolean
  n_months: number
}
export interface CrisisWindow {
  start: string
  end: string
}
export interface CrisisPerformancePayload extends CachedFields {
  windows: Record<string, CrisisWindow>
  /** strategy_name -> crisis_name -> CrisisCell */
  rows: Record<string, Record<string, CrisisCell>>
}

// 6. Marginal contribution to risk ───────────────────────────────────────────
export interface RiskContributionPayload extends CachedFields {
  labels: string[]
  mctr_equal_weight: number[]
  pct_risk_contribution_equal: number[]
  mctr_tangency_weight: number[] | null
  pct_risk_contribution_tangency: number[] | null
  tangency_weights: number[] | null
  /** May 25 2026 — true when max_sharpe_optimize fell back to
   *  min_variance because every strategy's excess return was
   *  non-positive (Sharpe maximisation was infeasible). The weights
   *  in the response are then min-variance weights; the frontend
   *  relabels the toggle so the user isn't told 'max Sharpe' when the
   *  numbers are min-variance. Optional for backward compatibility
   *  with cached payloads written before the field shipped. */
  tangency_fallback_to_min_variance?: boolean
}

// 7. Return distribution ─────────────────────────────────────────────────────
export interface BestWorstMonth {
  date: string
  ret: number
}
export interface DistributionRow {
  strategy: string
  skewness: number
  excess_kurtosis: number
  jarque_bera_stat: number | null
  jarque_bera_p: number | null
  normality_passes: boolean
  best_months: BestWorstMonth[]
  worst_months: BestWorstMonth[]
}
export interface DistributionPayload extends CachedFields {
  strategies: DistributionRow[]
}
