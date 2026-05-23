/**
 * citation-review-panel.test.tsx
 *
 * Covers the CitationReviewPanel — the 7-state citation review
 * workflow. The panel fetches /api/v1/citations/<generation_id>,
 * groups citations by state, and renders the four review actions
 * (accept, reject, select alternative, manual add) for items in a
 * needs-review state.
 *
 * Tests mock axios (the panel + store switched from raw fetch() to
 * axios on May 23 2026 so the X-API-Key session token is attached
 * to every request — fetch() doesn't inherit
 * axios.defaults.headers.common which was causing 401s).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

import CitationReviewPanel from '../components/reportwriter/CitationReviewPanel'
import { useCitationReviewStore } from '../stores/citationReviewStore'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: (err: unknown) => boolean
}


// Citation fixture covering all the states the panel renders for.
function makeCitations() {
  return [
    {
      id: 1, concept_id: 'sharpe_ratio',
      author: 'Sharpe, W. F.', year: '1994',
      title: 'The Sharpe Ratio',
      journal_or_institution: 'Journal of Portfolio Management',
      volume_issue_pages: '21(1), 49-58',
      url: 'https://www.jstor.org/stable/jpm.21.1.49',
      verification_status: 'verified',
      search_query_used: 'sharpe ratio definition',
      alternatives: [],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: 'Sharpe, W. F. (1994). The Sharpe Ratio.',
    },
    {
      id: 2, concept_id: 'cvar_coherent_risk',
      author: 'Acerbi, C.', year: '2002',
      title: 'Coherent measures of risk in a working paper',
      journal_or_institution: 'University of Milan',
      volume_issue_pages: null,
      url: 'https://www.uni-milan.edu/papers/wp1.pdf',
      verification_status: 'pending_review',
      search_query_used: 'CVaR coherent risk measure',
      alternatives: [
        {
          author: 'Rockafellar, R.', year: '2000',
          title: 'Optimization of conditional value-at-risk',
          journal_or_institution: 'Journal of Risk',
          volume_issue_pages: '2(3), 21-41',
          url: 'https://imf.org/papers/cvar.pdf',
          pass_source: 'pass_2_academic',
        },
      ],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: null,
    },
    {
      id: 3, concept_id: 'momentum_factor',
      author: null, year: null, title: null,
      journal_or_institution: null, volume_issue_pages: null,
      url: null,
      verification_status: 'not_found',
      search_query_used: 'momentum factor return',
      alternatives: [],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: null,
    },
  ]
}


beforeEach(() => {
  // Reset the store between tests so cached citations from one
  // test do not leak into the next.
  useCitationReviewStore.getState()._reset()
  mockedAxios.get = vi.fn()
  mockedAxios.post = vi.fn()
  mockedAxios.isAxiosError = (err) =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
})

afterEach(() => {
  vi.clearAllMocks()
  useCitationReviewStore.getState()._reset()
})


describe('CitationReviewPanel — empty state', () => {
  it('renders nothing when generationId is null', () => {
    const { container } = render(
      <CitationReviewPanel generationId={null} />)
    expect(container.firstChild).toBeNull()
  })
})


describe('CitationReviewPanel — fetch and render', () => {
  it('fetches citations on mount and groups by state', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: makeCitations() },
    })

    render(<CitationReviewPanel generationId={42} />)

    await waitFor(() => {
      // Header shows the needs-review count (pending_review +
      // not_found = 2).
      expect(screen.getByText(/2 need.* review/i)).toBeTruthy()
    })

    // Pending row visible.
    expect(screen.getByTestId('citation-row-cvar_coherent_risk'))
      .toBeTruthy()
    // Not-found row visible.
    expect(screen.getByTestId('citation-row-momentum_factor'))
      .toBeTruthy()
    // Verified item is in a collapsed details, not as a row.
    expect(screen.queryByTestId('citation-row-sharpe_ratio'))
      .toBeNull()
    // The verified summary IS rendered.
    expect(screen.getByText(/Verified \(1\)/)).toBeTruthy()
  })

  it('GETs /api/v1/citations/<gen_id> via axios (auth header attached)', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: [] },
    })
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledWith(
        '/api/v1/citations/42')
    })
  })

  it('shows error when the fetch fails', async () => {
    mockedAxios.get.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 500, data: { detail: 'Server error' }},
      message: 'Request failed with status code 500',
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => {
      // Either the detail or the message is surfaced.
      expect(screen.queryByText(/Server error|status code 500/)).toBeTruthy()
    })
  })
})


describe('CitationReviewPanel — actions', () => {
  it('accept_untrusted POSTs the right body and updates state', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: cits },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[1],
          verification_status: 'human_verified',
          reviewer_email: 'bob@queens.edu',
          review_action: 'accept_untrusted',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-accept-cvar_coherent_risk'))

    fireEvent.click(screen.getByTestId('citation-accept-cvar_coherent_risk'))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/citations/2/review',
        { action: 'accept_untrusted' })
    })

    // After accepting, the row moves out of the needs-review bucket.
    await waitFor(() => {
      expect(screen.getByText(/1 need.* review/i)).toBeTruthy()
    })
  })

  it('reject POSTs the right body', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: cits },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[2],
          verification_status: 'rejected',
          review_action: 'reject',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-reject-momentum_factor'))

    fireEvent.click(screen.getByTestId('citation-reject-momentum_factor'))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/citations/3/review',
        { action: 'reject' })
    })
  })

  it('select_alternative POSTs the picked entry', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: cits },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[1],
          verification_status: 'search_selected',
          review_action: 'select_alternative',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-alt-cvar_coherent_risk-0'))

    fireEvent.click(screen.getByTestId('citation-alt-cvar_coherent_risk-0'))

    await waitFor(() => {
      const calls = (mockedAxios.post as ReturnType<typeof vi.fn>).mock.calls
      const lastCall = calls[calls.length - 1]
      expect(lastCall[0]).toBe('/api/v1/citations/2/review')
      expect(lastCall[1].action).toBe('select_alternative')
      expect(lastCall[1].selected_alternative.author).toBe('Rockafellar, R.')
    })
  })

  it('manual_add toggles the form and submits the entered citation', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: cits },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[2],
          verification_status: 'manually_added',
          author: 'Jegadeesh, N.', year: '1993',
          title: 'Returns to Buying Winners',
          review_action: 'manual_add',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-manual-toggle-momentum_factor'))

    // Open the manual form.
    fireEvent.click(
      screen.getByTestId('citation-manual-toggle-momentum_factor'))

    // Fill the three required fields so submit enables.
    fireEvent.change(
      screen.getByTestId('citation-manual-author-momentum_factor'),
      { target: { value: 'Jegadeesh, N.' }})
    const inputs = screen.getAllByPlaceholderText(/Year|Title/i)
    fireEvent.change(inputs[0]!, { target: { value: '1993' }})
    fireEvent.change(inputs[1]!, { target: { value: 'Returns to Buying Winners' }})

    fireEvent.click(
      screen.getByTestId('citation-manual-submit-momentum_factor'))

    await waitFor(() => {
      const calls = (mockedAxios.post as ReturnType<typeof vi.fn>).mock.calls
      const lastCall = calls[calls.length - 1]
      const body = lastCall[1]
      expect(body.action).toBe('manual_add')
      expect(body.manual_citation.author).toBe('Jegadeesh, N.')
      expect(body.manual_citation.year).toBe('1993')
      expect(body.manual_citation.title).toBe('Returns to Buying Winners')
    })
  })
})


describe('CitationReviewPanel — header collapse states', () => {
  it('renders an "All reviewed" badge when no citation needs review', async () => {
    const allDone = makeCitations().map((c) => ({
      ...c, verification_status: 'verified',
    }))
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: allDone },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByText(/All reviewed/i)).toBeTruthy()
    })
  })
})
