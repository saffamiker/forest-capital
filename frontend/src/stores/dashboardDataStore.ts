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
 * WARMING-RETRY (May 23 2026 iteration 2): backend now returns
 * `warming: true` + `retry_after_ms` when the precomputed cache is
 * cold. The store schedules a retry after that delay (capped at 3
 * retries) so the data appears as soon as the background refresh
 * completes — no 30s timeouts on a fresh deploy. The Dashboard
 * renders a "computing..." state while warming.
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


// Maximum warming retries before giving up. The backend's
// retry_after_ms is 10s by default. Raised from 3 → 8 on
// May 24 2026 (P0 hotfix): the academic_analytics refresh runs
// six analytics reductions PLUS factor_loadings OLS plus the
// 100-point SLSQP frontier sweep — on a cold Render deploy the
// combined first-warm can run 40-60s. 3 retries (30s budget) was
// timing out before the cache row landed. 8 retries gives an
// 80s budget; the frontend renders "computing… (~60s)" while
// the cache populates.
const MAX_WARMING_RETRIES = 8


interface DashboardDataStore {
  cumulative: CumulativeReturns | null
  frontier: EfficientFrontierData | null
  cumulativeError: string | null
  frontierError: string | null
  /** True when at least one of the two endpoints returned
   *  `warming: true` on the most recent load — the Dashboard
   *  renders a "computing..." state instead of an error. */
  warming: boolean
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
  warming: false,
  loaded: false,
  loading: false,

  load: async () => {
    if (get().loaded || get().loading) return
    set({ loading: true })
    await _fetchAll(set, 0)
  },

  refresh: async () => {
    set({ loaded: false, loading: true, warming: false,
          cumulativeError: null, frontierError: null })
    await _fetchAll(set, 0)
  },

  _reset: () => set({
    cumulative: null, frontier: null,
    cumulativeError: null, frontierError: null,
    warming: false,
    loaded: false, loading: false,
  }),
}))


/** Schedule a retry of _fetchAll after `delayMs`. Wrapped so the
 *  retry path is testable and so a future change can swap to a
 *  proper exponential backoff if needed. */
function _scheduleRetry(
  set: (state: Partial<DashboardDataStore>) => void,
  delayMs: number,
  retriesSoFar: number,
): void {
  if (typeof window === 'undefined') return
  window.setTimeout(() => {
    void _fetchAll(set, retriesSoFar + 1)
  }, Math.max(1_000, delayMs))
}


// Shared fetch helper — runs cumulative + frontier in parallel.
// Each fetch sets its own error key independently so a transient
// frontier failure doesn't blank the cumulative chart (and vice
// versa). `loaded` flips to true only once both promises have
// settled (success or surfaced error) so a remount immediately
// reads whatever is in the store.
//
// `retriesSoFar` tracks the warming-retry chain so we stop after
// MAX_WARMING_RETRIES regardless of how persistently the backend
// reports `warming: true`. Past the cap we treat the warming flag
// as a real error and surface it.
async function _fetchAll(
  set: (state: Partial<DashboardDataStore>) => void,
  retriesSoFar: number,
): Promise<void> {
  // Cumulative — GET /api/v1/analytics/academic; the .cumulative_
  // returns field is what we cache. Server-side cached via the
  // analytics_metrics_cache layer (Item 7) so even repeated calls
  // are fast.
  const cumulativeP = axios.get<{
    cumulative_returns?: CumulativeReturns
    warming?: boolean
    retry_after_ms?: number
  }>(
    '/api/v1/analytics/academic',
    { timeout: 30000 },
  ).then(
    (res) => ({
      cumulative: res.data.cumulative_returns ?? null,
      cumulativeWarming: Boolean(res.data.warming),
      cumulativeRetryMs: Number(res.data.retry_after_ms ?? 10000),
      cumulativeError: (res.data.cumulative_returns || res.data.warming)
        ? null
        : 'Cumulative return series unavailable in cache — try Refresh',
    }),
    (err: unknown) => {
      const msg = axios.isAxiosError(err)
        ? (err.message || 'request failed')
        : (err as Error)?.message || 'load failed'
      return {
        cumulative: null,
        cumulativeWarming: false,
        cumulativeRetryMs: 0,
        cumulativeError: `Load failed: ${msg}`,
      }
    },
  )

  // Frontier — POST /api/optimize/weights with the default
  // MAX_SHARPE method. Frontier failures are surfaced (the
  // previous silent catch on the Dashboard meant users saw an
  // empty chart with no idea why).
  const frontierP = axios.post<{
    efficient_frontier?: EfficientFrontierData & { warming?: boolean }
    warming?: boolean
    retry_after_ms?: number
  }>(
    '/api/optimize/weights',
    { method: 'MAX_SHARPE' },
    { timeout: 30000 },
  ).then(
    (res) => ({
      frontier: res.data.efficient_frontier ?? null,
      frontierWarming: Boolean(res.data.warming
        || res.data.efficient_frontier?.warming),
      frontierRetryMs: Number(res.data.retry_after_ms ?? 10000),
      frontierError: null,
    }),
    (err: unknown) => {
      const msg = axios.isAxiosError(err)
        ? (err.message || 'request failed')
        : (err as Error)?.message || 'load failed'
      return {
        frontier: null,
        frontierWarming: false,
        frontierRetryMs: 0,
        frontierError: `Load failed: ${msg}`,
      }
    },
  )

  const [c, f] = await Promise.all([cumulativeP, frontierP])

  const stillWarming = c.cumulativeWarming || f.frontierWarming
  const canRetry = retriesSoFar < MAX_WARMING_RETRIES

  // Strip the warming/retry helper keys before set() so they don't
  // pollute the public store shape.
  set({
    cumulative:      c.cumulative,
    cumulativeError: c.cumulativeError,
    frontier:        f.frontier,
    frontierError:   f.frontierError,
    warming:         stillWarming,
    loaded:          true,
    loading:         stillWarming && canRetry,
  })

  if (stillWarming && canRetry) {
    const delay = Math.max(
      c.cumulativeRetryMs, f.frontierRetryMs, 10000)
    _scheduleRetry(set, delay, retriesSoFar)
  }
}
