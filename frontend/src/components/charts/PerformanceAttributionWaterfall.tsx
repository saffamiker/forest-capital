/**
 * PerformanceAttributionWaterfall — Brinson-Hood-Beebower decomposition of
 * active return per strategy. Bars: allocation, selection, interaction, total.
 *
 * Picks the top 4 strategies by total_active and renders a small grid of
 * waterfall charts — too many strategies clutter the chart. The audience
 * sees which strategies earn outperformance from asset allocation vs
 * timing (selection).
 */
import { useRef } from 'react'
import type { AttributionResult } from '../../types/charts'
import { colorFor, prettyName, tooltipLine, typeFor } from '../../lib/strategyColors'
import ChartExportButton from '../ChartExportButton'
import InfoIcon from '../InfoIcon'
import { useChartTheme } from '../../lib/useChartTheme'
import type { ChartTheme } from '../../lib/exportTheme'

interface Props {
  attribution: Record<string, AttributionResult>
}

function WaterfallSmall({
  name, attr, chartTheme,
}: { name: string; attr: AttributionResult; chartTheme: ChartTheme }) {
  const components = [
    { label: 'Alloc',  metric: 'Allocation effect', value: attr.allocation },
    { label: 'Select', metric: 'Selection effect',  value: attr.selection },
    { label: 'Inter',  metric: 'Interaction effect', value: attr.interaction },
    { label: 'Total',  metric: 'Total active return', value: attr.total_active },
  ]
  const allVals = components.map((c) => c.value)
  const absMax = Math.max(0.01, Math.max(...allVals.map(Math.abs)))
  const color = colorFor(name)
  const t = typeFor(name)
  const badgeColor = t === 'dynamic' ? '#3b82f6' : t === 'static' ? '#64748b' : null

  const W = 200
  const H = 110
  const PAD_X = 12
  const PAD_TOP = 8
  const PAD_BOTTOM = 24
  const innerW = W - PAD_X * 2
  const innerH = H - PAD_TOP - PAD_BOTTOM
  const barW = innerW / components.length - 6
  const zeroY = PAD_TOP + innerH / 2

  return (
    <div
      className="rounded p-2 border border-border/40"
      style={{ background: chartTheme.background }}
    >
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
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <line x1={PAD_X} x2={W - PAD_X} y1={zeroY} y2={zeroY} stroke={chartTheme.gridStroke} strokeWidth={1} />
        {components.map((c, i) => {
          const x = PAD_X + i * (barW + 6) + 3
          const h = Math.abs(c.value / absMax) * (innerH / 2)
          const y = c.value >= 0 ? zeroY - h : zeroY
          const barColor = c.label === 'Total' ? color : (c.value >= 0 ? '#10b981' : '#ef4444')
          return (
            <g key={c.label}>
              <title>{tooltipLine(name, c.metric, `${(c.value * 100).toFixed(2)}%`)}</title>
              <rect
                x={x} y={y} width={barW} height={Math.max(1, h)}
                fill={barColor} fillOpacity={c.label === 'Total' ? 1 : 0.65}
              />
              <text
                x={x + barW / 2} y={H - 10}
                fill={chartTheme.textSecondary} fontSize="9" textAnchor="middle"
              >
                {c.label}
              </text>
              <text
                x={x + barW / 2}
                y={c.value >= 0 ? y - 2 : y + h + 9}
                fill={chartTheme.textPrimary} fontSize="8" textAnchor="middle"
              >
                {(c.value * 100).toFixed(1)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

export default function PerformanceAttributionWaterfall({ attribution }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartTheme = useChartTheme()
  const entries = Object.entries(attribution)
  if (entries.length === 0) {
    return (
      <div className="card p-4" data-testid="performance-attribution-waterfall" ref={containerRef}>
        <h3 className="text-white font-semibold text-sm">Performance Attribution Waterfall</h3>
        <p className="text-muted text-xs mt-3">Loading attribution data…</p>
      </div>
    )
  }

  // Top 6 strategies by absolute total active return — keeps the chart legible
  const sorted = [...entries]
    .sort(([, a], [, b]) => Math.abs(b.total_active) - Math.abs(a.total_active))
    .slice(0, 6)

  // The lead strategy's decomposition, passed to the explainer.
  const topAttr = sorted[0]
  const explainValue =
    `Brinson-Hood-Beebower attribution, top ${sorted.length} strategies by `
    + `active return. ${prettyName(topAttr[0])}: allocation `
    + `${(topAttr[1].allocation * 100).toFixed(2)}%, selection `
    + `${(topAttr[1].selection * 100).toFixed(2)}%, interaction `
    + `${(topAttr[1].interaction * 100).toFixed(2)}%, total active `
    + `${(topAttr[1].total_active * 100).toFixed(2)}%.`

  return (
    <div className="card p-4" data-testid="performance-attribution-waterfall" ref={containerRef}>
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold text-sm">
            Performance Attribution Waterfall
            <InfoIcon
              tooltipKey="performance_attribution_waterfall"
              metricLabel="Performance Attribution Waterfall"
              size="md"
              currentValue={explainValue}
            />
          </h3>
          <ChartExportButton chartId="performance_attribution_waterfall" containerRef={containerRef} />
        </div>
        <p className="text-muted text-xs mt-0.5">
          Brinson-Hood-Beebower decomposition · top 6 by total active return · values in %
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {sorted.map(([name, attr]) => (
          <WaterfallSmall key={name} name={name} attr={attr} chartTheme={chartTheme} />
        ))}
      </div>
    </div>
  )
}
