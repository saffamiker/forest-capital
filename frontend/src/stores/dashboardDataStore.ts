/**
 * frontend/src/stores/dashboardDataStore.ts
 *
 * Session-scoped cache for the Dashboard's auxiliary panels.
 *
 * BEFORE: cumulative and frontier lived in Dashboard component
 * state. Every navigation away from the dashboard unmounted the
 * component, clearing the state. On return, the page re-fetched in
 * parallel with strategiesStore / regimeStore / characterisations /
 * frontier / cumulative — five concurrent calls, with the
 * cumulative + frontier ones racing through silent catch blocks.
 * Users reported the persistent "Cumulative return series
 * unavailable" + "Loading current macro conditions…" stuck state on
 * navigation return (May 23 2026 production fire).
 *
 * AFTER: this store holds cumulative + frontier across the session.
 * Same pattern as strategiesStore — load() is a no-op when loaded
 * is true, so the Dashboard mount fires it on every visit but only
 * the first hits the network. Errors are surfaced (the previous
 * silent catches meant a transient failure persisted as "Unavailable"
 * forever until a hard refresh).
 *
 * Macro intentionally stays in MacroResearchPanel's component state
 * — the panel polls /api/v1/research/latest every 30s to pick up
 * mid-session refreshes, and the polling logic is tied to the
 * mount/unmount lifecycle. The macro tile shows "Loading…" on
 * remount for at most one network round-trip, which is the correct
 * behaviour for live-refreshing content; the cumulative + frontier
 * fix is the user's reported issue.
 */
import { create } from 'zustand'
import axios from 'axios'

import type { EfficientFrontierData } from '../types/api'


// CumulativeReturns mirrors the inline shape Dashboard.tsx renders.
// Kept here (rather than imported from Dashboard.tsx) so the store
// has a clean public type — Dashboard imports this type back so
// both sides share a single definition.

export interface CumulativePoint {
  date: string
  [strategyName: string]: string | number | null
}

export interface CumulativeReturns {
  strategies: string[]
  points: CumulativePoint[]
}

export type { EfficientFrontierData } from '../types/api'


interface DashboardDataStore {
  cumulative: CumulativeReturns | null
  frontier: EfficientFrontierData | null
  cumulativeError: string | null
  frontierError: string | null
  loaded: boolean
  loading: boolean

  load: () => Promise<void>

  /** Force a re-fetch — used by the manual "refresh" button when
   *  the operator wants to see fresh data without a hard reload. */
  refresh: () => Promise<void>

  /** Test-only reset. */
  _reset: () => void
}


export const useDashboardDataStore = create<DashboardDataStore>((set, get) => ({
  cumulative: null,
  frontier: null,
  cumulativeError: null,
  frontierError: null,
  loaded: false,
  loading: false,

  load: async () => {
    if (get().loaded || get().loading) return
    set({ loading: true })
    await _fetchAll(set)
  },

  refresh: async () => {
    set({ loaded: false, loading: true,
          cumulativeError: null, frontierError: null })
    await _fetchAll(set)
  },

  _reset: () => set({
    cumulative: null, frontier: null,
    cumulativeError: null, frontierError: null,
    loaded: false, loading: false,
  }),
}))


// Shared fetch helper — runs cumulative + frontier in parallel.
// Each fetch sets its own error key independently so a transient
// frontier failure doesn't blank the cumulative chart (and vice
// versa). `loaded` flips to true only once both promises have
// settled (success or surfaced error) so a remount immediately
// reads whatever is in the store.
async function _fetchAll(
  set: (state: Partial<DashboardDataStore>) => void,
): Promise<void> {
  // Cumulative — GET /api/v1/analytics/academic; the .cumulative_
  // returns field is what we cache. Server-side cached via the
  // analytics_metrics_cache layer (Item 7) so even repeated calls
  // are fast.
  const cumulativeP = axios.get<{ cumulative_returns?: CumulativeReturns }>(
    '/api/v1/analytics/academic',
    { timeout: 30000 },
  ).then(
    (res) => ({
      cumulative: res.data.cumulative_returns ?? null,
      cumulativeError: res.data.cumulative_returns
        ? null
        : 'Cumulative return series unavailable in cache — try Refresh',
    }),
    (err: unknown) => {
      const msg = axios.isAxiosError(err)
        ? (err.message || 'request failed')
        : (err as Error)?.message || 'load failed'
      return { cumulative: null, cumulativeError: `Load failed: ${msg}` }
    },
  )

  // Frontier — POST /api/optimize/weights with the default
  // MAX_SHARPE method. Frontier failures are surfaced (the
  // previous silent catch on the Dashboard meant users saw an
  // empty chart with no idea why).
  const frontierP = axios.post<{ efficient_frontier: EfficientFrontierData }>(
    '/api/optimize/weights',
    { method: 'MAX_SHARPE' },
    { timeout: 30000 },
  ).then(
    (res) => ({
      frontier: res.data.efficient_frontier ?? null,
      frontierError: null,
    }),
    (err: unknown) => {
      const msg = axios.isAxiosError(err)
        ? (err.message || 'request failed')
        : (err as Error)?.message || 'load failed'
      return { frontier: null, frontierError: `Load failed: ${msg}` }
    },
  )

  const [c, f] = await Promise.all([cumulativeP, frontierP])
  set({ ...c, ...f, loaded: true, loading: false })
}
