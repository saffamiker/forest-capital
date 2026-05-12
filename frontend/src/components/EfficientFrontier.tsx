import {
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  Line,
  ComposedChart,
} from 'recharts'
import type { EfficientFrontierData, FrontierPoint, PortfolioPoint } from '../types/api'

const STRATEGY_COLORS: Record<string, string> = {
  BENCHMARK:          '#64748b',
  CLASSIC_60_40:      '#60a5fa',
  RISK_PARITY:        '#34d399',
  MIN_VARIANCE:       '#a78bfa',
  EQUAL_WEIGHT:       '#fb923c',
  MOMENTUM_ROTATION:  '#f472b6',
  REGIME_SWITCHING:   '#22c55e',
  VOL_TARGETING:      '#3b82f6',
  BLACK_LITTERMAN:    '#fbbf24',
  MAX_SHARPE_ROLLING: '#e879f9',
}

interface TooltipEntry {
  payload?: FrontierPoint & { strategy?: string }
}

const CustomTooltip = ({ active, payload }: { active?: boolean; payload?: TooltipEntry[] }) => {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div className="card p-3 text-xs space-y-1 shadow-xl min-w-[160px]">
      <div className="font-semibold text-white text-xs border-b border-border pb-1 mb-1">
        {d.strategy ?? 'Frontier'}
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-muted">Volatility</span>
        <span className="font-mono text-white">{d.volatility != null ? (d.volatility * 100).toFixed(1) : '—'}%</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-muted">Exp. Return</span>
        <span className="font-mono text-white">{d.expected_return != null ? (d.expected_return * 100).toFixed(1) : '—'}%</span>
      </div>
      {d.sharpe !== undefined && (
        <div className="flex justify-between gap-4">
          <span className="text-muted">Sharpe</span>
          <span className="font-mono text-white">{d.sharpe.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

export default function EfficientFrontier({ data }: { data: EfficientFrontierData }) {
  const {
    frontier_points = [],
    portfolio_points = [],
    max_sharpe_point,
  } = data

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-white font-semibold text-sm">Efficient Frontier</h3>
          <p className="text-muted text-xs mt-0.5">Risk vs expected return — all 10 strategies</p>
        </div>
        {max_sharpe_point && (
          <div className="text-right">
            <div className="text-2xs text-muted uppercase tracking-wide">Max Sharpe Point</div>
            <div className="font-mono text-xs text-electric">
              σ={(max_sharpe_point.volatility * 100).toFixed(1)}% / μ={(max_sharpe_point.expected_return * 100).toFixed(1)}%
            </div>
          </div>
        )}
      </div>

      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
          <XAxis
            dataKey="volatility"
            type="number"
            name="Volatility"
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fill: '#64748b', fontSize: 11, fontFamily: 'JetBrains Mono' }}
            label={{ value: 'Annualised Volatility', position: 'insideBottom', offset: -10, fill: '#64748b', fontSize: 11 }}
          />
          <YAxis
            dataKey="expected_return"
            type="number"
            name="Return"
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fill: '#64748b', fontSize: 11, fontFamily: 'JetBrains Mono' }}
            label={{ value: 'Expected Return', angle: -90, position: 'insideLeft', offset: 10, fill: '#64748b', fontSize: 11 }}
          />
          <Tooltip content={<CustomTooltip />} />

          {/* Frontier line */}
          <Line
            data={frontier_points}
            dataKey="expected_return"
            type="monotone"
            dot={false}
            stroke="#3b82f6"
            strokeWidth={1.5}
            strokeDasharray="4 2"
            name="Frontier"
          />

          {/* Portfolio scatter points */}
          {portfolio_points.map((pt: PortfolioPoint) => (
            <Scatter
              key={pt.strategy}
              name={pt.strategy}
              data={[pt]}
              fill={STRATEGY_COLORS[pt.strategy] ?? '#94a3b8'}
              r={5}
            />
          ))}

          {/* Max Sharpe reference lines */}
          {max_sharpe_point && (
            <>
              <ReferenceLine x={max_sharpe_point.volatility} stroke="#3b82f6" strokeDasharray="2 4" strokeOpacity={0.4} />
              <ReferenceLine y={max_sharpe_point.expected_return} stroke="#3b82f6" strokeDasharray="2 4" strokeOpacity={0.4} />
            </>
          )}
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-x-4 gap-y-1.5 mt-3 pt-3 border-t border-border">
        {portfolio_points.map((pt: PortfolioPoint) => (
          <div key={pt.strategy} className="flex items-center gap-1.5">
            <div
              className="w-2 h-2 rounded-full shrink-0"
              style={{ backgroundColor: STRATEGY_COLORS[pt.strategy] ?? '#94a3b8' }}
            />
            <span className="text-2xs text-muted truncate">{pt.strategy.replace(/_/g, ' ')}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
