/**
 * mobile-responsive.test.tsx
 *
 * Verifies the mobile-responsive implementation:
 *   1. the hamburger nav drawer — renders below lg, opens/closes on the
 *      hamburger, a nav-item selection and an overlay click
 *   2. the desktop horizontal nav is lg-only; the hamburger is lg:hidden
 *   3. the Dashboard strategy table scrolls horizontally with a frozen
 *      first column
 *   4. ExplainerPanel renders as a bottom sheet on mobile
 *   5. shared icon buttons meet the 44px touch-target minimum
 *
 * jsdom does not evaluate @media breakpoints, so the breakpoint-specific
 * behaviour is asserted by the presence of the responsive utility
 * classes; the drawer open/close behaviour is genuine React state and is
 * exercised directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import axios from 'axios'

// react-joyride renders nothing while the tour is idle, but stub it so
// the import is light and deterministic inside MainLayout.
vi.mock('react-joyride', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-joyride')>()
  return { ...actual, Joyride: () => null }
})
vi.mock('axios')

import { AuthContext } from '../App'
import { BrandProvider } from '../context/BrandContext'
import { UIProvider } from '../context/UIContext'
import { SessionProvider } from '../context/SessionContext'
import MainLayout from '../layouts/MainLayout'
import Dashboard from '../components/Dashboard'
import ExplainerPanel from '../components/ExplainerPanel'
import InfoIcon from '../components/InfoIcon'
import { useStrategiesStore } from '../stores/strategiesStore'
import { useQAStore } from '../stores/qaStore'
import type { StrategyResult } from '../types/strategies'

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
  defaults: { headers: { common: Record<string, unknown> } }
}

beforeEach(() => {
  // Route-aware GET — the QA status poll needs a well-formed payload
  // (QAStatusBadge reads strategy_hash.slice); everything else is happy
  // with an empty object.
  mockedAxios.get = vi.fn().mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/qa/status')) {
      return Promise.resolve({ data: {
        verdict: 'WARN', tier: 2, age_hours: 2,
        strategy_hash: 'testhash00', present_mode_allowed: true,
      } })
    }
    return Promise.resolve({ data: {} })
  })
  mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
  mockedAxios.isAxiosError = ((() => false) as unknown) as typeof axios.isAxiosError
  mockedAxios.defaults = { headers: { common: {} } }
  // Reset the QA store so a stale tieredStatus cannot leak between tests.
  useQAStore.setState({ tieredStatus: null, status: 'unknown' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ── Harness ───────────────────────────────────────────────────────────────────

const AUTH_VALUE = {
  session: {
    token: 't', email: 'ruurdsm@queens.edu',
    permissions: ['view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package', 'view_admin', 'manage_users'],
  },
  isVerifying: false,
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
}

// Every app context a screen-level component expects, plus a router.
function Providers({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/']}>
      <AuthContext.Provider value={AUTH_VALUE}>
        <BrandProvider>
          <UIProvider>
            <SessionProvider>{children}</SessionProvider>
          </UIProvider>
        </BrandProvider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

function renderMainLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AuthContext.Provider value={AUTH_VALUE}>
        <BrandProvider>
          <UIProvider>
            <SessionProvider>
              <Routes>
                <Route path="/" element={<MainLayout />}>
                  <Route index element={<div>Home route</div>} />
                  <Route path="analytics" element={<div>Analytics route</div>} />
                </Route>
              </Routes>
            </SessionProvider>
          </UIProvider>
        </BrandProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

describe('Mobile navigation drawer', () => {
  it('renders a hamburger that is hidden from lg up', () => {
    renderMainLayout()
    const burger = screen.getByTestId('nav-hamburger')
    expect(burger).toBeInTheDocument()
    expect(burger.className).toContain('lg:hidden')
  })

  it('keeps the horizontal nav for lg and up only', () => {
    renderMainLayout()
    const nav = screen.getByRole('navigation')
    expect(nav.className).toContain('hidden')
    expect(nav.className).toContain('lg:flex')
  })

  it('does not render the drawer until the hamburger is clicked', () => {
    renderMainLayout()
    expect(screen.queryByTestId('nav-drawer')).not.toBeInTheDocument()
  })

  it('opens the drawer on the hamburger and shows the nav items', async () => {
    const user = userEvent.setup()
    renderMainLayout()
    await user.click(screen.getByTestId('nav-hamburger'))
    const drawer = screen.getByTestId('nav-drawer')
    expect(drawer).toBeInTheDocument()
    // The grouped nav items are present inside the drawer.
    expect(within(drawer).getByText('Investment Outlook')).toBeInTheDocument()
    expect(within(drawer).getByText('Council')).toBeInTheDocument()
  })

  it('closes the drawer when a nav item is selected', async () => {
    const user = userEvent.setup()
    renderMainLayout()
    await user.click(screen.getByTestId('nav-hamburger'))
    const drawer = screen.getByTestId('nav-drawer')
    await user.click(within(drawer).getByText('Analytics'))
    expect(screen.queryByTestId('nav-drawer')).not.toBeInTheDocument()
  })

  it('closes the drawer when the overlay is clicked', async () => {
    const user = userEvent.setup()
    renderMainLayout()
    await user.click(screen.getByTestId('nav-hamburger'))
    expect(screen.getByTestId('nav-drawer')).toBeInTheDocument()
    await user.click(screen.getByTestId('nav-drawer-overlay'))
    expect(screen.queryByTestId('nav-drawer')).not.toBeInTheDocument()
  })

  it('toggles the hamburger aria-expanded state', async () => {
    const user = userEvent.setup()
    renderMainLayout()
    const burger = screen.getByTestId('nav-hamburger')
    expect(burger).toHaveAttribute('aria-expanded', 'false')
    await user.click(burger)
    expect(burger).toHaveAttribute('aria-expanded', 'true')
  })
})

// ── Tour interaction guard (UAT feedback #1, May 22 2026) ─────────────────────

describe('Nav right-side cluster stays clickable during the site tour', () => {
  // The Joyride overlay sits at z-90 and otherwise intercepts every
  // click on the nav header. Raising the right-side controls cluster
  // to z-100 puts Settings / Sign out / Testing Mode / Help / the
  // user email above the overlay so a tour-active user can still
  // reach the account icon. The Joyride config never blocks pointer
  // events on its own — z-index is what governs the hit-test.
  it('settings icon container carries z-[100] relative positioning', () => {
    renderMainLayout()
    const settings = screen.getByLabelText('Settings')
    // The right-side cluster is the Settings link's nearest ancestor
    // div carrying the z-[100] class.
    const cluster = settings.closest('div.z-\\[100\\]')
    expect(cluster).not.toBeNull()
    expect(cluster?.className).toContain('relative')
    expect(cluster?.className).toContain('z-[100]')
  })
})

// ── Dashboard strategy table ──────────────────────────────────────────────────

const SAMPLE_STRATEGY = {
  strategy_name: 'BENCHMARK',
  strategy_type: 'static',
  sharpe_ratio: 0.52,
  cagr: 0.085,
  max_drawdown: -0.51,
  is_significant: false,
  tier1_gates_passed: 0,
} as unknown as StrategyResult

describe('Dashboard strategy table', () => {
  beforeEach(() => {
    useStrategiesStore.setState({
      strategies: [SAMPLE_STRATEGY], dataRange: null,
      loading: false, error: null, loaded: true, lastFetchedAt: new Date(),
    })
  })

  it('wraps the comparison table in a horizontal scroll container', async () => {
    render(<Providers><Dashboard /></Providers>)
    await waitFor(() => expect(screen.getByText(/Strategy Comparison/)).toBeInTheDocument())
    const table = screen.getByRole('table')
    const scroller = table.closest('.overflow-x-auto')
    expect(scroller).not.toBeNull()
  })

  it('freezes the Strategy column sticky-left', async () => {
    render(<Providers><Dashboard /></Providers>)
    await waitFor(() => expect(screen.getByText(/Strategy Comparison/)).toBeInTheDocument())
    // The Strategy column header carries the sticky-left utilities.
    const strategyHeader = screen.getAllByText('Strategy')[0]
    const th = strategyHeader.closest('th')
    expect(th?.className).toContain('sticky')
    expect(th?.className).toContain('left-0')
  })
})

// ── ExplainerPanel bottom sheet ───────────────────────────────────────────────

describe('ExplainerPanel', () => {
  beforeEach(() => {
    // ExplainerPanel streams via fetch — a never-resolving stub keeps it
    // in its initial render without a network call.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, body: null,
    }) as unknown as typeof fetch
  })

  it('renders as a bottom sheet on mobile and a right drawer from sm up', () => {
    render(<Providers><ExplainerPanel metricLabel="Sharpe Ratio" onClose={vi.fn()} /></Providers>)
    const dialog = screen.getByRole('dialog')
    // Mobile bottom-sheet anchoring …
    expect(dialog.className).toContain('inset-x-0')
    expect(dialog.className).toContain('bottom-0')
    // … flipping to the right-side drawer from sm up.
    expect(dialog.className).toContain('sm:right-0')
  })
})

// ── Touch targets ─────────────────────────────────────────────────────────────

describe('Touch targets', () => {
  it('gives the InfoIcon button a 44px minimum tap target on mobile', () => {
    render(<InfoIcon tooltipKey="cagr" metricLabel="CAGR" />)
    const btn = screen.getByRole('button', { name: /Explain CAGR/ })
    expect(btn.className).toContain('min-h-[44px]')
    expect(btn.className).toContain('min-w-[44px]')
  })

  it('gives the nav hamburger a 44px tap target', () => {
    renderMainLayout()
    const burger = screen.getByTestId('nav-hamburger')
    // w-11 / h-11 == 44px in the Tailwind scale.
    expect(burger.className).toContain('w-11')
    expect(burger.className).toContain('h-11')
  })
})
