/**
 * performance-record-implied-allocation.test.tsx -- June 15 2026.
 *
 * Smoke test for the Implied Asset Allocation Over Time section
 * (stacked Equity / IG Bonds / HY Bonds areas, type="stepAfter")
 * plus the regime indicator band below it. The post-2022
 * cumulative LineChart sits ABOVE the allocation chart and is
 * smoke-tested separately on the same page render.
 *
 * Regime band is a hand-rolled SVG with one uniform-width <rect>
 * per calendar month from regime_timeline (filtered to >=
 * 2022-01-01). This test pins the cell count to the post-2022
 * monthly count so a regression that reintroduces the per-
 * rebalance-event spacing (which mis-aligns the band) is caught.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'

import PerformanceRecord from '../pages/PerformanceRecord'

// recharts uses ResponsiveContainer which measures the DOM; jsdom
// reports 0 dimensions which collapses every chart. The smoke test
// only inspects testids / static text, so a thin stub is enough.
vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 800, height: 300 }}>{children}</div>
    ),
  }
})

vi.mock('axios')
// Loose-typed `get` mock so the URL-keyed factory below doesn't trip
// the Mock<[url], ...> vs Mock<any[], unknown> tuple/variadic mismatch
// that strict tsc (Vercel's build step) flags. Vitest runs the test
// fine either way; this keeps the build green.
const mockedAxios = axios as unknown as {
  get: ((...args: unknown[]) => Promise<unknown>) & {
    mockImplementation: (
      fn: (url: unknown) => Promise<unknown>,
    ) => void
    mockReset?: () => void
  }
}

const PLAY_BY_PLAY = {
  available: true,
  events: [],
  scorecard: null,
  key_limitations: {},
  cumulative: {
    series: [
      { date: '2022-01-31', regime_conditional: 0, benchmark: 0,
        classic_6040: 0 },
      { date: '2026-05-31', regime_conditional: 0.5, benchmark: 0.3,
        classic_6040: 0.4 },
    ],
    event_markers: [],
  },
}

const COST_SENSITIVITY = {
  available: true,
  cost_sensitivity: {
    n_rebalances: 2,
    gross_sharpe: 0.8,
    benchmark_sharpe: 0.6,
    n_test_months: 50,
    scenarios: [],
    rebalance_events: [
      {
        date: '2022-03-31', regime: 'BULL',
        weights: { BENCHMARK: 0.5, REGIME_SWITCHING: 0.5 },
        total_shift: 0.5,
        asset_allocation: { equity: 0.70, ig: 0.25, hy: 0.05 },
      },
      {
        date: '2026-04-30', regime: 'BULL',
        weights: { BENCHMARK: 0.4, REGIME_SWITCHING: 0.6 },
        total_shift: 2.0,
        asset_allocation: { equity: 0.60, ig: 0.30, hy: 0.10 },
      },
    ],
  },
}

// 53 monthly regime points spanning the post-2022 OOS window
// (Jan 2022 through May 2026 inclusive). Synthesized from a
// deterministic rotation so the band can assert each regime
// label appears at least once; production reads the real HMM
// sequence verbatim.
function lastDay(y: number, m: number): string {
  // Month m is 1-12. Generate the last calendar day for the
  // month using a Date roll-forward.
  const d = new Date(Date.UTC(y, m, 0))
  const yyyy = d.getUTCFullYear()
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(d.getUTCDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}
const REGIMES = ['BULL', 'BEAR', 'TRANSITION']
function buildTimeline(): { date: string; regime: string }[] {
  const out: { date: string; regime: string }[] = []
  let i = 0
  for (let y = 2022; y <= 2026; y++) {
    const last = y === 2026 ? 5 : 12
    for (let m = 1; m <= last; m++) {
      out.push({ date: lastDay(y, m), regime: REGIMES[i % 3]! })
      i++
    }
  }
  return out
}
const TIMELINE_53 = buildTimeline()
const CHARTS_DATA = { regime_timeline: TIMELINE_53 }

function renderPage() {
  return render(
    <MemoryRouter>
      <PerformanceRecord />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  mockedAxios.get.mockImplementation((url) => {
    if (String(url).includes('/api/v1/play-by-play')) {
      return Promise.resolve({ data: PLAY_BY_PLAY })
    }
    if (String(url).includes('/api/v1/oos-cost-sensitivity')) {
      return Promise.resolve({ data: COST_SENSITIVITY })
    }
    if (String(url).includes('/api/v1/charts/data')) {
      return Promise.resolve({ data: CHARTS_DATA })
    }
    return Promise.resolve({ data: {} })
  })
})
afterEach(() => { vi.clearAllMocks() })


describe('PerformanceRecord -- Implied Asset Allocation Over Time', () => {

  it('renders the Implied Asset Allocation Over Time section header',
    async () => {
      renderPage()
      const section = await screen.findByTestId(
        'implied-allocation-over-time')
      expect(section.textContent).toMatch(
        /Implied Asset Allocation Over Time/i)
    })

  it('renders the inline legend with Equity / IG Bonds / HY Bonds',
    async () => {
      renderPage()
      const legend = await screen.findByTestId(
        'implied-allocation-legend')
      expect(legend.textContent).toMatch(/Equity/i)
      expect(legend.textContent).toMatch(/IG Bonds/i)
      expect(legend.textContent).toMatch(/HY Bonds/i)
    })

  it('renders the regime band when the timeline fetch succeeds',
    async () => {
      renderPage()
      await waitFor(() =>
        expect(screen.getByTestId('regime-band')).toBeInTheDocument())
      const band = screen.getByTestId('regime-band')
      // Band labels carry the three regime names so a reader of the
      // band can decode the colours.
      expect(band.textContent).toMatch(/BULL/)
      expect(band.textContent).toMatch(/BEAR/)
      expect(band.textContent).toMatch(/TRANSITION/)
    })

  it('renders one uniform-width SVG <rect> per calendar month '
    + '(53 cells across the post-2022 window)', async () => {
      renderPage()
      await waitFor(() =>
        expect(screen.getByTestId('regime-band')).toBeInTheDocument())
      const band = screen.getByTestId('regime-band')
      // The band is a hand-rolled SVG (not Recharts BarChart) -- one
      // <rect> per month, positioned by integer index. 53 months span
      // Jan 2022 through May 2026 inclusive. The earlier BarChart
      // variant inherited the 30 rebalance_event positions and
      // mis-spaced the cells against the true monthly time axis.
      const cells = band.querySelectorAll('svg rect')
      expect(cells.length).toBe(53)
    })

  it('reinstates the post-2022 cumulative LineChart above the '
    + 'allocation section', async () => {
      renderPage()
      // The cumulative chart sits above the allocation chart on the
      // same page render. PR #318 had removed it; PR #320 puts it
      // back verbatim from the pre-#318 version.
      await screen.findByText(/Cumulative return, post-2022/i)
    })

  it('hides the regime band silently when /api/v1/charts/data fails',
    async () => {
      mockedAxios.get.mockImplementation((url) => {
        if (String(url).includes('/api/v1/play-by-play')) {
          return Promise.resolve({ data: PLAY_BY_PLAY })
        }
        if (String(url).includes('/api/v1/oos-cost-sensitivity')) {
          return Promise.resolve({ data: COST_SENSITIVITY })
        }
        if (String(url).includes('/api/v1/charts/data')) {
          return Promise.reject(new Error('cold cache'))
        }
        return Promise.resolve({ data: {} })
      })
      renderPage()
      // The main section still renders -- only the band is hidden.
      await screen.findByTestId('implied-allocation-over-time')
      expect(screen.queryByTestId('regime-band'))
        .not.toBeInTheDocument()
    })

  it('shows the empty-state copy when no rebalance_events are cached',
    async () => {
      mockedAxios.get.mockImplementation((url) => {
        if (String(url).includes('/api/v1/play-by-play')) {
          return Promise.resolve({ data: PLAY_BY_PLAY })
        }
        if (String(url).includes('/api/v1/oos-cost-sensitivity')) {
          return Promise.resolve({
            data: {
              available: true,
              cost_sensitivity: {
                ...COST_SENSITIVITY.cost_sensitivity,
                rebalance_events: [],
              },
            },
          })
        }
        return Promise.resolve({ data: CHARTS_DATA })
      })
      renderPage()
      const section = await screen.findByTestId(
        'implied-allocation-over-time')
      expect(section.textContent).toMatch(
        /Implied asset allocation series not yet available/i)
    })
})
