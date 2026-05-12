/**
 * frontend/src/stores/strategiesStore.ts
 *
 * Single source of truth for all 10 strategy results.  Data is fetched
 * once per session from /api/backtest/compare and never re-fetched while
 * the session is alive.  Navigation between screens never triggers a
 * re-fetch — components read from this store and call load() which is
 * a no-op when data is already present.
 *
 * Chosen over per-component fetching because:
 *   - The compare endpoint takes 200ms (cache hit) or 30s (cold start)
 *   - Re-fetching on every navigation would make tab switching unusable
 *   - Zustand persist is intentionally NOT used — strategies are session
 *     data tied to the current pipeline run, not user preferences
 */

import { create } from 'zustand'
import axios from 'axios'
import type { StrategyResult } from '../types/strategies'

interface StrategiesState {
  strategies: StrategyResult[]
  loading: boolean
  error: string | null
  loaded: boolean           // true once a successful fetch has completed
  lastFetchedAt: Date | null

  load: () => Promise<void>  // no-op if already loaded
  reload: () => Promise<void> // force re-fetch regardless of loaded flag
  clear: () => void
}

export const useStrategiesStore = create<StrategiesState>((set, get) => ({
  strategies: [],
  loading: false,
  error: null,
  loaded: false,
  lastFetchedAt: null,

  load: async () => {
    // Skip if already loaded — this is the key invariant that prevents
    // re-fetching on navigation
    if (get().loaded || get().loading) return
    await get().reload()
  },

  reload: async () => {
    set({ loading: true, error: null })
    try {
      const res = await axios.get<{ strategies: StrategyResult[] }>(
        '/api/backtest/compare'
      )
      set({
        strategies: res.data.strategies ?? [],
        loaded: true,
        loading: false,
        lastFetchedAt: new Date(),
      })
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load strategies'
      set({ loading: false, error: String(msg) })
    }
  },

  clear: () => set({ strategies: [], loaded: false, error: null, lastFetchedAt: null }),
}))
