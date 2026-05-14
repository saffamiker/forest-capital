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

const ANALYSIS_FIXTURE: AdvisorAnalysis = {
  key_findings: ['Regime Switching passes all 5 Tier 1 gates.'],
  guidance: ['Lead with the 2022 correlation breakdown.'],
  citations: [
    {
      title: 'Stock-Bond Correlations',
      url: 'https://aqr.com/example',
      relevance: 'Confirms the 2022 regime shift.',
      verified: true,
    },
  ],
  potential_issues: ['Sample size borderline for regime-conditional tests.'],
  verified_sources: [{ title: 'AQR', url: 'https://aqr.com/example', verified: true }],
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

  it('shows the deliverable dropdown with all four options', async () => {
    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    const select = screen.getByTestId('advisor-deliverable-select') as HTMLSelectElement
    expect(select.querySelectorAll('option').length).toBe(4)
  })

  it('submits a request and renders verified citations on success', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    await user.click(screen.getByTestId('advisor-submit-button'))

    expect(await screen.findByTestId('advisor-result')).toBeInTheDocument()
    expect(screen.getByText(/Stock-Bond Correlations/)).toBeInTheDocument()
    // Every citation must show the "verified" affordance.
    expect(screen.getAllByText(/Verified via web search/i).length).toBe(1)
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


// ── Citation integrity at the UI layer ───────────────────────────────────────

describe('AdvisorPanel — citation integrity', () => {
  it('marks every rendered citation as verified', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    await user.click(screen.getByTestId('advisor-submit-button'))

    await screen.findByTestId('advisor-result')
    // Number of "Verified via web search" rows equals number of citations.
    const verifiedLabels = screen.getAllByText(/Verified via web search/i)
    expect(verifiedLabels.length).toBe(ANALYSIS_FIXTURE.citations.length)
  })

  it('renders potential_issues with warning affordance', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: ANALYSIS_FIXTURE })

    const user = userEvent.setup()
    renderInMode('analyst', <AdvisorPanel />)
    await user.click(screen.getByTestId('advisor-floating-button'))
    await user.click(screen.getByTestId('advisor-submit-button'))

    await screen.findByTestId('advisor-result')
    expect(screen.getByText(/Sample size borderline/)).toBeInTheDocument()
    expect(screen.getByText(/Potential issues/i)).toBeInTheDocument()
  })
})
