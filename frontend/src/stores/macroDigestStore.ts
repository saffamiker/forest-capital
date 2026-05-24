/**
 * macroDigestStore — stale-while-revalidate cache for the macro
 * research digest.
 *
 * May 24 2026 P1 hotfix. The previous MacroResearchPanel held the
 * digest in component-local useState, so every dashboard mount
 * triggered a fresh /api/v1/research/latest call. The team reported
 * the digest "reloading on every navigation" — accurate, the panel
 * was firing one GET per mount.
 *
 * This store mirrors the citationReviewStore + dashboardDataStore
 * stale-while-revalidate pattern (the precedent the user cited):
 *   - cached digest persists across navigation
 *   - load() is a no-op when the cached digest is under the staleness
 *     window — so a re-mount within the window shows the cached
 *     digest INSTANTLY with no network round-trip
 *   - past the staleness window, a re-mount renders the cached digest
 *     immediately AND schedules a background refresh — the user
 *     sees data first, fresh data within a second
 *   - force=true bypasses the freshness check (the Run-now button)
 *
 * Backend source of truth is the macro_research_digests table; the
 * platform-wide refresh cadence is daily (24h freshness gate inside
 * tools/research_engine). 5 minutes here is the FRONTEND
 * stale-while-revalidate window — well under the backend's 24h
 * cadence, so we never out-fresh the backend.
 */
import { create } from 'zustand'
import axios from 'axios'


export interface MacroSignal {
  category:    string
  signal:      string
  implication: string
  source_url:  string
}


export interface MacroDigest {
  id:                 number
  generated_at:       string | null
  triggered_by:       string
  summary_text:       string
  regime_implication: string
  key_signals:        MacroSignal[]
  citation_urls:      string[]
  model:              string | null
  metadata:           Record<string, unknown>
}


export interface LatestResponse {
  digest:            MacroDigest | null
  last_completed_at: string | null
}


// 5 minutes — well below the backend's 24h research-engine cadence
// but tight enough that a real refresh elsewhere on the platform
// (e.g. a manual Run-now) propagates within a few page changes.
const STALE_AFTER_MS = 5 * 60 * 1000


interface MacroDigestState {
  latest: LatestResponse | null
  lastFetchedAt: number | null
  loading: boolean
  error: string | null
  /** Polling triggered by Run-now is gated by this flag so a
   *  second Run-now click while one is mid-flight is a no-op. */
  triggeringRunNow: boolean

  load: (opts?: { force?: boolean }) => Promise<void>
  runNow: () => Promise<void>
  _reset: () => void
}


export const useMacroDigestStore = create<MacroDigestState>((set, get) => ({
  latest: null,
  lastFetchedAt: null,
  loading: false,
  error: null,
  triggeringRunNow: false,

  load: async (opts = {}) => {
    const { force = false } = opts
    const now = Date.now()
    const lastAt = get().lastFetchedAt
    const cached = get().latest

    // Stale-while-revalidate: a cached digest under the freshness
    // window is returned without hitting the network. The
    // dashboard mount is the high-traffic call site; this guard
    // is the bug-fix.
    if (!force && cached && lastAt && (now - lastAt) < STALE_AFTER_MS) {
      return
    }

    // Cold-cache OR stale → fetch in the background. We DO NOT set
    // loading=true when cached data exists — the user sees the
    // stale digest while the refresh runs.
    if (!cached) {
      set({ loading: true })
    }
    try {
      const r = await axios.get<LatestResponse>(
        '/api/v1/research/latest')
      set({
        latest: r.data,
        lastFetchedAt: Date.now(),
        loading: false,
        error: null,
      })
    } catch (exc) {
      // Transient failure must not blank the cached digest — keep
      // whatever's already in the store, just surface the error.
      set((s) => ({
        loading: false,
        error: (axios.isAxiosError(exc) && exc.response?.data?.detail
                ? String(exc.response.data.detail)
                : 'Could not load the macro digest.'),
        // Leave .latest unchanged.
        latest: s.latest,
      }))
    }
  },

  runNow: async () => {
    if (get().triggeringRunNow) return
    set({ triggeringRunNow: true, error: null })
    const baselineTs = get().latest?.last_completed_at ?? null
    try {
      await axios.post('/api/v1/research/run')
    } catch (exc) {
      set({
        triggeringRunNow: false,
        error: (axios.isAxiosError(exc) && exc.response?.data?.detail
                ? String(exc.response.data.detail)
                : 'Could not start a research run.'),
      })
      return
    }
    set({ triggeringRunNow: false })
    // Poll for completion. 30-90s typical for the Sonnet + web_
    // search round-trip; 5 attempts × 20s = 100s budget cap.
    let attempts = 0
    const MAX_ATTEMPTS = 5
    const POLL_INTERVAL_MS = 20000
    const poll = async () => {
      attempts += 1
      try {
        const r = await axios.get<LatestResponse>(
          '/api/v1/research/latest')
        set({
          latest: r.data,
          lastFetchedAt: Date.now(),
          error: null,
        })
        if (r.data.last_completed_at
            && r.data.last_completed_at !== baselineTs) {
          // Fresh digest landed — stop polling.
          return
        }
      } catch {
        // Transient — keep polling.
      }
      if (attempts < MAX_ATTEMPTS) {
        setTimeout(() => void poll(), POLL_INTERVAL_MS)
      }
    }
    setTimeout(() => void poll(), POLL_INTERVAL_MS)
  },

  _reset: () =>
    set({
      latest: null, lastFetchedAt: null,
      loading: false, error: null,
      triggeringRunNow: false,
    }),
}))
