/**
 * Forward Confidence Chart — the "future" panel of the landing-page arc,
 * rendered second (after the CIO card).
 *
 * Reads GET /api/v1/forward-projection (the Layer 4 forward Monte Carlo,
 * data_hash-cached; the 10,000-path simulation never runs on a read).
 * Plots three simulated series over 1/3/6/12 months, each with a median
 * line and its 90% band (p05/p95): the regime-conditional blend
 * (regime-path), the benchmark and the classic 60/40 (both from their
 * full-history distribution, no regime conditioning). Below the chart it
 * shows P(blend outperforms) each baseline at every horizon, the current
 * regime + confidence, the not-a-forecast limitation, and an "as of"
 * staleness indicator. Graceful empty state before the first warm.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { Loader2, MessageSquare } from 'lucide-react'
import InfoIcon from './InfoIcon'
import { useChartTheme } from '../lib/useChartTheme'

interface Band { median: number; p05: number; p95: number }
type SeriesBands = Record<string, Band>   // horizon -> band
interface Projection {
  horizons_months?: number[]
  bands?: Record<string, SeriesBands>      // series -> horizon -> band
  p_outperform?: Record<string, Record<string, number>>
  regime?: string | null
  regime_probability?: number | null
  transition_source?: string
  _computed_at?: string | null
}
interface Payload { available: boolean; projection: Projection | null }

const SERIES: { key: string; label: string; color: string }[] = [
  { key: 'blend', label: 'Regime-conditional blend', color: '#3b82f6' },
  { key: 'benchmark', label: 'Benchmark (S&P 500)', color: '#ef4444' },
  { key: 'classic_6040', label: 'Classic 60/40', color: '#94a3b8' },
]

const LIMITATION =
  'Forward simulation uses the HMM transition matrix and ' +
  'regime-conditional return distributions. Not a forecast.'

const pct = (x: number | null | undefined): string =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(0)}%`

function asOf(ts?: string | null): string {
  if (!ts) return 'latest warm'
  // Backend timestamps are UTC. Normalise to ISO and, when the string
  // carries no timezone marker, treat it as UTC ('Z') so the browser
  // converts to the user's local zone instead of misreading the UTC clock
  // as local. Render with the timezone abbreviation so it is unambiguous.
  let s = String(ts).trim().replace(' ', 'T')
  if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z'
  const d = new Date(s)
  return isNaN(d.getTime())
    ? String(ts)
    : d.toLocaleString(undefined, { timeZoneName: 'short' })
}

export default function ForwardConfidenceChart() {
  const navigate = useNavigate()
  const chartTheme = useChartTheme()
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)

  // Hand off to the council with the "prediction" scope so the
  // deliberation injects the cached forward projection (P(outperform),
  // transition matrix, blend). Question pre-filled and editable.
  const askCouncil = () =>
    navigate('/council', {
      state: {
        prefillQuestion:
          'What drives the outperformance probability at 12 months?',
        contextScope: 'prediction',
      },
    })

  useEffect(() => {
    let alive = true
    // axios (not raw fetch) so the X-API-Key auth header rides along via the
    // global default + request interceptor — a raw fetch sends no credentials
    // header, 401s, and silently renders the empty state.
    axios.get<Payload>('/api/v1/forward-projection')
      .then((r) => { if (alive) { setData(r.data); setLoading(false) } })
      .catch(() => { if (alive) { setData({ available: false, projection: null }); setLoading(false) } })
    return () => { alive = false }
  }, [])

  if (loading) {
    return (
      <div className="card p-5 m-4 md:m-6 flex items-center gap-2 text-muted">
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading the forward confidence projection…
      </div>
    )
  }

  const proj = data?.projection
  if (!data?.available || !proj || !proj.bands?.blend) {
    return (
      <div className="card p-5 m-4 md:m-6 text-muted text-sm">
        <div className="text-2xs uppercase tracking-wide mb-1">
          Forward Confidence Projection
        </div>
        The forward simulation has not been computed yet. It is generated
        on the next analytics warm and will appear here once cached.
      </div>
    )
  }

  const horizons = proj.horizons_months || [1, 3, 6, 12]
  const present = SERIES.filter((s) => proj.bands?.[s.key])

  // One row per horizon, flattened to <series>_median / _p05 / _p95.
  const rows = horizons.map((h) => {
    const row: Record<string, number | string> = { month: `${h}mo` }
    for (const s of present) {
      const b = proj.bands?.[s.key]?.[String(h)]
      if (b) {
        row[`${s.key}_median`] = b.median
        row[`${s.key}_p05`] = b.p05
        row[`${s.key}_p95`] = b.p95
      }
    }
    return row
  })

  const conf = typeof proj.regime_probability === 'number'
    ? `${(proj.regime_probability * 100).toFixed(0)}%` : '—'

  return (
    <div className="card p-5 m-4 md:m-6 border-l-2 border-electric">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="text-2xs text-muted uppercase tracking-wide flex items-center gap-1">
          Forward Confidence Projection
          <InfoIcon tooltipKey="monte_carlo_reference"
                    metricLabel="Forward Monte Carlo simulation" />
          <InfoIcon tooltipKey="confidence_band"
                    metricLabel="90% confidence band" />
        </div>
        <div className="text-2xs text-muted font-mono text-right flex items-center gap-1">
          Regime {proj.regime || '—'}
          <InfoIcon tooltipKey="regime_label" metricLabel="Current regime" />
          · confidence {conf} · As of {asOf(proj._computed_at)}
        </div>
      </div>

      <div className="mt-4">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke={chartTheme.gridStroke} strokeDasharray="3 3" />
            <XAxis dataKey="month" tick={{ fill: chartTheme.textSecondary, fontSize: 11 }} />
            <YAxis tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                   tick={{ fill: chartTheme.textSecondary, fontSize: 11 }} />
            <Tooltip
              contentStyle={chartTheme.tooltipContentStyle}
              labelStyle={chartTheme.tooltipLabelStyle}
              formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
            <Legend />
            <ReferenceLine y={0} stroke={chartTheme.textSecondary} strokeWidth={1} />
            {present.map((s) => [
              <Line key={`${s.key}_m`} type="monotone" dataKey={`${s.key}_median`}
                    name={s.label} stroke={s.color} strokeWidth={2} dot={false} />,
              <Line key={`${s.key}_hi`} type="monotone" dataKey={`${s.key}_p95`}
                    name={`${s.label} p95`} stroke={s.color} strokeWidth={1}
                    strokeDasharray="3 3" dot={false} legendType="none" />,
              <Line key={`${s.key}_lo`} type="monotone" dataKey={`${s.key}_p05`}
                    name={`${s.label} p05`} stroke={s.color} strokeWidth={1}
                    strokeDasharray="3 3" dot={false} legendType="none" />,
            ])}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* P(blend outperforms) at each horizon */}
      {proj.p_outperform && (
        <div className="mt-4 overflow-x-auto">
          <table className="text-sm w-full max-w-xl">
            <thead>
              <tr className="text-muted text-2xs uppercase">
                <th className="text-left font-medium">
                  P(blend outperforms)
                  <InfoIcon tooltipKey="p_outperform"
                            metricLabel="Probability the blend outperforms" />
                </th>
                {horizons.map((h) => (
                  <th key={h} className="text-right font-medium">{h}mo</th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono text-xs">
              {(['benchmark', 'classic_6040'] as const).map((base) => (
                proj.p_outperform?.[base] ? (
                  <tr key={base}>
                    <td className="text-left text-text font-sans">
                      vs {base === 'benchmark' ? 'Benchmark' : 'Classic 60/40'}
                    </td>
                    {horizons.map((h) => (
                      <td key={h} className="text-right text-text">
                        {pct(proj.p_outperform?.[base]?.[String(h)])}
                      </td>
                    ))}
                  </tr>
                ) : null
              ))}
            </tbody>
          </table>
        </div>
      )}

      <button
        type="button"
        onClick={askCouncil}
        className="mt-4 inline-flex items-center gap-1.5 text-xs text-electric
                   hover:underline min-h-[44px] sm:min-h-0">
        <MessageSquare className="w-3.5 h-3.5" />
        Ask about this
      </button>

      <p className="mt-4 pt-3 border-t border-border text-2xs text-muted italic">
        <InfoIcon tooltipKey="hmm_reference" metricLabel="Hidden Markov Model" />
        {' '}{LIMITATION}
      </p>
    </div>
  )
}
