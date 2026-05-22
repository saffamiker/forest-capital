/**
 * CorrelationHeatmap — the "Strategy Correlations" section on /analytics.
 *
 * Pins the contract for the heatmap shipped with item 8 commit 3:
 *   - Renders an 11x11 grid of off-diagonal cells (10 strategies + benchmark).
 *   - Period toggle (Full / Pre-2022 / Post-2022) actually swaps the data.
 *   - Diagonal cells display '1.00' and use the neutral-grey background
 *     rather than the deep red endpoint of the diverging colour scale
 *     (a strategy correlated with itself is trivially +1.00 and should
 *     never dominate visual attention).
 *   - Insight callout reads the lowest and highest non-self correlation
 *     from the matrix shown and re-renders with the period toggle.
 *
 * The hook is axios-mocked at the module level so the test never hits
 * a real endpoint; the matrix payload is hand-crafted so the assertions
 * can verify the exact pair the insight callout should surface.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

import { CorrelationHeatmap } from '../components/diversification/CorrelationHeatmap'

// ── Test payload ──────────────────────────────────────────────────────────────

// Eleven labels — ten strategies plus the benchmark. The exact names don't
// matter to the contract; the shape does.
const LABELS = [
  'BENCHMARK', 'CLASSIC_60_40', 'RISK_PARITY', 'MIN_VARIANCE',
  'EQUAL_WEIGHT', 'MOMENTUM_ROTATION', 'REGIME_SWITCHING',
  'VOL_TARGETING', 'BLACK_LITTERMAN', 'MAX_SHARPE_ROLLING',
  'BENCHMARK_DUPL',  // 11th — a sanity placeholder for the 11×11 contract
]

/** Builds an N×N symmetric matrix with 1.0 on the diagonal and a
 *  caller-provided value everywhere else. */
function uniformMatrix(n: number, off: number): (number | null)[][] {
  return Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => i === j ? 1.0 : off))
}

/** Override a single off-diagonal cell symmetrically. */
function setCell(m: (number | null)[][], i: number, j: number,
                  value: number): (number | null)[][] {
  const copy = m.map(row => [...row])
  copy[i][j] = value
  copy[j][i] = value
  return copy
}

beforeEach(() => {
  // Distinct matrices per period so the toggle's effect is observable
  // by the insight callout.
  const fullBase = uniformMatrix(11, 0.40)
  // Lowest pair on Full: cell (1, 5) — CLASSIC_60_40 vs MOMENTUM_ROTATION at -0.20.
  const full = setCell(setCell(fullBase, 1, 5, -0.20),
    // Highest non-self on Full: cell (6, 8) — REGIME_SWITCHING vs BLACK_LITTERMAN at 0.85.
    6, 8, 0.85)

  const preBase = uniformMatrix(11, 0.30)
  // Lowest on Pre-2022: cell (2, 7) — RISK_PARITY vs VOL_TARGETING at -0.55.
  const pre = setCell(preBase, 2, 7, -0.55)

  const postBase = uniformMatrix(11, 0.65)
  // Highest on Post-2022: cell (3, 9) — MIN_VARIANCE vs MAX_SHARPE_ROLLING at 0.95.
  const post = setCell(postBase, 3, 9, 0.95)

  mockedAxios.get = vi.fn().mockResolvedValue({
    data: {
      labels: LABELS,
      full,
      pre_2022: pre,
      post_2022: post,
      diagonal: 1.0,
    },
  })
})

// ── Contract tests ────────────────────────────────────────────────────────────

describe('CorrelationHeatmap — 11x11 grid and diagonal', () => {
  it('renders an 11x11 grid of cells (121 cells, 11 of them diagonal)', async () => {
    render(<CorrelationHeatmap />)

    // Hot path is sub-ms but axios resolves asynchronously — wait for
    // the grid to mount.
    await waitFor(() => expect(
      screen.getByTestId('correlation-heatmap')).toBeInTheDocument())

    // 11 × 11 = 121 cells, indexed by (i, j) data-testid.
    for (let i = 0; i < 11; i++) {
      for (let j = 0; j < 11; j++) {
        expect(screen.getByTestId(`correlation-cell-${i}-${j}`))
          .toBeInTheDocument()
      }
    }
  })

  it('diagonal cells display 1.00 and never compete with the +1.0 red endpoint',
    async () => {
      render(<CorrelationHeatmap />)
      await waitFor(() => expect(
        screen.getByTestId('correlation-heatmap')).toBeInTheDocument())

      // Each (i, i) cell shows 1.00 in neutral grey — not the red
      // endpoint of the diverging scale. The grey is rgb(71, 85, 105)
      // (slate-600) — distinct from the deep red rgb(185, 28, 28).
      for (let i = 0; i < 11; i++) {
        const cell = screen.getByTestId(`correlation-cell-${i}-${i}`)
        expect(cell.textContent).toBe('1.00')
        const bg = (cell as HTMLElement).style.backgroundColor
        // Whitespace differences across browsers normalised by the
        // computed style — match on the slate-600 RGB triple.
        expect(bg.replace(/\s+/g, '')).toContain('rgb(71,85,105)')
      }
    })
})

describe('CorrelationHeatmap — period toggle swaps the data', () => {
  it('insight callout updates when the period changes', async () => {
    const user = userEvent.setup()
    render(<CorrelationHeatmap />)

    await waitFor(() => expect(
      screen.getByTestId('correlation-insight')).toBeInTheDocument())

    // Full period: lowest pair = CLASSIC_60_40 / MOMENTUM_ROTATION at -0.20.
    // Highest = REGIME_SWITCHING / BLACK_LITTERMAN at 0.85.
    {
      const insight = screen.getByTestId('correlation-insight')
      expect(insight.textContent).toContain('CLASSIC_60_40')
      expect(insight.textContent).toContain('MOMENTUM_ROTATION')
      expect(insight.textContent).toContain('-0.20')
      expect(insight.textContent).toContain('REGIME_SWITCHING')
      expect(insight.textContent).toContain('BLACK_LITTERMAN')
      expect(insight.textContent).toContain('0.85')
    }

    // Click the Pre-2022 button — lowest pair becomes
    // RISK_PARITY / VOL_TARGETING at -0.55.
    await user.click(screen.getByTestId('correlation-period-pre_2022'))
    await waitFor(() => {
      const insight = screen.getByTestId('correlation-insight')
      expect(insight.textContent).toContain('RISK_PARITY')
      expect(insight.textContent).toContain('VOL_TARGETING')
      expect(insight.textContent).toContain('-0.55')
    })

    // Click the Post-2022 button — highest pair becomes
    // MIN_VARIANCE / MAX_SHARPE_ROLLING at 0.95.
    await user.click(screen.getByTestId('correlation-period-post_2022'))
    await waitFor(() => {
      const insight = screen.getByTestId('correlation-insight')
      expect(insight.textContent).toContain('MIN_VARIANCE')
      expect(insight.textContent).toContain('MAX_SHARPE_ROLLING')
      expect(insight.textContent).toContain('0.95')
    })
  })

  it('cells re-render with the new period matrix values', async () => {
    const user = userEvent.setup()
    render(<CorrelationHeatmap />)
    await waitFor(() => expect(
      screen.getByTestId('correlation-heatmap')).toBeInTheDocument())

    // On Full, cell (1, 5) = -0.20.
    expect(screen.getByTestId('correlation-cell-1-5').textContent).toBe('-0.20')

    await user.click(screen.getByTestId('correlation-period-pre_2022'))
    // On Pre-2022, cell (1, 5) is back to the uniform 0.30 baseline.
    await waitFor(() =>
      expect(screen.getByTestId('correlation-cell-1-5').textContent).toBe('0.30'))

    await user.click(screen.getByTestId('correlation-period-post_2022'))
    // On Post-2022, cell (1, 5) is the uniform 0.65 baseline.
    await waitFor(() =>
      expect(screen.getByTestId('correlation-cell-1-5').textContent).toBe('0.65'))
  })
})
