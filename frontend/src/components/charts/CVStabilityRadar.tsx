/**
 * CVStabilityRadar — six-axis robustness profile per strategy. Renders one
 * radar per strategy in a small-multiples grid so the audience can compare
 * shapes (balanced hexagon = robust, lopsided = weak on one dimension).
 *
 * Custom SVG: recharts has RadarChart but small-multiples grid layout is
 * cleaner with hand-drawn SVG sized exactly for our use case.
 */
import type { CVRadarPoint } from '../../types/charts'
import { colorFor, prettyName, tooltipLine, typeFor } from '../../lib/strategyColors'

const AXES: (keyof CVRadarPoint)[] = [
  'walk_forward', 'cpcv', 'permutation', 'regime', 'oos', 'stability',
]
const AXIS_LABELS: Record<keyof CVRadarPoint, string> = {
  walk_forward: 'WF',
  cpcv:         'CPCV',
  permutation:  'Perm',
  regime:       'Reg',
  oos:          'OOS',
  stability:    'Stab',
}

interface Props {
  radar: Record<string, CVRadarPoint>
}

function RadarSmall({ name, point }: { name: string; point: CVRadarPoint }) {
  const SIZE = 160
  const cx = SIZE / 2
  const cy = SIZE / 2
  const radius = SIZE * 0.34
  const color = colorFor(name)
  const t = typeFor(name)
  const badgeColor = t === 'dynamic' ? '#3b82f6' : t === 'static' ? '#64748b' : null

  // Polar coordinates for each axis (starting at 12 o'clock, going clockwise)
  const points = AXES.map((axis, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / AXES.length
    const v = Math.max(0, Math.min(1, point[axis]))
    return {
      axis,
      x: cx + Math.cos(angle) * radius * v,
      y: cy + Math.sin(angle) * radius * v,
      xLabel: cx + Math.cos(angle) * (radius + 12),
      yLabel: cy + Math.sin(angle) * (radius + 12),
      xAxisEnd: cx + Math.cos(angle) * radius,
      yAxisEnd: cy + Math.sin(angle) * radius,
    }
  })

  const polygon = points.map((p) => `${p.x},${p.y}`).join(' ')

  return (
    <div className="bg-navy-800/60 rounded p-2 border border-border/40">
      <div className="text-2xs mb-1 text-center flex items-center justify-center gap-1.5">
        <span className="text-muted">{prettyName(name)}</span>
        {badgeColor && (
          <span
            className="text-2xs px-1 py-0.5 rounded border font-medium"
            style={{
              color: badgeColor,
              borderColor: `${badgeColor}30`,
              background: `${badgeColor}10`,
            }}
          >
            {t!.toUpperCase()}
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full">
        {/* Axis spokes */}
        {points.map((p) => (
          <line
            key={p.axis as string}
            x1={cx} y1={cy} x2={p.xAxisEnd} y2={p.yAxisEnd}
            stroke="#1e3a5c" strokeWidth={0.5}
          />
        ))}
        {/* Concentric grid circles at 0.5 and 1.0 */}
        <circle cx={cx} cy={cy} r={radius} stroke="#1e3a5c" fill="none" strokeWidth={0.5} />
        <circle cx={cx} cy={cy} r={radius * 0.5} stroke="#1e3a5c" fill="none" strokeWidth={0.5} strokeDasharray="2,2" />
        {/* Filled polygon — title hovers anywhere in the shape */}
        <polygon points={polygon} fill={color} fillOpacity={0.35} stroke={color} strokeWidth={1.5}>
          <title>{tooltipLine(name, 'CV stability axes', `WF ${point.walk_forward.toFixed(2)}, CPCV ${point.cpcv.toFixed(2)}, Stab ${point.stability.toFixed(2)}`)}</title>
        </polygon>
        {/* Axis labels */}
        {points.map((p) => (
          <text
            key={p.axis as string}
            x={p.xLabel} y={p.yLabel + 3}
            fill="#64748b" fontSize="8" textAnchor="middle"
          >
            {AXIS_LABELS[p.axis as keyof CVRadarPoint]}
          </text>
        ))}
      </svg>
    </div>
  )
}

export default function CVStabilityRadar({ radar }: Props) {
  const entries = Object.entries(radar)
  if (entries.length === 0) {
    return (
      <div className="card p-4" data-testid="cv-stability-radar">
        <h3 className="text-white font-semibold text-sm">CV Stability Radar</h3>
        <p className="text-muted text-xs mt-3">Loading CV data…</p>
      </div>
    )
  }

  return (
    <div className="card p-4" data-testid="cv-stability-radar">
      <div className="mb-3">
        <h3 className="text-white font-semibold text-sm">CV Stability Radar</h3>
        <p className="text-muted text-xs mt-0.5">
          Six axes per strategy — walk-forward, CPCV, permutation, regime, OOS, composite stability
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        {entries.map(([name, point]) => (
          <RadarSmall key={name} name={name} point={point} />
        ))}
      </div>
    </div>
  )
}
