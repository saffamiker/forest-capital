/**
 * ThemeContext.tsx
 *
 * Light/dark theme state, persisted to localStorage as `fc_theme`. The
 * platform was built dark-first; this context adds a user-facing toggle
 * so the team can switch to light mode for the July 1 demo (dark themes
 * don't project well).
 *
 * The contract:
 *   - default: light when the user's OS prefers light, otherwise dark
 *   - persisted: localStorage `fc_theme` survives reloads
 *   - applied: adds/removes the `dark` class on document.documentElement
 *
 * Tailwind is configured with `darkMode: 'class'` (tailwind.config.ts),
 * so any element with a `dark:` companion class flips automatically.
 * Body + scrollbars + key surfaces read from CSS variables defined in
 * index.css — those always flip cleanly. Legacy components that use
 * raw `bg-navy-*` classes without a `dark:` companion stay dark in
 * both modes today; a follow-up sweep will add `dark:` variants.
 *
 * June 5 2026.
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

type Theme = 'light' | 'dark'

interface ThemeContextValue {
  theme: Theme
  toggleTheme: () => void
  setTheme: (next: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

const STORAGE_KEY = 'fc_theme'

/**
 * Initial theme resolution — localStorage first, then system preference,
 * then 'dark' as the platform default. Wrapped in a try so a private-mode
 * browser that throws on localStorage access still gets a working default.
 */
function resolveInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* ignore — private mode */
  }
  try {
    if (typeof window !== 'undefined' && window.matchMedia) {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      return prefersDark ? 'dark' : 'light'
    }
  } catch {
    /* ignore */
  }
  return 'dark'
}

/**
 * Apply the theme to document.documentElement and persist to localStorage.
 * Idempotent — calling twice with the same value is a no-op visually.
 */
function applyTheme(theme: Theme): void {
  try {
    const root = document.documentElement
    if (theme === 'dark') root.classList.add('dark')
    else root.classList.remove('dark')
  } catch {
    /* ignore — SSR / non-DOM environments */
  }
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* ignore — private mode */
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => resolveInitialTheme())

  // Apply on mount AND on every theme change.
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setThemeState(prev => (prev === 'dark' ? 'light' : 'dark'))
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

/**
 * Hook — read/write the active theme. Fails open with a noop fallback
 * when no provider is mounted (test environments, isolated component
 * mounts) so a consumer never crashes the page on a missing provider.
 * In production the provider is mounted at App root, so the fallback
 * is never hit.
 */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    return {
      theme: 'dark',
      toggleTheme: () => {},
      setTheme: () => {},
    }
  }
  return ctx
}
