/**
 * RegimeConditionalPerformance — strategy Sharpe in BULL / BEAR / TRANSITION
 * regimes. Grouped bar chart so the audience can see at a glance which
 * strategies hold up across regimes (similar bar heights) vs which only
 * work in bull markets (BULL tall, BEAR short).
 */
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import type { RegimeConditional } from '../../types/charts'
import { prettyName, typeFor } from '../../lib/strategyColors'

interface Props {
  regimeConditional: Record<string, RegimeConditional>
}

const REGIME_COLORS = {
  BULL:       '#10b981',
  BEAR:       '#ef4444',
  TRANSITION: '#f59e0b',
} as const

export default function RegimeConditionalPerformance({ regimeConditional }: Props) {
  const entries = Object.entries(regimeConditional)
  if (entries.length === 0) {
    return (
      <div className="card p-4" data-testid="regime-conditional-performance">
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

  return (
    <div className="card p-4" data-testid="regime-conditional-performance">
      <div className="mb-3">
        <h3 className="text-white font-semibold text-sm">Regime-Conditional Performance</h3>
        <p className="text-muted text-xs mt-0.5">
          Sharpe ratio per strategy by regime — balanced bars indicate all-weather strategies
        </p>
      </div>

      <div style={{ width: '100%', height: 320 }}>
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 20, left: 0, bottom: 60 }}>
            <CartesianGrid stroke="#1e3a5c" strokeDasharray="3 3" />
            <XAxis
              dataKey="strategy"
              tick={{ fill: '#64748b', fontSize: 9 }}
              stroke="#1e3a5c"
              angle={-30}
              textAnchor="end"
              interval={0}
              height={70}
            />
            <YAxis
              tick={{ fill: '#64748b', fontSize: 10 }}
              stroke="#1e3a5c"
              label={{ value: 'Sharpe', angle: -90, position: 'insideLeft', fill: '#64748b', fontSize: 10 }}
            />
            <Tooltip
              contentStyle={{ background: '#0d1929', border: '1px solid #1e3a5c', fontSize: 11 }}
              labelStyle={{ color: '#cbd5e1' }}
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
