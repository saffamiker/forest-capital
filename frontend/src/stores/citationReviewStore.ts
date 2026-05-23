/**
 * frontend/src/stores/citationReviewStore.ts
 *
 * Persistent citation-review state — survives navigation away from
 * the Report Writer and remount of CitationReviewPanel.
 *
 * BEFORE (May 23 2026 bug report): every piece of citation-review
 * state lived in local useState inside CitationReviewPanel and the
 * per-row CitationRow components — citations array, panel-open
 * boolean, per-row manual-form toggle, busy id. Navigating away
 * unmounted the component, clearing every value. On return, the
 * component refetched /api/v1/citations/<id> from scratch, flashing
 * a loading spinner over a (correctly cached) backend response.
 * Users perceived this as the search rerunning — even though the
 * actual 3-pass Anthropic search only fires on Step 2 of the report
 * writer pipeline and the backing rows in citations_cache survive
 * indefinitely.
 *
 * AFTER (this store):
 *   - citationsByGenerationId persists the per-generation row set
 *     across navigation, keyed by generation_id so multiple
 *     generations can coexist
 *   - lastFetchedAt tracks when each generation_id was last loaded
 *     so the panel can stale-while-revalidate (render cached rows
 *     instantly, soft-refresh in the background)
 *   - manualOpenByCitationId persists the per-row "Add manually"
 *     form toggle across remounts — the bug the user reported as
 *     "Show details expanded state resets on navigation"
 *
 * STALE-WHILE-REVALIDATE: on mount, if a cached entry exists for
 * the generation_id the panel renders it immediately and triggers
 * a background refetch. The refetch never blocks render — the user
 * sees data first, fresh data within a second. Stale threshold is
 * STALE_AFTER_MS; below that, the soft refresh is skipped entirely.
 *
 * The store mirrors the existing dashboardDataStore pattern — same
 * load/refresh/_reset API, same Zustand convention.
 */
import { create } from 'zustand'


// ── Type contracts (mirror CitationReviewPanel's local types) ───────────────


export interface CitationAlternative {
  author?: string | null
  year?: string | null
  title?: string | null
  journal_or_institution?: string | null
  volume_issue_pages?: string | null
  url?: string | null
  pass_source?: string | null
}


export interface Citation {
  id: number
  concept_id: string
  author: string | null
  year: string | null
  title: string | null
  journal_or_institution: string | null
  volume_issue_pages: string | null
  url: string | null
  verification_status: string
  search_query_used: string | null
  alternatives: CitationAlternative[]
  reviewer_email: string | null
  reviewed_at: string | null
  review_action: string | null
  formatted: string | null
}


// Soft-refresh threshold. Below this age the cached entry is
// considered current and no background refetch fires. Above it,
// the panel renders the stale value instantly AND schedules a
// background refresh so the next interaction reflects the latest
// reviewer state. 5 minutes is comfortable — the citations_cache
// table is the source of truth and only changes via reviewer
// actions or a fresh Step 2 search.
const STALE_AFTER_MS = 5 * 60 * 1000


interface CitationReviewState {
  /** Per-generation citation list, keyed by generation_id. */
  citationsByGenerationId: Record<number, Citation[]>
  /** Per-generation last-fetched timestamp (ms) — drives stale check. */
  lastFetchedAt: Record<number, number>
  /** In-flight requests, keyed by generation_id, so duplicate concurrent
   *  loads share one fetch. */
  inFlight: Record<number, Promise<void> | undefined>
  /** Per-citation manual-form open toggle. Persists across remounts. */
  manualOpenByCitationId: Record<number, boolean>
  /** Per-generation error, keyed by generation_id. */
  errorByGenerationId: Record<number, string | null>

  /** Load citations for a generation. Soft-refresh by default —
   *  returns immediately if cached data is fresh; rehydrate from
   *  cache and trigger background refresh if stale. force=true
   *  bypasses the freshness check. */
  load: (generationId: number, opts?: { force?: boolean }) => Promise<void>

  /** Optimistic replace — called after a successful review POST so
   *  the row reflects the new state without waiting for a refetch. */
  upsertCitation: (generationId: number, citation: Citation) => void

  /** Per-citation manual-form toggle. */
  setManualOpen: (citationId: number, open: boolean) => void

  /** Test-only reset. */
  _reset: () => void
}


const _initial = {
  citationsByGenerationId: {} as Record<number, Citation[]>,
  lastFetchedAt:           {} as Record<number, number>,
  inFlight:                {} as Record<number, Promise<void> | undefined>,
  manualOpenByCitationId:  {} as Record<number, boolean>,
  errorByGenerationId:     {} as Record<number, string | null>,
}


export const useCitationReviewStore = create<CitationReviewState>((set, get) => ({
  ..._initial,

  load: async (generationId, opts = {}) => {
    const { force = false } = opts
    const now = Date.now()
    const lastAt = get().lastFetchedAt[generationId]
    const cached = get().citationsByGenerationId[generationId]
    const inFlight = get().inFlight[generationId]

    // Stale-while-revalidate: if a cached entry exists AND it is
    // under the staleness threshold, return immediately without
    // hitting the network. The user sees the cached citations
    // exactly as they left them.
    if (!force && cached && lastAt && (now - lastAt) < STALE_AFTER_MS) {
      return
    }

    // De-dup concurrent loads — if a fetch for this generation is
    // already in flight, await IT rather than starting a second.
    if (inFlight) {
      return inFlight
    }

    const fetchPromise = (async () => {
      try {
        const res = await fetch(
          `/api/v1/citations/${generationId}`,
          { credentials: 'include' })
        if (!res.ok) {
          throw new Error(`Citation fetch returned ${res.status}`)
        }
        const data = await res.json() as { citations: Citation[] }
        set((s) => ({
          citationsByGenerationId: {
            ...s.citationsByGenerationId,
            [generationId]: data.citations ?? [],
          },
          lastFetchedAt: {
            ...s.lastFetchedAt,
            [generationId]: Date.now(),
          },
          errorByGenerationId: {
            ...s.errorByGenerationId,
            [generationId]: null,
          },
        }))
      } catch (e) {
        set((s) => ({
          errorByGenerationId: {
            ...s.errorByGenerationId,
            [generationId]: (e as Error).message,
          },
        }))
      } finally {
        // Clear the in-flight flag so the next load triggers a
        // fresh fetch (subject to the staleness check).
        set((s) => {
          const next = { ...s.inFlight }
          delete next[generationId]
          return { inFlight: next }
        })
      }
    })()

    set((s) => ({
      inFlight: { ...s.inFlight, [generationId]: fetchPromise },
    }))
    return fetchPromise
  },

  upsertCitation: (generationId, citation) => {
    set((s) => {
      const existing = s.citationsByGenerationId[generationId] ?? []
      const next = existing.map(
        (c) => c.id === citation.id ? citation : c)
      return {
        citationsByGenerationId: {
          ...s.citationsByGenerationId,
          [generationId]: next,
        },
      }
    })
  },

  setManualOpen: (citationId, open) => {
    set((s) => ({
      manualOpenByCitationId: {
        ...s.manualOpenByCitationId,
        [citationId]: open,
      },
    }))
  },

  _reset: () => set(_initial),
}))


/** True when the cached entry for a generation_id is older than the
 *  stale threshold — drives the "should we soft-refresh in the
 *  background?" decision. Exported so components can opt into the
 *  background refresh without re-implementing the threshold. */
export function isStale(lastFetchedAt: number | undefined): boolean {
  if (!lastFetchedAt) return true
  return (Date.now() - lastFetchedAt) >= STALE_AFTER_MS
}
