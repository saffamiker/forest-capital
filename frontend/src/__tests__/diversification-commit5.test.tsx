/**
 * Commit 5 + 7 contract tests for the diversification suite.
 *
 * Three components, each fetched off its own analytics endpoint:
 *   - CrisisPerformanceTable — /api/v1/analytics/crisis-performance
 *   - RiskContributionBar    — /api/v1/analytics/risk-contribution
 *   - DistributionTable      — /api/v1/analytics/distribution
 *
 * Plus the test-sweep contracts the spec calls out:
 *   - Heatmap renders all 11x11 cells               (commit 3 file)
 *   - Period toggle updates all three heatmap variants (commit 3 file)
 *   - Capture ratio scatter places benchmark at (100, 100) (commit 4 file)
 *   - Crisis table flags partial periods correctly      (this file)
 *   - Distribution table flags non-normal strategies    (this file)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

import { CrisisPerformanceTable }
  from '../components/diversification/CrisisPerformanceTable'
import { RiskContributionBar }
  from '../components/diversification/RiskContributionBar'
import { DistributionTable }
  from '../components/diversification/DistributionTable'


// ── CrisisPerformanceTable — partial flag (commit 7 contract) ─────────────────

describe('CrisisPerformanceTable — partial-window detection', () => {
  beforeEach(() => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/crisis-performance')) {
        return Promise.resolve({ data: {
          windows: {
            'GFC 2008':       { start: '2008-09-01', end: '2009-03-31' },
            '2022 Rate Shock': { start: '2022-01-01', end: '2022-12-31' },
          },
          rows: {
            // BENCHMARK — full coverage on both windows.
            BENCHMARK: {
              'GFC 2008': { cagr: -0.45, max_dd: -0.50, sharpe: -1.2,
                            partial: false, n_months: 7 },
              '2022 Rate Shock': { cagr: -0.18, max_dd: -0.20,
                                   sharpe: -0.9, partial: false, n_months: 12 },
            },
            // REGIME_SWITCHING — partial GFC window (started later).
            REGIME_SWITCHING: {
              'GFC 2008': { cagr: -0.30, max_dd: -0.35, sharpe: -0.8,
                            partial: true, n_months: 4 },
              '2022 Rate Shock': { cagr: -0.05, max_dd: -0.08,
                                   sharpe: -0.4, partial: false, n_months: 12 },
            },
          },
        } })
      }
      return Promise.resolve({ data: {} })
    })
  })

  it('renders a row per strategy and a column per crisis window', async () => {
    render(<CrisisPerformanceTable />)
    await waitFor(() => expect(
      screen.getByTestId('crisis-performance-table')).toBeInTheDocument())
    expect(screen.getByTestId('crisis-row-BENCHMARK')).toBeInTheDocument()
    expect(screen.getByTestId('crisis-row-REGIME_SWITCHING')).toBeInTheDocument()
    // Window date subheaders surface beneath each crisis name.
    expect(screen.getByText('2008-09-01 → 2009-03-31')).toBeInTheDocument()
    expect(screen.getByText('2022-01-01 → 2022-12-31')).toBeInTheDocument()
  })

  it('flags partial periods with the warning icon (commit 7)', async () => {
    render(<CrisisPerformanceTable />)
    await waitFor(() => expect(
      screen.getByTestId('crisis-performance-table')).toBeInTheDocument())

    // BENCHMARK on GFC -- full window, no partial flag.
    const benchGfc = screen.getByTestId('crisis-cell-BENCHMARK-GFC 2008')
    expect(benchGfc.querySelector('[aria-label="partial window"]')).toBeNull()

    // REGIME_SWITCHING on GFC -- partial=true, warning icon present.
    const rsGfc = screen.getByTestId('crisis-cell-REGIME_SWITCHING-GFC 2008')
    expect(rsGfc.querySelector('[aria-label="partial window"]'))
      .not.toBeNull()
    // Tooltip names the partial coverage explicitly.
    expect(rsGfc.getAttribute('title')).toContain('partial window')
    expect(rsGfc.getAttribute('title')).toContain('4 months')
  })

  it('renders "no data" for a missing strategy/window cell', async () => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/crisis-performance')) {
        return Promise.resolve({ data: {
          windows: {
            'GFC 2008':       { start: '2008-09-01', end: '2009-03-31' },
            '2022 Rate Shock': { start: '2022-01-01', end: '2022-12-31' },
          },
          rows: {
            // The strategy only has data for the 2022 window — GFC is missing.
            POST_2022_ONLY: {
              '2022 Rate Shock': { cagr: 0.05, max_dd: -0.03, sharpe: 0.4,
                                   partial: false, n_months: 12 },
            },
          },
        } })
      }
      return Promise.resolve({ data: {} })
    })
    render(<CrisisPerformanceTable />)
    await waitFor(() => expect(
      screen.getByTestId('crisis-performance-table')).toBeInTheDocument())
    // The missing GFC cell renders the "no data" placeholder.
    const row = screen.getByTestId('crisis-row-POST_2022_ONLY')
    expect(within(row).getByText('no data')).toBeInTheDocument()
  })
})


// ── RiskContributionBar — toggle gates on optimizer availability ──────────────

describe('RiskContributionBar — equal-weight + tangency toggle', () => {
  it('renders a row per label with equal-weight contributions',
    async () => {
      mockedAxios.get = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/risk-contribution')) {
          return Promise.resolve({ data: {
            labels: ['A', 'B', 'C', 'D'],
            mctr_equal_weight: [0.10, 0.20, 0.30, 0.40],
            pct_risk_contribution_equal: [10, 20, 30, 40],
            mctr_tangency_weight: [0.15, 0.25, 0.30, 0.30],
            pct_risk_contribution_tangency: [15, 25, 30, 30],
            tangency_weights: [0.20, 0.30, 0.25, 0.25],
          } })
        }
        return Promise.resolve({ data: {} })
      })
      render(<RiskContributionBar />)
      await waitFor(() => expect(
        screen.getByTestId('risk-contribution-bar')).toBeInTheDocument())
      for (const lbl of ['A', 'B', 'C', 'D']) {
        expect(screen.getByTestId(`risk-contribution-row-${lbl}`))
          .toBeInTheDocument()
      }
    })

  it('toggle switches to tangency view when optimizer converged',
    async () => {
      mockedAxios.get = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/risk-contribution')) {
          return Promise.resolve({ data: {
            labels: ['A', 'B'],
            mctr_equal_weight: [0.10, 0.20],
            pct_risk_contribution_equal: [40, 60],
            mctr_tangency_weight: [0.15, 0.25],
            pct_risk_contribution_tangency: [70, 30],
            tangency_weights: [0.50, 0.50],
          } })
        }
        return Promise.resolve({ data: {} })
      })
      const user = userEvent.setup()
      render(<RiskContributionBar />)
      await waitFor(() => expect(
        screen.getByTestId('risk-contribution-bar')).toBeInTheDocument())
      // Equal-weight is the default; A renders at 40.0%.
      const rowA = screen.getByTestId('risk-contribution-row-A')
      expect(rowA.textContent).toContain('40.0%')

      await user.click(screen.getByTestId('risk-contribution-scheme-tangency'))
      // Tangency view — A jumps to 70.0%.
      await waitFor(() => {
        const a = screen.getByTestId('risk-contribution-row-A')
        expect(a.textContent).toContain('70.0%')
      })
    })

  it('disables the tangency toggle when optimizer did not converge',
    async () => {
      mockedAxios.get = vi.fn().mockImplementation((url: string) => {
        if (url.includes('/risk-contribution')) {
          return Promise.resolve({ data: {
            labels: ['A', 'B'],
            mctr_equal_weight: [0.10, 0.20],
            pct_risk_contribution_equal: [40, 60],
            mctr_tangency_weight: null,
            pct_risk_contribution_tangency: null,
            tangency_weights: null,
          } })
        }
        return Promise.resolve({ data: {} })
      })
      render(<RiskContributionBar />)
      await waitFor(() => expect(
        screen.getByTestId('risk-contribution-bar')).toBeInTheDocument())
      const tangencyBtn = screen.getByTestId('risk-contribution-scheme-tangency')
      expect(tangencyBtn).toBeDisabled()
      expect(tangencyBtn.getAttribute('title')).toContain('did not converge')
    })
})


// ── DistributionTable — non-normal flag (commit 7 contract) ───────────────────

describe('DistributionTable — Jarque-Bera normality flag', () => {
  beforeEach(() => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/distribution')) {
        return Promise.resolve({ data: {
          strategies: [
            // Passes normality (JB p > 0.05).
            { strategy: 'NORMAL_STRAT', skewness: -0.10,
              excess_kurtosis: 0.20, jarque_bera_stat: 1.5,
              jarque_bera_p: 0.475, normality_passes: true,
              best_months: [{ date: '2020-04-30', ret: 0.082 }],
              worst_months: [{ date: '2008-10-31', ret: -0.075 }] },
            // Fails normality (JB p < 0.05) — fat-tailed.
            { strategy: 'FAT_TAILED', skewness: -1.50,
              excess_kurtosis: 8.30, jarque_bera_stat: 450.0,
              jarque_bera_p: 0.0008, normality_passes: false,
              best_months: [{ date: '2009-04-30', ret: 0.140 }],
              worst_months: [{ date: '2008-10-31', ret: -0.220 }] },
          ],
        } })
      }
      return Promise.resolve({ data: {} })
    })
  })

  it('marks normal-passing strategies with the positive pill', async () => {
    render(<DistributionTable />)
    await waitFor(() => expect(
      screen.getByTestId('distribution-table')).toBeInTheDocument())
    const cell = screen.getByTestId('distribution-normal-NORMAL_STRAT')
    expect(cell.textContent).toContain('Normal')
    const pill = cell.querySelector('span.text-positive')
    expect(pill).not.toBeNull()
    expect(pill?.className).toContain('border-positive/40')
  })

  it('marks non-normal strategies with the warning pill', async () => {
    render(<DistributionTable />)
    await waitFor(() => expect(
      screen.getByTestId('distribution-table')).toBeInTheDocument())
    const cell = screen.getByTestId('distribution-normal-FAT_TAILED')
    expect(cell.textContent).toContain('Non-normal')
    const pill = cell.querySelector('span.text-warning')
    expect(pill).not.toBeNull()
    expect(pill?.className).toContain('border-warning/40')
  })

  it('shows the section warning banner when any strategy is non-normal',
    async () => {
      render(<DistributionTable />)
      await waitFor(() => expect(
        screen.getByTestId('distribution-table')).toBeInTheDocument())
      expect(screen.getByText(
        /fail the Jarque-Bera normality test/)).toBeInTheDocument()
    })

  it('formats best / worst months as YYYY-MM (return%)', async () => {
    render(<DistributionTable />)
    await waitFor(() => expect(
      screen.getByTestId('distribution-table')).toBeInTheDocument())
    // NORMAL_STRAT best month: 2020-04-30 / 0.082 -> "2020-04 (+8.2%)".
    const row = screen.getByTestId('distribution-row-NORMAL_STRAT')
    expect(row.textContent).toContain('2020-04 (+8.2%)')
    expect(row.textContent).toContain('2008-10 (-7.5%)')
  })

  it('tiny JB p-values render as "<0.001"', async () => {
    render(<DistributionTable />)
    await waitFor(() => expect(
      screen.getByTestId('distribution-table')).toBeInTheDocument())
    // FAT_TAILED has JB p = 0.0008 — below the 0.001 readability floor.
    const row = screen.getByTestId('distribution-row-FAT_TAILED')
    expect(row.textContent).toContain('<0.001')
  })
})
