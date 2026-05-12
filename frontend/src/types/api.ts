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
  hmm_regime: number
  hmm_probabilities: number[]
  regimes_agree: boolean
  vix_level: number
  yield_curve_slope: number
  credit_spread: number
  equity_trend?: number
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
