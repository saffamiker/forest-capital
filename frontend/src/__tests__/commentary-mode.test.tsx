/**
 * commentary-mode.test.tsx
 *
 * Verifies the three Commentary-mode invariants that this sprint added:
 *
 *   1. glossaryStore is idempotent — hovering 50 metrics fires one
 *      /api/explain/terms call, not 50.
 *   2. ExplainableText renders chrome only in Commentary mode. In
 *      Analyst or Present mode the children are emitted unchanged.
 *   3. ChartCommentStrip always renders the Sources line (when
 *      provenance exists) regardless of mode, but only renders the
 *      narrative body in Commentary / Present mode.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, renderHook, screen, act } from '@testing-library/react'
import axios from 'axios'

import { useGlossaryStore } from '../stores/glossaryStore'
import { UIProvider } from '../context/UIContext'
import ExplainableText from '../components/ExplainableText'
import ChartCommentStrip from '../components/ChartCommentStrip'
import LearnModeBanner from '../components/LearnModeBanner'
import { useProvenanceStore } from '../stores/provenanceStore'
import { useStrategiesStore } from '../stores/strategiesStore'

// Render-helper: every Commentary-mode component reads useUI(), so we wrap
// in UIProvider. The provider reads sessionStorage on init, which is how
// each test sets the active mode.
function renderInMode(mode: 'analyst' | 'commentary' | 'present', node: ReactNode) {
  sessionStorage.setItem('fc_ui_mode', mode)
  return render(<UIProvider>{node}</UIProvider>)
}

vi.mock('axios')
const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
}

beforeEach(() => {
  useGlossaryStore.setState({
    terms: {}, parameters: {}, personas: {}, qa: {}, charts: {},
    termsLoaded: false, termsLoading: false, inflight: new Set<string>(),
  })
  useProvenanceStore.setState({
    series: {}, crossValidation: null, lastPipelineRun: null, loading: false, error: null,
  })
  useStrategiesStore.setState({
    strategies: [], dataRange: null, loading: false, error: null,
    loaded: false, lastFetchedAt: null,
  })
  mockedAxios.post = vi.fn().mockResolvedValue({ data: {
    sharpe_ratio: { hover: 'Return per unit of risk', what: 'Excess return / volatility', why: 'Lets us compare strategies on equal footing' },
  }})
  mockedAxios.isAxiosError = (() => false) as never
})

afterEach(() => {
  vi.restoreAllMocks()
  // Force Analyst mode between tests — UIContext is module-level via React
  // context, so we reset via sessionStorage which UIContext reads on init.
  sessionStorage.clear()
})


describe('glossaryStore.loadTerms()', () => {
  it('fires exactly one /api/explain/terms call across N invocations', async () => {
    const { result } = renderHook(() => useGlossaryStore())
    await act(async () => { await result.current.loadTerms() })
    await act(async () => { await result.current.loadTerms() })
    await act(async () => { await result.current.loadTerms() })
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/explain/terms', expect.any(Object))
  })

  it('populates terms after a successful load', async () => {
    const { result } = renderHook(() => useGlossaryStore())
    await act(async () => { await result.current.loadTerms({ significant_strategies: [] }) })
    expect(result.current.terms.sharpe_ratio).toBeDefined()
    expect(result.current.terms.sharpe_ratio.hover).toBe('Return per unit of risk')
    expect(result.current.termsLoaded).toBe(true)
  })

  it('fails silent and leaves terms empty when the endpoint errors', async () => {
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('500'))
    const { result } = renderHook(() => useGlossaryStore())
    await act(async () => { await result.current.loadTerms() })
    expect(result.current.terms).toEqual({})
    expect(result.current.termsLoaded).toBe(true)   // still marked loaded so we don't retry
  })
})


describe('glossaryStore.loadChart()', () => {
  it('caches per chart_id — second request for same id is a no-op', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {
      chart_id: 'cpcv', hover_summary: 'Sharpe distribution across CPCV paths',
      purpose: '', how_to_read: '', key_callouts: [], narrative: '', what_to_watch: '',
    }})
    const { result } = renderHook(() => useGlossaryStore())
    await act(async () => { await result.current.loadChart('cpcv', 'box_plot', {}, {}) })
    await act(async () => { await result.current.loadChart('cpcv', 'box_plot', {}, {}) })
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('different chart_ids trigger independent requests', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {
      chart_id: 'x', hover_summary: '', purpose: '', how_to_read: '',
      key_callouts: [], narrative: '', what_to_watch: '',
    }})
    const { result } = renderHook(() => useGlossaryStore())
    await act(async () => { await result.current.loadChart('cpcv', 'box_plot', {}, {}) })
    await act(async () => { await result.current.loadChart('radar', 'radar', {}, {}) })
    expect(mockedAxios.post).toHaveBeenCalledTimes(2)
  })
})


describe('ExplainableText mode-conditional rendering', () => {
  it('renders children unchanged in Analyst mode (no underline, no icon)', () => {
    // Pre-seed the glossary so the only reason for "no chrome" is the mode.
    useGlossaryStore.setState({
      terms: { sharpe_ratio: { hover: 'h', what: 'w', why: 'w' } },
      termsLoaded: true, termsLoading: false,
    })
    renderInMode('analyst', <ExplainableText term="sharpe_ratio">SHARPE</ExplainableText>)
    expect(screen.getByText('SHARPE')).toBeInTheDocument()
    // No info button — chrome is absent in Analyst mode.
    expect(screen.queryByLabelText(/Explain sharpe_ratio/i)).not.toBeInTheDocument()
  })

  it('renders interactive chrome in Commentary mode when glossary entry exists', () => {
    useGlossaryStore.setState({
      terms: { sharpe_ratio: { hover: 'h', what: 'w', why: 'w' } },
      termsLoaded: true, termsLoading: false,
    })
    renderInMode('commentary', <ExplainableText term="sharpe_ratio">SHARPE</ExplainableText>)
    expect(screen.getByText('SHARPE')).toBeInTheDocument()
    expect(screen.getByLabelText(/Explain sharpe_ratio/i)).toBeInTheDocument()
  })

  it('renders muted state in Commentary mode when glossary entry is missing', () => {
    renderInMode('commentary', <ExplainableText term="unknown_term">VALUE</ExplainableText>)
    // Children render, but no clickable explain button.
    expect(screen.getByText('VALUE')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Explain unknown_term/i)).not.toBeInTheDocument()
  })
})


describe('LearnModeBanner', () => {
  it('renders nothing in Analyst mode', () => {
    const { container } = renderInMode('analyst', <LearnModeBanner />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the explanatory banner in Commentary mode', () => {
    renderInMode('commentary', <LearnModeBanner />)
    expect(screen.getByTestId('learn-mode-banner')).toBeInTheDocument()
    expect(screen.getByText(/Commentary mode/i)).toBeInTheDocument()
  })
})


describe('ChartCommentStrip', () => {
  it('renders nothing when no provenance and Analyst mode', () => {
    const { container } = renderInMode(
      'analyst',
      <ChartCommentStrip chartId="nonexistent_chart_id_xyz" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders Sources line even in Analyst mode when provenance exists', () => {
    // Seed provenance for a chart in the registry
    useProvenanceStore.setState({
      series: {
        equity_monthly: {
          series_id: 'equity_monthly',
          display_name: 'S&P 500 Monthly Returns',
          source_type: 'excel_provided',
          source_detail: {
            file: 'FNA_670.xlsx', sheet: 'S&P', provided_by: 'Dr. Panttser',
            original_source: 'Y-charts',
          },
          frequency: 'monthly',
          date_range_start: '2002-01-01',
          date_range_end: '2024-12-31',
          row_count: 282,
          loaded_at: '2026-05-13',
          validation_status: 'pass',
        } as never,
      },
      crossValidation: null, lastPipelineRun: null, loading: false, error: null,
    })
    renderInMode('analyst', <ChartCommentStrip chartId="cumulative_returns" />)
    // Sources line visible — provenance is presentation-critical in all modes.
    expect(screen.getByText('Sources')).toBeInTheDocument()
  })

  it('triggers /api/explain/chart in Commentary mode and caches the result', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {
      chart_id: 'cumulative_returns', hover_summary: 'Growth of $1 across strategies',
      purpose: 'p', how_to_read: 'h', key_callouts: [], narrative: 'n', what_to_watch: 'w',
    }})
    renderInMode(
      'commentary',
      <ChartCommentStrip chartId="cumulative_returns" chartType="line_cumulative" chartData={{}} />,
    )
    // Allow the useEffect to flush
    await act(async () => { await Promise.resolve() })
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/explain/chart', expect.objectContaining({
      chart_id: 'cumulative_returns',
    }))

    // Re-render: cached, no new request
    renderInMode(
      'commentary',
      <ChartCommentStrip chartId="cumulative_returns" chartType="line_cumulative" chartData={{}} />,
    )
    await act(async () => { await Promise.resolve() })
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('does not call /api/explain/chart in Analyst mode', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
    renderInMode(
      'analyst',
      <ChartCommentStrip chartId="cumulative_returns" chartType="line" chartData={{}} />,
    )
    await act(async () => { await Promise.resolve() })
    // No explainer call in Analyst — the strip body is hidden so fetching
    // would be wasted bandwidth.
    // Use a non-destructuring predicate: mock.calls is typed as any[][] and
    // each entry "may have fewer than 1 element" by TS's typing, so the
    // destructuring tuple pattern ([url]: [string]) is rejected at compile
    // time. Indexing the array inside the function body works because we
    // never look up an element that doesn't exist — calls without args
    // simply have call[0] === undefined and fail the equality check.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const explainCalls = mockedAxios.post.mock.calls.filter(
      (call: any[]) => call[0] === '/api/explain/chart',
    )
    expect(explainCalls.length).toBe(0)
  })
})


describe('ExplainableText hover cost optimisation', () => {
  it('hovering 10 different metrics fires /api/explain/terms exactly once', async () => {
    // The cache invariant: every metric on the dashboard wraps in
    // ExplainableText, but only one HTTP call goes out per session
    // regardless of how many components mount. Without this guarantee,
    // a busy dashboard with 50 wrapped values would fire 50 LLM
    // requests in parallel — exactly the cost regression this test
    // exists to catch.
    const TERMS = [
      'sharpe_ratio', 'cagr', 'max_drawdown', 'fdr', 'dsr',
      'cv_stability', 'p_value_ttest', 'oos_sharpe', 'alpha', 'beta',
    ]

    // Seed the store with one term so the consumer doesn't render the
    // muted-state path; the load() call we're counting fires
    // independently when the component mounts.
    useGlossaryStore.setState({
      terms: { sharpe_ratio: { hover: 'h', what: 'w', why: 'w' } },
      termsLoaded: false,    // not yet loaded — load() will fire
      termsLoading: false,
    })

    renderInMode(
      'commentary',
      <>
        {TERMS.map((t) => (
          <ExplainableText key={t} term={t}>{t}</ExplainableText>
        ))}
      </>,
    )

    // Allow the useEffect microtasks for all 10 components to flush.
    await act(async () => { await Promise.resolve() })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const termCalls = mockedAxios.post.mock.calls.filter(
      (call: any[]) => call[0] === '/api/explain/terms',
    )
    expect(termCalls.length).toBe(1)
  })

  it('subsequent re-renders of the same ExplainableText do not refire load', async () => {
    // Once termsLoaded=true, the store's guard short-circuits every
    // future call. This test catches the regression where someone
    // accidentally drops the loaded check and re-fetches on every
    // hover.
    useGlossaryStore.setState({
      terms: { sharpe_ratio: { hover: 'h', what: 'w', why: 'w' } },
      termsLoaded: true,     // pre-marked loaded → load() should be a no-op
      termsLoading: false,
    })

    const { rerender } = renderInMode(
      'commentary',
      <ExplainableText term="sharpe_ratio">SHARPE</ExplainableText>,
    )
    await act(async () => { await Promise.resolve() })
    // rerender replaces the entire element including the UIProvider wrapper;
    // re-supply the provider so the second render still has context.
    rerender(
      <UIProvider>
        <ExplainableText term="sharpe_ratio">SHARPE</ExplainableText>
      </UIProvider>,
    )
    await act(async () => { await Promise.resolve() })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const termCalls = mockedAxios.post.mock.calls.filter(
      (call: any[]) => call[0] === '/api/explain/terms',
    )
    expect(termCalls.length).toBe(0)
  })

  it('ExplainableText does not fire load in Analyst mode (no chrome rendered)', async () => {
    // Cost guardrail: don't pay for explanations the user can't see.
    useGlossaryStore.setState({
      terms: {}, termsLoaded: false, termsLoading: false,
    })
    renderInMode(
      'analyst',
      <ExplainableText term="sharpe_ratio">SHARPE</ExplainableText>,
    )
    await act(async () => { await Promise.resolve() })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const termCalls = mockedAxios.post.mock.calls.filter(
      (call: any[]) => call[0] === '/api/explain/terms',
    )
    expect(termCalls.length).toBe(0)
  })
})
