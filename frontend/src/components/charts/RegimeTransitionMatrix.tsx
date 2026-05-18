/**
 * RegimeTransitionMatrix — empirical P(regime_{t+1} | regime_t) computed
 * from the threshold-classified regime history. 3×3 heatmap renders the
 * probabilities directly. Diagonal cells = regime persistence; off-diagonal
 * = transition likelihoods.
 *
 * Reads to the audience: "if we're in BULL today, there's an 86% chance
 * we're still in BULL next month" — supports the persistence narrative
 * for regime-switching strategies.
 */
import { useRef } from 'react'
import type { TransitionMatrix, Regime } from '../../types/charts'
import ChartExportButton from '../ChartExportButton'
import InfoIcon from '../InfoIcon'

interface Props {
  matrix: TransitionMatrix
}

const REGIMES: Regime[] = ['BULL', 'TRANSITION', 'BEAR']
const REGIME_COLORS: Record<Regime, string> = {
  BULL:       '#10b981',
  TRANSITION: '#f59e0b',
  BEAR:       '#ef4444',
}

function cellColor(p: number): string {
  // Probability cell intensity — 0 = transparent, 1 = full blue
  const alpha = Math.max(0.05, Math.min(0.9, p * 0.9 + 0.05))
  return `rgba(59, 130, 246, ${alpha})`
}

export default function RegimeTransitionMatrix({ matrix }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const isEmpty = !matrix.BULL || Object.values(matrix.BULL).every((v) => v === 0)

  if (isEmpty) {
    return (
      <div className="card p-4" data-testid="regime-transition-matrix" ref={containerRef}>
        <h3 className="text-white font-semibold text-sm">Regime Transition Matrix</h3>
        <p className="text-muted text-xs mt-3">Loading transition data…</p>
      </div>
    )
  }

  // The actual matrix entries, passed to the explainer so it interprets
  // these probabilities rather than the metric in the abstract.
  const pctOf = (from: Regime, to: Regime): string =>
    `${((matrix[from]?.[to] ?? 0) * 100).toFixed(0)}%`
  const explainValue =
    `Persistence (diagonal): BULL→BULL ${pctOf('BULL', 'BULL')}, `
    + `TRANSITION→TRANSITION ${pctOf('TRANSITION', 'TRANSITION')}, `
    + `BEAR→BEAR ${pctOf('BEAR', 'BEAR')}. Key transitions: `
    + `BULL→BEAR ${pctOf('BULL', 'BEAR')}, BEAR→BULL ${pctOf('BEAR', 'BULL')}, `
    + `TRANSITION→BEAR ${pctOf('TRANSITION', 'BEAR')}.`

  return (
    <div className="card p-4" data-testid="regime-transition-matrix" ref={containerRef}>
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold text-sm">
            Regime Transition Matrix
            <InfoIcon tooltipKey="regime_transition_matrix" metricLabel="Regime Transition Matrix" size="md" currentValue={explainValue} />
          </h3>
          <ChartExportButton chartId="regime_transition_matrix" containerRef={containerRef} />
        </div>
        <p className="text-muted text-xs mt-0.5">
          Empirical P(next month regime | current regime) · diagonal = persistence
        </p>
      </div>

      <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="text-left py-2 pr-3 text-muted text-2xs uppercase tracking-wide
                            sticky left-0 z-10 bg-navy-800">
              From ↓ / To →
            </th>
            {REGIMES.map((to) => (
              <th key={to} className="px-3 py-2 text-center">
                <span
                  className="inline-block px-2 py-0.5 rounded text-2xs"
                  style={{ background: `${REGIME_COLORS[to]}30`, color: REGIME_COLORS[to] }}
                >
                  {to}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {REGIMES.map((from) => (
            <tr key={from} className="border-t border-border/50">
              <td className="py-2 pr-3 sticky left-0 z-[5] bg-navy-800">
                <span
                  className="inline-block px-2 py-0.5 rounded text-2xs"
                  style={{ background: `${REGIME_COLORS[from]}30`, color: REGIME_COLORS[from] }}
                >
                  {from}
                </span>
              </td>
              {REGIMES.map((to) => {
                const p = matrix[from]?.[to] ?? 0
                return (
                  <td key={to} className="px-3 py-2 text-center">
                    <div
                      className="rounded py-3 font-mono"
                      style={{
                        background: cellColor(p),
                        color: '#f9fafb',
                        border: from === to ? '1px solid rgba(255,255,255,0.2)' : '1px solid transparent',
                      }}
                      title={`${from} → ${to} · P(next month): ${(p * 100).toFixed(1)}%`}
                    >
                      {(p * 100).toFixed(1)}%
                    </div>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
}
