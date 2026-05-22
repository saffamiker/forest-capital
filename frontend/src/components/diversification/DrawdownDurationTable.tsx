/**
 * DrawdownDurationTable — the "Drawdown Duration" section.
 *
 * One row per strategy with the avg / max underwater duration, the
 * avg / longest recovery, and a flag for strategies currently in
 * drawdown.
 *
 * The point of this table — separate from the existing Drawdown
 * Comparison table on the analytics page, which shows DEPTHS — is to
 * capture the TIME dimension of risk. A -20% drawdown that recovers
 * in 6 months is fundamentally different from one that recovers in
 * 36, and that distinction is invisible in the depth-only view. A
 * strategy currently underwater carries an amber pill plus its
 * current_drawdown_months count, so the reader sees the live state
 * before scanning historical figures.
 *
 * Backend payload from /api/v1/analytics/drawdown-duration (item 8
 * commit 1). All months are reported as integers (the backend rounds).
 */
import { Loader2, AlertCircle } from 'lucide-react'
import { useDrawdownDuration } from '../../lib/useDiversificationData'


function fmtMonths(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n === 1 ? '1 mo' : `${Math.round(n)} mo`
}


export function DrawdownDurationTable() {
  const { data, loading, error } = useDrawdownDuration()

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="drawdown-duration-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading drawdown duration data…
        </div>
      </div>
    )
  }
  if (error || !data || data.strategies.length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">
          Drawdown Duration
        </h2>
        <p className="text-sm text-muted">
          Drawdown duration data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  const anyCurrentlyUnderwater = data.strategies.some(
    (r) => r.currently_in_drawdown)

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="drawdown-duration-table">
      <div className="mb-3">
        <h2 className="text-base font-semibold text-white">
          Drawdown Duration
        </h2>
        <p className="text-xs text-muted mt-0.5">
          Time spent underwater complements the depth figures elsewhere on
          this page: a -20% drawdown that recovers in 6 months is a
          fundamentally different risk profile from one that recovers in 36.
          Strategies currently in drawdown carry an amber pill.
        </p>
        {anyCurrentlyUnderwater && (
          <p className="text-2xs text-warning mt-2 inline-flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            One or more strategies are presently underwater — read the
            current-drawdown column carefully.
          </p>
        )}
      </div>

      <div className="overflow-x-auto" data-testid="drawdown-duration-scroll">
        <table className="w-full text-xs"
               data-testid="drawdown-duration-grid">
          <thead>
            <tr className="text-2xs uppercase tracking-wider text-muted
                            border-b border-border">
              <th className="sticky left-0 z-10 bg-navy-800 text-left
                              px-2 py-2 font-medium"
                  style={{ minWidth: '160px' }}>
                Strategy
              </th>
              <th className="px-2 py-2 text-right font-medium">
                Avg duration
              </th>
              <th className="px-2 py-2 text-right font-medium">
                Max duration
              </th>
              <th className="px-2 py-2 text-right font-medium">
                Avg recovery
              </th>
              <th className="px-2 py-2 text-right font-medium">
                Longest recovery
              </th>
              <th className="px-2 py-2 text-right font-medium">
                Current
              </th>
            </tr>
          </thead>
          <tbody>
            {data.strategies.map((r) => (
              <tr key={r.strategy}
                  data-testid={`drawdown-duration-row-${r.strategy}`}
                  className={`border-b border-border/40
                              hover:bg-navy-700/40 transition-colors
                              ${r.currently_in_drawdown ? 'bg-warning/5' : ''}`}>
                <td className="sticky left-0 z-10 bg-navy-800
                                px-2 py-1.5 text-slate-300 font-mono"
                    style={{ minWidth: '160px' }}>
                  {r.strategy}
                </td>
                <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                  {fmtMonths(r.avg_duration_months)}
                </td>
                <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                  {fmtMonths(r.max_duration_months)}
                </td>
                <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                  {fmtMonths(r.avg_recovery_months)}
                </td>
                <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                  {fmtMonths(r.longest_recovery_months)}
                </td>
                <td className="px-2 py-1.5 text-right font-mono"
                    data-testid={`drawdown-duration-current-${r.strategy}`}>
                  {r.currently_in_drawdown
                    ? (
                      <span className="inline-flex items-center gap-1
                                        px-1.5 py-0.5 rounded-full text-2xs
                                        bg-warning/15 text-warning border
                                        border-warning/40 whitespace-nowrap">
                        <AlertCircle className="w-2.5 h-2.5" />
                        {fmtMonths(r.current_drawdown_months)}
                      </span>
                    )
                    : <span className="text-muted">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
