/**
 * useDataStatus — read access to the data-status store.
 *
 * Re-exports the store types and the tableOf helper so every existing
 * call site keeps its imports unchanged. The hook itself is now a thin
 * wrapper over useDataStatusStore — every consumer reads the same
 * cached value (5-minute TTL) and only the first mount per session
 * fires the underlying /api/v1/admin/data-status request. Audit F3,
 * May 22 2026.
 */
import { useEffect } from 'react'
import {
  useDataStatusStore, tableOf,
} from '../stores/dataStatusStore'
import type {
  DataStatus, DataStatusTable,
} from '../stores/dataStatusStore'

export type { DataStatus, DataStatusTable }
export { tableOf }

export function useDataStatus(): { status: DataStatus | null; loading: boolean } {
  const status = useDataStatusStore((s) => s.status)
  const loading = useDataStatusStore((s) => s.loading)
  const load = useDataStatusStore((s) => s.load)
  useEffect(() => { void load() }, [load])
  return { status, loading }
}
