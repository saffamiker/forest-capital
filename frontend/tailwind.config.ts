import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  // iOS Safari Layer A — hover:* utilities now compile inside
  // @media (hover: hover) { … }, so a phone tap never makes a hover
  // style fire and stick on the tapped button. Devices that support
  // hover (mouse / trackpad / iPad-with-pencil) behave identically —
  // the media query is true on every desktop, so the AI buttons,
  // [+ Text] button, and chart-card hover states render the same as
  // before. The four called out in the GROUP 3C report are covered by
  // this flag; so is every other hover:* class in the codebase, which
  // is the upside of fixing it at config level rather than per-button.
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      colors: {
        navy: {
          950: '#060912',
          900: '#0a0e1a',
          800: '#0d1424',
          700: '#111a2e',
          600: '#162038',
          500: '#1a2742',
          400: '#213252',
        },
        electric: '#3b82f6',
        success: '#22c55e',
        warning: '#f59e0b',
        danger:  '#ef4444',
        muted:   '#64748b',
        border:  '#1e2d47',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }],
      },
    },
  },
  plugins: [],
}

export default config
