/**
 * Strategy presentation helpers shared by every chart that differentiates
 * strategies visually. One source of truth so the colour, the strategy
 * class (DYNAMIC/STATIC), the display name, and the tooltip format stay
 * consistent across all 13 chart components (Dashboard + the 12 Sprint 6).
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

export type StrategyType = 'dynamic' | 'static'

// Sole source of truth for strategy classification. Was duplicated in
// DisagreementHeatmap.tsx before Sprint 6 chart UX work; now imported by
// every chart that needs a DYNAMIC/STATIC badge.
export const STRATEGY_TYPES: Record<string, StrategyType> = {
  BENCHMARK:          'static',
  CLASSIC_60_40:      'static',
  RISK_PARITY:        'static',
  MIN_VARIANCE:       'static',
  EQUAL_WEIGHT:       'static',
  MOMENTUM_ROTATION:  'dynamic',
  REGIME_SWITCHING:   'dynamic',
  VOL_TARGETING:      'dynamic',
  BLACK_LITTERMAN:    'dynamic',
  MAX_SHARPE_ROLLING: 'dynamic',
}

export function colorFor(strategy: string): string {
  return STRATEGY_COLORS[strategy] ?? '#64748b'
}

export function prettyName(strategy: string): string {
  return strategy.replace(/_/g, ' ')
}

export function typeFor(strategy: string): StrategyType | null {
  return STRATEGY_TYPES[strategy] ?? null
}

/**
 * Canonical tooltip text: "Strategy Name [DYNAMIC|STATIC] · Metric: value".
 * Every chart's tooltip — including recharts and hand-drawn SVG titles —
 * passes through this helper so the format is identical everywhere. If
 * the strategy class is unknown the badge segment is omitted rather than
 * substituted with a placeholder.
 */
export function tooltipLine(
  strategy: string,
  metric: string,
  value: string | number,
): string {
  const t = typeFor(strategy)
  const cls = t ? ` ${t.toUpperCase()}` : ''
  return `${prettyName(strategy)}${cls} · ${metric}: ${value}`
}
