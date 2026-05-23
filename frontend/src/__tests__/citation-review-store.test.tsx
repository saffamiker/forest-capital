/**
 * citation-review-store.test.tsx — citation panel persistence
 * across navigation (May 23 2026 bug report).
 *
 * Two bugs collapsed into one fix:
 *
 *   1. "Show details" expanded state on citation tiles resets on
 *      navigation. The per-row "Add manually" toggle (referred to
 *      colloquially as Show details) lived in local useState
 *      inside CitationRow, so an unmount/remount cleared it.
 *
 *   2. Citation search results lost on navigation, forcing full
 *      rerun. Citations array also lived in local useState in
 *      CitationReviewPanel; remount cleared it and the panel
 *      re-fetched /api/v1/citations/<id> from scratch — showing
 *      a loading flash even though the citations_cache backend
 *      table had the data persisted.
 *
 * Fix: citationReviewStore (Zustand) keys both pieces of state by
 * generation_id + citation_id. The panel reads from the store and
 * stale-while-revalidates — cached rows render instantly on
 * remount, then a soft background refetch confirms freshness.
 *
 * These tests pin the persistence contract — the store must
 * survive component unmount/remount, the manual toggle must
 * persist per-citation, and a fresh load on a non-stale cache
 * must not re-fetch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

import CitationReviewPanel from
  '../components/reportwriter/CitationReviewPanel'
import { useCitationReviewStore, isStale }
  from '../stores/citationReviewStore'


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


let originalFetch: typeof global.fetch

beforeEach(() => {
  originalFetch = global.fetch
  useCitationReviewStore.getState()._reset()
})

afterEach(() => {
  global.fetch = originalFetch
  vi.clearAllMocks()
  useCitationReviewStore.getState()._reset()
})


// ── Citation array persists across unmount/remount ───────────────────────────


describe('CitationReviewPanel — citations persist across unmount', () => {
  it('does NOT re-fetch when remounted within the stale window', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    // First mount — fetches.
    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId(
      'citation-row-cvar_coherent_risk'))
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Unmount (simulates navigating away).
    unmount()

    // Second mount — must NOT re-fetch; the store still holds the
    // citations from the first mount. The user sees them
    // instantly with no loading flash.
    render(<CitationReviewPanel generationId={42} />)
    // The pending-review row is visible immediately, no waitFor
    // needed — the store rendered it synchronously on mount.
    expect(
      screen.getByTestId('citation-row-cvar_coherent_risk'),
    ).toBeTruthy()
    // The fetch count is unchanged — no rerun.
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('a different generation_id triggers its own fetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { rerender } = render(
      <CitationReviewPanel generationId={42} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/citations/42', expect.any(Object)))

    // Switch to a different generation — must fetch fresh.
    rerender(<CitationReviewPanel generationId={43} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/citations/43', expect.any(Object)))
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // Switch BACK to 42 — store still holds it, no third fetch.
    rerender(<CitationReviewPanel generationId={42} />)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('no loading flash on remount when cache is warm', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId(
      'citation-row-cvar_coherent_risk'))
    unmount()

    // Re-mount. The Loader2 spinner is what indicates "loading" —
    // it MUST NOT appear when the store has cached data.
    const { container } = render(
      <CitationReviewPanel generationId={42} />)
    // Loader2 renders as an <svg> with class animate-spin. If the
    // panel re-flashed the loading state we'd see one in the
    // header right after mount.
    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeNull()
  })
})


// ── Per-row manual-form toggle persists across unmount ──────────────────────


describe('CitationReviewPanel — manual form toggle persists', () => {
  it('open manual form on row stays open after remount', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId(
      'citation-manual-toggle-cvar_coherent_risk'))

    // Open the manual form on the pending row.
    fireEvent.click(
      screen.getByTestId(
        'citation-manual-toggle-cvar_coherent_risk'))
    expect(
      screen.getByTestId(
        'citation-manual-author-cvar_coherent_risk'),
    ).toBeTruthy()

    // Navigate away.
    unmount()

    // Re-enter. The form must STILL be open — the store remembers
    // the toggle was set.
    render(<CitationReviewPanel generationId={42} />)
    expect(
      screen.getByTestId(
        'citation-manual-author-cvar_coherent_risk'),
    ).toBeTruthy()
  })

  it('toggle is per-citation (independent rows)', async () => {
    // Open manual on citation 1, close on citation 2. Each row's
    // state is keyed by citation.id, so they do not interfere.
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


// ── Stale-while-revalidate ──────────────────────────────────────────────────


describe('citationReviewStore — stale-while-revalidate', () => {
  it('load is a no-op when cached entry is within the freshness window', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.load(42) })
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Second load within the stale window — no new fetch.
    await act(async () => { await store.load(42) })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('force=true bypasses the freshness check', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

    const store = useCitationReviewStore.getState()
    await act(async () => { await store.load(42) })
    await act(async () => { await store.load(42, { force: true }) })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('isStale flags an entry with no timestamp as stale', () => {
    expect(isStale(undefined)).toBe(true)
    // A recent timestamp is fresh.
    expect(isStale(Date.now())).toBe(false)
    // An hour ago is stale (well past the 5-minute threshold).
    expect(isStale(Date.now() - 60 * 60 * 1000)).toBe(true)
  })

  it('upsertCitation replaces a row in place without a refetch', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ citations: makeCitations() }),
    } as Response)
    global.fetch = fetchMock as unknown as typeof fetch

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
    // No additional fetch — the upsert was purely client-side.
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('concurrent load() calls de-dup to one fetch', async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      // Return a promise that doesn't resolve immediately so two
      // concurrent loads truly overlap.
      new Promise((resolve) => {
        setTimeout(() => resolve({
          ok: true,
          json: async () => ({ citations: makeCitations() }),
        } as Response), 20)
      }))
    global.fetch = fetchMock as unknown as typeof fetch

    const store = useCitationReviewStore.getState()
    await act(async () => {
      await Promise.all([store.load(42), store.load(42), store.load(42)])
    })
    // Even though three load() calls fired in parallel, the store
    // de-duped them to a single network round-trip.
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
