/**
 * citation-review-store.test.tsx — citation panel persistence
 * across navigation + axios auth (May 23 2026).
 *
 * Updated May 26 2026 — the redesigned panel now drives off
 * GET /api/v1/citations/findings/{id} (migration 045). The
 * legacy GET /api/v1/citations/{id} is still wired through
 * the store's load() method, which the store-only tests
 * exercise directly. The panel tests in this file mock the
 * new findings endpoint.
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
  delete: ReturnType<typeof vi.fn>
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
      supporting_extract: null,
      selection_rationale: null,
      confidence_score: null,
      finding_supported: null,
      citation_type: 'theoretical',
      trust_flag: null,
      scoring_rationale: null,
      matched_finding_ids: [] as number[],
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
      supporting_extract: null,
      selection_rationale: null,
      confidence_score: null,
      finding_supported: null,
      citation_type: 'methodological',
      trust_flag: null,
      scoring_rationale: null,
      matched_finding_ids: [] as number[],
    },
  ]
}


// Helper — wraps the citations in a single Level-1 finding so the
// new panel layout has a section under which the citation rows
// render. Without a finding the redesigned panel renders nothing.
function makeFindingsResponse(generationId: number) {
  return {
    data: {
      generation_id: generationId,
      seeded_at: new Date().toISOString(),
      findings: [
        {
          id: 9001,
          source: 'audit' as const,
          source_id: 'D04',
          title: 'Strategy return-series coverage',
          description: 'Splice junction date not exposed.',
          rank: 'high' as const,
          status: 'warning',
          severity: 'warning',
          matched_count: 0,
        },
      ],
      citations: makeCitations(),
    },
  }
}


beforeEach(() => {
  useCitationReviewStore.getState()._reset()
  mockedAxios.get    = vi.fn()
  mockedAxios.post   = vi.fn()
  mockedAxios.delete = vi.fn()
  mockedAxios.isAxiosError = (err) =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
})

afterEach(() => {
  vi.clearAllMocks()
  useCitationReviewStore.getState()._reset()
})


describe('CitationReviewPanel — citations persist across unmount', () => {
  it('does NOT re-fetch when remounted within the stale window', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))

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
    mockedAxios.get
      .mockResolvedValueOnce(makeFindingsResponse(42))
      .mockResolvedValueOnce(makeFindingsResponse(43))

    const { rerender } = render(
      <CitationReviewPanel generationId={42} />)
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledWith(
      '/api/v1/citations/findings/42'))

    rerender(<CitationReviewPanel generationId={43} />)
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledWith(
      '/api/v1/citations/findings/43'))
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)

    rerender(<CitationReviewPanel generationId={42} />)
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })

  it('no loading flash on remount when cache is warm', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))

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
    mockedAxios.get.mockResolvedValue(makeFindingsResponse(42))

    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
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


describe('citationReviewStore — stale-while-revalidate (legacy load)', () => {
  // The legacy load() method against /api/v1/citations/{id} is still
  // exposed on the store for any caller that wants citations without
  // findings context. These tests pin its contract.
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
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/v1/citations/42')
  })
})


// ── loadFindings — the new endpoint backing the redesigned panel ────────────


describe('citationReviewStore — loadFindings', () => {
  it('GETs /api/v1/citations/findings/{id} and populates both slices', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    const store = useCitationReviewStore.getState()
    await act(async () => { await store.loadFindings(42) })

    expect(mockedAxios.get).toHaveBeenCalledWith(
      '/api/v1/citations/findings/42')
    const st = useCitationReviewStore.getState()
    expect(st.findingsByGenerationId[42]?.length).toBe(1)
    expect(st.citationsByGenerationId[42]?.length).toBe(2)
  })

  it('is a no-op within the stale window, force=true bypasses it', async () => {
    mockedAxios.get.mockResolvedValue(makeFindingsResponse(42))
    const store = useCitationReviewStore.getState()

    await act(async () => { await store.loadFindings(42) })
    await act(async () => { await store.loadFindings(42) })
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)

    await act(async () => { await store.loadFindings(42, { force: true }) })
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })

  it('records error on a failed fetch', async () => {
    mockedAxios.get.mockRejectedValueOnce({
      isAxiosError: true,
      response: { data: { detail: 'boom' }},
      message: 'Request failed',
    })
    const store = useCitationReviewStore.getState()
    await act(async () => { await store.loadFindings(42) })
    expect(
      useCitationReviewStore.getState()
        .findingsErrorByGenerationId[42],
    ).toBe('boom')
  })
})


// ── toggleMatch — optimistic update + revert on failure ─────────────────────


describe('citationReviewStore — toggleMatch', () => {
  it('POSTs to /match when adding a match, increments matched_count', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    mockedAxios.post.mockResolvedValueOnce({
      data: { matched: true },
    })
    const store = useCitationReviewStore.getState()
    await act(async () => { await store.loadFindings(42) })
    await act(async () => {
      await store.toggleMatch(42, 1, 9001, false)
    })

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/v1/citations/match',
      { citation_id: 1, finding_id: 9001 })
    const st = useCitationReviewStore.getState()
    expect(
      st.citationsByGenerationId[42]?.find((c) => c.id === 1)
        ?.matched_finding_ids).toEqual([9001])
    expect(
      st.findingsByGenerationId[42]?.[0]?.matched_count).toBe(1)
  })

  it('DELETEs /match when removing a match, decrements matched_count', async () => {
    // Pre-load with citation 1 already matched to finding 9001.
    const resp = makeFindingsResponse(42)
    resp.data.citations[0]!.matched_finding_ids = [9001]
    resp.data.findings[0]!.matched_count = 1
    mockedAxios.get.mockResolvedValueOnce(resp)
    mockedAxios.delete.mockResolvedValueOnce({ data: { removed: true }})

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.loadFindings(42) })
    await act(async () => {
      await store.toggleMatch(42, 1, 9001, true)
    })

    expect(mockedAxios.delete).toHaveBeenCalledWith(
      '/api/v1/citations/match',
      { data: { citation_id: 1, finding_id: 9001 }})
    const st = useCitationReviewStore.getState()
    expect(
      st.citationsByGenerationId[42]?.find((c) => c.id === 1)
        ?.matched_finding_ids).toEqual([])
    expect(
      st.findingsByGenerationId[42]?.[0]?.matched_count).toBe(0)
  })

  it('reverts optimistic update on POST failure', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    mockedAxios.post.mockRejectedValueOnce(new Error('network'))

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.loadFindings(42) })

    let threw = false
    await act(async () => {
      try {
        await store.toggleMatch(42, 1, 9001, false)
      } catch {
        threw = true
      }
    })
    expect(threw).toBe(true)

    // matched_count stayed at 0; citation matched_finding_ids stayed empty.
    const st = useCitationReviewStore.getState()
    expect(
      st.citationsByGenerationId[42]?.find((c) => c.id === 1)
        ?.matched_finding_ids).toEqual([])
    expect(
      st.findingsByGenerationId[42]?.[0]?.matched_count).toBe(0)
  })
})
