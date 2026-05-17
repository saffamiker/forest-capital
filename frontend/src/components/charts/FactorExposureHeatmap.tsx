/**
 * FactorExposureHeatmap — Fama-French 3-factor loadings per strategy.
 * Rows: strategies. Columns: Mkt-RF, SMB, HML, plus alpha and R².
 * Cell colour: blue for positive loading, red for negative; intensity
 * scales with magnitude.
 *
 * Audience reads: which strategies are levered to market (high Mkt-RF),
 * to value (high HML), to small-cap (high SMB). Alpha column shows
 * unexplained outperformance; R² shows how much of the strategy's
 * return variance the factor model captures.
 */
import { useRef } from 'react'
import type { FactorLoadings } from '../../types/charts'
import { prettyName, tooltipLine } from '../../lib/strategyColors'
import StrategyTypeBadge from '../StrategyTypeBadge'
import ChartExportButton from '../ChartExportButton'
import InfoIcon from '../InfoIcon'

interface Props {
  factorLoadings: Record<string, FactorLoadings>
}

const FACTOR_KEYS = ['mkt_rf', 'smb', 'hml'] as const
const FACTOR_LABELS: Record<typeof FACTOR_KEYS[number], string> = {
  mkt_rf: 'Mkt-RF',
  smb:    'SMB',
  hml:    'HML',
}

function cellColor(value: number, max: number): string {
  if (max === 0) return '#1a2438'
  const norm = Math.max(-1, Math.min(1, value / max))
  if (norm > 0) {
    const a = Math.abs(norm) * 0.7 + 0.1
    return `rgba(59, 130, 246, ${a})`     // blue for positive
  }
  const a = Math.abs(norm) * 0.7 + 0.1
  return `rgba(239, 68, 68, ${a})`        // red for negative
}

export default function FactorExposureHeatmap({ factorLoadings }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const entries = Object.entries(factorLoadings)
  if (entries.length === 0) {
    return (
      <div className="card p-4" data-testid="factor-exposure-heatmap" ref={containerRef}>
        <h3 className="text-white font-semibold text-sm">Factor Exposure Heatmap</h3>
        <p className="text-muted text-xs mt-3">Loading factor loadings…</p>
      </div>
    )
  }

  // Determine max absolute loading across all factors/strategies for colour normalisation
  const maxLoading = Math.max(
    0.01,
    ...entries.flatMap(([, l]) => FACTOR_KEYS.map((k) => Math.abs(l[k]))),
  )

  return (
    <div className="card p-4" data-testid="factor-exposure-heatmap" ref={containerRef}>
      <div className="mb-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold text-sm">
            Factor Exposure Heatmap
            <InfoIcon tooltipKey="factor_exposure_heatmap" metricLabel="Factor Exposure Heatmap" size="md" />
          </h3>
          <ChartExportButton chartId="factor_exposure_heatmap" containerRef={containerRef} />
        </div>
        <p className="text-muted text-xs mt-0.5">
          Fama-French 3-factor OLS loadings · blue = positive, red = negative
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted text-2xs uppercase tracking-wide">
              <th className="text-left py-2 pr-3">Strategy</th>
              {FACTOR_KEYS.map((k) => (
                <th key={k} className="px-2 py-2 text-center">{FACTOR_LABELS[k]}</th>
              ))}
              <th className="text-right px-2 py-2">α (monthly)</th>
              <th className="text-right px-2 py-2">R²</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([name, loadings]) => (
              <tr key={name} className="border-t border-border/50">
                <td className="py-1.5 pr-3">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-white font-mono">{prettyName(name)}</span>
                    <StrategyTypeBadge strategy={name} />
                  </div>
                </td>
                {FACTOR_KEYS.map((k) => (
                  <td key={k} className="px-2 py-1.5 text-center">
                    <span
                      className="inline-block px-2 py-1 rounded font-mono text-2xs"
                      style={{ background: cellColor(loadings[k], maxLoading), color: '#f9fafb' }}
                      title={tooltipLine(name, FACTOR_LABELS[k] + ' loading', loadings[k].toFixed(2))}
                    >
                      {loadings[k].toFixed(2)}
                    </span>
                  </td>
                ))}
                <td className="text-right px-2 py-1.5 font-mono text-slate-300">
                  {(loadings.alpha * 10000).toFixed(0)} bps
                </td>
                <td className="text-right px-2 py-1.5 font-mono text-slate-300">
                  {loadings.r_squared.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
