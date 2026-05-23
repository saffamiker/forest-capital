/**
 * ContextFreshnessBadge — surfaces the freshness of every agent
 * context layer so the user knows how current the prompts the
 * council reads are.
 *
 * Item 5 (May 23 2026 — analytics context injection, badges).
 *
 * Three independent caches feed into every agent prompt:
 *
 *   macro_context        the last research digest (24h target)
 *   analytics_context    the narrative cache (refreshes on the
 *                         strategy-cache refresh tick)
 *   diversification_context  the structured analytics block (same
 *                         refresh tick as analytics_context)
 *
 * Each layer is "fresh" when its timestamp is under 24 hours old,
 * "stale" when 24h–7 days, "missing" when null. A red dot signals
 * "this layer is missing entirely" — the agent will run text-only
 * for that layer. An amber dot is the stale-data fallback.
 *
 * Render is intentionally small — the badge sits in a corner of
 * the dashboard rather than competing with chart content. Click
 * the badge to expand a tooltip with per-layer detail.
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, AlertCircle, XCircle } from 'lucide-react'


interface FreshnessResponse {
  macro_context: string | null
  analytics_context: string | null
  diversification_context: string | null
}


type LayerStatus = 'fresh' | 'stale' | 'missing'


function classifyAge(iso: string | null): LayerStatus {
  if (!iso) return 'missing'
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return 'missing'
  const ageMs = Date.now() - t
  if (ageMs < 24 * 60 * 60 * 1000) return 'fresh'
  if (ageMs < 7 * 24 * 60 * 60 * 1000) return 'stale'
  return 'stale'  // older than a week → still stale, not missing
}


const STATUS_BADGE: Record<LayerStatus, {
  icon: typeof CheckCircle2
  colour: string
  label: string
}> = {
  fresh:    { icon: CheckCircle2,
              colour: 'text-green-400',  label: 'fresh' },
  stale:    { icon: AlertCircle,
              colour: 'text-amber-300',  label: 'stale' },
  missing:  { icon: XCircle,
              colour: 'text-text-muted', label: 'missing' },
}


const LAYER_LABEL: Record<keyof FreshnessResponse, string> = {
  macro_context:           'Macro digest',
  analytics_context:       'Analytics narrative',
  diversification_context: 'Diversification metrics',
}


function fmtRelative(iso: string | null): string {
  if (!iso) return 'never'
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return 'unknown'
  const ageMs = Date.now() - t
  const hours = ageMs / 3_600_000
  if (hours < 1) return `${Math.max(0, Math.round(ageMs / 60_000))}m ago`
  if (hours < 24) return `${Math.round(hours)}h ago`
  return `${Math.round(hours / 24)}d ago`
}


export default function ContextFreshnessBadge() {
  const [data, setData] = useState<FreshnessResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/context/freshness',
        { credentials: 'include' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json() as FreshnessResponse)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void refresh()
    // Refresh every 5 minutes — the layers update on data ingestion
    // ticks, not by the second. 5m is a comfortable balance between
    // staying current and not hammering the endpoint.
    const id = window.setInterval(refresh, 5 * 60 * 1000)
    return () => window.clearInterval(id)
  }, [refresh])

  if (error) {
    return (
      <span
        data-testid="context-freshness-error"
        className="text-2xs text-text-muted inline-flex items-center gap-1"
        title={`Freshness check failed: ${error}`}>
        <XCircle className="w-3 h-3" />
        context: ?
      </span>
    )
  }

  if (!data) {
    return (
      <span
        data-testid="context-freshness-loading"
        className="text-2xs text-text-muted">
        context: …
      </span>
    )
  }

  // Aggregate status — worst of three.
  const statuses: LayerStatus[] = [
    classifyAge(data.macro_context),
    classifyAge(data.analytics_context),
    classifyAge(data.diversification_context),
  ]
  const overall: LayerStatus = statuses.includes('missing') ? 'missing'
    : statuses.includes('stale')   ? 'stale'
    : 'fresh'
  const Bridge = STATUS_BADGE[overall].icon

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        data-testid="context-freshness-badge"
        aria-label={`Context freshness: ${overall}`}
        className={`text-2xs inline-flex items-center gap-1 px-1.5 py-0.5
                     rounded border border-navy-700
                     hover:border-electric-blue/40
                     ${STATUS_BADGE[overall].colour}`}>
        <Bridge className="w-3 h-3" />
        context: {STATUS_BADGE[overall].label}
      </button>

      {open ? (
        <div
          data-testid="context-freshness-popover"
          className="absolute right-0 mt-1 z-50 min-w-[18rem]
                     bg-navy-900 border border-navy-700 rounded
                     shadow-lg p-2 space-y-1 text-xs">
          <p className="text-2xs text-text-muted uppercase tracking-wider
                         pb-1 border-b border-navy-800">
            Agent prompt context layers
          </p>
          {(Object.keys(LAYER_LABEL) as (keyof FreshnessResponse)[]).map(
            (key) => {
              const ts = data[key]
              const status = classifyAge(ts)
              const Icon = STATUS_BADGE[status].icon
              return (
                <div
                  key={key}
                  data-testid={`context-freshness-row-${key}`}
                  className="flex items-center justify-between gap-2">
                  <span className="text-text-secondary">
                    {LAYER_LABEL[key]}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1
                                 ${STATUS_BADGE[status].colour}`}>
                    <Icon className="w-3 h-3" />
                    {fmtRelative(ts)}
                  </span>
                </div>
              )
            },
          )}
          <p className="text-2xs text-text-muted pt-1 border-t border-navy-800">
            Refreshes every 5 minutes.
          </p>
        </div>
      ) : null}
    </div>
  )
}
