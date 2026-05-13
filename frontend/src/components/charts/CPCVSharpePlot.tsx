/**
 * CPCVSharpePlot — Sharpe distribution across CPCV-like blocks per strategy.
 * Rendered as horizontal whisker boxes (min/Q1/median/Q3/max) so the audience
 * sees the dispersion at a glance: tight box = robust, wide box = path-dependent.
 *
 * Custom SVG rather than recharts because recharts doesn't ship a box plot
 * primitive and a hand-drawn box is < 30 lines.
 */
import type { CPCVStats } from '../../types/charts'
import { colorFor, prettyName } from '../../lib/strategyColors'

interface Props {
  cpcv: Record<string, CPCVStats>
}

export default function CPCVSharpePlot({ cpcv }: Props) {
  const entries = Object.entries(cpcv).filter(([, v]) => v.n_paths > 0)
  if (entries.length === 0) {
    return (
      <div className="card p-4" data-testid="cpcv-sharpe-plot">
        <h3 className="text-white font-semibold text-sm">CPCV Sharpe Distribution</h3>
        <p className="text-muted text-xs mt-3">Loading CPCV data…</p>
      </div>
    )
  }

  // Domain across all strategies — symmetric whiskers need a shared x scale
  const allValues = entries.flatMap(([, v]) =>
    [v.sharpe_min, v.sharpe_max, v.sharpe_q1, v.sharpe_q3, v.sharpe_median],
  )
  const xMin = Math.min(0, Math.floor(Math.min(...allValues) * 10) / 10)
  const xMax = Math.max(1, Math.ceil(Math.max(...allValues) * 10) / 10)
  const span = xMax - xMin || 1

  // Sort by median Sharpe so the audience reads top-to-bottom worst-to-best
  const sorted = [...entries].sort(([, a], [, b]) => a.sharpe_median - b.sharpe_median)

  const PLOT_HEIGHT = sorted.length * 32 + 40
  const PLOT_WIDTH = 720
  const PAD_LEFT = 160
  const PAD_RIGHT = 24
  const PAD_TOP = 16
  const innerW = PLOT_WIDTH - PAD_LEFT - PAD_RIGHT

  const xPos = (v: number) => PAD_LEFT + ((v - xMin) / span) * innerW

  return (
    <div className="card p-4" data-testid="cpcv-sharpe-plot">
      <div className="mb-3">
        <h3 className="text-white font-semibold text-sm">CPCV Sharpe Distribution</h3>
        <p className="text-muted text-xs mt-0.5">
          Sharpe ratio across 8 non-overlapping blocks — whisker shows min/Q1/median/Q3/max
        </p>
      </div>

      <svg viewBox={`0 0 ${PLOT_WIDTH} ${PLOT_HEIGHT}`} className="w-full" preserveAspectRatio="xMidYMid meet">
        {/* x-axis grid */}
        {[0, 0.5, 1.0, 1.5].filter((g) => g >= xMin && g <= xMax).map((g) => (
          <g key={g}>
            <line
              x1={xPos(g)} x2={xPos(g)}
              y1={PAD_TOP} y2={PLOT_HEIGHT - 20}
              stroke="#1e3a5c" strokeDasharray="3,3" strokeWidth={1}
            />
            <text x={xPos(g)} y={PLOT_HEIGHT - 6} fill="#64748b" fontSize="10" textAnchor="middle">
              {g.toFixed(1)}
            </text>
          </g>
        ))}
        {/* Zero reference */}
        <line x1={xPos(0)} x2={xPos(0)} y1={PAD_TOP} y2={PLOT_HEIGHT - 20} stroke="#ef4444" strokeWidth={1} opacity={0.4} />

        {sorted.map(([name, v], i) => {
          const y = PAD_TOP + i * 32 + 16
          const color = colorFor(name)
          return (
            <g key={name}>
              <text x={PAD_LEFT - 8} y={y + 4} fill="#cbd5e1" fontSize="11" textAnchor="end">
                {prettyName(name)}
              </text>
              {/* Whisker line */}
              <line x1={xPos(v.sharpe_min)} x2={xPos(v.sharpe_max)} y1={y} y2={y} stroke={color} strokeWidth={1.5} />
              {/* Min/max caps */}
              <line x1={xPos(v.sharpe_min)} x2={xPos(v.sharpe_min)} y1={y - 4} y2={y + 4} stroke={color} strokeWidth={1.5} />
              <line x1={xPos(v.sharpe_max)} x2={xPos(v.sharpe_max)} y1={y - 4} y2={y + 4} stroke={color} strokeWidth={1.5} />
              {/* Box (Q1 to Q3) */}
              <rect
                x={xPos(v.sharpe_q1)} y={y - 8}
                width={Math.max(2, xPos(v.sharpe_q3) - xPos(v.sharpe_q1))}
                height={16}
                fill={color} fillOpacity={0.3}
                stroke={color} strokeWidth={1.5}
              />
              {/* Median */}
              <line
                x1={xPos(v.sharpe_median)} x2={xPos(v.sharpe_median)}
                y1={y - 8} y2={y + 8}
                stroke="#f9fafb" strokeWidth={2}
              />
            </g>
          )
        })}
      </svg>
    </div>
  )
}
