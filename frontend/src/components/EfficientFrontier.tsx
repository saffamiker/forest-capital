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
import { useRef } from 'react'
import type { EfficientFrontierData, FrontierPoint, PortfolioPoint } from '../types/api'
import InfoIcon from './InfoIcon'
import ChartExportButton from './ChartExportButton'
import type { ChartTheme } from '../lib/exportTheme'
import { DARK_CHART_THEME } from '../lib/exportTheme'

interface TooltipEntry {
  payload?: FrontierPoint & { strategy?: string }
}

const CustomTooltip = ({
  active, payload, theme = DARK_CHART_THEME,
}: { active?: boolean; payload?: TooltipEntry[]; theme?: ChartTheme }) => {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div
      className="p-3 text-xs space-y-1 shadow-xl min-w-[160px] rounded"
      style={{ background: theme.tooltipContentStyle.backgroundColor as string,
               border: `1px solid ${theme.border}` }}
    >
      <div className="font-semibold text-xs pb-1 mb-1"
           style={{ color: theme.textPrimary, borderBottom: `1px solid ${theme.border}` }}>
        {d.strategy ?? 'Frontier'}
      </div>
      <div className="flex justify-between gap-4">
        <span style={{ color: theme.textSecondary }}>Volatility</span>
        <span className="font-mono" style={{ color: theme.textPrimary }}>{d.volatility != null ? (d.volatility * 100).toFixed(1) : '—'}%</span>
      </div>
      <div className="flex justify-between gap-4">
        <span style={{ color: theme.textSecondary }}>Exp. Return</span>
        <span className="font-mono" style={{ color: theme.textPrimary }}>{d.expected_return != null ? (d.expected_return * 100).toFixed(1) : '—'}%</span>
      </div>
      {d.sharpe != null && (
        <div className="flex justify-between gap-4">
          <span style={{ color: theme.textSecondary }}>Sharpe</span>
          <span className="font-mono" style={{ color: theme.textPrimary }}>{d.sharpe.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

export default function EfficientFrontier({
  data, theme = DARK_CHART_THEME,
}: { data: EfficientFrontierData; theme?: ChartTheme }) {
  const {
    frontier_points = [],
    portfolio_points = [],
    max_sharpe_point,
  } = data
  const containerRef = useRef<HTMLDivElement>(null)
  const light = theme.mode === 'light'
  // The frontier (non-strategy) line — medium blue on dark, darker on white.
  const frontierStroke = light ? '#1e40af' : '#3b82f6'

  return (
    <div
      className={light ? 'p-4 rounded-lg' : 'card p-4'}
      data-tour="efficient-frontier"
      ref={containerRef}
      style={light ? { background: theme.background, border: `1px solid ${theme.border}` } : undefined}
    >
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="font-semibold text-sm flex items-center" style={{ color: theme.textPrimary }}>
            Efficient Frontier
            {!light && (
              <InfoIcon
                tooltipKey="efficient_frontier"
                metricLabel="Efficient Frontier"
                size="md"
                {...(max_sharpe_point
                  ? { currentValue:
                      `Max Sharpe Point: σ=${(max_sharpe_point.volatility * 100).toFixed(1)}%, `
                      + `μ=${(max_sharpe_point.expected_return * 100).toFixed(1)}%` }
                  : {})}
              />
            )}
          </h3>
          <p className="text-xs mt-0.5" style={{ color: theme.textSecondary }}>
            Risk vs expected return — all 10 strategies
          </p>
        </div>
        <div className="flex items-start gap-3">
          {max_sharpe_point && (
            <div className="text-right">
              <div className="text-2xs uppercase tracking-wide flex items-center justify-end"
                   style={{ color: theme.textSecondary }}>
                Max Sharpe Point
                {!light && (
                  <InfoIcon
                    tooltipKey="max_sharpe_point"
                    metricLabel="Max Sharpe Point"
                    currentValue={
                      `σ=${(max_sharpe_point.volatility * 100).toFixed(1)}% / `
                      + `μ=${(max_sharpe_point.expected_return * 100).toFixed(1)}%`
                    }
                  />
                )}
              </div>
              <div className="font-mono text-xs" style={{ color: frontierStroke }}>
                σ={(max_sharpe_point.volatility * 100).toFixed(1)}% / μ={(max_sharpe_point.expected_return * 100).toFixed(1)}%
              </div>
            </div>
          )}
          {!light && (
            <ChartExportButton chartId="efficient_frontier" containerRef={containerRef} />
          )}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.gridStroke} />
          <XAxis
            dataKey="volatility"
            type="number"
            name="Volatility"
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={theme.axisTick}
            label={{ value: 'Annualised Volatility', position: 'insideBottom', offset: -10, fill: theme.textSecondary, fontSize: 11 }}
          />
          <YAxis
            dataKey="expected_return"
            type="number"
            name="Return"
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={theme.axisTick}
            label={{ value: 'Expected Return', angle: -90, position: 'insideLeft', offset: 10, fill: theme.textSecondary, fontSize: 11 }}
          />
          <Tooltip content={<CustomTooltip theme={theme} />} />

          {/* Frontier line */}
          <Line
            data={frontier_points}
            dataKey="expected_return"
            type="monotone"
            dot={false}
            stroke={frontierStroke}
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
              fill={theme.colorFor(pt.strategy)}
              r={5}
            />
          ))}

          {/* Max Sharpe reference lines */}
          {max_sharpe_point && (
            <>
              <ReferenceLine x={max_sharpe_point.volatility} stroke={frontierStroke} strokeDasharray="2 4" strokeOpacity={0.4} />
              <ReferenceLine y={max_sharpe_point.expected_return} stroke={frontierStroke} strokeDasharray="2 4" strokeOpacity={0.4} />
            </>
          )}
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-x-4 gap-y-1.5 mt-3 pt-3"
           style={{ borderTop: `1px solid ${theme.border}` }}>
        {portfolio_points.map((pt: PortfolioPoint) => (
          <div key={pt.strategy} className="flex items-center gap-1.5">
            <div
              className="w-2 h-2 rounded-full shrink-0"
              style={{ backgroundColor: theme.colorFor(pt.strategy) }}
            />
            <span className="text-2xs truncate" style={{ color: theme.textSecondary }}>
              {pt.strategy.replace(/_/g, ' ')}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
