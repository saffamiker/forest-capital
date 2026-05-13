/**
 * RegimeTimeline — colour-coded horizontal band of regime states across the
 * full 2002-2025 monthly series. BULL=green, BEAR=red, TRANSITION=amber.
 * Audience sees regime persistence (long colour blocks) and crisis dates
 * (red bars around GFC 2008, COVID 2020, rate hikes 2022).
 *
 * The chart aux endpoint returns the threshold-classified series. HMM
 * states aren't shown alongside (yet) because fitting HMM on every chart
 * fetch is too slow; this is acceptable for Sprint 6 because the threshold
 * series captures the headline regime story.
 */
import type { RegimeTimelinePoint } from '../../types/charts'

interface Props {
  timeline: RegimeTimelinePoint[]
}

const REGIME_COLORS = {
  BULL:       '#10b981',
  BEAR:       '#ef4444',
  TRANSITION: '#f59e0b',
} as const

export default function RegimeTimeline({ timeline }: Props) {
  if (timeline.length === 0) {
    return (
      <div className="card p-4" data-testid="regime-timeline">
        <h3 className="text-white font-semibold text-sm">Regime Timeline</h3>
        <p className="text-muted text-xs mt-3">Loading regime classifications…</p>
      </div>
    )
  }

  const WIDTH = 960
  const HEIGHT = 80
  const PAD_LEFT = 60
  const PAD_RIGHT = 12
  const PAD_TOP = 24
  const innerW = WIDTH - PAD_LEFT - PAD_RIGHT
  const innerH = HEIGHT - PAD_TOP - 20
  const cellW = innerW / timeline.length

  // Year tick positions: one tick per January
  const yearTicks = timeline
    .map((p, i) => ({ year: p.date.slice(0, 4), i }))
    .filter((t, idx, arr) => idx === 0 || t.year !== arr[idx - 1].year)

  // Summary counts
  const counts = timeline.reduce<Record<string, number>>((acc, p) => {
    acc[p.regime] = (acc[p.regime] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="card p-4" data-testid="regime-timeline">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <h3 className="text-white font-semibold text-sm">Regime Timeline</h3>
          <p className="text-muted text-xs mt-0.5">
            Threshold classification per month · {timeline[0]?.date.slice(0, 7)} → {timeline[timeline.length - 1]?.date.slice(0, 7)}
          </p>
        </div>
        <div className="flex gap-3 text-2xs">
          {(['BULL', 'BEAR', 'TRANSITION'] as const).map((r) => (
            <div key={r} className="flex items-center gap-1.5">
              <span className="w-2 h-2 inline-block rounded-sm" style={{ background: REGIME_COLORS[r] }} />
              <span className="text-muted">{r}: <span className="text-cbd5e1 font-mono">{counts[r] ?? 0}</span></span>
            </div>
          ))}
        </div>
      </div>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" preserveAspectRatio="none">
        {timeline.map((p, i) => (
          <rect
            key={p.date}
            x={PAD_LEFT + i * cellW}
            y={PAD_TOP}
            width={cellW + 0.5}
            height={innerH}
            fill={REGIME_COLORS[p.regime]}
            opacity={0.85}
          >
            <title>{`${p.date}: ${p.regime}`}</title>
          </rect>
        ))}
        {yearTicks.map((t) => (
          <g key={`${t.year}-${t.i}`}>
            <line
              x1={PAD_LEFT + t.i * cellW}
              x2={PAD_LEFT + t.i * cellW}
              y1={PAD_TOP - 3}
              y2={PAD_TOP + innerH + 3}
              stroke="#1e3a5c"
              strokeWidth={0.5}
            />
            <text
              x={PAD_LEFT + t.i * cellW}
              y={HEIGHT - 4}
              fill="#64748b"
              fontSize="9"
              textAnchor="middle"
            >
              {t.year}
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}
