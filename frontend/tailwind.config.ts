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
        electric: {
          // May 24 2026 — the `electric-blue` class is used widely
          // across the report writer and the navigation chrome.
          // Tailwind 3 lets the same key serve both a flat colour
          // (text-electric) and a scale (text-electric-blue) by
          // including a DEFAULT entry alongside the named shades.
          DEFAULT: '#3b82f6',
          blue:    '#3b82f6',
        },
        // May 24 2026 — text token aliases. 90 uses of
        // text-text-primary / text-text-secondary / text-text-muted
        // existed across the codebase but the names weren't defined
        // in this config, so Tailwind compiled them to no-op CSS.
        // The dark-theme pages mostly rendered fine because they
        // inherited a white-on-dark parent, but the native file-
        // input button (Choose File) defaults to black-on-white at
        // browser level — surfacing as the "black on dark"
        // unreadable button the user reported (P4).
        text: {
          primary:   '#f1f5f9',
          secondary: '#cbd5e1',
          muted:     '#64748b',
          disabled:  '#475569',
        },
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
