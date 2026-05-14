/**
 * ProbabilisticSharpeChart — Sharpe point estimates with 95% confidence
 * intervals per strategy. Rendered as a horizontal error-bar plot so the
 * audience sees not just the point estimate but the precision: wide CI =
 * uncertain estimate, narrow CI = sample size was sufficient.
 */
import type { StrategyResult } from '../../types/strategies'
import { colorFor, prettyName, tooltipLine, typeFor } from '../../lib/strategyColors'

interface Props {
  strategies: StrategyResult[]
}

export default function ProbabilisticSharpeChart({ strategies }: Props) {
  const usable = strategies
    .filter((s) => s.sharpe_ci_95 && s.sharpe_ci_95.length === 2)
    .sort((a, b) => (a.sharpe_ratio ?? 0) - (b.sharpe_ratio ?? 0))

  if (usable.length === 0) {
    return (
      <div className="card p-4" data-testid="probabilistic-sharpe-chart">
        <h3 className="text-white font-semibold text-sm">Probabilistic Sharpe — 95% Confidence Intervals</h3>
        <p className="text-muted text-xs mt-3">No CI data available yet.</p>
      </div>
    )
  }

  // x-axis domain across all strategies for a shared scale
  const allValues = usable.flatMap((s) => [
    s.sharpe_ci_95![0], s.sharpe_ci_95![1], s.sharpe_ratio ?? 0,
  ])
  const xMin = Math.min(0, Math.floor(Math.min(...allValues) * 10) / 10)
  const xMax = Math.max(1.5, Math.ceil(Math.max(...allValues) * 10) / 10)
  const span = xMax - xMin || 1

  const WIDTH = 720
  const ROW_H = 28
  const HEIGHT = usable.length * ROW_H + 40
  const PAD_LEFT = 160
  const PAD_RIGHT = 60
  const PAD_TOP = 12
  const innerW = WIDTH - PAD_LEFT - PAD_RIGHT
  const xPos = (v: number) => PAD_LEFT + ((v - xMin) / span) * innerW

  return (
    <div className="card p-4" data-testid="probabilistic-sharpe-chart">
      <div className="mb-3">
        <h3 className="text-white font-semibold text-sm">
          Probabilistic Sharpe — 95% Confidence Intervals
        </h3>
        <p className="text-muted text-xs mt-0.5">
          Wide intervals indicate Sharpe estimate is uncertain even when point estimate is high
        </p>
      </div>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full">
        {[0, 0.5, 1.0, 1.5].filter((g) => g >= xMin && g <= xMax).map((g) => (
          <g key={g}>
            <line
              x1={xPos(g)} x2={xPos(g)}
              y1={PAD_TOP} y2={HEIGHT - 20}
              stroke="#1e3a5c" strokeDasharray="3,3" strokeWidth={1}
            />
            <text x={xPos(g)} y={HEIGHT - 6} fill="#64748b" fontSize="10" textAnchor="middle">
              {g.toFixed(1)}
            </text>
          </g>
        ))}

        {usable.map((s, i) => {
          const y = PAD_TOP + i * ROW_H + ROW_H / 2
          const color = colorFor(s.strategy_name)
          const [ciLow, ciHigh] = s.sharpe_ci_95!
          const sr = s.sharpe_ratio ?? 0
          const t = typeFor(s.strategy_name)
          // Inline DYNAMIC/STATIC text after the strategy name — SVG can't
          // host a JSX badge so we use a styled <tspan> instead. Same idea
          // as the StrategyTypeBadge component: electric blue for dynamic,
          // slate for static, omitted entirely when class is unknown.
          const badgeColor = t === 'dynamic' ? '#3b82f6' : t === 'static' ? '#64748b' : null
          const badgeText = t ? t.toUpperCase() : ''
          const tooltip = tooltipLine(s.strategy_name, 'Sharpe', sr.toFixed(2))
          return (
            <g key={s.strategy_name}>
              <title>{tooltip}</title>
              <text x={PAD_LEFT - 8} y={y + 4} fill="#cbd5e1" fontSize="11" textAnchor="end">
                {prettyName(s.strategy_name)}
                {badgeColor && (
                  <tspan dx="6" fill={badgeColor} fontSize="9" fontWeight={600} letterSpacing="0.06em">
                    {badgeText}
                  </tspan>
                )}
              </text>
              {/* CI bar */}
              <line x1={xPos(ciLow)} x2={xPos(ciHigh)} y1={y} y2={y} stroke={color} strokeWidth={2} opacity={0.7}>
                <title>{tooltipLine(s.strategy_name, 'Sharpe 95% CI', `[${ciLow.toFixed(2)}, ${ciHigh.toFixed(2)}]`)}</title>
              </line>
              {/* CI caps */}
              <line x1={xPos(ciLow)} x2={xPos(ciLow)} y1={y - 5} y2={y + 5} stroke={color} strokeWidth={1.5} />
              <line x1={xPos(ciHigh)} x2={xPos(ciHigh)} y1={y - 5} y2={y + 5} stroke={color} strokeWidth={1.5} />
              {/* Point estimate */}
              <circle cx={xPos(sr)} cy={y} r={4} fill={color} stroke="#f9fafb" strokeWidth={1.5}>
                <title>{tooltip}</title>
              </circle>
              {/* Value label to the right */}
              <text
                x={xPos(ciHigh) + 8} y={y + 4}
                fill="#cbd5e1" fontSize="10" fontFamily="monospace"
              >
                {sr.toFixed(2)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
