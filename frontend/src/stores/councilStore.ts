/**
 * frontend/src/stores/councilStore.ts
 *
 * Persists the most recent council query and response for the session.
 * When the user navigates away from the Council tab and returns, they
 * see the previous response immediately rather than an empty screen or
 * a duplicate API call. A new query replaces the previous response.
 *
 * runQuery() is the single entry point that owns the network call so
 * the component never touches axios directly — the same pattern as
 * strategiesStore.reload() and chartsStore.reload().
 */

import { create } from 'zustand'
import axios from 'axios'
import { useGlossaryStore } from './glossaryStore'
import type { CouncilResponse } from '../types/agents'

interface CouncilResult extends CouncilResponse {
  error?: boolean
}

interface CouncilState {
  query: string                  // current text in the input box
  lastQuery: string              // the query that produced `result`
  result: CouncilResult | null   // last response (survives navigation)
  loading: boolean
  error: string | null
  // Viewer council allocation — {used, limit} for a limited user, null
  // for an unlimited user (or until the first query/me-fetch resolves).
  councilUsage: { used: number; limit: number } | null

  setQuery: (q: string) => void
  runQuery: (q: string) => Promise<void>
  abort: () => void
  clear: () => void
}

// Module-level so abort() can reach the in-flight request without
// threading a non-serialisable AbortController through Zustand state.
let _controller: AbortController | null = null

export const useCouncilStore = create<CouncilState>((set, get) => ({
  query: '',
  lastQuery: '',
  result: null,
  loading: false,
  error: null,
  councilUsage: null,

  setQuery: (q) => set({ query: q }),

  runQuery: async (q) => {
    const trimmed = q.trim()
    if (!trimmed || get().loading) return
    _controller = new AbortController()
    set({ loading: true, error: null, result: null, lastQuery: trimmed })
    try {
      const res = await axios.post<CouncilResponse>(
        '/api/council/query', { query: trimmed }, { signal: _controller.signal },
      )
      // A limited viewer's response carries the post-query allocation.
      const usage = res.data.council_queries_limit != null
        ? { used: res.data.council_queries_used ?? 0,
            limit: res.data.council_queries_limit }
        : get().councilUsage
      set({ result: res.data, loading: false, councilUsage: usage })
      // Re-anchor the Commentary-mode glossary to this completed
      // session: clear the once-per-session termsLoaded guard and
      // immediately force-reload terms with the council output, so each
      // term's `this_session` field reflects the actual results rather
      // than the empty context of the first mount-time load. force:true
      // bypasses the loadTerms guards (including termsLoading — so an
      // initial mount-time load still in flight doesn't block this
      // re-anchor) and keeps the reload silent — no UI flash. The
      // termsLoaded flag is also cleared so any unrelated subsequent
      // non-force loadTerms() call still sees the slot as stale until
      // this reload completes. Fire-and-forget — runs on success only.
      useGlossaryStore.setState({ termsLoaded: false })
      void useGlossaryStore.getState().loadTerms(
        res.data as unknown as Record<string, unknown>,
        { force: true })
    } catch (err) {
      // A user-initiated cancel is not an error — clear loading, show nothing.
      if (axios.isCancel(err)) {
        set({ loading: false })
        return
      }
      // A 429 council_limit_reached carries {limit, used} — surface it as
      // the blocked state so the screen shows the contact-Michael message.
      if (axios.isAxiosError(err) && err.response?.status === 429) {
        const d = err.response.data?.detail
        if (d && typeof d === 'object' && d.error === 'council_limit_reached') {
          set({
            loading: false,
            error: 'council_limit_reached',
            councilUsage: { used: Number(d.used), limit: Number(d.limit) },
            result: { error: true, query: trimmed, messages: [], final_recommendation: '', consensus_reached: false },
          })
          return
        }
      }
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      const msg = (typeof detail === 'string' && detail) ? detail
        : (axios.isAxiosError(err) ? err.message : 'Council query failed')
      set({
        loading: false,
        error: String(msg),
        result: { error: true, query: trimmed, messages: [], final_recommendation: '', consensus_reached: false },
      })
    } finally {
      _controller = null
    }
  },

  abort: () => {
    _controller?.abort()
    _controller = null
    set({ loading: false })
  },

  clear: () => {
    _controller?.abort()
    _controller = null
    set({ query: '', lastQuery: '', result: null, loading: false, error: null })
  },
}))
