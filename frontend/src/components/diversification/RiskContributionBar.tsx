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
import InfoIcon from '../InfoIcon'
import DataExplainButton from '../DataExplainButton'


import { useRiskContribution } from '../../lib/useDiversificationData'


type WeightScheme = 'equal' | 'tangency'

const SCHEME_LABEL: Record<WeightScheme, string> = {
  equal:    'Equal weight',
  tangency: 'Tangency (max Sharpe)',
}

// When max_sharpe_optimize falls back to min_variance (all-negative
// excess returns case), the backend still ships tangency_weights but
// they are min-variance weights. Relabel the toggle so the user
// reads the numbers correctly rather than mistaking min-variance for
// max-Sharpe.
const SCHEME_LABEL_FALLBACK: Record<WeightScheme, string> = {
  equal:    'Equal weight',
  tangency: 'Min variance (Sharpe infeasible)',
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
  // Tangency was computed but the optimizer fell back to min_variance
  // because every strategy's excess return was non-positive. The
  // weights are still valid; only the label changes.
  const tangencyIsFallback = !!data?.tangency_fallback_to_min_variance

  // If tangency is missing, force the toggle back to equal.
  const activeScheme: WeightScheme = scheme === 'tangency' && !tangencyAvailable
    ? 'equal' : scheme

  // Pick the label set once per render — relabel the tangency button
  // when its weights are a min-variance fallback so the toggle never
  // reads 'max Sharpe' over min-variance numbers.
  const labels = tangencyIsFallback
    ? SCHEME_LABEL_FALLBACK : SCHEME_LABEL

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
          <h2 className="text-base font-semibold text-white
                         flex items-center min-w-0">
            <span className="truncate">
              Marginal Contribution to Risk
            </span>
            <InfoIcon
              tooltipKey="risk_contribution_bar"
              metricLabel="Marginal Contribution to Risk"
              size="md" />
          </h2>
          <p className="text-xs text-muted mt-0.5">
            What percentage of portfolio risk each strategy contributes,
            computed from the strategy-return covariance matrix over
            the full study period. A bar wider than its capital share
            is a risk concentrator; narrower is a diversifier. The {activeScheme === 'equal'
              ? '1/N equal-weight'
              : tangencyIsFallback
                ? 'min-variance (Sharpe infeasible)'
                : 'optimizer tangency'} reference is the dashed grey line.
          </p>
        </div>
        <div className="flex gap-1 shrink-0"
             data-testid="risk-contribution-scheme-toggle">
          {(['equal', 'tangency'] as WeightScheme[]).map((p) => {
            const disabled = p === 'tangency' && !tangencyAvailable
            // Disabled-state tooltip explains the SPECIFIC reason — a
            // cvxpy / solver failure (the optimizer never returned
            // weights at all), distinct from the all-negative-excess
            // fallback (which produces min-variance weights and a
            // RELABELED-but-still-clickable Tangency toggle).
            const tooltip = disabled
              ? 'Optimizer could not produce tangency weights (cvxpy '
                + 'unavailable or solver error). The all-negative-excess '
                + 'case falls back to min-variance instead, in which '
                + 'case the toggle is enabled and relabeled.'
              : (p === 'tangency' && tangencyIsFallback
                ? 'Sharpe maximisation was infeasible — every '
                  + "strategy's excess return is non-positive. The "
                  + 'weights shown are min-variance weights (the '
                  + 'next-best long-only mix).'
                : undefined)
            return (
              <button
                key={p}
                type="button"
                onClick={() => !disabled && setScheme(p)}
                disabled={disabled}
                data-testid={`risk-contribution-scheme-${p}`}
                title={tooltip}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  activeScheme === p
                    ? 'border-electric bg-electric/10 text-electric'
                    : disabled
                      ? 'border-border/40 text-muted/40 cursor-not-allowed'
                      : 'border-border text-muted hover:text-white hover:border-border/80'
                }`}>
                {labels[p]}
              </button>
            )
          })}
          <DataExplainButton
            metric="Marginal Contribution to Risk"
            context="academic_project"
          />
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
          // Strict threshold (May 23 2026 fix): concentrator iff
          // contribution > weight, where weight = 1/n in equal scheme
          // or the tangency-optimised weight otherwise. The earlier
          // 0.5pp dead band misclassified MOMENTUM_ROTATION (11.6%
          // vs 11.11% equal-weight reference — delta=+0.49) as
          // neutral. The reference 1/n is already dynamic in `weight`
          // (computed from labels.length on every render), so the
          // threshold updates whenever the visible strategy set
          // changes. The dead band added no analytical value — it
          // existed only to give "borderline" entries a third
          // colour, and the borderline state was visually identical
          // to a real neutral strategy, which was confusing.
          const isConcentrator = r.pct > r.weight
          // Diversifier = everything else (contribution <= weight).
          // Exact equality (pct === weight) is a floating-point
          // edge case; classifying it as "diversifier" rather than
          // "neutral" keeps the legend a clean two-tone scheme that
          // matches the visible bar colours.
          return (
            <div key={r.label}
                 data-testid={`risk-contribution-row-${r.label}`}
                 data-classification={
                   isConcentrator ? 'concentrator' : 'diversifier'}
                 className="grid grid-cols-[160px_1fr_80px] items-center gap-3">
              <div className="text-xs text-slate-300 font-mono truncate">
                {r.label}
              </div>
              <div className="relative h-5 bg-navy-900/60 rounded overflow-hidden">
                <div
                  className={`absolute inset-y-0 left-0 ${
                    isConcentrator ? 'bg-warning/60' : 'bg-electric/60'
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
                isConcentrator ? 'text-warning' : 'text-electric'
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
        <span className="inline-block w-2 h-2 rounded-sm bg-electric/60 mr-1
                          align-middle" />
        diversifier (contribution ≤ weight)
        <span className="mx-3">·</span>
        thin vertical line = capital weight reference
      </p>
    </div>
  )
}
