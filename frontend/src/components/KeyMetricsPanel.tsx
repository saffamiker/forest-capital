/**
 * KeyMetricsPanel.tsx -- June 21 2026.
 *
 * The "Key Metrics -- Cache Verified" panel on the Reports page.
 * Sits below the Generate Documents cards, above Team Activity.
 *
 * Reads /api/v1/strategy-cache/key-metrics on demand (lazy --
 * doesn't fetch until the panel is expanded). Every metric row
 * shows label / value / source so Bob can confirm any figure in
 * the brief or deck against the cache directly, without leaving
 * the platform.
 *
 * Collapsed by default so the panel doesn't add scroll weight to
 * the Reports page for users who don't need to verify on every
 * visit. The header carries the data_hash + the "Cache verified"
 * pill, both visible even when collapsed.
 */
import { useState } from 'react'
import axios from 'axios'
import {
  ChevronDown, ChevronRight, CheckCircle, AlertCircle, Loader2,
  ShieldCheck,
} from 'lucide-react'

interface MetricRow {
  label:  string
  value:  string
  source: string
}

interface KeyMetricsResponse {
  data_hash:   string
  available:   boolean
  computed_at: string | null
  metrics: {
    strategy_performance?: MetricRow[]
    oos_metrics?:          MetricRow[]
    correlation_regime?:   MetricRow[]
    live_signal?:          MetricRow[]
  }
  message?: string
}

const SECTION_LABELS: Record<string, string> = {
  strategy_performance: 'Strategy performance',
  oos_metrics:          'OOS metrics (post-2022 validation window)',
  correlation_regime:   'Correlation regime',
  live_signal:          'Live signal',
}

const SECTION_ORDER: Array<keyof KeyMetricsResponse['metrics']> = [
  'strategy_performance',
  'oos_metrics',
  'correlation_regime',
  'live_signal',
]

export default function KeyMetricsPanel() {
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<KeyMetricsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchMetrics = async () => {
    if (data || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await axios.get<KeyMetricsResponse>(
        '/api/v1/strategy-cache/key-metrics')
      setData(res.data)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load cache metrics'
      setError(String(msg))
    } finally {
      setLoading(false)
    }
  }

  const handleToggle = () => {
    if (!expanded) {
      void fetchMetrics()
    }
    setExpanded(!expanded)
  }

  return (
    <section
      data-section-id="key-metrics"
      data-section-label="Key Metrics"
      className="card"
      data-testid="key-metrics-panel">
      <button
        type="button"
        onClick={handleToggle}
        data-testid="key-metrics-toggle"
        className="w-full flex items-center justify-between gap-3
                   px-4 py-3 hover:bg-navy-700/30 transition-colors
                   rounded">
        <div className="flex items-center gap-2 min-w-0">
          {expanded
            ? <ChevronDown className="w-4 h-4 text-muted shrink-0" />
            : <ChevronRight className="w-4 h-4 text-muted shrink-0" />}
          <ShieldCheck className="w-4 h-4 text-success shrink-0" />
          <h2 className="text-white font-semibold text-sm">
            Key Metrics — Cache Verified
          </h2>
          {data?.available && (
            <span
              data-testid="key-metrics-data-hash"
              className="text-2xs text-muted uppercase tracking-wide
                         font-mono ml-1">
              hash {data.data_hash}
            </span>
          )}
        </div>
        {data?.available && (
          <span className="text-2xs text-success bg-success/10
                          border border-success/30 px-2 py-0.5
                          rounded uppercase tracking-wide font-medium">
            Cache verified
          </span>
        )}
      </button>

      {!expanded && (
        <p className="text-2xs text-muted px-4 pb-3 pl-10">
          Every figure below is read directly from the strategy cache.
          These are the numbers your brief and deck must match. Click
          to expand.
        </p>
      )}

      {expanded && (
        <div className="px-4 pb-4 pt-2 border-t border-border">
          {loading && (
            <div className="flex items-center justify-center gap-2
                            py-4 text-muted text-xs">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading cache metrics…
            </div>
          )}
          {error && (
            <div className="flex items-start gap-2 px-3 py-2 rounded
                            border border-danger/30 bg-danger/5
                            text-danger text-xs mb-3">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
          {!loading && !error && data && !data.available && (
            <div className="flex items-start gap-2 px-3 py-2 rounded
                            border border-warning/30 bg-warning/5
                            text-warning text-xs mb-3">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                {data.message
                  || 'Strategy cache is not yet warm.'}
              </span>
            </div>
          )}
          {!loading && !error && data?.available && (
            <>
              {data.computed_at && (
                <div className="text-2xs text-muted mb-3
                                flex items-center gap-1.5">
                  <CheckCircle className="w-3 h-3 text-success" />
                  CIO snapshot: {new Date(data.computed_at)
                    .toLocaleString()}
                </div>
              )}
              {SECTION_ORDER.map((key) => {
                const rows = data.metrics[key]
                if (!rows || rows.length === 0) return null
                return (
                  <div key={key} className="mb-4">
                    <h3 className="text-2xs text-muted uppercase
                                   tracking-wide mb-2 font-semibold">
                      {SECTION_LABELS[key]}
                    </h3>
                    <table className="w-full text-xs">
                      <tbody>
                        {rows.map((row) => (
                          <tr
                            key={row.label}
                            className="border-b border-border/30">
                            <td className="py-1.5 pr-3 text-slate-300">
                              {row.label}
                            </td>
                            <td className="py-1.5 pr-3 text-white
                                           font-mono text-right
                                           whitespace-nowrap">
                              {row.value}
                            </td>
                            <td className="py-1.5 text-muted
                                           font-mono text-2xs
                                           hidden md:table-cell">
                              {row.source}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}
    </section>
  )
}
