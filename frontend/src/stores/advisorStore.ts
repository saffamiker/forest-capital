/**
 * frontend/src/stores/advisorStore.ts
 *
 * Session cache for the Academic Advisor (Agent 10) — keyed by
 * deliverable type so per-deliverable feedback persists across panel
 * open/close cycles without re-burning the ~$0.04-0.06 cost of a fresh
 * web_search-enabled Sonnet call.
 *
 * The cache key for analyse() is "{deliverable_type}:{query_hash}":
 * different queries for the same deliverable produce different cache
 * entries, but reopening the panel with the same query reads from cache.
 * The cache is session-only — page reload starts fresh, matching the
 * Explainer Agent's behaviour and the project-wide rule that AI content
 * is tied to a session, not persisted indefinitely.
 */
import { create } from 'zustand'
import axios from 'axios'
import type {
  AdvisorAnalysis,
  AdvisorVerification,
  AdvisorCitationsResponse,
  DeliverableType,
} from '../types/advisor'

interface AdvisorState {
  // Cache key: `${deliverable_type}:${query}` (query truncated to 200 chars).
  // Truncation prevents a single huge query from dominating the cache —
  // 200 chars is more than enough to disambiguate practitioner-style
  // queries like "what to focus on for the midpoint".
  analyses: Record<string, AdvisorAnalysis>
  // Verifications keyed by finding text (lowercased, trimmed). One finding
  // verified once per session, not once per panel-open.
  verifications: Record<string, AdvisorVerification>
  // Citations keyed by finding text. Independent of verifications because
  // a team member may want raw citations for a different rhetorical purpose
  // than a plausibility check.
  citationLookups: Record<string, AdvisorCitationsResponse>

  // Inflight tracking — prevents duplicate requests when the user
  // double-clicks "Get Advisor Feedback" before the first response lands.
  inflight: Set<string>

  // Latest UX state, exposed so AdvisorPanel can show a single error/loading
  // surface without each consumer reimplementing the same try/catch.
  loading: boolean
  error: string | null

  analyse: (
    query: string,
    deliverableType: DeliverableType,
    strategyResults?: Record<string, unknown>,
  ) => Promise<AdvisorAnalysis | null>

  verifyFinding: (
    finding: string,
    magnitude?: string | number,
    period?: string,
  ) => Promise<AdvisorVerification | null>

  fetchCitations: (
    finding: string,
    nSources?: number,
  ) => Promise<AdvisorCitationsResponse | null>

  clear: () => void
}

function cacheKeyForAnalysis(query: string, deliverableType: DeliverableType): string {
  return `${deliverableType}:${query.trim().slice(0, 200).toLowerCase()}`
}

function cacheKeyForFinding(finding: string): string {
  return finding.trim().toLowerCase()
}

export const useAdvisorStore = create<AdvisorState>((set, get) => ({
  analyses:        {},
  verifications:   {},
  citationLookups: {},
  inflight:        new Set<string>(),
  loading:         false,
  error:           null,

  analyse: async (query, deliverableType, strategyResults) => {
    const key = cacheKeyForAnalysis(query, deliverableType)
    const cached = get().analyses[key]
    if (cached) return cached
    if (get().inflight.has(`analyse:${key}`)) return null

    const inflight = new Set(get().inflight)
    inflight.add(`analyse:${key}`)
    set({ inflight, loading: true, error: null })

    try {
      const res = await axios.post<AdvisorAnalysis>(
        '/api/advisor/analyse',
        {
          query,
          deliverable_type: deliverableType,
          strategy_results: strategyResults,
        },
      )
      set((s) => ({
        analyses: { ...s.analyses, [key]: res.data },
        inflight: new Set([...s.inflight].filter((k) => k !== `analyse:${key}`)),
        loading:  false,
      }))
      return res.data
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to reach the Academic Advisor'
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== `analyse:${key}`)),
        loading:  false,
        error:    String(msg),
      }))
      return null
    }
  },

  verifyFinding: async (finding, magnitude, period) => {
    const key = cacheKeyForFinding(finding)
    const cached = get().verifications[key]
    if (cached) return cached
    if (get().inflight.has(`verify:${key}`)) return null

    const inflight = new Set(get().inflight)
    inflight.add(`verify:${key}`)
    set({ inflight, loading: true, error: null })

    try {
      const res = await axios.post<AdvisorVerification>(
        '/api/advisor/verify-finding',
        { finding, magnitude, period },
      )
      set((s) => ({
        verifications: { ...s.verifications, [key]: res.data },
        inflight:      new Set([...s.inflight].filter((k) => k !== `verify:${key}`)),
        loading:       false,
      }))
      return res.data
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to verify finding'
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== `verify:${key}`)),
        loading:  false,
        error:    String(msg),
      }))
      return null
    }
  },

  fetchCitations: async (finding, nSources = 3) => {
    const key = cacheKeyForFinding(finding)
    const cached = get().citationLookups[key]
    if (cached) return cached
    if (get().inflight.has(`citations:${key}`)) return null

    const inflight = new Set(get().inflight)
    inflight.add(`citations:${key}`)
    set({ inflight, loading: true, error: null })

    try {
      const res = await axios.post<AdvisorCitationsResponse>(
        '/api/advisor/citations',
        { finding, n_sources: nSources },
      )
      set((s) => ({
        citationLookups: { ...s.citationLookups, [key]: res.data },
        inflight:        new Set([...s.inflight].filter((k) => k !== `citations:${key}`)),
        loading:         false,
      }))
      return res.data
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to fetch citations'
      set((s) => ({
        inflight: new Set([...s.inflight].filter((k) => k !== `citations:${key}`)),
        loading:  false,
        error:    String(msg),
      }))
      return null
    }
  },

  clear: () =>
    set({
      analyses:        {},
      verifications:   {},
      citationLookups: {},
      inflight:        new Set(),
      loading:         false,
      error:           null,
    }),
}))

// Exported for testing — keeps the cache-key derivation as a single
// source of truth across store + tests.
export { cacheKeyForAnalysis, cacheKeyForFinding }
