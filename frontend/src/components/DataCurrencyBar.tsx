/**
 * DataCurrencyBar — a small, muted one-line indicator of how current the
 * platform's data is. Sits directly below the page title on every
 * visualisation screen (Dashboard, Analytics, Statistical Evidence,
 * Regime Analysis), in a consistent position.
 *
 * Three states, in priority order:
 *   1. Market data itself is > 30 days stale → an amber warning.
 *   2. The Fama-French factor model lags the market data → a muted
 *      note that Carhart loadings reflect the earlier factor end date.
 *   3. All current → "Data through April 2026 (286 months · …)".
 *
 * All facts come from GET /api/v1/admin/data-status via useDataStatus.
 */
import { Info, AlertTriangle } from 'lucide-react'
import { useDataStatus, tableOf } from '../hooks/useDataStatus'

/** "2002-07-31" → "2002-07". */
function ym(iso: string | null): string {
  return iso ? iso.slice(0, 7) : '—'
}

/** A coarse relative-time label for a months-old data date. */
function relTime(iso: string | null): string {
  if (!iso) return 'unknown'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'unknown'
  const days = Math.round((Date.now() - then) / 86_400_000)
  if (days < 1) return 'today'
  if (days < 30) return `${days}d ago`
  const months = Math.round(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.round(months / 12)}y ago`
}

export default function DataCurrencyBar() {
  const { status, loading } = useDataStatus()
  if (loading || !status?.available) return null

  const mkt = tableOf(status, 'market_data_monthly')
  const ff = tableOf(status, 'ff_factors_monthly')
  // Nothing to report until the market data table is populated.
  if (!mkt || mkt.row_count === 0 || !mkt.max_date) return null

  const mktLabel = mkt.display_label ?? ym(mkt.max_date)

  // State 1 — the market data itself is stale (> 30 days behind).
  if (mkt.staleness === 'red') {
    return (
      <div className="flex items-center gap-1.5 text-2xs text-warning">
        <AlertTriangle className="w-3 h-3 shrink-0" />
        <span>
          Market data through {mktLabel} · Last updated {relTime(mkt.max_date)}
        </span>
      </div>
    )
  }

  // State 2 — the factor model lags the (current) market data.
  const ffLags = !!(ff && ff.max_date && ff.max_date < mkt.max_date)
  if (ffLags && ff) {
    const ffLabel = ff.display_label ?? ym(ff.max_date)
    return (
      <div className="text-2xs text-muted space-y-0.5">
        <div>
          Market data through {mktLabel} · Factor model through {ffLabel}
        </div>
        <div className="flex items-center gap-1 text-muted/70">
          <Info className="w-3 h-3 shrink-0" />
          <span>Carhart loadings reflect data through {ffLabel}</span>
        </div>
      </div>
    )
  }

  // State 3 — all data current.
  return (
    <div className="text-2xs text-muted">
      Data through {mktLabel}{' '}
      ({mkt.row_count} months · {ym(mkt.min_date)} to {ym(mkt.max_date)})
    </div>
  )
}
