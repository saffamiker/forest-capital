/**
 * DistributionTable — the "Return Distribution" section.
 *
 * Per-strategy return-distribution moments and shape diagnostics:
 *   skewness         — symmetry. Negative = fatter LEFT tail (bad).
 *   excess kurtosis  — fatness of the tails relative to normal.
 *                       Positive = leptokurtic = fatter tails (riskier
 *                       than a normal-assumption Sharpe suggests).
 *   Jarque-Bera      — joint test of normality. Stat is roughly chi-2(2)
 *                       distributed under the null.
 *   p-value          — JB p; a strategy with p < 0.05 fails the
 *                       normality assumption.
 *
 * Plus best / worst single-month observations to anchor the moments
 * to concrete history. The reader's takeaway: any strategy that
 * fails normality at p < 0.05 should NOT be evaluated on Sharpe alone
 * (Sharpe assumes normal returns); use the Sortino, VaR/CVaR, and
 * tail-risk views elsewhere on this page.
 *
 * Backend payload from /api/v1/analytics/distribution (item 8 commit 1).
 */
import { Loader2, AlertCircle } from 'lucide-react'
import { useDistribution } from '../../lib/useDiversificationData'


function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}`
}

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${(v * 100).toFixed(digits)}%`
}

function fmtP(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  // Tiny p-values (computed at the floor of scipy's chi-2 tail) render
  // as < 0.001 for readability.
  if (v < 0.001) return '<0.001'
  return v.toFixed(3)
}

function fmtDateRet(d: { date: string; ret: number }): string {
  // Date arrives as YYYY-MM-DD; render as YYYY-MM (month resolution
  // is what matters at this level of analysis).
  const ym = d.date.slice(0, 7)
  return `${ym} (${fmtPct(d.ret, 1)})`
}


export function DistributionTable() {
  const { data, loading, error } = useDistribution()

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="distribution-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading return distribution data…
        </div>
      </div>
    )
  }
  if (error || !data || !data.strategies || data.strategies.length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">
          Return Distribution
        </h2>
        <p className="text-sm text-muted">
          Return distribution data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  const anyNonNormal = data.strategies.some((r) => !r.normality_passes)

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="distribution-table">
      <div className="mb-3">
        <h2 className="text-base font-semibold text-white">
          Return Distribution
        </h2>
        <p className="text-xs text-muted mt-0.5">
          Moments and shape diagnostics of the monthly return distribution.
          Negative skewness = fatter left tail (downside-asymmetric).
          Positive excess kurtosis = fatter tails than normal (Sharpe
          understates the risk). Jarque-Bera p &lt; 0.05 rejects normality
          — those strategies are marked, and their Sharpe should be
          read alongside the tail-risk view above.
        </p>
        {anyNonNormal && (
          <p className="text-2xs text-warning mt-2 inline-flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            One or more strategies fail the Jarque-Bera normality test —
            do not evaluate them on Sharpe alone.
          </p>
        )}
      </div>

      <div className="overflow-x-auto" data-testid="distribution-scroll">
        <table className="w-full text-xs"
               data-testid="distribution-grid">
          <thead>
            <tr className="text-2xs uppercase tracking-wider text-muted
                            border-b border-border">
              <th className="sticky left-0 z-10 bg-navy-800 text-left
                              px-2 py-2 font-medium"
                  style={{ minWidth: '160px' }}>
                Strategy
              </th>
              <th className="px-2 py-2 text-right font-medium">Skew</th>
              <th className="px-2 py-2 text-right font-medium">
                Ex. Kurt
              </th>
              <th className="px-2 py-2 text-right font-medium">JB stat</th>
              <th className="px-2 py-2 text-right font-medium">JB p</th>
              <th className="px-2 py-2 text-center font-medium">Normal?</th>
              <th className="px-2 py-2 text-left font-medium">Best</th>
              <th className="px-2 py-2 text-left font-medium">Worst</th>
            </tr>
          </thead>
          <tbody>
            {data.strategies.map((r) => {
              const isNormal = r.normality_passes
              const bestMonth = r.best_months[0]
              const worstMonth = r.worst_months[0]
              return (
                <tr key={r.strategy}
                    data-testid={`distribution-row-${r.strategy}`}
                    className={`border-b border-border/40
                                hover:bg-navy-700/40 transition-colors
                                ${!isNormal ? 'bg-warning/5' : ''}`}>
                  <td className="sticky left-0 z-10 bg-navy-800
                                  px-2 py-1.5 text-slate-300 font-mono"
                      style={{ minWidth: '160px' }}>
                    {r.strategy}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${
                    r.skewness < 0 ? 'text-negative' : 'text-slate-300'
                  }`}>
                    {fmtSigned(r.skewness)}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${
                    r.excess_kurtosis > 1 ? 'text-warning' : 'text-slate-300'
                  }`}>
                    {fmtSigned(r.excess_kurtosis)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {r.jarque_bera_stat !== null
                      ? r.jarque_bera_stat.toFixed(1)
                      : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right text-slate-300 font-mono">
                    {fmtP(r.jarque_bera_p)}
                  </td>
                  <td className="px-2 py-1.5 text-center"
                      data-testid={`distribution-normal-${r.strategy}`}>
                    {isNormal
                      ? <span className="inline-flex items-center gap-1
                                          px-1.5 py-0.5 rounded-full text-2xs
                                          bg-positive/15 text-positive border
                                          border-positive/40 whitespace-nowrap">
                          Normal
                        </span>
                      : <span className="inline-flex items-center gap-1
                                          px-1.5 py-0.5 rounded-full text-2xs
                                          bg-warning/15 text-warning border
                                          border-warning/40 whitespace-nowrap">
                          <AlertCircle className="w-2.5 h-2.5" />
                          Non-normal
                        </span>}
                  </td>
                  <td className="px-2 py-1.5 text-positive font-mono text-2xs">
                    {bestMonth ? fmtDateRet(bestMonth) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-negative font-mono text-2xs">
                    {worstMonth ? fmtDateRet(worstMonth) : '—'}
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
