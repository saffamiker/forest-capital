/**
 * frontend/src/components/ReportReadinessIndicator.tsx
 *
 * Workstream C — Reports-page readiness affordances (May 28 2026).
 *
 * Two surfaces, one source of truth (useReportReadinessStore):
 *
 *   ReportReadinessBanner  — a sticky info card at the top of the
 *     Reports page declaring "Reports ready" / "N items must be
 *     reviewed before reporting" so the team sees the verdict on
 *     entry rather than discovering it from a 422 on Generate.
 *
 *   ReportBlockingModal    — a full-page modal that lists every
 *     blocking item. Rendered when the user clicks Generate while
 *     blockers exist; lets the user dismiss and navigate back to
 *     the QA / Statistical Audit tabs to act on each item.
 *
 * The frontend never enforces the gate by itself — the backend
 * (_require_report_ready) does. The frontend gate is a UX layer that
 * surfaces blockers BEFORE generation is attempted, plus a fallback
 * that catches the 422 detail when a stale frontend lets the click
 * through.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, CheckCircle2, Loader2, RefreshCw, ShieldAlert, X,
} from 'lucide-react'
import axios from 'axios'
import {
  readinessBlockerLabels,
  useReportReadinessStore,
} from '../stores/reportReadinessStore'


/**
 * Banner — drops into the Reports page header. Always visible
 * (no auto-hide on ready) because absence-of-indicator is a worse
 * signal than a green "Reports ready" tile.
 */
export function ReportReadinessBanner() {
  const { readiness, loading, load } = useReportReadinessStore()

  useEffect(() => { void load() }, [load])

  if (!readiness && loading) {
    return (
      <div
        data-testid="report-readiness-banner-loading"
        className="rounded border border-border bg-navy-800 px-4 py-3
                   text-xs text-muted">
        Checking report readiness…
      </div>
    )
  }
  if (!readiness) {
    // Fail-open — endpoint unreachable / no audit history yet. Don't
    // block UI, but signal the unknown state so the team is not lulled
    // into thinking the platform has cleared them.
    return (
      <div
        data-testid="report-readiness-banner-unknown"
        className="rounded border border-border bg-navy-800 px-4 py-3
                   text-xs text-muted">
        Report readiness is unavailable — run an audit to populate.
      </div>
    )
  }

  if (readiness.is_ready) {
    return (
      <div
        data-testid="report-readiness-banner-ready"
        className="rounded border border-success/40 bg-success/10 px-4 py-3
                   flex items-start gap-3">
        <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" />
        <div className="min-w-0">
          <p className="text-sm font-semibold text-success">
            Reports ready
          </p>
          <p className="text-2xs text-success/80 mt-0.5">
            Both audit surfaces are clear of unreviewed blocking items.
            Generated reports can proceed to submission review.
          </p>
        </div>
      </div>
    )
  }

  const n = readiness.blocking_count
  return (
    <div
      data-testid="report-readiness-banner-blocked"
      className="rounded border border-amber-500/40 bg-amber-500/10 px-4 py-3
                 flex items-start gap-3">
      <ShieldAlert className="w-4 h-4 text-amber-300 shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-amber-200">
          {n} audit item{n === 1 ? '' : 's'} must be reviewed before
          generating a report.
        </p>
        <p className="text-2xs text-amber-200/80 mt-0.5">
          Acknowledge, mark intentional, or revoke each outstanding
          item on the QA Audit tab. Generation buttons remain visible
          but will surface a blocking modal until readiness clears.
        </p>
      </div>
    </div>
  )
}


/**
 * Modal — rendered when the user clicks Generate while blockers exist,
 * OR when the backend gate returns a 422 (a stale frontend's only
 * fall-through path). The blocker list can be either a frontend-
 * computed list from the store, or the server's `blockers` array
 * lifted from the 422 detail; the modal renders both shapes
 * identically.
 */
export function ReportBlockingModal({
  open, onClose, blockers, message, coldCaches,
}: {
  open: boolean
  onClose: () => void
  blockers: string[]
  message?: string
  // Bridge #91 — when the 422 carries error="caches_not_warm" the
  // caller passes the cold-cache list through; the modal then
  // renders a Warm Caches action button alongside Close.
  coldCaches?: string[]
}) {
  // Reload readiness when the modal opens so the list is fresh —
  // a blocker the team has just resolved in another tab should not
  // continue to appear here.
  const reload = useReportReadinessStore((s) => s.reload)
  const [warming, setWarming] = useState(false)
  const [warmError, setWarmError] = useState<string | null>(null)
  const cacheGate = (coldCaches && coldCaches.length > 0)
    || (!!message && /caches are not warm/i.test(message))
  useEffect(() => {
    if (open) void reload()
  }, [open, reload])

  const onWarmClick = async () => {
    setWarming(true)
    setWarmError(null)
    try {
      await axios.post('/api/v1/admin/warm-analytics-cache')
      // Refresh readiness so the next Generate click clears the gate.
      await reload()
      onClose()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data as { detail?: string })?.detail
            ?? err.message
        : 'Warm failed — try again.'
      setWarmError(String(msg))
    } finally {
      setWarming(false)
    }
  }

  // Esc-to-close — the modal is non-destructive, so unconditional
  // dismiss is safe.
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  const headline = useMemo(() => {
    if (message) return message
    const n = blockers.length
    if (n === 0) return 'Report readiness check'
    return `${n} audit item${n === 1 ? '' : 's'} must be reviewed before generating a report.`
  }, [blockers.length, message])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 p-4"
      onClick={onClose}
      data-testid="report-blocking-modal">
      <div className="card p-5 max-w-lg w-full space-y-3"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-5 h-5 text-warning shrink-0 mt-0.5" />
            <h3 className="text-sm font-semibold text-white">
              Report not ready
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="report-blocking-modal-close"
            aria-label="Close"
            className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-slate-200 leading-relaxed">
          {headline}
        </p>
        {blockers.length > 0 && (
          <div className="rounded border border-border bg-navy-900
                          px-3 py-2 max-h-64 overflow-y-auto">
            <ul className="text-2xs text-slate-300 leading-relaxed
                           space-y-1 list-disc list-inside">
              {blockers.map((b, idx) => (
                <li key={idx} data-testid={`report-blocker-${idx}`}>
                  {b}
                </li>
              ))}
            </ul>
          </div>
        )}
        {/* Cold-cache list -- when the gate fires with caches_not_warm
            the 422 detail carries a cold_caches array. Surfacing each
            cache by name tells the user exactly which warm to trigger
            (a generic "warm the caches" instruction was previously
            the only signal). The Warm Caches action below addresses
            them all at once; the list is purely informational. */}
        {coldCaches && coldCaches.length > 0 && (
          <div className="rounded border border-amber-500/40 bg-amber-500/5
                          px-3 py-2"
               data-testid="report-blocking-cold-caches">
            <p className="text-2xs text-amber-200 leading-relaxed mb-1
                          font-semibold">
              Brief generation requires the following caches to be warm:
            </p>
            <ul className="text-2xs text-amber-100 leading-relaxed
                           space-y-0.5 list-disc list-inside">
              {coldCaches.map((c, idx) => (
                <li key={idx}
                    data-testid={`report-cold-cache-${idx}`}>
                  {c}
                </li>
              ))}
            </ul>
            <p className="text-2xs text-amber-300 leading-relaxed mt-1.5">
              Trigger a warm and retry.
            </p>
          </div>
        )}
        <p className="text-2xs text-muted leading-relaxed">
          {cacheGate
            ? ('Warm the analytics caches before generating -- a cold '
               + 'cache produces a [DATA PENDING] placeholder in the '
               + 'generated document.')
            : ('Each item must be acknowledged, marked intentional, '
               + 'or revoked on the QA Audit tab before report '
               + 'generation is re-enabled.')}
        </p>
        {warmError && (
          <p className="text-2xs text-danger leading-relaxed">
            Warm failed — {warmError}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          {cacheGate && (
            <button
              type="button"
              onClick={() => void onWarmClick()}
              disabled={warming}
              data-testid="report-blocking-modal-warm-caches"
              className="px-3 py-1.5 rounded text-xs font-medium
                         bg-electric text-white hover:bg-blue-500
                         disabled:opacity-60 disabled:cursor-wait
                         transition-colors flex items-center gap-1.5">
              {warming
                ? <><Loader2 className="w-3 h-3 animate-spin" /> Warming…</>
                : <><RefreshCw className="w-3 h-3" /> Warm Caches</>}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            data-testid="report-blocking-modal-dismiss"
            className="px-3 py-1.5 rounded text-xs font-medium
                       bg-electric/10 border border-electric/40 text-electric
                       hover:bg-electric/20 transition-colors">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}


/**
 * Hook for a generation button: returns the readiness state plus a
 * helper that surfaces the blocking modal labels. A caller uses
 * `is_ready` to decide whether clicking the button should call the
 * generate endpoint or open the modal with `blockerLabels`.
 */
export function useReportReadinessGate() {
  const { readiness, load, reload } = useReportReadinessStore()
  useEffect(() => { void load() }, [load])
  const labels = readinessBlockerLabels(readiness)
  return {
    is_ready: readiness?.is_ready ?? null,
    blocking_count: readiness?.blocking_count ?? 0,
    blockerLabels: labels,
    // June 21 2026 -- surfaced from the readiness payload so the
    // Presentation Script card can flip its button state without
    // a second round-trip. null = unknown (endpoint failed or not
    // loaded yet); the card falls back to the disabled state then.
    deck_story_plan_available:
      readiness?.deck_story_plan_available ?? null,
    reload,
  }
}
