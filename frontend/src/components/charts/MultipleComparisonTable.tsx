/**
 * MultipleComparisonTable — raw vs FDR-corrected p-values per strategy.
 * Highlights strategies whose raw p-value would pass but whose corrected
 * p-value fails the threshold — exactly the cases the FDR correction was
 * designed to catch.
 */
import { useRef } from 'react'
import type { StrategyResult } from '../../types/strategies'
import { prettyName } from '../../lib/strategyColors'
import StrategyTypeBadge from '../StrategyTypeBadge'
import ChartExportButton from '../ChartExportButton'
import InfoIcon from '../InfoIcon'

interface Props {
  strategies: StrategyResult[]
}

const THRESHOLD = 0.005

function classifyMovement(raw: number, corrected: number): {
  label: string
  className: string
} {
  if (raw < THRESHOLD && corrected < THRESHOLD) return { label: 'Survived', className: 'text-success' }
  if (raw < THRESHOLD && corrected >= THRESHOLD) return { label: 'Lost after FDR', className: 'text-warning' }
  if (raw >= THRESHOLD && corrected >= THRESHOLD) return { label: 'Never passed', className: 'text-muted' }
  return { label: 'Marginal', className: 'text-muted' }
}

export default function MultipleComparisonTable({ strategies }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sorted = [...strategies].sort(
    (a, b) => (a.p_value_ttest ?? 1) - (b.p_value_ttest ?? 1),
  )

  // Survival counts through the FDR correction — the explainer reads them.
  const survived = strategies.filter(
    (s) => (s.p_value_ttest ?? 1) < THRESHOLD
      && (s.p_value_corrected ?? 1) < THRESHOLD,
  ).length
  const lostAfterFdr = strategies.filter(
    (s) => (s.p_value_ttest ?? 1) < THRESHOLD
      && (s.p_value_corrected ?? 1) >= THRESHOLD,
  ).length
  const explainValue =
    `${strategies.length} strategies, Benjamini-Hochberg FDR threshold `
    + `p<0.005. ${survived} survived correction; ${lostAfterFdr} lost `
    + `significance after FDR.`

  return (
    <div className="card p-4" data-testid="multiple-comparison-table" ref={containerRef}>
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold text-sm">
            Multiple Comparison Correction
            <InfoIcon tooltipKey="multiple_comparison_table" metricLabel="Multiple Comparison Correction" size="md" currentValue={explainValue} />
          </h3>
          <ChartExportButton chartId="multiple_comparison_correction" containerRef={containerRef} />
        </div>
        <p className="text-muted text-xs mt-0.5">
          Raw vs Benjamini-Hochberg FDR-corrected p-values · threshold p &lt; 0.005
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted text-2xs uppercase tracking-wide border-b border-border">
              <th className="text-left py-2 pr-3 sticky left-0 z-10 bg-navy-800">Strategy</th>
              <th className="text-right px-2 py-2">Raw p</th>
              <th className="px-2 py-2"></th>
              <th className="text-right px-2 py-2">FDR q</th>
              <th className="text-left px-2 py-2">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => {
              const raw = s.p_value_ttest ?? 1
              const corrected = s.p_value_corrected ?? 1
              const verdict = classifyMovement(raw, corrected)
              const arrow = corrected > raw ? '→' : '='
              return (
                <tr key={s.strategy_name} className="border-t border-border/50 hover:bg-navy-800/40 transition-colors">
                  <td className="py-1.5 pr-3 sticky left-0 z-[5] bg-navy-800">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-white font-mono">{prettyName(s.strategy_name)}</span>
                      <StrategyTypeBadge strategy={s.strategy_name} />
                    </div>
                  </td>
                  <td className="text-right px-2 py-1.5 font-mono text-slate-300">
                    {raw.toFixed(4)}
                  </td>
                  <td className="px-2 py-1.5 text-center text-muted">{arrow}</td>
                  <td className="text-right px-2 py-1.5 font-mono text-white">
                    {corrected.toFixed(4)}
                  </td>
                  <td className={`px-2 py-1.5 ${verdict.className}`}>{verdict.label}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
