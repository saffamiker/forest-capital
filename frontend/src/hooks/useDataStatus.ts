/**
 * useDataStatus — fetches GET /api/v1/admin/data-status once and exposes
 * the per-table data-currency facts (row count, date range, a human
 * "April 2026" display_label, and a green/amber/red staleness pill).
 *
 * The DataCurrencyBar on every visualisation screen reads it, and the
 * Analytics page uses it for the factor-model coverage line and the
 * factor-loadings footnote. One source of truth for "how current is the
 * data" across the app.
 */
import { useEffect, useState } from 'react'
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

export function useDataStatus(): { status: DataStatus | null; loading: boolean } {
  const [status, setStatus] = useState<DataStatus | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<DataStatus>('/api/v1/admin/data-status')
      .then((res) => { if (!cancelled) setStatus(res.data) })
      .catch(() => { if (!cancelled) setStatus(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  return { status, loading }
}

/** The named table from a DataStatus, or null. */
export function tableOf(
  status: DataStatus | null, name: string,
): DataStatusTable | null {
  return status?.tables.find((t) => t.name === name) ?? null
}
