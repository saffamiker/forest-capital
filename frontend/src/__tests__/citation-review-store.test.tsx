/**
 * citation-review-store.test.tsx — citation panel persistence
 * across navigation + axios auth (May 23 2026).
 *
 * The store backs both within-session persistence (the Zustand
 * cache survives unmount/remount within the same login) AND
 * authenticated requests — it switched from raw fetch() to axios
 * so the X-API-Key header on axios.defaults.headers.common is
 * attached to every /api/v1/citations request.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import axios from 'axios'

import CitationReviewPanel from
  '../components/reportwriter/CitationReviewPanel'
import { useCitationReviewStore, isStale }
  from '../stores/citationReviewStore'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: (err: unknown) => boolean
}


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
      title: 'Coherent measures of risk',
      journal_or_institution: 'University of Milan',
      volume_issue_pages: null,
      url: 'https://www.uni-milan.edu/papers/wp1.pdf',
      verification_status: 'pending_review',
      search_query_used: 'CVaR coherent risk measure',
      alternatives: [],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: null,
    },
  ]
}


beforeEach(() => {
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


describe('CitationReviewPanel — citations persist across unmount', () => {
  it('does NOT re-fetch when remounted within the stale window', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: makeCitations() },
    })

    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId(
      'citation-row-cvar_coherent_risk'))
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)

    unmount()
    render(<CitationReviewPanel generationId={42} />)
    expect(
      screen.getByTestId('citation-row-cvar_coherent_risk'),
    ).toBeTruthy()
    // The cached entry served the second mount — no second fetch.
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('a different generation_id triggers its own fetch', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { citations: makeCitations() },
    })

    const { rerender } = render(
      <CitationReviewPanel generationId={42} />)
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledWith(
      '/api/v1/citations/42'))

    rerender(<CitationReviewPanel generationId={43} />)
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledWith(
      '/api/v1/citations/43'))
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)

    rerender(<CitationReviewPanel generationId={42} />)
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })

  it('no loading flash on remount when cache is warm', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: makeCitations() },
    })

    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId(
      'citation-row-cvar_coherent_risk'))
    unmount()

    const { container } = render(
      <CitationReviewPanel generationId={42} />)
    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeNull()
  })
})


describe('CitationReviewPanel — manual form toggle persists', () => {
  it('open manual form on row stays open after remount', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { citations: makeCitations() },
    })

    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId(
      'citation-manual-toggle-cvar_coherent_risk'))

    fireEvent.click(
      screen.getByTestId(
        'citation-manual-toggle-cvar_coherent_risk'))
    expect(
      screen.getByTestId(
        'citation-manual-author-cvar_coherent_risk'),
    ).toBeTruthy()

    unmount()
    render(<CitationReviewPanel generationId={42} />)
    expect(
      screen.getByTestId(
        'citation-manual-author-cvar_coherent_risk'),
    ).toBeTruthy()
  })

  it('toggle is per-citation (independent rows)', async () => {
    useCitationReviewStore.getState().setManualOpen(1, true)
    useCitationReviewStore.getState().setManualOpen(2, false)
    expect(
      useCitationReviewStore.getState().manualOpenByCitationId[1],
    ).toBe(true)
    expect(
      useCitationReviewStore.getState().manualOpenByCitationId[2],
    ).toBe(false)
  })
})


describe('citationReviewStore — stale-while-revalidate', () => {
  it('load is a no-op when cached entry is within the freshness window', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { citations: makeCitations() },
    })

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.load(42) })
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)

    await act(async () => { await store.load(42) })
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('force=true bypasses the freshness check', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { citations: makeCitations() },
    })

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.load(42) })
    await act(async () => { await store.load(42, { force: true }) })
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })

  it('isStale flags an entry with no timestamp as stale', () => {
    expect(isStale(undefined)).toBe(true)
    expect(isStale(Date.now())).toBe(false)
    expect(isStale(Date.now() - 60 * 60 * 1000)).toBe(true)
  })

  it('upsertCitation replaces a row in place without a refetch', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: makeCitations() },
    })

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.load(42) })

    const updated = {
      ...makeCitations()[1],
      verification_status: 'human_verified',
      review_action: 'accept_untrusted',
      reviewer_email: 'bob@queens.edu',
    }
    act(() => { store.upsertCitation(42, updated) })

    const row = useCitationReviewStore.getState()
      .citationsByGenerationId[42]
      ?.find((c) => c.id === 2)
    expect(row?.verification_status).toBe('human_verified')
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('concurrent load() calls de-dup to one fetch', async () => {
    mockedAxios.get.mockImplementation(() =>
      new Promise((resolve) => {
        setTimeout(() => resolve({
          data: { citations: makeCitations() },
        }), 20)
      }))

    const store = useCitationReviewStore.getState()
    await act(async () => {
      await Promise.all([store.load(42), store.load(42), store.load(42)])
    })
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('uses axios so the X-API-Key auth header is attached', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: { citations: [] },
    })
    const store = useCitationReviewStore.getState()
    await act(async () => { await store.load(42) })
    // Verifies the store uses axios (not raw fetch) — the test
    // would not see the call recorded on mockedAxios.get otherwise.
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/v1/citations/42')
  })
})
