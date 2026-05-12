/**
 * frontend/src/stores/regimeStore.ts
 *
 * Regime detection data with a 15-minute client-side TTL.  The backend
 * also caches for 15 minutes, so the worst case on a FRED outage day is:
 * the first request takes 30–60s (FRED timeout), then every subsequent
 * request for the next 15 minutes returns instantly from backend cache.
 * This store adds a second cache layer so multiple components can read
 * regime data without even making an API call.
 *
 * Background refresh: when TTL expires, the next call to load() fetches
 * fresh data in the background without clearing the stale data first —
 * the dashboard continues to show the last known regime while refreshing.
 */

import { create } from 'zustand'
import axios from 'axios'
import type { RegimeData } from '../types/api'

const TTL_MS = 15 * 60 * 1000  // 15 minutes — matches backend cache

interface RegimeState {
  regime: RegimeData | null
  loading: boolean
  error: string | null
  fetchedAt: Date | null

  load: () => Promise<void>   // respects TTL — no-op if fresh
  reload: () => Promise<void> // force refresh regardless of TTL
}

function isStale(fetchedAt: Date | null): boolean {
  if (!fetchedAt) return true
  return Date.now() - fetchedAt.getTime() > TTL_MS
}

export const useRegimeStore = create<RegimeState>((set, get) => ({
  regime: null,
  loading: false,
  error: null,
  fetchedAt: null,

  load: async () => {
    // Already fresh — skip network call entirely
    if (!isStale(get().fetchedAt) && get().regime != null) return
    // Already fetching — don't stack concurrent requests
    if (get().loading) return
    await get().reload()
  },

  reload: async () => {
    set({ loading: true, error: null })
    try {
      const res = await axios.get<RegimeData>('/api/regime/current')
      set({ regime: res.data, loading: false, fetchedAt: new Date() })
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load regime data'
      set({ loading: false, error: String(msg) })
    }
  },
}))
