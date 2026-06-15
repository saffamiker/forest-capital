/**
 * performance-record-implied-allocation.test.tsx -- June 15 2026.
 *
 * Smoke test for the rebuilt Implied Asset Allocation Over Time
 * section on PerformanceRecord. Replaces the post-2022 cumulative
 * line chart with stacked Equity / IG Bonds / HY Bonds areas
 * (type="stepAfter") plus a regime indicator BarChart band below it.
 *
 * The component had no direct render tests before this PR -- this
 * file pins the section header, legend, and presence of the regime
 * band so a future refactor that drops or renames one of those
 * surfaces flags the change.
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

const CHARTS_DATA = {
  regime_timeline: [
    { date: '2022-01-31', regime: 'BULL' },
    { date: '2022-02-28', regime: 'BULL' },
    { date: '2022-03-31', regime: 'BEAR' },
    { date: '2026-04-30', regime: 'BULL' },
    { date: '2026-05-31', regime: 'TRANSITION' },
  ],
}

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

  it('renders the new section header (not the old cumulative title)',
    async () => {
      renderPage()
      const section = await screen.findByTestId(
        'implied-allocation-over-time')
      expect(section.textContent).toMatch(
        /Implied Asset Allocation Over Time/i)
      // The pre-rebuild "Cumulative return, post-2022" wording is
      // gone -- no other section should pick it up.
      expect(screen.queryByText(/Cumulative return, post-2022/i))
        .not.toBeInTheDocument()
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
