/**
 * frontend/src/stores/chartsStore.ts
 *
 * Single source of truth for the auxiliary chart data payload returned by
 * /api/v1/charts/data. Used by all six Statistical Evidence charts and all
 * six Regime Analysis charts. Mirrors the pattern of strategiesStore and
 * regimeStore: load() is a no-op when data is already present, navigating
 * between screens never re-fetches.
 */
import { create } from 'zustand'
import axios from 'axios'
import type { ChartDataPayload } from '../types/charts'

interface ChartsState {
  data: ChartDataPayload | null
  loading: boolean
  error: string | null
  loaded: boolean
  lastFetchedAt: Date | null

  load: () => Promise<void>
  reload: () => Promise<void>
  clear: () => void
}

export const useChartsStore = create<ChartsState>((set, get) => ({
  data: null,
  loading: false,
  error: null,
  loaded: false,
  lastFetchedAt: null,

  load: async () => {
    if (get().loaded || get().loading) return
    await get().reload()
  },

  reload: async () => {
    set({ loading: true, error: null })
    try {
      const res = await axios.get<ChartDataPayload>('/api/v1/charts/data')
      set({
        data: res.data,
        loaded: true,
        loading: false,
        lastFetchedAt: new Date(),
      })
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load chart data'
      set({ loading: false, error: String(msg) })
    }
  },

  clear: () => set({ data: null, loaded: false, error: null, lastFetchedAt: null }),
}))
