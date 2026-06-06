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

// June 6 2026 — light-mode palette for the theme toggle. The dark palette
// uses bright pastels that read clearly against the platform's navy
// background; on a white background those pastels are washed out and
// some series become indistinguishable. The light-mode set substitutes
// darker, more saturated hues that hold contrast against white.
// Strategy → hue mapping is preserved (same colour family per strategy)
// so a viewer who saw a chart dark and then flips to light immediately
// recognises each series — the only difference is saturation/contrast.
export const LIGHT_STRATEGY_COLORS: Record<string, string> = {
  BENCHMARK:          '#475569',  // slate-600  (was slate-500)
  CLASSIC_60_40:      '#2563eb',  // blue-600   (was blue-400)
  RISK_PARITY:        '#059669',  // emerald-600 (was emerald-400)
  MIN_VARIANCE:       '#7c3aed',  // violet-600 (was violet-400)
  EQUAL_WEIGHT:       '#ea580c',  // orange-600 (was orange-400)
  MOMENTUM_ROTATION:  '#db2777',  // pink-600   (was pink-400)
  REGIME_SWITCHING:   '#16a34a',  // green-600  (was green-500)
  VOL_TARGETING:      '#1d4ed8',  // blue-700   (was blue-500)
  BLACK_LITTERMAN:    '#d97706',  // amber-600  (was amber-400)
  MAX_SHARPE_ROLLING: '#a21caf',  // fuchsia-700 (was fuchsia-400)
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

// Theme-aware accessor — picks LIGHT_STRATEGY_COLORS or STRATEGY_COLORS
// based on the theme argument. Callers that have a theme in hand should
// prefer this over the dark-only colorFor() so the page flips cleanly
// on toggle. Defaults to 'dark' so existing callers keep working
// without a signature break.
export function colorForTheme(
  strategy: string, theme: 'dark' | 'light' = 'dark',
): string {
  const palette = theme === 'light' ? LIGHT_STRATEGY_COLORS : STRATEGY_COLORS
  return palette[strategy] ?? (theme === 'light' ? '#475569' : '#64748b')
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
