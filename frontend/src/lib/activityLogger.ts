/**
 * activityLogger — batched UI telemetry for the Team Activity feature.
 *
 * A module-level singleton, deliberately not React state: the queue must
 * survive component unmounts (a login event queued on the auth page has
 * to outlive the navigation to the dashboard).
 *
 * Batching contract:
 *   - events queue in memory
 *   - flushed to POST /api/v1/activity/events every 30 seconds
 *   - flushed immediately once the queue reaches 50 events
 *   - flushed on page unload
 *   - SILENT on every failure — telemetry must never surface an error
 *
 * The unload flush uses fetch({ keepalive: true }) rather than
 * navigator.sendBeacon: the activity endpoint is authenticated, and
 * sendBeacon cannot attach the X-API-Key / X-Session-* headers. A
 * keepalive fetch is the modern equivalent that still carries headers
 * and still completes after the document starts unloading.
 */
import axios from 'axios'

export type ActivityEventType =
  | 'login' | 'logout' | 'page_view' | 'feature_click' | 'export'

export interface QueuedEvent {
  event_type: ActivityEventType
  page?: string
  feature?: string
  duration_seconds?: number
  metadata?: Record<string, unknown>
}

const ENDPOINT = '/api/v1/activity/events'
const FLUSH_INTERVAL_MS = 30_000
const MAX_BATCH = 50

let queue: QueuedEvent[] = []
let timer: ReturnType<typeof setInterval> | null = null

/** Regular flush — axios carries the auth + session headers from its
 *  defaults automatically. Fire-and-forget; failures are swallowed. */
function sendViaAxios(events: QueuedEvent[]): void {
  void axios.post(ENDPOINT, { events }).catch(() => { /* silent */ })
}

/** Unload flush — a keepalive fetch with the headers copied off the
 *  axios defaults (a raw fetch does not inherit them). */
function sendViaKeepalive(events: QueuedEvent[]): void {
  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const common = axios.defaults.headers.common as Record<string, unknown>
    for (const h of ['X-API-Key', 'X-Session-ID', 'X-Session-Type']) {
      const v = common[h]
      if (typeof v === 'string') headers[h] = v
    }
    void fetch(ENDPOINT, {
      method: 'POST',
      keepalive: true,
      headers,
      body: JSON.stringify({ events }),
    }).catch(() => { /* silent */ })
  } catch {
    /* silent — telemetry never throws */
  }
}

/** Flushes the queue. `unload` selects the keepalive transport. */
export function flushActivity(unload = false): void {
  if (queue.length === 0) return
  const batch = queue.splice(0, queue.length)
  if (unload) sendViaKeepalive(batch)
  else sendViaAxios(batch)
}

/** Queues one event. Flushes early when the batch cap is reached. */
export function track(event: QueuedEvent): void {
  queue.push(event)
  if (queue.length >= MAX_BATCH) flushActivity()
}

/**
 * Starts the 30-second flush timer and the unload handler. Call once
 * from an authenticated layout; returns a teardown function that stops
 * the timer and does a final flush.
 */
export function startActivityLogger(): () => void {
  if (timer === null) {
    timer = setInterval(() => flushActivity(), FLUSH_INTERVAL_MS)
  }
  const onUnload = () => flushActivity(true)
  window.addEventListener('beforeunload', onUnload)
  return () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    window.removeEventListener('beforeunload', onUnload)
    flushActivity(true)
  }
}

// ── Convenience trackers ──────────────────────────────────────────────────────
// Imported directly by components for explicit feature instrumentation.

/** Route change. `page` is the route arrived at; `durationSeconds` is the
 *  time spent on the previous route (undefined on the first navigation). */
export function trackPageView(page: string, durationSeconds?: number): void {
  track(durationSeconds !== undefined
    ? { event_type: 'page_view', page, duration_seconds: durationSeconds }
    : { event_type: 'page_view', page })
}

export function trackLogin(): void {
  track({ event_type: 'login' })
}

/** Logout — queued, then flushed at once so it is not lost to the
 *  imminent session teardown. */
export function trackLogout(): void {
  track({ event_type: 'logout' })
  flushActivity()
}

export function trackFeature(feature: string, metadata?: Record<string, unknown>): void {
  track(metadata
    ? { event_type: 'feature_click', feature, metadata }
    : { event_type: 'feature_click', feature })
}

export function trackExport(feature: string, metadata?: Record<string, unknown>): void {
  track(metadata
    ? { event_type: 'export', feature, metadata }
    : { event_type: 'export', feature })
}

/** Test-only — drains the queue without sending. */
export function _resetActivityQueue(): void {
  queue = []
}
