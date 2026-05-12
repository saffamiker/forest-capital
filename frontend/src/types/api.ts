export interface MagicLinkResponse {
  message: string
  // "sent"    → email is approved; link was dispatched
  // "pending" → email is not on the approved list; no link sent
  status: 'sent' | 'pending'
  dev_mode: boolean
}

export type RegimeType = 'BULL' | 'BEAR' | 'TRANSITION'

export interface RegimeData {
  threshold_regime: RegimeType
  hmm_regime: number | null
  hmm_probabilities: number[] | null
  regimes_agree: boolean
  vix_level: number | null
  yield_curve_slope: number | null
  credit_spread: number | null
  equity_trend?: number | null
  // Computed from market_data_monthly — never hardcoded in frontend.
  // Null when build_monthly_returns() is unavailable (cold start / test env).
  pre_2022_avg_correlation: number | null
  post_2022_avg_correlation: number | null
  as_of?: string
}

export interface FrontierPoint {
  volatility: number
  expected_return: number
  sharpe?: number
  strategy?: string
}

export interface PortfolioPoint extends FrontierPoint {
  strategy: string
}

export interface EfficientFrontierData {
  frontier_points: FrontierPoint[]
  portfolio_points: PortfolioPoint[]
  max_sharpe_point?: FrontierPoint
  min_variance_point?: FrontierPoint
}
