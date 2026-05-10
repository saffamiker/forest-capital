export const colors = {
  // Backgrounds
  bg_primary:   '#0a0e1a',
  bg_surface:   '#111827',
  bg_elevated:  '#1a2438',
  bg_overlay:   '#0d1929',

  // Borders
  border_subtle: '#1f2937',
  border_medium: '#1e3a5c',
  border_strong: '#2d4a6b',

  // Text
  text_primary:   '#f9fafb',
  text_secondary: '#cbd5e1',
  text_muted:     '#64748b',
  text_disabled:  '#374151',

  // Accents
  accent_blue:    '#3b82f6',
  accent_teal:    '#0d9488',
  accent_amber:   '#f59e0b',
  accent_purple:  '#8b5cf6',
  accent_red:     '#ef4444',
  accent_green:   '#10b981',
  accent_crimson: '#be123c',

  // Semantic
  positive:        '#10b981',
  negative:        '#ef4444',
  warning:         '#f59e0b',
  neutral:         '#64748b',
  significant:     '#10b981',
  not_significant: '#ef4444',

  // Strategy colours — fixed across all charts
  strategy: {
    VOL_TARGETING:      '#3b82f6',
    MAX_SHARPE_ROLLING: '#8b5cf6',
    BLACK_LITTERMAN:    '#0d9488',
    REGIME_SWITCHING:   '#f59e0b',
    RISK_PARITY:        '#10b981',
    MOMENTUM_ROTATION:  '#06b6d4',
    MIN_VARIANCE:       '#64748b',
    CLASSIC_60_40:      '#94a3b8',
    EQUAL_WEIGHT:       '#475569',
    BENCHMARK:          '#ef4444',
  },

  // Agent card left-border accents
  agent: {
    cio:             '#1e40af',
    equity:          '#3b82f6',
    fixed_income:    '#0d9488',
    risk_manager:    '#f59e0b',
    quant:           '#64748b',
    gemini:          '#8b5cf6',
    qa:              '#be123c',
    uiux:            '#0f766e',
  },
} as const

// Spacing — 4px base grid
export const spacing = {
  xs:   '4px',
  sm:   '8px',
  md:   '12px',
  lg:   '16px',
  xl:   '24px',
  xxl:  '32px',
  xxxl: '48px',
} as const

// Typography
export const typography = {
  font_data:      "'JetBrains Mono', 'Fira Code', monospace",
  font_ui:        "'Inter', 'DM Sans', sans-serif",
  size_xs:        '11px',
  size_sm:        '12px',
  size_md:        '14px',
  size_lg:        '16px',
  size_xl:        '20px',
  size_xxl:       '28px',
  weight_normal:  400,
  weight_medium:  500,
  weight_bold:    700,
  tracking_wide:  '0.06em',
  tracking_xwide: '0.12em',
} as const

// Borders
export const borders = {
  radius_sm:   '4px',
  radius_md:   '8px',
  radius_lg:   '12px',
  radius_full: '9999px',
} as const

// Shadows
export const shadows = {
  card:  '0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.3)',
  modal: '0 20px 60px rgba(0,0,0,0.6)',
  strip: '0 -1px 0 rgba(30,58,92,0.5)',
} as const

// Chart axis / grid defaults (recharts)
export const chartDefaults = {
  grid_stroke:    '#1e2d47',
  grid_dash:      '3 3',
  axis_fill:      '#64748b',
  axis_font_size: 10,
  axis_font:      "'JetBrains Mono', monospace",
  tooltip_bg:     '#0d1424',
  tooltip_border: '#1e2d47',
  tooltip_radius: 6,
} as const
