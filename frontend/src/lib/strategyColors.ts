/**
 * Stable colour mapping for the 10 strategies. Used by every chart that
 * differentiates strategies visually. Defined in one place so the legend
 * is consistent across all 13 chart components (Dashboard + 12 Sprint 6).
 *
 * Slate tones for static strategies, vivid hues for dynamic ones — the
 * audience can read strategy class at a glance without reading the legend.
 */
export const STRATEGY_COLORS: Record<string, string> = {
  BENCHMARK:          '#64748b',
  CLASSIC_60_40:      '#60a5fa',
  RISK_PARITY:        '#34d399',
  MIN_VARIANCE:       '#a78bfa',
  EQUAL_WEIGHT:       '#fb923c',
  MOMENTUM_ROTATION:  '#f472b6',
  REGIME_SWITCHING:   '#22c55e',
  VOL_TARGETING:      '#3b82f6',
  BLACK_LITTERMAN:    '#fbbf24',
  MAX_SHARPE_ROLLING: '#e879f9',
}

export function colorFor(strategy: string): string {
  return STRATEGY_COLORS[strategy] ?? '#64748b'
}

export function prettyName(strategy: string): string {
  return strategy.replace(/_/g, ' ')
}
