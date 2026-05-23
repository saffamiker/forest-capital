/**
 * CrisisPerformanceTable — the "Crisis Performance" section.
 *
 * Strategy × crisis-window matrix showing per-strategy CAGR, max
 * drawdown, and Sharpe ratio across five named historical windows:
 *   GFC 2008          (Sept 2008 – Mar 2009)
 *   EU Debt 2011-2012
 *   COVID Crash 2020  (Feb 2020 – Mar 2020)
 *   COVID Recovery    (Apr 2020 – Dec 2020)
 *   2022 Rate Shock   (Jan 2022 – Dec 2022)
 *
 * Each cell shows three stacked figures. A strategy whose history
 * doesn't cover the full window (e.g. REGIME_SWITCHING starts 2002-10
 * — outside the dotcom window in some configs) carries a `partial`
 * flag from the backend; the cell renders a small ⚠ indicator and the
 * tooltip names the number of months actually present in the window
 * vs the window's expected length.
 *
 * Backend payload from /api/v1/analytics/crisis-performance (item 8
 * commit 1). The `windows` map gives start/end per crisis name; `rows`
 * is a nested map strategy_name -> crisis_name -> CrisisCell. A missing
 * cell (some strategies have no data in some windows) renders as
 * "no data" rather than zero.
 */
import { Loader2, AlertCircle } from 'lucide-react'
import InfoIcon from '../InfoIcon'
import DataExplainButton from '../DataExplainButton'
import { useCrisisPerformance } from '../../lib/useDiversificationData'
import type { CrisisCell } from '../../types/diversification'


function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${(v * 100).toFixed(1)}%`
}

function fmtSharpe(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(2)
}


function pnlTone(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'text-muted'
  return v >= 0 ? 'text-positive' : 'text-negative'
}


/**
 * Renders one cell — three stacked figures plus a partial indicator
 * when the strategy didn't span the full window.
 */
function CrisisCellView({
  strategyName, crisisName, cell,
}: {
  strategyName: string
  crisisName: string
  cell: CrisisCell | undefined
}) {
  if (!cell) {
    return (
      <td className="px-2 py-1.5 text-center align-top text-2xs text-muted">
        no data
      </td>
    )
  }
  const tooltip = cell.partial
    ? `${strategyName} on ${crisisName}: ${cell.n_months} months covered (partial window)`
    : `${strategyName} on ${crisisName}: ${cell.n_months} months`
  return (
    <td className="px-2 py-1.5 align-top whitespace-nowrap"
        title={tooltip}
        data-testid={`crisis-cell-${strategyName}-${crisisName}`}>
      <div className={`text-xs font-mono ${pnlTone(cell.cagr)}`}>
        {fmtPct(cell.cagr)}
      </div>
      <div className={`text-2xs font-mono ${pnlTone(cell.max_dd)}`}>
        DD {fmtPct(cell.max_dd)}
      </div>
      <div className="text-2xs font-mono text-slate-400">
        Sh {fmtSharpe(cell.sharpe)}
        {cell.partial && (
          <AlertCircle className="inline-block w-3 h-3 ml-1 text-warning"
                       aria-label="partial window" />
        )}
      </div>
    </td>
  )
}


export function CrisisPerformanceTable() {
  const { data, loading, error } = useCrisisPerformance()

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="crisis-performance-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading crisis performance data…
        </div>
      </div>
    )
  }
  if (error || !data || !data.rows || !data.windows
      || Object.keys(data.rows).length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">
          Crisis Performance
        </h2>
        <p className="text-sm text-muted">
          Crisis performance data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  const strategies = Object.keys(data.rows)
  const crisisNames = Object.keys(data.windows)

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="crisis-performance-table">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-white
                         flex items-center min-w-0">
            <span className="truncate">Crisis Performance</span>
            <InfoIcon
              tooltipKey="crisis_performance_table"
              metricLabel="Crisis Performance"
              size="md" />
          </h2>
          <p className="text-xs text-muted mt-0.5">
            Performance through five named market events. Each cell shows
            window CAGR, max drawdown (DD), and Sharpe ratio (Sh). Partial
            windows (where the strategy started after the crisis began) are
            flagged with a ⚠ indicator.
          </p>
        </div>
        <div className="shrink-0">
          <DataExplainButton
            metric="Crisis Performance"
            context="academic_project"
          />
        </div>
      </div>

      <div className="overflow-x-auto" data-testid="crisis-performance-scroll">
        <table className="w-full text-xs"
               data-testid="crisis-performance-grid">
          <thead>
            <tr className="text-2xs uppercase tracking-wider text-muted
                            border-b border-border">
              <th className="sticky left-0 z-10 bg-navy-800 text-left
                              px-2 py-2 font-medium"
                  style={{ minWidth: '160px' }}>
                Strategy
              </th>
              {crisisNames.map((c) => (
                <th key={c}
                    className="px-2 py-2 text-left font-medium align-bottom"
                    style={{ minWidth: '120px' }}>
                  <div className="text-slate-300">{c}</div>
                  <div className="text-2xs text-muted font-normal normal-case
                                  tracking-normal font-mono">
                    {data.windows[c].start} → {data.windows[c].end}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {strategies.map((s) => (
              <tr key={s}
                  data-testid={`crisis-row-${s}`}
                  className="border-b border-border/40
                              hover:bg-navy-700/40 transition-colors">
                <th className="sticky left-0 z-10 bg-navy-800
                                px-2 py-1.5 text-left text-slate-300
                                font-mono text-xs"
                    style={{ minWidth: '160px' }}>
                  {s}
                </th>
                {crisisNames.map((c) => (
                  <CrisisCellView
                    key={c}
                    strategyName={s}
                    crisisName={c}
                    cell={data.rows[s]?.[c]}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
