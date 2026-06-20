/**
 * advisor.test.tsx
 *
 * Tests for the Academic Advisor (Agent 10) frontend bridge:
 *   1. advisorStore caches analyses by deliverable+query and verifications
 *      by finding text.
 *   2. AdvisorPanel renders the floating button on most modes and hides
 *      it (and the panel) entirely in Present mode.
 *   3. AdvisorPanel respects the controlled-open API used by the Reports
 *      screen.
 *   4. Citations that come back are always rendered with the verified
 *      affordance — the frontend never shows an unverified citation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, renderHook, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

import { useAdvisorStore, cacheKeyForAnalysis, cacheKeyForFinding } from '../stores/advisorStore'
import { UIProvider } from '../context/UIContext'
import AdvisorPanel from '../components/AdvisorPanel'
import type { AdvisorAnalysis, AdvisorVerification, AdvisorCitationsResponse } from '../types/advisor'

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

const EXCERPT_TEXT = 'Direct passage from the fetched page corroborating the finding.'

const ANALYSIS_FIXTURE: AdvisorAnalysis = {
  key_findings: ['Regime Switching passes all 5 Tier 1 gates.'],
  guidance: ['Lead with the 2022 correlation breakdown.'],
  citations: [
    {
      title: 'Stock-Bond Correlations',
      url: 'https://aqr.com/example',
      relevance: 'Confirms the 2022 regime shift.',
      excerpt: EXCERPT_TEXT,
      verified: true,
    },
  ],
  potential_issues: ['Sample size borderline for regime-conditional tests.'],
  verified_sources: [{ title: 'AQR', url: 'https://aqr.com/example', verified: true }],
  deliverable_type: 'midpoint',
}

// Same shape but with excerpt=null — simulates the case where web_fetch
// failed or wasn't called for this URL. The UI must show fallback text.
const ANALYSIS_FIXTURE_NO_EXCERPT: AdvisorAnalysis = {
  key_findings: ['Regime Switching passes all 5 Tier 1 gates.'],
  guidance: ['Lead with the 2022 correlation breakdown.'],
  citations: [
    {
      title: 'Paywalled Paper',
      url: 'https://paywalled.example/paper',
      relevance: 'Should be cited but page could not be retrieved.',
      excerpt: null,
      verified: true,
    },
  ],
  potential_issues: [],
  verified_sources: [{ title: 'Paywalled', url: 'https://paywalled.example/paper', verified: true }],
  deliverable_type: 'midpoint',
}

const VERIFY_FIXTURE: AdvisorVerification = {
  supporting_evidence: [
    { title: 'NBER paper', url: 'https://nber.org/example', summary: 'Magnitude is consistent with academic literature.' },
  ],
  contradicting_evidence: [],
  verdict: 'plausible',
  reasoning: 'Consistent with published rate-hike cycle evidence.',
  verified_sources: [{ title: 'NBER', url: 'https://nber.org/example', verified: true }],
}

const CITATIONS_FIXTURE: AdvisorCitationsResponse = {
  citations: [
    {
      title: 'Determinants of Portfolio Performance',
      authors: 'Brinson, Hood, Beebower',
      year: 1986,
      url: 'https://cfainstitute.org/example',
      relevance: 'Foundational attribution paper.',
      excerpt: 'Investment policy explains over 90 percent of return variance for a typical pension fund.',
      verified: true,
    },
  ],
  verified_sources: [{ title: 'CFA', url: 'https://cfainstitute.org/example', verified: true }],
}

beforeEach(() => {
  useAdvisorStore.setState({
    analyses: {},
    verifications: {},
    citationLookups: {},
    inflight: new Set<string>(),
    loading: false,
    error: null,
  })
  mockedAxios.post = vi.fn()
  mockedAxios.isAxiosError = (() => false) as never
})

afterEach(() => {
  vi.clearAllMocks()
})


// ── advisorStore: cache invariants ───────────────────────────────────────────

describe('advisorStore — cache invariants', () => {
  it('analyse() caches by deliverable + query and skips second call', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const { result } = renderHook(() => useAdvisorStore())

    await act(async () => {
      await result.current.analyse('test query', 'midpoint')
    })
    await act(async () => {
      await result.current.analyse('test query', 'midpoint')
    })

    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('analyse() fires a fresh call for a different deliverable', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const { result } = renderHook(() => useAdvisorStore())

    await act(async () => {
      await result.current.analyse('q', 'midpoint')
    })
    await act(async () => {
      await result.current.analyse('q', 'appendix')
    })

    expect(mockedAxios.post).toHaveBeenCalledTimes(2)
  })

  it('verifyFinding() caches by finding text', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: VERIFY_FIXTURE })

    const { result } = renderHook(() => useAdvisorStore())

    await act(async () => {
      await result.current.verifyFinding('Regime Switching Sharpe 0.94')
    })
    await act(async () => {
      await result.current.verifyFinding('Regime Switching Sharpe 0.94')
    })

    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('fetchCitations() caches by finding text', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: CITATIONS_FIXTURE })

    const { result } = renderHook(() => useAdvisorStore())

    await act(async () => {
      await result.current.fetchCitations('FDR correction')
    })
    await act(async () => {
      await result.current.fetchCitations('FDR correction')
    })

    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('clear() empties every cache', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const { result } = renderHook(() => useAdvisorStore())

    await act(async () => {
      await result.current.analyse('q', 'midpoint')
    })
    expect(Object.keys(result.current.analyses).length).toBeGreaterThan(0)

    act(() => result.current.clear())
    expect(Object.keys(result.current.analyses).length).toBe(0)
    expect(Object.keys(result.current.verifications).length).toBe(0)
    expect(Object.keys(result.current.citationLookups).length).toBe(0)
  })

  it('cacheKeyForAnalysis normalises whitespace + case', () => {
    const a = cacheKeyForAnalysis('  Hello World  ', 'midpoint')
    const b = cacheKeyForAnalysis('hello world', 'midpoint')
    expect(a).toBe(b)
  })

  it('cacheKeyForFinding trims and lowercases', () => {
    expect(cacheKeyForFinding('  Some Finding  ')).toBe('some finding')
  })

  it('exposes error state on axios failure', async () => {
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('network'))

    const { result } = renderHook(() => useAdvisorStore())

    await act(async () => {
      await result.current.analyse('q', 'midpoint')
    })

    expect(result.current.error).toBeTruthy()
    expect(result.current.loading).toBe(false)
  })
})


// ── AdvisorPanel: mode visibility ───────────────────────────────────────────

describe('AdvisorPanel — visibility per mode', () => {
  it('renders the floating button in Analyst mode', () => {
    renderInMode('analyst', <AdvisorPanel />)
    expect(screen.getByTestId('advisor-floating-button')).toBeInTheDocument()
  })

  it('renders the floating button in Commentary mode', () => {
    renderInMode('commentary', <AdvisorPanel />)
    expect(screen.getByTestId('advisor-floating-button')).toBeInTheDocument()
  })

  it('hides the floating button in Present mode', () => {
    renderInMode('present', <AdvisorPanel />)
    expect(screen.queryByTestId('advisor-floating-button')).not.toBeInTheDocument()
  })

  it('hides the panel entirely in Present mode even when controlled open', () => {
    renderInMode('present', <AdvisorPanel open onClose={() => undefined} />)
    expect(screen.queryByTestId('advisor-panel')).not.toBeInTheDocument()
  })
})


// ── AdvisorPanel: interaction ────────────────────────────────────────────────

describe('AdvisorPanel — interaction', () => {
  it('opens the panel when the floating button is clicked', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    expect(screen.getByTestId('advisor-panel')).toBeInTheDocument()
  })

  it('shows the deliverable dropdown with the three remaining options',
    async () => {
      // PR #338 retired the midpoint deliverable; the dropdown now
      // carries only appendix / brief / presentation.
      const user = userEvent.setup()
      renderInMode('analyst', <AdvisorPanel />)
      await user.click(screen.getByTestId('advisor-floating-button'))
      const select = screen.getByTestId(
        'advisor-deliverable-select') as HTMLSelectElement
      expect(select.querySelectorAll('option').length).toBe(3)
    })

  it('submits a request and renders verified citations on success', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))

    expect(await screen.findByTestId('advisor-result')).toBeInTheDocument()
    expect(screen.getByText(/Stock-Bond Correlations/)).toBeInTheDocument()
    // Every citation must show the verified affordance. With excerpt
    // present, the row reads "Verified — passage retrieved".
    expect(screen.getAllByText(/Verified — passage retrieved/i).length).toBe(1)
  })

  it('respects controlled open prop', () => {
    renderInMode('analyst', <AdvisorPanel open onClose={() => undefined} />)
    // Floating button hidden when controlled
    expect(screen.queryByTestId('advisor-floating-button')).not.toBeInTheDocument()
    // Panel visible immediately
    expect(screen.getByTestId('advisor-panel')).toBeInTheDocument()
  })

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel open onClose={onClose} />)

    await user.click(screen.getByLabelText('Close advisor panel'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('preselects the deliverable type passed via initialDeliverable', () => {
    renderInMode('analyst', <AdvisorPanel open onClose={() => undefined} initialDeliverable="appendix" />)
    const select = screen.getByTestId('advisor-deliverable-select') as HTMLSelectElement
    expect(select.value).toBe('appendix')
  })
})


// ── Input validation (Phase 12) ──────────────────────────────────────────────
//
// Get Advisor Feedback must be disabled until a non-whitespace query is
// typed. Prevents firing the $0.04-0.06 web-search call against an empty
// string and the model defaulting to a generic placeholder response.

describe('AdvisorPanel — query input validation', () => {
  it('submit button is disabled when the panel first opens (empty query)', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    const submit = screen.getByTestId('advisor-submit-button') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
  })

  it('submit button stays disabled when only whitespace is typed', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    await user.type(screen.getByTestId('advisor-query-input'), '   ')
    const submit = screen.getByTestId('advisor-submit-button') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
  })

  it('submit button enables once a single non-whitespace character is typed', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    await user.type(screen.getByTestId('advisor-query-input'), 'x')
    const submit = screen.getByTestId('advisor-submit-button') as HTMLButtonElement
    expect(submit.disabled).toBe(false)
  })

  it('submit button re-disables if the query is cleared', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    const input = screen.getByTestId('advisor-query-input')
    await user.type(input, 'some text')
    await user.clear(input)
    const submit = screen.getByTestId('advisor-submit-button') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
  })

  it('placeholder text guides the user', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    const input = screen.getByTestId('advisor-query-input') as HTMLTextAreaElement
    expect(input.placeholder).toBe(
      'Ask about your findings, deliverables, or what to focus on...',
    )
  })

  it('clicking submit while disabled never fires an axios call', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Don't type anything — try to click submit anyway.
    await user.click(screen.getByTestId('advisor-submit-button'))
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('submits with the trimmed query (no leading/trailing whitespace)', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    await user.type(screen.getByTestId('advisor-query-input'), '  trimmed query  ')
    await user.click(screen.getByTestId('advisor-submit-button'))

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/advisor/analyse',
      expect.objectContaining({ query: 'trimmed query' }),
    )
  })
})


// ── Citation integrity at the UI layer ───────────────────────────────────────

describe('AdvisorPanel — citation integrity', () => {
  it('marks every rendered citation as verified — excerpt present', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))

    await screen.findByTestId('advisor-result')
    // With excerpt present, the row reads "Verified — passage retrieved".
    expect(screen.getByText(/passage retrieved/i)).toBeInTheDocument()
  })

  it('marks citation with missing excerpt as not retrievable', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE_NO_EXCERPT })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))

    await screen.findByTestId('advisor-result')
    expect(screen.getByText(/passage not retrievable/i)).toBeInTheDocument()
  })

  it('renders potential_issues with warning affordance', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))

    await screen.findByTestId('advisor-result')
    expect(screen.getByText(/Sample size borderline/)).toBeInTheDocument()
    expect(screen.getByText(/Potential issues/i)).toBeInTheDocument()
  })
})


// ── Excerpt tooltip rendering ────────────────────────────────────────────────

describe('AdvisorPanel — excerpt tooltip', () => {
  async function renderResultAndHover(fixture: AdvisorAnalysis) {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: fixture })
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))
    await screen.findByTestId('advisor-result')
    const link = screen.getByTestId('advisor-citation-link')
    await user.hover(link)
    return { user, link }
  }

  it('renders the excerpt in a tooltip on hover when fetch succeeded', async () => {
    await renderResultAndHover(ANALYSIS_FIXTURE)
    const tooltip = await screen.findByTestId('advisor-citation-tooltip')
    expect(tooltip).toBeInTheDocument()
    expect(tooltip.textContent).toContain(EXCERPT_TEXT)
  })

  it('renders the fallback message on hover when excerpt is null', async () => {
    await renderResultAndHover(ANALYSIS_FIXTURE_NO_EXCERPT)
    const tooltip = await screen.findByTestId('advisor-citation-tooltip')
    expect(tooltip.textContent).toContain('Excerpt unavailable')
    expect(tooltip.textContent).toContain('click to verify directly')
  })

  it('citation link opens the source in a new tab', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))
    await screen.findByTestId('advisor-result')

    const link = screen.getByTestId('advisor-citation-link') as HTMLAnchorElement
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('sets the title attribute to the excerpt for native fallback / a11y', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))
    await screen.findByTestId('advisor-result')

    const link = screen.getByTestId('advisor-citation-link') as HTMLAnchorElement
    expect(link.getAttribute('title')).toBe(EXCERPT_TEXT)
  })

  it('title attribute falls back to "Excerpt unavailable" when excerpt is null', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE_NO_EXCERPT })
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    // Submit is disabled until a non-whitespace query is typed.
    await user.type(screen.getByTestId('advisor-query-input'), 'midpoint guidance')
    await user.click(screen.getByTestId('advisor-submit-button'))
    await screen.findByTestId('advisor-result')

    const link = screen.getByTestId('advisor-citation-link') as HTMLAnchorElement
    expect(link.getAttribute('title')).toContain('Excerpt unavailable')
  })

  it('hides the tooltip on mouse leave', async () => {
    const { user, link } = await renderResultAndHover(ANALYSIS_FIXTURE)
    expect(screen.getByTestId('advisor-citation-tooltip')).toBeInTheDocument()
    await user.unhover(link)
    expect(screen.queryByTestId('advisor-citation-tooltip')).not.toBeInTheDocument()
  })
})
