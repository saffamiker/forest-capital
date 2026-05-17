/**
 * strategyMetadata.ts
 *
 * Frontend mirror of the backend strategy_metadata.py — the plain-English
 * record of each strategy's rules: its type, construction approach,
 * economic rationale, key parameter (dynamic strategies only), and
 * rebalancing frequency.
 *
 * Used by the ⓘ InfoIcon on each strategy name in the Dashboard strategy
 * table: the hover tooltip (derived in explainerTooltips.ts) shows the
 * type and one-line description; the click opens the ExplainerPanel with
 * the full metadata passed as the data source for the explanation.
 *
 * Keyed by strategy_name exactly as the backtester stamps it on each
 * result (e.g. "VOL_TARGETING").
 */

export interface StrategyMeta {
  /** Human-readable name. */
  name: string
  /** Mirrors the strategy_type the backtester stamps on each result. */
  type: 'static' | 'dynamic'
  /** One-sentence description — the hover tooltip body. */
  description: string
  /** Construction approach / allocation rules. */
  construction: string
  /** Economic rationale — why the strategy should work. */
  rationale: string
  /** The tunable parameter — dynamic strategies only. */
  keyParameter?: string
  /** Rebalancing frequency. */
  rebalancing: string
}

export const STRATEGY_METADATA: Record<string, StrategyMeta> = {
  BENCHMARK: {
    name: '100% Equity (Benchmark)',
    type: 'static',
    description:
      'The 100% S&P 500 baseline required by the brief — every other '
      + 'strategy is judged against it.',
    construction: '100% equity (S&P 500); held throughout.',
    rationale: 'The required reference point — pure equity, no diversification.',
    rebalancing: 'Buy and hold — no rebalancing.',
  },
  CLASSIC_60_40: {
    name: 'Classic 60/40',
    type: 'static',
    description:
      'The canonical balanced policy allocation — equities for growth, '
      + 'investment-grade bonds for ballast.',
    construction: 'Fixed 60% equity / 40% investment-grade bonds.',
    rationale:
      'Equities drive long-run growth; investment-grade bonds dampen '
      + 'drawdowns when they are negatively correlated with equity.',
    rebalancing: 'Quarterly, to fixed target weights.',
  },
  RISK_PARITY: {
    name: 'Risk Parity',
    type: 'static',
    description:
      'Weights are optimised so equity, IG and HY each contribute an '
      + 'equal share of portfolio risk.',
    construction:
      'Weights optimised so each of equity, IG and HY contributes an '
      + 'equal share of total portfolio risk.',
    rationale:
      'Equalising risk contributions stops any single sleeve from '
      + 'dominating drawdowns.',
    rebalancing: 'Quarterly, to optimised target weights.',
  },
  MIN_VARIANCE: {
    name: 'Minimum Variance',
    type: 'static',
    description:
      'Weights are optimised to minimise portfolio variance over a '
      + 'rolling 36-month window.',
    construction:
      'Weights optimised to minimise portfolio variance over a rolling '
      + '36-month covariance window.',
    rationale:
      'Covariance is estimable with far less error than expected return, '
      + 'so a variance-only objective is more robust out of sample.',
    rebalancing: 'Quarterly, rolling 36-month covariance window.',
  },
  EQUAL_WEIGHT: {
    name: 'Equal Weight',
    type: 'static',
    description:
      'Naive 1/N diversification across the three asset classes — a '
      + 'hard-to-beat baseline.',
    construction: 'Fixed one-third each to equity, IG and HY.',
    rationale:
      '1/N diversification is notoriously hard to beat out of sample '
      + '(DeMiguel et al. 2009).',
    rebalancing: 'Quarterly, to fixed target weights.',
  },
  MOMENTUM_ROTATION: {
    name: 'Momentum Rotation',
    type: 'dynamic',
    description:
      'Rotates into recent winners while excluding the weakest of the '
      + 'three sleeves.',
    construction:
      'Each quarter, score equity, IG and HY by a composite momentum '
      + 'signal over 1-, 3-, 6- and 12-month lookbacks (weighted toward '
      + '12 months); hold the top two at 50% each.',
    rationale:
      'Asset classes that have outperformed recently tend to keep '
      + 'outperforming over the following months (Jegadeesh & Titman 1993).',
    keyParameter:
      'Lookback windows — 1 / 3 / 6 / 12 months, weighted 0.10 / 0.20 / '
      + '0.30 / 0.40.',
    rebalancing: 'Quarterly.',
  },
  REGIME_SWITCHING: {
    name: 'Regime Switching',
    type: 'dynamic',
    description:
      'A small, transparent set of regime allocations driven by one '
      + 'robust signal — the equity trend.',
    construction:
      'Classify the market each quarter as BULL, BEAR or TRANSITION from '
      + 'the trailing 3-month equity trend, then allocate per regime — '
      + 'BULL 80/20 equity/IG, BEAR 20/60/20, TRANSITION 50/40/10.',
    rationale:
      'Equity drawdowns cluster; cutting equity and adding bonds when '
      + 'momentum turns down limits participation in bear markets.',
    keyParameter: 'Regime-assessment window — 3 months.',
    rebalancing: 'Quarterly.',
  },
  VOL_TARGETING: {
    name: 'Volatility Targeting',
    type: 'dynamic',
    description:
      'Holds portfolio risk roughly constant rather than letting it '
      + 'swing with the market.',
    construction:
      'Each month, scale the equity weight so the portfolio targets 10% '
      + 'annualised volatility, using the trailing 21-day realised '
      + 'volatility of equity; the remainder goes to IG bonds.',
    rationale:
      'Volatility is persistent — targeting constant risk de-risks into '
      + 'turbulent periods and re-risks into calm ones (Moreira & Muir 2017).',
    keyParameter: 'Target volatility — 10% annualised.',
    rebalancing: 'Monthly.',
  },
  BLACK_LITTERMAN: {
    name: 'Black-Litterman',
    type: 'dynamic',
    description:
      'Weights are optimised from a Black-Litterman posterior — the '
      + 'covariance regularisation is the main benefit at this stage.',
    construction:
      'Each quarter, form the Black-Litterman posterior from an '
      + 'equal-weight equilibrium prior over a rolling 36-month window, '
      + 'then solve a mean-variance optimisation on the posterior.',
    rationale:
      'Anchoring to an equilibrium prior before tilting prevents the '
      + 'extreme corner portfolios raw mean-variance produces on noisy '
      + '36-month estimates.',
    keyParameter: 'Rolling window — 36 months.',
    rebalancing: 'Quarterly, rolling 36-month window.',
  },
  MAX_SHARPE_ROLLING: {
    name: 'Max Sharpe Rolling',
    type: 'dynamic',
    description:
      'Weights are optimised for Sharpe each quarter; the 36-month '
      + 'window trades estimation error against regime staleness.',
    construction:
      'Each quarter, solve for the maximum-Sharpe portfolio over the '
      + 'trailing 36 months under the long-only weight bounds.',
    rationale:
      'Continuously re-estimating the best risk-adjusted mix adapts the '
      + 'allocation as the covariance and return structure shifts.',
    keyParameter: 'Rolling window — 36 months.',
    rebalancing: 'Quarterly, rolling 36-month window.',
  },
}

/** The InfoIcon tooltip key for a strategy name (e.g. "strategy_vol_targeting"). */
export function strategyTooltipKey(strategyName: string): string {
  return `strategy_${strategyName.toLowerCase()}`
}

/**
 * The full metadata blob passed to the ExplainerPanel as current_value —
 * the explainer agent anchors its explanation on these rules.
 */
export function strategyMetaSummary(meta: StrategyMeta): string {
  const parts = [
    `Type: ${meta.type === 'dynamic' ? 'Dynamic' : 'Static'}.`,
    `Construction: ${meta.construction}`,
    `Economic rationale: ${meta.rationale}`,
  ]
  if (meta.keyParameter) parts.push(`Key parameter: ${meta.keyParameter}`)
  parts.push(`Rebalancing: ${meta.rebalancing}`)
  return parts.join(' ')
}
