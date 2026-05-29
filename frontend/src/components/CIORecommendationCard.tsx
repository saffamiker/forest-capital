/**
 * CIO Live Recommendation Card — the "present" panel of the landing-page
 * past/present/future arc, rendered first and above the fold.
 *
 * Reads GET /api/v1/recommendation (the data_hash-cached four-component
 * council recommendation; never recomputed on a read). Shows the current
 * regime + confidence, the signal, the recommendation, the dissenting
 * view, the key risk, a collapsible limitations panel, an "as of"
 * staleness indicator, and the CFA-style disclosure. Every figure is
 * server-provided; a graceful empty state shows before the first warm.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import { ChevronDown, ChevronRight, Loader2, AlertTriangle } from 'lucide-react'
import InfoIcon from './InfoIcon'

interface Confidence {
  regime?: string | null
  probability?: number | null
  ess?: number | null
  ess_warning?: boolean | null
}
interface Recommendation {
  signal?: string | null
  recommendation?: string | null
  confidence?: Confidence | null
  dissenting_view?: string | null
  key_risk?: string | null
  limitations?: string[] | null
  computed_at?: string | null
  model?: string | null
}
interface Payload {
  available: boolean
  recommendation: Recommendation | null
}

const REGIME_TONE: Record<string, string> = {
  BULL: 'text-positive',
  BEAR: 'text-negative',
  TRANSITION: 'text-warning',
}

const CFA_DISCLOSURE =
  'For educational use within the FNA 670 practicum. Not investment ' +
  'advice. Simulated and backtested results do not guarantee future ' +
  'performance. Material limitations are disclosed above.'

function asOf(ts?: string | null): string {
  if (!ts) return 'unknown'
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

export default function CIORecommendationCard() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [showLimitations, setShowLimitations] = useState(false)

  useEffect(() => {
    let alive = true
    // axios (not raw fetch) so the X-API-Key auth header rides along via the
    // global default + request interceptor — a raw fetch sends no credentials
    // header, 401s, and silently renders the empty state.
    axios.get<Payload>('/api/v1/recommendation')
      .then((r) => { if (alive) { setData(r.data); setLoading(false) } })
      .catch(() => { if (alive) { setData({ available: false, recommendation: null }); setLoading(false) } })
    return () => { alive = false }
  }, [])

  if (loading) {
    return (
      <div className="card p-5 m-4 md:m-6 flex items-center gap-2 text-muted">
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading the live CIO recommendation…
      </div>
    )
  }

  const rec = data?.recommendation
  if (!data?.available || !rec) {
    return (
      <div className="card p-5 m-4 md:m-6 text-muted text-sm">
        <div className="text-2xs uppercase tracking-wide mb-1">
          CIO Live Recommendation
        </div>
        The live recommendation has not been computed yet. It is generated
        on the next analytics warm and will appear here once cached.
      </div>
    )
  }

  const conf = rec.confidence || {}
  const regime = conf.regime || '—'
  const tone = REGIME_TONE[regime] || 'text-text'
  const probPct = typeof conf.probability === 'number'
    ? `${(conf.probability * 100).toFixed(0)}%` : '—'
  const limitations = rec.limitations || []

  return (
    <div className="card p-5 m-4 md:m-6 border-l-2 border-electric">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide flex items-center gap-1">
            CIO Live Recommendation
            <InfoIcon tooltipKey="recommendation_structure"
                      metricLabel="Four-component recommendation structure" />
          </div>
          <div className="flex items-baseline gap-3 mt-1">
            <span className={`text-2xl font-bold ${tone}`}>{regime}</span>
            <InfoIcon tooltipKey="regime_label" metricLabel="Current regime"
                      currentValue={regime} />
            <span className="text-sm text-muted font-mono">
              confidence {probPct}
              <InfoIcon tooltipKey="posterior_probability"
                        metricLabel="Regime posterior confidence"
                        currentValue={probPct} />
              {conf.ess_warning ? (
                <span className="ml-2 text-warning">· low ESS
                  <InfoIcon tooltipKey="ess_warning"
                            metricLabel="Effective sample size warning" />
                </span>
              ) : null}
            </span>
          </div>
        </div>
        <div className="text-2xs text-muted font-mono text-right">
          As of {asOf(rec.computed_at)}
        </div>
      </div>

      <div className="mt-4 space-y-2 text-sm">
        {rec.signal && (
          <p><span className="text-muted">Signal: </span>{rec.signal}</p>
        )}
        {rec.recommendation && (
          <p><span className="text-muted">Recommendation: </span>
            <span className="text-text">{rec.recommendation}</span></p>
        )}
        {rec.dissenting_view && (
          <p><span className="text-muted">Dissenting view: </span>
            {rec.dissenting_view}</p>
        )}
        {rec.key_risk && (
          <p className="flex gap-1.5">
            <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <span>
              <span className="text-muted">Key risk: </span>
              <InfoIcon tooltipKey="key_risk" metricLabel="Key risk" />
              {rec.key_risk}
            </span>
          </p>
        )}
      </div>

      {limitations.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowLimitations((s) => !s)}
            className="flex items-center gap-1 text-2xs uppercase tracking-wide
                       text-muted hover:text-text min-h-[44px] sm:min-h-0">
            {showLimitations ? <ChevronDown className="w-3.5 h-3.5" />
                             : <ChevronRight className="w-3.5 h-3.5" />}
            Limitations ({limitations.length})
          </button>
          {showLimitations && (
            <ul className="mt-2 ml-1 space-y-1 text-xs text-muted list-disc list-inside">
              {limitations.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          )}
        </div>
      )}

      <p className="mt-4 pt-3 border-t border-border text-2xs text-muted italic">
        <InfoIcon tooltipKey="cfa_disclosure" metricLabel="CFA disclosure" />
        {' '}{CFA_DISCLOSURE}
      </p>
    </div>
  )
}
