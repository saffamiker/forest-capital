/**
 * frontend/src/stores/dataStatusStore.ts
 *
 * Single source of truth for GET /api/v1/admin/data-status — the per-
 * table data-currency facts (row count, date range, "April 2026"
 * display label, green/amber/red staleness pill) that the
 * DataCurrencyBar, the Dashboard staleness pill, and the Analytics
 * factor-model coverage line all read.
 *
 * Previously each consumer was a fresh useDataStatus hook firing its
 * own /api/v1/admin/data-status fetch on mount (audit F3, May 22 2026).
 * On a single Dashboard render that was at least two duplicate fetches
 * (loadDataStatus in the page + DataCurrencyBar via useDataStatus);
 * navigating Dashboard → Analytics added a third. Lifting to a Zustand
 * store with a 5-minute TTL collapses every consumer's fetch into one
 * shared call per session.
 *
 * 5-minute TTL: data-status only changes on a data ingestion (rare),
 * so a 5-minute staleness window is generous. The store still exposes
 * reload() for the Settings page when an operator wants a forced
 * refresh.
 */
import { create } from 'zustand'
import axios from 'axios'

export interface DataStatusTable {
  name: string
  row_count: number
  min_date: string | null
  max_date: string | null
  display_label: string | null
  last_updated: string | null
  staleness: string
}

export interface DataStatus {
  available: boolean
  study_period: { start: string; end: string; n_months: number } | null
  tables: DataStatusTable[]
}

const TTL_MS = 5 * 60 * 1000

interface DataStatusState {
  status: DataStatus | null
  loading: boolean
  fetchedAt: Date | null

  load: () => Promise<void>    // respects TTL — no-op if fresh
  reload: () => Promise<void>  // force refresh regardless of TTL
}

function isStale(fetchedAt: Date | null): boolean {
  if (!fetchedAt) return true
  return Date.now() - fetchedAt.getTime() > TTL_MS
}

export const useDataStatusStore = create<DataStatusState>((set, get) => ({
  status: null,
  loading: false,
  fetchedAt: null,

  load: async () => {
    // Already fresh — skip network call entirely.
    if (!isStale(get().fetchedAt) && get().status != null) return
    // Already fetching — don't stack concurrent requests when several
    // consumers all mount on the same render tick.
    if (get().loading) return
    await get().reload()
  },

  reload: async () => {
    set({ loading: true })
    try {
      const res = await axios.get<DataStatus>('/api/v1/admin/data-status')
      set({ status: res.data, loading: false, fetchedAt: new Date() })
    } catch {
      // Fail-open: the freshness pill simply renders nothing when the
      // status is null. The DataCurrencyBar and Analytics page already
      // treat null as "no status known yet".
      set({ loading: false })
    }
  },
}))

/** The named table from a DataStatus, or null. Defensive against a
 *  malformed payload (`{}` is what tests with a generic axios.get mock
 *  produce when no per-endpoint stub matches the URL) — the optional
 *  chain on `status` alone is not enough because `tables` itself can
 *  be undefined on a partially-formed response. */
export function tableOf(
  status: DataStatus | null, name: string,
): DataStatusTable | null {
  if (!status || !Array.isArray(status.tables)) return null
  return status.tables.find((t) => t.name === name) ?? null
}
