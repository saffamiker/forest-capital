/**
 * TailRiskTable — the "Tail Risk" section.
 *
 * One row per strategy with historical-simulation VaR and CVaR at the
 * 95% and 99% confidence levels, reported in BOTH monthly and
 * annualized terms. The annual figures are what an investment audience
 * intuitively reads ("how bad does the 1-in-100 month look in a year")
 * but the monthly figures are the primitive — they're what was
 * actually computed from the strategy's monthly return distribution.
 *
 * Backend payload from /api/v1/analytics/tail-risk (item 8 commit 1).
 * VaR and CVaR are the unsigned magnitude of the loss — already
 * negated server-side, so the column displays them as negative
 * percentages with a `-` sign for readability.
 *
 * Highlighting:
 *   - The CVaR 99% annual cell is the bear-case headline. Cells deeper
 *     than the worst-third of the cohort are tinted amber so a scan
 *     down the column immediately surfaces the tail-fragile strategies.
 *   - The strategy column is sticky-left so the metric columns can
 *     scroll horizontally on narrow viewports without losing context.
 */
import { Loader2 } from 'lucide-react'
import { useMemo } from 'react'
import { useTailRisk } from '../../lib/useDiversificationData'
import type { TailRiskRow } from '../../types/diversification'


/** Formats a (positive) loss magnitude as a negative percentage. */
function fmtLoss(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `-${(v * 100).toFixed(2)}%`
}


/**
 * Highlights the worst-third on CVaR-99-annual — these are the
 * strategies whose 1-in-100 ANNUAL loss is in the cohort's bottom
 * tercile. Returns the set of strategy names to amber-tint.
 */
function worstThirdByCvar99Annual(rows: TailRiskRow[]): Set<string> {
  if (rows.length === 0) return new Set()
  const sorted = [...rows].sort(
    (a, b) => (b.cvar_99_annual ?? 0) - (a.cvar_99_annual ?? 0))
  const cutoff = Math.ceil(sorted.length / 3)
  return new Set(sorted.slice(0, cutoff).map((r) => r.strategy))
}


export function TailRiskTable() {
  const { data, loading, error } = useTailRisk()

  const amberSet = useMemo(
    () => worstThirdByCvar99Annual(data?.strategies ?? []),
    [data])

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="tail-risk-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading tail risk metrics…
        </div>
      </div>
    )
  }
  if (error || !data || data.strategies.length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">Tail Risk</h2>
        <p className="text-sm text-muted">
          Tail risk data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="tail-risk-table">
      <div className="mb-3">
        <h2 className="text-base font-semibold text-white">Tail Risk</h2>
        <p className="text-xs text-muted mt-0.5">
          Historical-simulation Value-at-Risk and Conditional VaR at the 95% and
          99% confidence levels, monthly and annualized. CVaR (Expected
          Shortfall) is the average loss in the tail beyond VaR — the
          conservative read of a strategy's bear-case month / year. Worst-third
          on CVaR&nbsp;99% annual is tinted amber.
        </p>
      </div>

      <div className="overflow-x-auto" data-testid="tail-risk-scroll">
        <table className="w-full text-xs"
               data-testid="tail-risk-grid">
          <thead>
            <tr className="text-2xs uppercase tracking-wider text-muted
                            border-b border-border">
              <th className="sticky left-0 z-10 bg-navy-800 text-left
                              px-2 py-2 font-medium"
                  style={{ minWidth: '160px' }}>
                Strategy
              </th>
              <th className="px-2 py-2 text-right font-medium"
                  colSpan={4}>Monthly</th>
              <th className="px-2 py-2 text-right font-medium"
                  colSpan={4}>Annualized</th>
            </tr>
            <tr className="text-2xs uppercase tracking-wider text-muted
                            border-b border-border">
              <th className="sticky left-0 z-10 bg-navy-800" />
              <th className="px-2 py-1 text-right font-medium">VaR 95</th>
              <th className="px-2 py-1 text-right font-medium">VaR 99</th>
              <th className="px-2 py-1 text-right font-medium">CVaR 95</th>
              <th className="px-2 py-1 text-right font-medium">CVaR 99</th>
              <th className="px-2 py-1 text-right font-medium">VaR 95</th>
              <th className="px-2 py-1 text-right font-medium">VaR 99</th>
              <th className="px-2 py-1 text-right font-medium">CVaR 95</th>
              <th className="px-2 py-1 text-right font-medium">CVaR 99</th>
            </tr>
          </thead>
          <tbody>
            {data.strategies.map((r) => {
              const isAmber = amberSet.has(r.strategy)
              return (
                <tr key={r.strategy}
                    data-testid={`tail-risk-row-${r.strategy}`}
                    className={`border-b border-border/40
                                hover:bg-navy-700/40 transition-colors
                                ${isAmber ? 'bg-warning/5' : ''}`}>
                  <td className="sticky left-0 z-10 bg-navy-800
                                  px-2 py-1.5 text-slate-300 font-mono"
                      style={{ minWidth: '160px' }}>
                    {r.strategy}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.var_95_monthly)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.var_99_monthly)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.cvar_95_monthly)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.cvar_99_monthly)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.var_95_annual)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.var_99_annual)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtLoss(r.cvar_95_annual)}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono font-semibold
                                  ${isAmber ? 'text-warning' : 'text-slate-300'}`}
                      data-testid={`tail-risk-cvar99-${r.strategy}`}>
                    {fmtLoss(r.cvar_99_annual)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
