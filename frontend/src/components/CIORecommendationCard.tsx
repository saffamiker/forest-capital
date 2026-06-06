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
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { ChevronDown, ChevronRight, Loader2, AlertTriangle, MessageSquare } from 'lucide-react'
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
  // Live overlay from /api/v1/recommendation: present only when the
  // daily HMM (live label) and monthly HMM (blend weights) disagree.
  // Reflects the moment of the read, not the moment the prose was
  // written — never baked into the cached recommendation.
  divergence_disclosure?: string | null
  // Live regime-conditional blend weights, overlaid from the cached
  // forward projection so the tile can show the blend + flag a binding
  // concentration constraint. Absent before the first warm.
  blend_weights?: Record<string, number> | null
  computed_at?: string | null
  model?: string | null
  // The Python pipeline emits `_model` (underscore prefix) carrying
  // either the LLM model id (e.g. claude-sonnet-4-6) or the literal
  // "deterministic_fallback" sentinel when the LLM call failed and the
  // user is seeing the structured fail-open recommendation. The card
  // surfaces an inline notice when the sentinel is present so the
  // user can never mistake the fallback for a live LLM run.
  _model?: string | null
}
interface Payload {
  available: boolean
  recommendation: Recommendation | null
}

// Box constraints the meta-portfolio optimizer operates under. Static
// disclosure — these are config invariants, not live values.
const PORTFOLIO_CONSTRAINTS: { label: string; value: string }[] = [
  { label: 'Strategy ceiling', value: '40% max per strategy' },
  { label: 'Strategy floor', value: '5% min per strategy' },
  { label: 'Asset ceiling', value: '50% max per asset class' },
  { label: 'Asset floor', value: '5% min per asset class' },
  { label: 'Rebalance trigger', value: 'Regime posterior shift' },
]

// The blend is at/near the 40% concentration ceiling when any single
// strategy weight reaches 38%.
const NEAR_CEILING = 0.38

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
  const navigate = useNavigate()
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [showLimitations, setShowLimitations] = useState(false)

  // Hand off to the council with the "recommendation" scope so the
  // deliberation injects this tile's live cached data (regime, blend,
  // dissent). The question is pre-filled and editable, never auto-sent.
  const askCouncil = () =>
    navigate('/council', {
      state: {
        prefillQuestion: 'Why is the blend positioned defensively right now?',
        contextScope: 'recommendation',
      },
    })

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
  const blendTop = rec.blend_weights
    ? Object.entries(rec.blend_weights)
        .filter(([, v]) => v > 0.01)
        .sort((a, b) => b[1] - a[1])
    : []
  // The strategy at/near the 40% concentration ceiling (>=38%), if any.
  const nearCeiling = blendTop.find(([, v]) => v >= NEAR_CEILING)

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
        {rec.divergence_disclosure && (
          <p
            className="mt-2 flex gap-1.5 rounded border border-warning/30 bg-warning/5 px-2 py-1.5 text-xs"
            data-testid="cio-divergence-disclosure"
          >
            <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0 mt-0.5" />
            <span className="text-warning">{rec.divergence_disclosure}</span>
          </p>
        )}
        {rec._model === 'deterministic_fallback' && (
          <p
            className="mt-2 flex gap-1.5 rounded border border-warning/30 bg-warning/5 px-2 py-1.5 text-xs"
            data-testid="cio-deterministic-fallback-notice"
          >
            <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0 mt-0.5" />
            <span className="text-warning">
              Live regime unavailable — showing last deterministic recommendation.
            </span>
          </p>
        )}
      </div>

      {/* Live regime-conditional blend (top weights) */}
      {blendTop.length > 0 && (
        <p className="mt-3 text-sm">
          <span className="text-muted">Blend: </span>
          <span className="font-mono text-xs text-slate-300">
            {blendTop.slice(0, 4)
              .map(([n, v]) => `${n} ${(v * 100).toFixed(0)}%`)
              .join('  ·  ')}
          </span>
        </p>
      )}

      {/* ── Portfolio Constraints (standing disclosure) ───────────── */}
      <div className="mt-4">
        <div className="text-2xs text-muted uppercase tracking-wide mb-1.5">
          Portfolio Constraints
        </div>
        <table className="text-xs w-full max-w-sm">
          <tbody>
            {PORTFOLIO_CONSTRAINTS.map((c) => (
              <tr key={c.label}>
                <td className="text-left text-muted py-0.5 pr-4">{c.label}</td>
                <td className="text-right text-slate-300 font-mono py-0.5">
                  {c.value}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={`mt-2 text-2xs leading-relaxed ${
          nearCeiling ? 'text-warning' : 'text-muted'}`}>
          {nearCeiling
            ? `Note: ${nearCeiling[0]} is at or near the concentration `
              + 'ceiling. The blend cannot increase defensiveness further '
              + 'without a constraint relaxation.'
            : 'No constraints currently binding.'}
        </p>
      </div>

      <button
        type="button"
        onClick={askCouncil}
        className="mt-4 inline-flex items-center gap-1.5 text-xs text-electric
                   hover:underline min-h-[44px] sm:min-h-0">
        <MessageSquare className="w-3.5 h-3.5" />
        Ask about this
      </button>

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
