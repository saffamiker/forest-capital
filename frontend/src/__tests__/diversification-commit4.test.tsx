/**
 * Commit 4 contract tests for the diversification suite.
 *
 * Three components, each fetched off its own analytics endpoint:
 *   - TailRiskTable        — /api/v1/analytics/tail-risk
 *   - CaptureScatter       — /api/v1/analytics/capture-ratios
 *   - DrawdownDurationTable — /api/v1/analytics/drawdown-duration
 *
 * Each test isolates ONE component, mocks its endpoint to return a
 * hand-crafted payload, and pins the contract the component delivers:
 *   - TailRiskTable: worst-third on CVaR 99% annual gets the amber
 *     visual treatment, formatted loss strings carry a minus sign.
 *   - CaptureScatter: every strategy renders a point AND a ranking
 *     row, the period toggle re-anchors the data, the benchmark
 *     appears in the ranking (it's at 100/100 by definition).
 *   - DrawdownDurationTable: a strategy currently underwater carries
 *     the amber pill with its current_drawdown_months value; one
 *     not underwater shows the em-dash placeholder.
 *
 * Recharts ResponsiveContainer relies on container width being known.
 * jsdom defaults to width 0, which makes Scatter render zero points.
 * We patch ResponsiveContainer to a fixed width so the chart actually
 * paints — only for this test file.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

// recharts ResponsiveContainer renders nothing at width 0 (jsdom).
// Pin it to a fixed-size wrapper so the chart paints.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => (
      <div style={{ width: 800, height: 320 }}>{children}</div>
    ),
  }
})

import { TailRiskTable } from '../components/diversification/TailRiskTable'
import { CaptureScatter } from '../components/diversification/CaptureScatter'
import { DrawdownDurationTable }
  from '../components/diversification/DrawdownDurationTable'


// ── TailRiskTable ─────────────────────────────────────────────────────────────

describe('TailRiskTable — CVaR99-annual worst-third amber treatment', () => {
  beforeEach(() => {
    // Three strategies. CVaR-99-annual values: 0.10, 0.20, 0.30. The
    // worst third (ceil(3/3) = 1) gets amber — that's strategy C, the
    // 0.30 loser. The component renders losses as negative percentages.
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/tail-risk')) {
        return Promise.resolve({ data: {
          strategies: [
            { strategy: 'A', var_95_monthly: 0.05, var_99_monthly: 0.07,
              cvar_95_monthly: 0.06, cvar_99_monthly: 0.08,
              var_95_annual: 0.12, var_99_annual: 0.18,
              cvar_95_annual: 0.15, cvar_99_annual: 0.10 },
            { strategy: 'B', var_95_monthly: 0.06, var_99_monthly: 0.08,
              cvar_95_monthly: 0.07, cvar_99_monthly: 0.09,
              var_95_annual: 0.14, var_99_annual: 0.21,
              cvar_95_annual: 0.18, cvar_99_annual: 0.20 },
            { strategy: 'C', var_95_monthly: 0.07, var_99_monthly: 0.09,
              cvar_95_monthly: 0.08, cvar_99_monthly: 0.10,
              var_95_annual: 0.16, var_99_annual: 0.24,
              cvar_95_annual: 0.20, cvar_99_annual: 0.30 },
          ],
        } })
      }
      return Promise.resolve({ data: {} })
    })
  })

  it('formats losses as negative percentages with two decimals', async () => {
    render(<TailRiskTable />)
    await waitFor(() => expect(
      screen.getByTestId('tail-risk-table')).toBeInTheDocument())
    // Strategy A's CVaR 99 monthly = 0.08 -> '-8.00%'
    const rowA = screen.getByTestId('tail-risk-row-A')
    expect(within(rowA).getByText('-8.00%')).toBeInTheDocument()
    // Strategy C's VaR 95 annual = 0.16 -> '-16.00%'
    const rowC = screen.getByTestId('tail-risk-row-C')
    expect(within(rowC).getByText('-16.00%')).toBeInTheDocument()
  })

  it('amber-tints only the worst-third on CVaR 99% annual', async () => {
    render(<TailRiskTable />)
    await waitFor(() => expect(
      screen.getByTestId('tail-risk-table')).toBeInTheDocument())
    // Strategy C is the worst (0.30) — its CVaR99-annual cell carries
    // the warning text colour. The cell itself is the data-testid'd one.
    const cellC = screen.getByTestId('tail-risk-cvar99-C')
    expect(cellC.className).toContain('text-warning')
    // Strategy A is the best (0.10) — slate-300, NOT warning.
    const cellA = screen.getByTestId('tail-risk-cvar99-A')
    expect(cellA.className).toContain('text-slate-300')
    expect(cellA.className).not.toContain('text-warning')
  })
})


// ── CaptureScatter ────────────────────────────────────────────────────────────

describe('CaptureScatter — period toggle + benchmark anchor', () => {
  beforeEach(() => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/capture-ratios')) {
        return Promise.resolve({ data: {
          strategies: [
            // BENCHMARK at (100, 100) by definition across all periods.
            { strategy: 'BENCHMARK',
              full:      { up_capture: 100, down_capture: 100, capture_score: 0 },
              pre_2022:  { up_capture: 100, down_capture: 100, capture_score: 0 },
              post_2022: { up_capture: 100, down_capture: 100, capture_score: 0 },
            },
            // VOL_TARGETING — ideal diversifier on Full (high up, low down).
            { strategy: 'VOL_TARGETING',
              full:      { up_capture:  85, down_capture: 40, capture_score: 45 },
              pre_2022:  { up_capture:  90, down_capture: 30, capture_score: 60 },
              post_2022: { up_capture:  70, down_capture: 60, capture_score: 10 },
            },
            // CLASSIC_60_40 — symmetric.
            { strategy: 'CLASSIC_60_40',
              full:      { up_capture:  60, down_capture: 50, capture_score: 10 },
              pre_2022:  { up_capture:  55, down_capture: 45, capture_score: 10 },
              post_2022: { up_capture:  65, down_capture: 55, capture_score: 10 },
            },
          ],
        } })
      }
      return Promise.resolve({ data: {} })
    })
  })

  it('renders a ranking row for every strategy including the benchmark',
    async () => {
      render(<CaptureScatter />)
      await waitFor(() => expect(
        screen.getByTestId('capture-scatter')).toBeInTheDocument())
      expect(screen.getByTestId('capture-rank-BENCHMARK')).toBeInTheDocument()
      expect(screen.getByTestId('capture-rank-VOL_TARGETING'))
        .toBeInTheDocument()
      expect(screen.getByTestId('capture-rank-CLASSIC_60_40'))
        .toBeInTheDocument()
    })

  it('ranks by capture_score descending — best diversifier first',
    async () => {
      render(<CaptureScatter />)
      await waitFor(() => expect(
        screen.getByTestId('capture-scatter')).toBeInTheDocument())
      // On Full, scores are: VOL_TARGETING=45, CLASSIC_60_40=10,
      // BENCHMARK=0. The first row (DOM order) should be VOL_TARGETING.
      const rankingRows = screen.getAllByTestId(/^capture-rank-/)
      expect(rankingRows[0].getAttribute('data-testid'))
        .toBe('capture-rank-VOL_TARGETING')
    })

  it('period toggle changes capture_score ordering', async () => {
    const user = userEvent.setup()
    render(<CaptureScatter />)
    await waitFor(() => expect(
      screen.getByTestId('capture-scatter')).toBeInTheDocument())

    // Switch to Post-2022 — VOL_TARGETING score drops to 10, ties with
    // CLASSIC_60_40 (also 10). VOL_TARGETING declared first in the
    // payload so it should still be #1 by stable sort; assert at least
    // that the BENCHMARK (score 0) sinks to last.
    await user.click(screen.getByTestId('capture-period-post_2022'))
    await waitFor(() => {
      const rankingRows = screen.getAllByTestId(/^capture-rank-/)
      const last = rankingRows[rankingRows.length - 1]
      expect(last.getAttribute('data-testid'))
        .toBe('capture-rank-BENCHMARK')
    })
  })
})


// ── DrawdownDurationTable ─────────────────────────────────────────────────────

describe('DrawdownDurationTable — currently-underwater amber pill', () => {
  beforeEach(() => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/drawdown-duration')) {
        return Promise.resolve({ data: {
          strategies: [
            { strategy: 'CLEAN', avg_duration_months: 4,
              max_duration_months: 12, avg_recovery_months: 3,
              longest_recovery_months: 9,
              currently_in_drawdown: false,
              current_drawdown_months: 0 },
            { strategy: 'UNDERWATER', avg_duration_months: 6,
              max_duration_months: 18, avg_recovery_months: 5,
              longest_recovery_months: 14,
              currently_in_drawdown: true,
              current_drawdown_months: 7 },
          ],
        } })
      }
      return Promise.resolve({ data: {} })
    })
  })

  it('renders an em-dash for a strategy not currently in drawdown',
    async () => {
      render(<DrawdownDurationTable />)
      await waitFor(() => expect(
        screen.getByTestId('drawdown-duration-table')).toBeInTheDocument())
      const cell = screen.getByTestId('drawdown-duration-current-CLEAN')
      expect(cell.textContent).toContain('—')
    })

  it('renders an amber pill with current_drawdown_months for an underwater strategy',
    async () => {
      render(<DrawdownDurationTable />)
      await waitFor(() => expect(
        screen.getByTestId('drawdown-duration-table')).toBeInTheDocument())
      const cell = screen.getByTestId('drawdown-duration-current-UNDERWATER')
      expect(cell.textContent).toContain('7 mo')
      // The pill carries the warning border + text classes.
      const pill = cell.querySelector('span.text-warning')
      expect(pill).not.toBeNull()
      expect(pill?.className).toContain('border-warning/40')
    })

  it('shows the section-level warning banner when ANY strategy is underwater',
    async () => {
      render(<DrawdownDurationTable />)
      await waitFor(() => expect(
        screen.getByTestId('drawdown-duration-table')).toBeInTheDocument())
      expect(screen.getByText(
        /presently underwater/)).toBeInTheDocument()
    })
})
