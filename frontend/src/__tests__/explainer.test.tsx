import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import InfoIcon from '../components/InfoIcon'
import ExplainerPanel from '../components/ExplainerPanel'
import ExplainableText from '../components/ExplainableText'
import { EXPLAINER_TOOLTIPS } from '../constants/explainerTooltips'
import { UIProvider } from '../context/UIContext'
import { useGlossaryStore } from '../stores/glossaryStore'

describe('explainerTooltips content', () => {
  it('every tooltip key has non-empty content', () => {
    const keys = Object.keys(EXPLAINER_TOOLTIPS)
    expect(keys.length).toBeGreaterThan(0)
    for (const key of keys) {
      expect(typeof EXPLAINER_TOOLTIPS[key]).toBe('string')
      expect(EXPLAINER_TOOLTIPS[key].trim().length).toBeGreaterThan(0)
    }
  })
})

describe('InfoIcon', () => {
  afterEach(() => { vi.useRealTimers() })

  it('renders the ⓘ icon button', () => {
    render(<InfoIcon tooltipKey="cagr" metricLabel="CAGR" />)
    expect(screen.getByLabelText('Explain CAGR')).toBeInTheDocument()
  })

  it('renders nothing for an unknown tooltip key', () => {
    const { container } = render(
      <InfoIcon tooltipKey="not_a_real_key" metricLabel="Mystery" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the static tooltip on hover after the 300ms delay', () => {
    vi.useFakeTimers()
    render(<InfoIcon tooltipKey="cagr" metricLabel="CAGR" />)
    fireEvent.mouseEnter(screen.getByLabelText('Explain CAGR'))
    act(() => { vi.advanceTimersByTime(300) })
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('Compound Annual Growth Rate')
  })
})

describe('ExplainerPanel', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('calls POST /api/council/explain on mount', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
        }),
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <ExplainerPanel
          metricLabel="Sharpe Ratio"
          currentValue="0.63"
          onClose={() => {}}
        />
      </MemoryRouter>,
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/council/explain')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string).metric).toBe('Sharpe Ratio')
  })
})


describe('ExplainableText — Ask the Council from the click panel', () => {
  // Molly UAT Group 3 — the inline term-explanation panel was missing
  // the Ask-the-Council affordance that lived on both drawer-style
  // explainers (ExplainerPanel + DataExplainPanel). The button is now
  // restored alongside the existing "Learn more · academic context"
  // link, and clicking it navigates to /council with a contextual
  // question pre-filled in route state. This test pins the contract
  // so a future redesign of the term panel cannot silently drop the
  // button again.

  beforeEach(() => {
    // UIProvider reads from sessionStorage on mount — set the key so
    // the Provider renders in Commentary mode. ExplainableText shows
    // chrome only in that mode.
    sessionStorage.setItem('fc_ui_mode', 'commentary')
    // Seed the glossary so the click panel renders. termsLoaded set
    // so loadTerms() does not fire a fetch during the test.
    useGlossaryStore.setState({
      terms: {
        sharpe_ratio: {
          hover: 'Risk-adjusted return — return per unit of volatility.',
          what: 'The Sharpe Ratio is excess return divided by volatility.',
          why: 'It is the standard risk-adjusted performance measure.',
          this_session: 'BENCHMARK 0.52, VOL_TARGETING 1.02.',
        },
      },
      // Stamp termsLastLoadedAt so loadTerms()'s 60s debounce keeps
      // the seeded terms intact rather than firing a fetch and
      // clobbering them with the fallback set.
      termsLastLoadedAt: Date.now(),
    })
  })

  afterEach(() => {
    sessionStorage.removeItem('fc_ui_mode')
    useGlossaryStore.setState({ terms: {}, termsLastLoadedAt: null })
  })

  it('renders the Ask the Council button inside the click panel', () => {
    render(
      <MemoryRouter>
        <UIProvider>
          <ExplainableText term="sharpe_ratio">Sharpe Ratio</ExplainableText>
        </UIProvider>
      </MemoryRouter>,
    )
    // The panel is closed by default — open it by clicking the term.
    fireEvent.click(screen.getByLabelText('Explain sharpe_ratio'))
    // The Learn more link AND the Ask the Council button must both
    // appear in the click panel.
    expect(
      screen.getByText('Learn more · academic context'),
    ).toBeInTheDocument()
    expect(screen.getByText('Ask the Council about this')).toBeInTheDocument()
  })

  it('clicking the button navigates to /council with prefillQuestion', async () => {
    // A small probe surfaces the current location after navigate.
    let observedPath = ''
    let observedState: unknown = null
    const { useLocation } = await import('react-router-dom')
    function LocationProbe() {
      const loc = useLocation()
      observedPath = loc.pathname
      observedState = loc.state
      return null
    }
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <UIProvider>
          <ExplainableText term="sharpe_ratio">Sharpe Ratio</ExplainableText>
        </UIProvider>
        <LocationProbe />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByLabelText('Explain sharpe_ratio'))
    fireEvent.click(screen.getByText('Ask the Council about this'))
    expect(observedPath).toBe('/council')
    const state = observedState as { prefillQuestion: string }
    expect(state.prefillQuestion).toContain('sharpe_ratio')
    expect(state.prefillQuestion).toContain('2022 correlation regime break')
  })
})
