/**
 * WalkForwardChart — rolling OOS Sharpe per strategy across walk-forward
 * windows. Each line shows one strategy's OOS Sharpe sampled every 6
 * months on a 36-month-train / 12-month-test cadence (matches CLAUDE.md
 * Section 8). Strategies with stable lines are robust across time windows.
 */
import { useMemo, useRef, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Legend } from 'recharts'
import type { WalkForwardWindow } from '../../types/charts'
import { colorFor, prettyName, tooltipLine } from '../../lib/strategyColors'
import { GRID_STROKE, AXIS_TICK, AXIS_TICK_COLOR, TOOLTIP_CONTENT_STYLE, TOOLTIP_LABEL_STYLE, REGIME_BREAK_DATE, REGIME_BREAK_COLOR, REGIME_BREAK_LABEL } from '../../lib/chartStyle'
import ChartExportButton from '../ChartExportButton'
import InfoIcon from '../InfoIcon'

interface Props {
  walkForward: Record<string, WalkForwardWindow[]>
}

export default function WalkForwardChart({ walkForward }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const strategies = Object.keys(walkForward)
  const [visible, setVisible] = useState<Set<string>>(() => {
    // Default-on: the 4 strategies most likely to be discussed in council
    return new Set(['BENCHMARK', 'VOL_TARGETING', 'REGIME_SWITCHING', 'CLASSIC_60_40'])
  })

  // Pivot {strategy: [{window_end, oos_sharpe}, ...]} into row-major data
  // for recharts: [{window_end: '2010-01', BENCHMARK: 0.5, VOL_TARGETING: 0.7}, ...]
  const data = useMemo(() => {
    const byDate: Record<string, Record<string, number | string>> = {}
    for (const [name, windows] of Object.entries(walkForward)) {
      for (const w of windows) {
        if (!byDate[w.window_end]) byDate[w.window_end] = { window_end: w.window_end }
        byDate[w.window_end][name] = w.oos_sharpe
      }
    }
    return Object.values(byDate).sort((a, b) => String(a.window_end).localeCompare(String(b.window_end)))
  }, [walkForward])

  // The ReferenceLine on a category x-axis only renders when its x value
  // matches an actual window_end tick. Walk-forward windows are
  // 6-month-stepped, so '2022-01-31' itself rarely exists — anchor the
  // marker to the last window_end at or before the regime break so it
  // always lands on a real tick. window_end strings sort lexically.
  const regimeBreakX = useMemo<string | null>(() => {
    const ends = data.map((d) => String(d.window_end))
    const atOrBefore = ends.filter((e) => e <= REGIME_BREAK_DATE)
    return atOrBefore.length > 0 ? atOrBefore[atOrBefore.length - 1]! : null
  }, [data])

  const toggle = (name: string) => {
    setVisible((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  if (data.length === 0) {
    return (
      <div className="card p-4" data-testid="walk-forward-chart" ref={containerRef}>
        <h3 className="text-white font-semibold text-sm">Walk-Forward OOS Sharpe</h3>
        <p className="text-muted text-xs mt-3">Loading walk-forward data…</p>
      </div>
    )
  }

  return (
    <div className="card p-4" data-testid="walk-forward-chart" ref={containerRef}>
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold text-sm">
            Walk-Forward OOS Sharpe
            <InfoIcon tooltipKey="walk_forward_chart" metricLabel="Walk-Forward OOS Sharpe" size="md" />
          </h3>
          <ChartExportButton chartId="walk_forward_oos_sharpe" containerRef={containerRef} />
        </div>
        <p className="text-muted text-xs mt-0.5">
          Rolling 36-month train / 12-month test, stepped every 6 months · click to toggle
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-3">
        {strategies.map((name) => {
          const on = visible.has(name)
          return (
            <button
              key={name}
              onClick={() => toggle(name)}
              className={`text-2xs px-2 py-0.5 rounded border transition-colors ${on ? 'text-white' : 'border-border text-muted opacity-50'}`}
              style={on ? {
                backgroundColor: `${colorFor(name)}20`,
                borderColor: `${colorFor(name)}60`,
                color: colorFor(name),
              } : {}}
            >
              {prettyName(name)}
            </button>
          )
        })}
      </div>

      <div style={{ width: '100%', height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 20, left: 0, bottom: 8 }}>
            <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" />
            <XAxis
              dataKey="window_end"
              tick={AXIS_TICK}
              tickFormatter={(v) => String(v).slice(0, 7)}
              stroke={GRID_STROKE}
            />
            <YAxis
              tick={AXIS_TICK}
              stroke={GRID_STROKE}
              label={{ value: 'OOS Sharpe', angle: -90, position: 'insideLeft', fill: AXIS_TICK_COLOR, fontSize: 10 }}
            />
            <Tooltip
              contentStyle={TOOLTIP_CONTENT_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              // Standardised "Strategy DYNAMIC · OOS Sharpe: 1.02" rows
              // — same format used everywhere else via tooltipLine().
              formatter={(value: number, name: string) => [
                tooltipLine(name, 'OOS Sharpe', value.toFixed(2)),
                '',
              ]}
            />
            <ReferenceLine y={0} stroke="#ef4444" strokeOpacity={0.4} strokeDasharray="2 2" />
            {regimeBreakX && (
              <ReferenceLine
                x={regimeBreakX}
                stroke={REGIME_BREAK_COLOR}
                strokeDasharray="4 3"
                label={{ value: REGIME_BREAK_LABEL, fill: REGIME_BREAK_COLOR, fontSize: 10, position: 'top' }}
              />
            )}
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {strategies.filter((n) => visible.has(n)).map((name) => (
              <Line
                key={name}
                type="monotone"
                // connectNulls bridges windows where a strategy has no
                // entry in chartData.walk_forward — without it recharts
                // breaks the polyline at every sparse point, producing
                // disconnected dots that only appear on hover. The
                // walk-forward windows are 6-month-stepped, so different
                // strategies legitimately land on slightly different
                // window_end dates.
                connectNulls={true}
                dataKey={name}
                name={prettyName(name)}
                stroke={colorFor(name)}
                strokeWidth={1.8}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
