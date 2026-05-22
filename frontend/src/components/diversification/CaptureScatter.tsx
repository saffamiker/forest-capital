/**
 * CaptureScatter — the "Up / Down Capture" section.
 *
 * Scatter plot of up_capture (x-axis) vs down_capture (y-axis) for the
 * ten strategies, with the benchmark anchored at (100, 100) by
 * definition. Period toggle (Full / Pre-2022 / Post-2022) re-anchors
 * every point in place — the geometry of the chart is what tells the
 * story, so we keep the axes fixed and let the points move.
 *
 * Interpretation:
 *   - A strategy in the UPPER-LEFT quadrant (high up-capture, low
 *     down-capture) is the ideal diversifier — captures most of the
 *     benchmark's upside while sidestepping its downside.
 *   - A strategy in the LOWER-RIGHT quadrant is the worst case —
 *     loses with the benchmark but doesn't ride the recovery.
 *   - The 45° line up_capture = down_capture is the "no asymmetry"
 *     baseline. Above it: better-than-symmetric (good). Below: worse.
 *
 * Capture score (up - down) is also returned by the backend per
 * strategy and shown in a sortable summary table below the scatter
 * for readers who prefer numbers.
 *
 * Backend payload from /api/v1/analytics/capture-ratios (item 8
 * commit 1). The CaptureWindow values are PERCENTAGES (100 means
 * captures the benchmark exactly).
 */
import { useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Cell,
} from 'recharts'
import { useCaptureRatios } from '../../lib/useDiversificationData'
import type { CaptureWindow } from '../../types/diversification'


type PeriodKey = 'full' | 'pre_2022' | 'post_2022'

const PERIOD_LABEL: Record<PeriodKey, string> = {
  full:      'Full period',
  pre_2022:  'Pre-2022',
  post_2022: 'Post-2022',
}


/**
 * Strategy-specific accent colours (mirrors the convention used on the
 * Dashboard for cross-screen recognition). The benchmark always reads
 * as the cohort red so the eye finds it first.
 */
const STRATEGY_COLOUR: Record<string, string> = {
  BENCHMARK:           '#ef4444',
  CLASSIC_60_40:       '#94a3b8',
  RISK_PARITY:         '#10b981',
  MIN_VARIANCE:        '#64748b',
  EQUAL_WEIGHT:        '#475569',
  MOMENTUM_ROTATION:   '#06b6d4',
  REGIME_SWITCHING:    '#f59e0b',
  VOL_TARGETING:       '#3b82f6',
  BLACK_LITTERMAN:     '#0d9488',
  MAX_SHARPE_ROLLING:  '#8b5cf6',
}
const DEFAULT_COLOUR = '#cbd5e1'  // slate-300


interface ScatterPoint {
  strategy: string
  up: number
  down: number
  score: number
  colour: string
}


function extractPoints(
  strategies: { strategy: string; full: CaptureWindow;
                pre_2022: CaptureWindow; post_2022: CaptureWindow }[],
  period: PeriodKey,
): ScatterPoint[] {
  return strategies
    .map((s) => {
      const w = s[period]
      if (w.up_capture === null || w.down_capture === null) return null
      return {
        strategy: s.strategy,
        up: w.up_capture,
        down: w.down_capture,
        score: w.capture_score ?? (w.up_capture - w.down_capture),
        colour: STRATEGY_COLOUR[s.strategy] ?? DEFAULT_COLOUR,
      }
    })
    .filter((p): p is ScatterPoint => p !== null)
}


export function CaptureScatter() {
  const { data, loading, error } = useCaptureRatios()
  const [period, setPeriod] = useState<PeriodKey>('full')

  const points = useMemo(
    () => data ? extractPoints(data.strategies, period) : [],
    [data, period])

  if (loading) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="capture-scatter-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading capture ratios…
        </div>
      </div>
    )
  }
  if (error || !data || data.strategies.length === 0) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}>
        <h2 className="text-base font-semibold text-white mb-2">
          Up / Down Capture
        </h2>
        <p className="text-sm text-muted">
          Capture ratio data unavailable.
          {error ? <span className="block mt-1 text-xs">{error}</span> : null}
        </p>
      </div>
    )
  }

  // Sort ranking table by capture_score descending — best diversifier first.
  const ranked = [...points].sort((a, b) => b.score - a.score)

  return (
    <div className="card p-5"
         style={{ borderLeft: '3px solid #3b82f6' }}
         data-testid="capture-scatter">
      <div className="flex items-start justify-between mb-3 gap-2 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-white">
            Up / Down Capture
          </h2>
          <p className="text-xs text-muted mt-0.5">
            Each point is one strategy's up-market vs down-market capture of
            the benchmark return. Upper-left (high up, low down) is the
            ideal diversifier; the 45° line is symmetric capture.
            Benchmark anchors at (100, 100) by definition.
          </p>
        </div>
        <div className="flex gap-1 shrink-0"
             data-testid="capture-period-toggle">
          {(['full', 'pre_2022', 'post_2022'] as PeriodKey[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPeriod(p)}
              data-testid={`capture-period-${p}`}
              className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                period === p
                  ? 'border-electric bg-electric/10 text-electric'
                  : 'border-border text-muted hover:text-white hover:border-border/80'
              }`}>
              {PERIOD_LABEL[p]}
            </button>
          ))}
        </div>
      </div>

      <div style={{ width: '100%', height: 320 }}
           data-testid="capture-scatter-chart">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            {/* 45° reference: up_capture = down_capture (symmetric). */}
            <ReferenceLine
              segment={[{ x: 0, y: 0 }, { x: 150, y: 150 }]}
              stroke="#475569" strokeDasharray="4 4" />
            {/* Benchmark anchor: x=100 vertical, y=100 horizontal. */}
            <ReferenceLine x={100} stroke="#475569" strokeWidth={1} />
            <ReferenceLine y={100} stroke="#475569" strokeWidth={1} />
            <XAxis
              type="number" dataKey="up" name="Up capture"
              domain={[0, 150]} tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: 'Up capture (%)', position: 'bottom',
                       fill: '#94a3b8', fontSize: 11 }} />
            <YAxis
              type="number" dataKey="down" name="Down capture"
              domain={[0, 150]} tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: 'Down capture (%)', angle: -90,
                       position: 'insideLeft', fill: '#94a3b8',
                       fontSize: 11 }} />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              contentStyle={{ backgroundColor: '#0f172a',
                              border: '1px solid #1e3a5c',
                              borderRadius: 6, fontSize: 12 }}
              formatter={(value: number) => `${value.toFixed(1)}%`}
              labelFormatter={() => ''}
              content={(props) => {
                const { active, payload } = props
                if (!active || !payload || payload.length === 0) return null
                const p = payload[0].payload as ScatterPoint
                return (
                  <div className="bg-navy-900 border border-border rounded px-3 py-2 text-xs">
                    <div className="text-white font-semibold mb-1">
                      {p.strategy}
                    </div>
                    <div className="text-slate-300 font-mono">
                      Up: {p.up.toFixed(1)}%
                    </div>
                    <div className="text-slate-300 font-mono">
                      Down: {p.down.toFixed(1)}%
                    </div>
                    <div className="text-slate-300 font-mono mt-1">
                      Score: {p.score.toFixed(1)}
                    </div>
                  </div>
                )
              }} />
            <Scatter data={points}>
              {points.map((p) => (
                <Cell key={p.strategy} fill={p.colour}
                      stroke={p.colour} strokeWidth={1} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {/* Ranking table — capture score (up - down) sorted descending so
          the most diversifying strategies surface first. */}
      <div className="mt-4 overflow-x-auto" data-testid="capture-ranking-scroll">
        <table className="w-full text-xs"
               data-testid="capture-ranking">
          <thead>
            <tr className="text-2xs uppercase tracking-wider text-muted
                            border-b border-border">
              <th className="text-left px-2 py-1.5 font-medium">Strategy</th>
              <th className="text-right px-2 py-1.5 font-medium">Up</th>
              <th className="text-right px-2 py-1.5 font-medium">Down</th>
              <th className="text-right px-2 py-1.5 font-medium">Score</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((p) => (
              <tr key={p.strategy}
                  data-testid={`capture-rank-${p.strategy}`}
                  className="border-b border-border/40 hover:bg-navy-700/40
                              transition-colors">
                <td className="px-2 py-1 text-slate-300 font-mono">
                  <span className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
                        style={{ backgroundColor: p.colour }} />
                  {p.strategy}
                </td>
                <td className="px-2 py-1 text-right text-slate-300 font-mono">
                  {p.up.toFixed(1)}%
                </td>
                <td className="px-2 py-1 text-right text-slate-300 font-mono">
                  {p.down.toFixed(1)}%
                </td>
                <td className={`px-2 py-1 text-right font-mono font-semibold
                                ${p.score > 0 ? 'text-positive'
                                              : 'text-negative'}`}>
                  {p.score > 0 ? '+' : ''}{p.score.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
