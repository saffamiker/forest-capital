/**
 * RiskContributionBar — the "Marginal Contribution to Risk" section.
 *
 * Stacked horizontal bars showing what percentage of portfolio risk
 * each strategy contributes under two weighting schemes:
 *   - EQUAL_WEIGHT: 1/N across all strategies (the natural neutral
 *     benchmark — would each strategy contribute its 1/N share if
 *     risks were independent? Almost never).
 *   - TANGENCY:     the max-Sharpe long-only mix from the optimizer.
 *
 * A strategy whose pct_risk_contribution far exceeds its weight is a
 * risk concentrator: it's eating more of the portfolio's volatility
 * than its capital allocation would suggest. The opposite (pct < weight)
 * is the diversifier — it's reducing portfolio risk by less than its
 * capital share.
 *
 * Backend payload from /api/v1/analytics/risk-contribution (item 8
 * commit 1). The tangency arrays may be null when the optimizer
 * couldn't converge (e.g., a degenerate covariance matrix in a
 * sub-period); the UI gracefully degrades to the equal-weight view
 * only with a one-line note.
 */
import { useState, useMemo } from 'react'
import { Loader2 } from 'lucide-react'


import { useRiskContribution } from '../../lib/useDiversificationData'


type WeightScheme = 'equal' | 'tangency'

const SCHEME_LABEL: Record<WeightScheme, string> = {
  equal:    'Equal weight',
  tangency: 'Tangency (max Sharpe)',
}


function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v.toFixed(digits)}%`
}


export function RiskContributionBar() {
  const { data, loading, error } = useRiskContribution()
  const [scheme, setScheme] = useState<WeightScheme>('equal')

  const tangencyAvailable = !!(data?.pct_risk_contribution_tangency
    && data.tangency_weights)

  // If tangency is missing, force the toggle back to equal.
  const activeScheme: WeightScheme = scheme === 'tangency' && !tangencyAvailable
    ? 'equal' : scheme

  const rows = useMemo(() => {
    if (!data || !Array.isArray(data.labels)) return []
    const labels = data.labels
    if (labels.length === 0) return []
    const equalWeight = 100 / labels.length
    return labels.map((label, i) => {
      const pct = activeScheme === 'tangency'
        ? data.pct_risk_contribution_tangency?.[i]
        : data.pct_risk_contribution_equal?.[i]
      const weight = activeScheme === 'tangency'
        ? (data.tangency_weights?.[i] ?? 0) * 100
        : equalWeight
      return {
        label,
        pct: pct ?? 0,
        weight,
        delta: (pct ?? 0) - weight,  // > 0: concentrator. < 0: diversifier.
      }
    })
  }, [data, activeScheme])

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="risk-contribution-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading risk contribution data…
        </div>
      </div>
    )
  }
  if (error || !data || !data.labels || data.labels.length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">
          Marginal Contribution to Risk
        </h2>
        <p className="text-sm text-muted">
          Risk contribution data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  // Find max pct for bar scaling — bar width is relative to the max
  // observed contribution so the chart fills the column even on
  // distributions where the max is well below 100%.
  const maxPct = Math.max(...rows.map((r) => r.pct), 1)

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="risk-contribution-bar">
      <div className="flex items-start justify-between mb-3 gap-2 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-white">
            Marginal Contribution to Risk
          </h2>
          <p className="text-xs text-muted mt-0.5">
            What percentage of portfolio risk each strategy contributes.
            A bar wider than its capital share is a risk concentrator;
            narrower is a diversifier. The {activeScheme === 'equal'
              ? '1/N equal-weight'
              : 'optimizer tangency'} reference is the dashed grey line.
          </p>
        </div>
        <div className="flex gap-1 shrink-0"
             data-testid="risk-contribution-scheme-toggle">
          {(['equal', 'tangency'] as WeightScheme[]).map((p) => {
            const disabled = p === 'tangency' && !tangencyAvailable
            return (
              <button
                key={p}
                type="button"
                onClick={() => !disabled && setScheme(p)}
                disabled={disabled}
                data-testid={`risk-contribution-scheme-${p}`}
                title={disabled
                  ? 'Optimizer did not converge — tangency unavailable'
                  : undefined}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  activeScheme === p
                    ? 'border-electric bg-electric/10 text-electric'
                    : disabled
                      ? 'border-border/40 text-muted/40 cursor-not-allowed'
                      : 'border-border text-muted hover:text-white hover:border-border/80'
                }`}>
                {SCHEME_LABEL[p]}
              </button>
            )
          })}
        </div>
      </div>

      {scheme === 'tangency' && !tangencyAvailable && (
        <p className="text-2xs text-warning mb-2">
          Optimizer did not converge — showing equal-weight view instead.
        </p>
      )}

      <div className="space-y-2" data-testid="risk-contribution-rows">
        {rows.map((r) => {
          const pctOfMax = (r.pct / maxPct) * 100
          const weightLineLeft = (r.weight / maxPct) * 100
          const isConcentrator = r.delta > 0.5
          const isDiversifier = r.delta < -0.5
          return (
            <div key={r.label}
                 data-testid={`risk-contribution-row-${r.label}`}
                 className="grid grid-cols-[160px_1fr_80px] items-center gap-3">
              <div className="text-xs text-slate-300 font-mono truncate">
                {r.label}
              </div>
              <div className="relative h-5 bg-navy-900/60 rounded overflow-hidden">
                <div
                  className={`absolute inset-y-0 left-0 ${
                    isConcentrator
                      ? 'bg-warning/60'
                      : isDiversifier ? 'bg-positive/60' : 'bg-electric/60'
                  }`}
                  style={{ width: `${Math.max(0, Math.min(100, pctOfMax))}%` }}
                />
                {/* Reference line for the strategy's capital weight. */}
                <div
                  className="absolute inset-y-0 w-px bg-slate-300/70"
                  style={{ left: `${Math.max(0, Math.min(100, weightLineLeft))}%` }}
                  title={`Capital weight: ${fmtPct(r.weight)}`}
                />
              </div>
              <div className={`text-right font-mono text-xs ${
                isConcentrator
                  ? 'text-warning'
                  : isDiversifier ? 'text-positive' : 'text-slate-300'
              }`}>
                {fmtPct(r.pct)}
              </div>
            </div>
          )
        })}
      </div>

      <p className="text-2xs text-muted mt-3 leading-relaxed">
        <span className="inline-block w-2 h-2 rounded-sm bg-warning/60 mr-1
                          align-middle" />
        risk concentrator (contribution &gt; weight)
        <span className="mx-3">·</span>
        <span className="inline-block w-2 h-2 rounded-sm bg-positive/60 mr-1
                          align-middle" />
        diversifier (contribution &lt; weight)
        <span className="mx-3">·</span>
        thin vertical line = capital weight reference
      </p>
    </div>
  )
}
