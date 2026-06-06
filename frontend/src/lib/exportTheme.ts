/**
 * exportTheme.ts — chart theming for the academic export package.
 *
 * Charts render dark in the app and LIGHT in the export package (white
 * backgrounds, suitable for printing and embedding in a Word document).
 *
 * Mechanism: a `ChartTheme` object is passed to each export-target chart
 * as an optional `theme` prop. The prop defaults to DARK_CHART_THEME, so
 * the normal dark UI is completely unaffected — a chart only renders
 * light when the off-screen export renderer explicitly passes
 * LIGHT_CHART_THEME. (The originally-proposed `data-export-theme="light"`
 * CSS-attribute flip cannot recolour the ten distinct strategy series —
 * CSS has no per-series selector — so per-series colour is resolved here
 * in JS instead.)
 */
import {
  GRID_STROKE, AXIS_TICK, TOOLTIP_CONTENT_STYLE, TOOLTIP_LABEL_STYLE,
  REGIME_BREAK_COLOR,
} from './chartStyle'
import { STRATEGY_COLORS, LIGHT_STRATEGY_COLORS } from './strategyColors'

export interface ChartTheme {
  mode: 'dark' | 'light'
  /** Card / page background behind the chart. */
  background: string
  /** Gridline stroke. */
  gridStroke: string
  /** Primary text — chart titles. */
  textPrimary: string
  /** Secondary text — axis labels, captions. */
  textSecondary: string
  /** Border / divider. */
  border: string
  /** Positive / negative value colours. */
  positive: string
  negative: string
  /** The benchmark series. */
  benchmark: string
  /** The 2022 regime-break ReferenceLine. */
  regimeBreak: string
  /** Axis tick style for recharts `tick={...}`. */
  axisTick: { fill: string; fontSize: number; fontFamily: string }
  /** recharts `<Tooltip contentStyle / labelStyle>`. */
  tooltipContentStyle: Record<string, string | number>
  tooltipLabelStyle: Record<string, string | number>
  /** Strategy series colour by name. */
  colorFor: (strategy: string) => string
  /** Indexed palette for charts that colour series positionally. */
  seriesColors: string[]
}

// ── Dark theme — mirrors the live app (chartStyle.ts + strategyColors.ts) ──────
const DARK_SERIES = [
  '#60a5fa', '#34d399', '#a78bfa', '#fb923c', '#f472b6',
  '#22c55e', '#3b82f6', '#fbbf24', '#e879f9', '#06b6d4',
]

export const DARK_CHART_THEME: ChartTheme = {
  mode: 'dark',
  background: '#0d1424',
  gridStroke: GRID_STROKE,
  textPrimary: '#f9fafb',
  textSecondary: '#64748b',
  border: '#1e2d47',
  positive: '#22c55e',
  negative: '#ef4444',
  benchmark: '#64748b',
  regimeBreak: REGIME_BREAK_COLOR,
  axisTick: AXIS_TICK,
  tooltipContentStyle: TOOLTIP_CONTENT_STYLE,
  tooltipLabelStyle: TOOLTIP_LABEL_STYLE,
  colorFor: (s) => STRATEGY_COLORS[s] ?? '#64748b',
  seriesColors: DARK_SERIES,
}

// ── Light theme — for the academic export package + the live app's
// light-mode toggle (June 6 2026). LIGHT_STRATEGY_COLORS is the
// shared source-of-truth set defined in lib/strategyColors.ts;
// imported above so the same palette flips both the live app and the
// export package. Strategy colours are DARKENED versions of the dark-
// theme hues so all ten stay distinguishable on white (the bright
// dark-mode yellow / pink / sky would wash out on a printed page).

const LIGHT_SERIES = [
  '#2563eb', '#059669', '#7c3aed', '#ea580c', '#db2777',
  '#15803d', '#1e40af', '#b45309', '#a21caf', '#0e7490',
]

export const LIGHT_CHART_THEME: ChartTheme = {
  mode: 'light',
  background: '#FFFFFF',
  gridStroke: '#E2E8F0',
  textPrimary: '#1A1A2E',
  textSecondary: '#4A4A6A',
  border: '#E2E8F0',
  positive: '#16A34A',
  negative: '#DC2626',
  benchmark: '#374151',
  regimeBreak: '#1D4ED8',
  axisTick: { fill: '#4A4A6A', fontSize: 11, fontFamily: 'JetBrains Mono' },
  tooltipContentStyle: {
    backgroundColor: '#F8F9FA',
    border: '1px solid #E2E8F0',
    borderRadius: 6,
    fontSize: 12,
    color: '#1A1A2E',
  },
  tooltipLabelStyle: { color: '#4A4A6A', fontSize: 11 },
  colorFor: (s) => LIGHT_STRATEGY_COLORS[s] ?? '#4A4A6A',
  seriesColors: LIGHT_SERIES,
}
