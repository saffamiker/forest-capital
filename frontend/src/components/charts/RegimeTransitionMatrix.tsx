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
import type { TransitionMatrix, Regime } from '../../types/charts'

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
  const isEmpty = !matrix.BULL || Object.values(matrix.BULL).every((v) => v === 0)

  if (isEmpty) {
    return (
      <div className="card p-4" data-testid="regime-transition-matrix">
        <h3 className="text-white font-semibold text-sm">Regime Transition Matrix</h3>
        <p className="text-muted text-xs mt-3">Loading transition data…</p>
      </div>
    )
  }

  return (
    <div className="card p-4" data-testid="regime-transition-matrix">
      <div className="mb-3">
        <h3 className="text-white font-semibold text-sm">Regime Transition Matrix</h3>
        <p className="text-muted text-xs mt-0.5">
          Empirical P(next month regime | current regime) · diagonal = persistence
        </p>
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="text-left py-2 pr-3 text-muted text-2xs uppercase tracking-wide">
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
              <td className="py-2 pr-3">
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
  )
}
