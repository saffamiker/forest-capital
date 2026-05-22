/**
 * analytics-double-affordance.test.tsx
 *
 * FIX 2 — the AcademicAnalytics table headers used to render BOTH the
 * ExplainableText dotted-underline-and-click affordance AND the InfoIcon
 * hover tooltip on the same header (every header that declared both a
 * glossary `term` and an `infoKey`). Two overlapping affordances for the
 * same metric is confusing. The fix suppresses the InfoIcon on headers
 * that already carry an ExplainableText; headers WITHOUT a glossary term
 * still keep the InfoIcon.
 *
 * The test mounts the page with a minimal SummaryRow so the Summary
 * Statistics table renders. It then verifies, on the rendered table
 * column headers:
 *   - CAGR (has term="cagr"): NO "Explain CAGR" button
 *   - Sharpe (has term="sharpe_ratio"): NO "Explain Sharpe Ratio" button
 *   - Excess Return (no term, infoKey only): the InfoIcon IS present
 *   - Annualised Volatility (no term, infoKey only): the InfoIcon IS present
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('axios', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import axios from 'axios'
import AcademicAnalytics from '../pages/AcademicAnalytics'
import { UIProvider } from '../context/UIContext'

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

const SUMMARY_ROW = {
  asset: 'Equity (SPY)',
  cagr: 0.0858,
  excess_return: 0.0,
  ann_volatility: 0.155,
  sharpe_ratio: 0.522,
  information_ratio: null,
  max_drawdown: -0.508,
  skewness: -0.6,
  n_months: 282,
  period_start: '2002-07-01',
  period_end: '2025-12-31',
}

const FACTOR_ROW = {
  strategy: 'BENCHMARK',
  alpha_annualized: 0.0,
  mkt_rf: 1.0,
  smb: 0.02,
  hml: -0.05,
  mom: 0.01,
  r_squared: 0.98,
  alpha_sig: false,
  mkt_rf_sig: true,
  smb_sig: false,
  hml_sig: false,
  mom_sig: false,
  model: 'carhart_4factor',
}

beforeEach(() => {
  mockedAxios.get.mockReset()
  mockedAxios.get.mockImplementation((url: string) => {
    if (url === '/api/v1/analytics/academic') {
      return Promise.resolve({
        data: {
          available: true,
          study_period: { start: '2002-07-01', end: '2025-12-31',
                          n_months: 282 },
          summary_statistics: [SUMMARY_ROW],
          factor_loadings: [FACTOR_ROW],
        },
      })
    }
    if (url === '/api/v1/analytics/sensitivity') {
      return Promise.resolve({
        data: { available: false, strategies: [] },
      })
    }
    if (url === '/api/v1/admin/data-status') {
      // useDataStatus consumes this — must be the full shape, not {}.
      return Promise.resolve({
        data: { available: true, study_period: null, tables: [] },
      })
    }
    return Promise.resolve({ data: {} })
  })
})

describe('AcademicAnalytics — no double affordance on table headers', () => {
  const renderPage = () => render(
    <UIProvider>
      <MemoryRouter><AcademicAnalytics /></MemoryRouter>
    </UIProvider>)

  // Locate a column header <th> by its visible label. The header label is
  // wrapped in ExplainableText (a <span>) when a term is set, so a text
  // match queries that <span>; .closest('th') walks back up to the <th>.
  async function findHeader(text: string | RegExp): Promise<HTMLElement> {
    const label = await screen.findByText(text)
    const th = label.closest('th')
    if (!th) throw new Error(`No <th> ancestor for label "${text}"`)
    return th
  }

  it('does not render InfoIcon on a CAGR header (term + infoKey)',
    async () => {
      renderPage()
      const cagr = await findHeader('CAGR')
      // The Explain button is the InfoIcon — must be ABSENT on a header
      // that is already wrapped in ExplainableText.
      expect(within(cagr).queryByRole(
        'button', { name: /Explain CAGR/i })).toBeNull()
    })

  it('does not render InfoIcon on a Sharpe header (term + infoKey)',
    async () => {
      renderPage()
      const sharpe = await findHeader('Sharpe')
      expect(within(sharpe).queryByRole(
        'button', { name: /Explain Sharpe Ratio/i })).toBeNull()
    })

  it('keeps InfoIcon on Excess Return (infoKey only, no term)',
    async () => {
      renderPage()
      const excess = await findHeader('Excess Return (ann.)')
      // No ExplainableText wrap on this column — the InfoIcon must remain.
      expect(within(excess).getByRole(
        'button', { name: /Explain Excess Return/i })).toBeInTheDocument()
    })

  it('keeps InfoIcon on Annualised Volatility (infoKey only, no term)',
    async () => {
      renderPage()
      const vol = await findHeader('Ann. Volatility')
      expect(within(vol).getByRole(
        'button', { name: /Explain Annualised Volatility/i }))
        .toBeInTheDocument()
    })

  // Molly UAT Group 5 — the Carhart Four-Factor Loadings chart title
  // previously had NO InfoIcon. The per-column ExplainableText wrappers
  // suppressed the InfoIcons on the metric columns AND the title
  // carried no icon at all, so in Analyst and Present mode the chart
  // had no explainer affordance whatsoever. The title-level InfoIcon
  // (tooltipKey "ff_factor_loadings") covers the Carhart model
  // explanation in every mode.
  it('renders the title-level InfoIcon on Carhart Four-Factor Loadings',
    async () => {
      renderPage()
      // The chart title is rendered as an <h2>; the InfoIcon button
      // sits inside that h2 next to the title text.
      const title = await screen.findByText('Carhart Four-Factor Loadings')
      const h2 = title.closest('h2')
      expect(h2).not.toBeNull()
      const explain = within(h2!).getByRole(
        'button', { name: /Explain Carhart Four-Factor Loadings/i })
      expect(explain).toBeInTheDocument()
    })
})
