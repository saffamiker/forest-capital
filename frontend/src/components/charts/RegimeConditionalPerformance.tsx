/**
 * RegimeConditionalPerformance — strategy Sharpe in BULL / BEAR / TRANSITION
 * regimes. Grouped bar chart so the audience can see at a glance which
 * strategies hold up across regimes (similar bar heights) vs which only
 * work in bull markets (BULL tall, BEAR short).
 */
import { useRef } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import type { RegimeConditional } from '../../types/charts'
import { prettyName, typeFor } from '../../lib/strategyColors'
import { GRID_STROKE, AXIS_TICK, AXIS_TICK_COLOR, TOOLTIP_CONTENT_STYLE, TOOLTIP_LABEL_STYLE } from '../../lib/chartStyle'
import ChartExportButton from '../ChartExportButton'
import InfoIcon from '../InfoIcon'

interface Props {
  regimeConditional: Record<string, RegimeConditional>
}

const REGIME_COLORS = {
  BULL:       '#10b981',
  BEAR:       '#ef4444',
  TRANSITION: '#f59e0b',
} as const

export default function RegimeConditionalPerformance({ regimeConditional }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const entries = Object.entries(regimeConditional)
  if (entries.length === 0) {
    return (
      <div className="card p-4" data-testid="regime-conditional-performance" ref={containerRef}>
        <h3 className="text-white font-semibold text-sm">Regime-Conditional Performance</h3>
        <p className="text-muted text-xs mt-3">Loading regime data…</p>
      </div>
    )
  }

  // Carry the raw strategy key alongside the display name so the tooltip
  // formatter can produce the standard "Strategy DYNAMIC · regime: Sharpe" line.
  const data = entries.map(([name, regimes]) => ({
    strategy: prettyName(name),
    strategyKey: name,
    BULL:       regimes.BULL.sharpe,
    BEAR:       regimes.BEAR.sharpe,
    TRANSITION: regimes.TRANSITION.sharpe,
  }))

  // Sharpe ranges per regime — the explainer reads the actual spread.
  const sharpeRange = (key: 'BULL' | 'BEAR' | 'TRANSITION'): string => {
    const vals = data.map((d) => d[key])
    return `${key} ${Math.min(...vals).toFixed(2)} to ${Math.max(...vals).toFixed(2)}`
  }
  const explainValue =
    `Sharpe ratio by regime across ${data.length} strategies — `
    + (['BULL', 'BEAR', 'TRANSITION'] as const).map(sharpeRange).join(', ') + '.'

  return (
    <div className="card p-4" data-testid="regime-conditional-performance" ref={containerRef}>
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold text-sm">
            Regime-Conditional Performance
            <InfoIcon
              tooltipKey="regime_conditional_performance"
              metricLabel="Regime-Conditional Performance"
              size="md"
              currentValue={explainValue}
            />
          </h3>
          <ChartExportButton chartId="regime_conditional_performance" containerRef={containerRef} />
        </div>
        <p className="text-muted text-xs mt-0.5">
          Sharpe ratio per strategy by regime — balanced bars indicate all-weather strategies
        </p>
      </div>

      <div style={{ width: '100%', height: 320 }}>
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 20, left: 0, bottom: 60 }}>
            <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" />
            <XAxis
              dataKey="strategy"
              tick={AXIS_TICK}
              stroke={GRID_STROKE}
              angle={-30}
              textAnchor="end"
              interval={0}
              height={70}
            />
            <YAxis
              tick={AXIS_TICK}
              stroke={GRID_STROKE}
              label={{ value: 'Sharpe', angle: -90, position: 'insideLeft', fill: AXIS_TICK_COLOR, fontSize: 10 }}
            />
            <Tooltip
              contentStyle={TOOLTIP_CONTENT_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              // Custom label: "Strategy Name DYNAMIC" — same shape as the
              // tooltipLine helper but split into label (header) + per-regime
              // rows because recharts renders multi-series tooltips natively.
              labelFormatter={(label, payload) => {
                const row = payload?.[0]?.payload as { strategyKey?: string } | undefined
                const key = row?.strategyKey ?? ''
                const t = typeFor(key)
                return t ? `${label} ${t.toUpperCase()}` : String(label)
              }}
              formatter={(value: number, name: string) => [
                `${name} Sharpe: ${value.toFixed(2)}`,
                '',
              ]}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar dataKey="BULL"       fill={REGIME_COLORS.BULL}       isAnimationActive={false} />
            <Bar dataKey="BEAR"       fill={REGIME_COLORS.BEAR}       isAnimationActive={false} />
            <Bar dataKey="TRANSITION" fill={REGIME_COLORS.TRANSITION} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
