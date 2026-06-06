/**
 * useChartTheme.ts — bridge between ThemeContext and the chart layer.
 *
 * Charts in the live app accept an optional `theme: ChartTheme` prop
 * defaulting to DARK_CHART_THEME (per lib/exportTheme.ts). The export
 * renderer explicitly passes LIGHT_CHART_THEME for the academic export
 * package; the live app never did. This hook lets a chart component
 * (or its caller) pick the right ChartTheme based on the current
 * ThemeContext so the toggle flips the chart automatically.
 *
 * Usage in a chart component:
 *
 *   import { useChartTheme } from '../lib/useChartTheme'
 *   const fallback = useChartTheme()
 *   const theme = themeProp ?? fallback
 *
 * That keeps the export path unchanged (theme prop wins) while making
 * the live app respond to the dark/light toggle.
 *
 * June 6 2026.
 */
import { useTheme } from '../context/ThemeContext'
import {
  DARK_CHART_THEME, LIGHT_CHART_THEME, type ChartTheme,
} from './exportTheme'

export function useChartTheme(): ChartTheme {
  const { theme } = useTheme()
  return theme === 'light' ? LIGHT_CHART_THEME : DARK_CHART_THEME
}
