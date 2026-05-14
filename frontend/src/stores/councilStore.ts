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

  setQuery: (q: string) => void
  runQuery: (q: string) => Promise<void>
  clear: () => void
}

export const useCouncilStore = create<CouncilState>((set, get) => ({
  query: '',
  lastQuery: '',
  result: null,
  loading: false,
  error: null,

  setQuery: (q) => set({ query: q }),

  runQuery: async (q) => {
    const trimmed = q.trim()
    if (!trimmed || get().loading) return
    set({ loading: true, error: null, result: null, lastQuery: trimmed })
    try {
      const res = await axios.post<CouncilResponse>('/api/council/query', { query: trimmed })
      set({ result: res.data, loading: false })
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Council query failed'
      set({
        loading: false,
        error: String(msg),
        result: { error: true, query: trimmed, messages: [], final_recommendation: '', consensus_reached: false },
      })
    }
  },

  clear: () =>
    set({ query: '', lastQuery: '', result: null, loading: false, error: null }),
}))
