/**
 * explainerTooltips.ts
 *
 * Static, pre-written tooltip content for the contextual explainer
 * (InfoIcon). One to two plain-English sentences per metric / chart /
 * column, written for a senior investment audience.
 *
 * HOVER content only — shown instantly with no API call. The CLICK
 * interaction (ExplainerPanel) calls POST /api/council/explain for a
 * live, data-anchored explanation; that path uses none of this file.
 *
 * Key naming convention: snake_case identifiers. Chart keys end with no
 * suffix (e.g. cumulative_return_chart); table-column keys are the bare
 * metric name (e.g. cagr, sharpe). Every key wired into an InfoIcon
 * must have a non-empty entry here — test_explainer_tooltips enforces it.
 *
 * The per-strategy entries (strategy_*) are derived from strategyMetadata
 * so the type + one-line description stay a single source of truth with
 * the ExplainerPanel click data.
 */
import { STRATEGY_METADATA, strategyTooltipKey } from './strategyMetadata'

// One hover tooltip per strategy: "Static/Dynamic strategy. <description>".
const STRATEGY_TOOLTIPS: Record<string, string> = Object.fromEntries(
  Object.entries(STRATEGY_METADATA).map(([id, m]) => [
    strategyTooltipKey(id),
    `${m.type === 'dynamic' ? 'Dynamic' : 'Static'} strategy. ${m.description}`,
  ]),
)

export const EXPLAINER_TOOLTIPS: Record<string, string> = {
  // ── Analytics page — charts ────────────────────────────────────────────────
  cumulative_return_chart:
    'Shows how $1 invested in each strategy at inception would have grown '
    + 'over the full study period. Strategies above the benchmark line '
    + 'outperformed pure equity.',
  rolling_correlation_chart:
    '12-month rolling correlation between equity and bond returns. Values '
    + 'near 1 mean assets move together — reducing the diversification '
    + 'benefit of holding both.',
  correlation_regime_break:
    'The 2022 Federal Reserve hiking cycle caused equity-bond correlation '
    + 'to shift from slightly negative (diversifying) to strongly positive '
    + '— the central finding of this project.',
  rolling_excess_return:
    '12-month rolling outperformance versus the 100% equity benchmark. '
    + 'Green regions are periods of outperformance, red regions '
    + 'underperformance.',
  sensitivity_analysis:
    "Shows how each dynamic strategy's Sharpe ratio changes as its key "
    + 'parameter varies. The vertical line marks the current setting — '
    + 'steeper slopes mean more parameter sensitivity.',

  // ── Analytics page — summary statistics columns ────────────────────────────
  cagr:
    'Compound Annual Growth Rate — the annualised return assuming '
    + 'reinvestment of all gains.',
  volatility:
    'Annualised standard deviation of monthly returns — measures how much '
    + 'returns fluctuate around their average.',
  sharpe:
    'Return per unit of risk, above the risk-free rate. Higher is better. '
    + 'Above 0.5 is generally considered good.',
  max_drawdown:
    'The largest peak-to-trough loss in the study period. Measures the '
    + 'worst-case loss an investor would have experienced.',
  skewness:
    'Asymmetry of the return distribution. Negative skew means larger '
    + 'losses than gains of equal frequency — relevant for downside risk.',
  excess_return:
    'Annual return above the 100% equity benchmark. Positive means the '
    + 'strategy outperformed buy-and-hold.',
  information_ratio:
    'Excess return divided by tracking error. Measures consistency of '
    + 'outperformance — higher means more reliable alpha.',

  // ── Analytics page — tables ────────────────────────────────────────────────
  regime_conditional_table:
    "Each strategy's performance split at the 2022 correlation regime "
    + 'break. Strategies with positive post-2022 Sharpe held up after '
    + 'diversification stopped working.',
  drawdown_table:
    'Maximum loss and recovery time per strategy. Recovery months '
    + 'measures how long it took to reach a new equity high after the '
    + 'worst drawdown.',

  // ── Analytics page — Carhart four-factor loadings ──────────────────────────
  ff_factor_loadings:
    'Carhart four-factor regression. Alpha is the return unexplained by '
    + 'market, size, value, and momentum exposure. * marks statistical '
    + 'significance at p < 0.05.',
  ff_alpha:
    'Annualised excess return above what the four factors predict. '
    + 'Positive significant alpha means the strategy adds value beyond '
    + 'factor exposure.',
  ff_mkt_rf:
    'Market beta — sensitivity to overall market moves. 1.0 means the '
    + 'strategy moves with the market.',
  ff_smb:
    'Small-minus-big factor loading — exposure to the small-cap premium. '
    + 'Negative means a large-cap tilt.',
  ff_hml:
    'High-minus-low factor loading — exposure to the value premium. '
    + 'Negative means a growth tilt.',
  ff_mom:
    'Momentum factor loading — exposure to the momentum premium. Positive '
    + 'means the strategy benefits when recent winners keep winning.',
  ff_r2:
    'Proportion of strategy returns explained by the four factors. High '
    + 'R² means returns are largely factor-driven, not strategy-specific.',

  // ── Dashboard page — strategy table columns ────────────────────────────────
  sharpe_ci:
    'Sharpe ratio with a 95% confidence interval. Wider intervals mean '
    + 'less statistical certainty in the ratio estimate.',
  dsr:
    'Deflated Sharpe Ratio — adjusts for multiple-testing bias. Below 0.5 '
    + 'suggests the Sharpe may be due to chance rather than skill.',
  p_fdr:
    'P-value under FDR correction for multiple comparisons. Green '
    + '(< 0.05) means statistically significant after controlling for the '
    + 'number of strategies tested.',
  cv_score:
    'Composite score across multiple performance metrics. Higher is '
    + 'better — used to rank strategies holistically rather than on a '
    + 'single metric.',
  turnover:
    'Average annual portfolio turnover — the proportion of the portfolio '
    + 'replaced per year. Higher turnover implies higher transaction costs '
    + 'in practice.',
  tier:
    'Strategy ranking tier based on the composite score. Tier 1 is the '
    + 'highest performing — used to group strategies for presentation.',

  // ── Statistical Evidence page — charts ─────────────────────────────────────
  cpcv_sharpe_plot:
    'Sharpe ratio across non-overlapping CPCV blocks per strategy. A tight '
    + 'whisker box means the result is robust; a wide box means it is '
    + 'path-dependent and less reliable.',
  cv_stability_radar:
    "A six-axis robustness profile per strategy — walk-forward, CPCV, "
    + 'permutation, regime, OOS, and composite stability. A balanced hexagon '
    + 'indicates an all-round robust strategy.',
  factor_exposure_heatmap:
    'Fama-French three-factor OLS loadings per strategy. Blue cells are '
    + 'positive exposure, red negative; the alpha column shows return not '
    + 'explained by the factors.',
  multiple_comparison_table:
    'Raw versus Benjamini-Hochberg FDR-corrected p-values. Highlights '
    + 'strategies that look significant raw but fail once corrected for the '
    + 'number of strategies tested.',
  performance_attribution_waterfall:
    'Brinson-Hood-Beebower decomposition of active return into allocation, '
    + 'selection, and interaction effects — shows where each strategy earns '
    + 'its outperformance.',
  probabilistic_sharpe_chart:
    'Sharpe point estimates with 95% confidence intervals per strategy. Wide '
    + 'intervals mean the Sharpe estimate is uncertain even when the point '
    + 'estimate looks high.',
  regime_conditional_performance:
    'Sharpe ratio per strategy split by BULL, BEAR, and TRANSITION regimes. '
    + 'Balanced bar heights indicate an all-weather strategy rather than one '
    + 'that only works in bull markets.',
  regime_timeline:
    'Threshold-classified market regime for every month of the study '
    + 'period. Long colour blocks show regime persistence; red bars cluster '
    + 'around historical crises.',
  regime_transition_matrix:
    'Empirical probability of moving from one regime to another next month. '
    + 'The diagonal measures regime persistence — high values support '
    + 'regime-switching strategies.',
  significance_journey_matrix:
    'The five Tier 1 statistical gates per strategy with the actual metric '
    + 'value in each cell. A strategy must pass all five to count as '
    + 'statistically significant.',
  walk_forward_chart:
    'Rolling out-of-sample Sharpe per strategy across walk-forward windows. '
    + 'Stable lines indicate a strategy robust across different time periods '
    + 'rather than one lucky window.',

  // ── Dashboard page — efficient frontier ────────────────────────────────────
  efficient_frontier:
    'The set of portfolios offering maximum return for each level of '
    + 'risk. Strategies above the curve achieve returns not achievable by '
    + 'any static mix of the three assets — evidence of dynamic strategy '
    + 'value.',
  max_sharpe_point:
    'The tangency portfolio — the static mix of equity, IG bonds, and HY '
    + 'bonds that maximises the Sharpe ratio. Dynamic strategies that plot '
    + 'above this point outperform the theoretical static optimum.',

  // ── Dashboard page — strategy names (derived from strategyMetadata) ────────
  ...STRATEGY_TOOLTIPS,
}

/** A tooltip key with a guaranteed non-empty entry. */
export type ExplainerTooltipKey = keyof typeof EXPLAINER_TOOLTIPS

/** Returns the static tooltip text for a key, or undefined when unknown. */
export function getTooltip(key: string): string | undefined {
  return EXPLAINER_TOOLTIPS[key]
}
