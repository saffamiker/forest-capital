/**
 * WarmAnalyticsCacheButton — Settings → Admin control for the
 * analytics cache. May 24 2026.
 *
 * The platform auto-warms the analytics_metrics_cache at startup
 * (lifespan hook fires the warm op as a background task with 3
 * retries + exponential backoff). This component:
 *
 *   1. Polls GET /api/v1/admin/cache-status on mount and every
 *      10s. Renders one of four states based on the response:
 *        warm     → green check + "computed N min ago"
 *        warming  → spinner + "Warming now…"
 *        failed   → red banner + retry button
 *        idle     → button reads "Warm cache" (manual override)
 *
 *   2. POST /api/v1/admin/warm-analytics-cache when the button is
 *      clicked. The endpoint runs ONE warm attempt and returns
 *      when the rows land; the polled status reflects the new
 *      state.
 *
 * Status read is open to every authenticated user — the UI gates
 * the Warm Cache BUTTON behind manage_users (sysadmin only) so a
 * viewer sees the current state but can't trigger a manual warm.
 */
import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { Flame, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import TeamGate from '../TeamGate'


type WarmStatus = 'idle' | 'warming' | 'warm' | 'failed'


interface CacheStatus {
  status: WarmStatus
  in_progress: boolean
  attempts: number
  last_attempt_at: string | null
  last_success_at: string | null
  last_success_age_seconds: number | null
  last_attempt_error: string | null
  last_took_s: number | null
  last_landed: Record<string, boolean>
  cache_present: {
    academic_analytics: boolean
    efficient_frontier: boolean
  }
}


function _formatAge(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return ''
  if (seconds < 60) return `${Math.round(seconds)}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}


export default function WarmAnalyticsCacheButton() {
  const [status, setStatus] = useState<CacheStatus | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const r = await axios.get<CacheStatus>('/api/v1/admin/cache-status')
      setStatus(r.data)
    } catch (e) {
      // Status reads are best-effort; a transient failure shouldn't
      // blank the panel — keep the prior status, just record the
      // error so the next render can surface it if it persists.
      if (status === null) {
        const msg = axios.isAxiosError(e)
          ? (e.response?.data?.detail || e.message)
          : (e as Error).message
        setError(String(msg))
      }
    }
  }, [status])

  useEffect(() => {
    void fetchStatus()
    const interval = setInterval(() => { void fetchStatus() }, 10000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  const onWarm = useCallback(async () => {
    setTriggering(true)
    setError(null)
    try {
      await axios.post('/api/v1/admin/warm-analytics-cache')
      await fetchStatus()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
      await fetchStatus()
    } finally {
      setTriggering(false)
    }
  }, [fetchStatus])

  const isWarm = status?.status === 'warm'
  const isWarming = status?.status === 'warming' || status?.in_progress || triggering
  const isFailed = status?.status === 'failed' && !isWarming

  return (
    <div data-testid="warm-analytics-cache" className="space-y-2">
      <p className="text-xs text-muted leading-relaxed">
        The analytics cache (Cumulative Returns, Efficient Frontier,
        Diversification suite) warms automatically at startup with
        3 retries. This control is the manual override when you
        want a fresh compute — e.g. after a data-ingest pipeline
        re-run.
      </p>

      {/* Current cache status — visible to every authenticated user */}
      {status && isWarm ? (
        <div
          data-testid="warm-analytics-cache-status-warm"
          className="flex items-start gap-2 px-3 py-2 rounded
                     border border-green-500/30 bg-green-500/5
                     text-xs text-green-300">
          <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <div className="space-y-0.5 leading-snug">
            <p className="font-medium">
              Cache warm
              {status.last_success_age_seconds !== null
                ? ` — computed ${_formatAge(status.last_success_age_seconds)}`
                : ''}
              {status.last_took_s !== null
                ? ` (${status.last_took_s.toFixed(1)}s)`
                : ''}
              .
            </p>
            <p className="text-green-300/80">
              Academic analytics:{' '}
              {status.cache_present.academic_analytics
                ? '✓ present' : '— missing'}
              {' · '}
              Efficient frontier:{' '}
              {status.cache_present.efficient_frontier
                ? '✓ present' : '— missing'}
            </p>
          </div>
        </div>
      ) : null}

      {status && isWarming ? (
        <div
          data-testid="warm-analytics-cache-status-warming"
          className="flex items-start gap-2 px-3 py-2 rounded
                     border border-electric-blue/30 bg-electric-blue/5
                     text-xs text-electric-blue">
          <Loader2 className="w-3.5 h-3.5 shrink-0 mt-0.5 animate-spin" />
          <div className="space-y-0.5 leading-snug">
            <p className="font-medium">
              Warming now — attempt {status.attempts || 1}
              {status.attempts > 1 ? ' (retry after backoff)' : ''}.
            </p>
            <p className="text-electric-blue/80">
              The compute takes 30–60s on a cold cache. The
              dashboard reads the live data while the cache
              builds; switch to the Dashboard tab and the panel
              will populate when it finishes.
            </p>
          </div>
        </div>
      ) : null}

      {status && isFailed ? (
        <div
          data-testid="warm-analytics-cache-status-failed"
          className="flex items-start gap-2 px-3 py-2 rounded
                     border border-red-500/30 bg-red-500/5
                     text-xs text-red-300">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <div className="space-y-0.5 leading-snug">
            <p className="font-medium">
              Auto-warm failed after {status.attempts} attempt
              {status.attempts === 1 ? '' : 's'}.
            </p>
            {status.last_attempt_error ? (
              <p className="text-red-300/80 break-words">
                {status.last_attempt_error}
              </p>
            ) : null}
            <p className="text-red-300/60 italic">
              Click Warm cache below to retry. If it keeps failing,
              check the Render logs for analytics_cache_warm_failed.
            </p>
          </div>
        </div>
      ) : null}

      {/* Manual override — sysadmin only */}
      <TeamGate permission="manage_users" showDisabled={false}>
        <button
          type="button"
          onClick={onWarm}
          disabled={isWarming}
          data-testid="warm-analytics-cache-button"
          className="inline-flex items-center gap-2 px-3 py-2 rounded
                     border border-electric-blue/40 bg-electric-blue/10
                     text-electric-blue text-xs font-medium
                     hover:bg-electric-blue/20 transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed
                     min-h-[36px]">
          {isWarming ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Warming…
            </>
          ) : (
            <>
              <Flame className="w-3.5 h-3.5" />
              {isWarm ? 'Re-warm cache' : 'Warm cache'}
            </>
          )}
        </button>
      </TeamGate>

      {error && !isWarming ? (
        <p
          data-testid="warm-analytics-cache-trigger-error"
          className="text-xs text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  )
}
