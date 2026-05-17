/**
 * chartStyle.ts — one source of truth for recharts visual styling.
 *
 * recharts needs literal colour strings, so these constants stand in for
 * Tailwind tokens inside chart components. Values mirror tailwind.config:
 *   gridline / border  → `border`   #1e2d47
 *   tooltip background  → `navy-800` #0d1424
 *   axis tick / muted   → `muted`    #64748b
 *
 * Before this module each chart hardcoded its own near-miss greys
 * (#1e3a5c / #1f2937 / #1e2d47 gridlines; #1a2438 / #0d1929 / #0d1424
 * tooltips). Import from here so every chart renders identically.
 */

/** CartesianGrid stroke — the `border` token. */
export const GRID_STROKE = '#1e2d47'

/** Axis tick colour — the `muted` token. */
export const AXIS_TICK_COLOR = '#64748b'

/** Standard axis tick style for XAxis/YAxis `tick={...}`. */
export const AXIS_TICK = {
  fill: AXIS_TICK_COLOR,
  fontSize: 10,
  fontFamily: 'JetBrains Mono',
} as const

/** recharts <Tooltip contentStyle={...}> — navy-800 surface, border edge. */
export const TOOLTIP_CONTENT_STYLE = {
  backgroundColor: '#0d1424',
  border: '1px solid #1e2d47',
  borderRadius: 6,
  fontSize: 12,
} as const

/** recharts <Tooltip labelStyle={...}>. */
export const TOOLTIP_LABEL_STYLE = {
  color: '#94a3b8',
  fontSize: 11,
} as const

/**
 * The 2022 equity-bond correlation regime break. Every time-series chart
 * spanning 2022 marks it with a dashed ReferenceLine in this colour and
 * with this label, so the central project finding looks identical
 * everywhere it appears.
 */
export const REGIME_BREAK_DATE = '2022-01-31'
export const REGIME_BREAK_COLOR = '#8b5cf6'
export const REGIME_BREAK_LABEL = '2022 Regime Break'
