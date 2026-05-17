/**
 * useActivityTracking — automatic UI telemetry for the Team Activity feature.
 *
 * Mounted once from the authenticated layout. It starts the batching
 * logger and emits a page_view on every route change. Explicit feature
 * events (exports, council submissions, …) are tracked by the components
 * themselves via the convenience trackers in activityLogger.
 */
import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { startActivityLogger, trackPageView } from './activityLogger'

export function useActivityTracking(): void {
  const location = useLocation()
  // The route currently being viewed and when it was entered — used to
  // attribute a duration to the route the user is leaving.
  const prevRef = useRef<{ path: string; enteredAt: number } | null>(null)

  // Start the 30s flush timer and the unload handler once.
  useEffect(() => startActivityLogger(), [])

  // page_view on every route change — page is the route arrived at,
  // duration_seconds is the time spent on the route just left.
  useEffect(() => {
    const now = Date.now()
    const path = location.pathname
    const prev = prevRef.current
    const duration = prev
      ? Math.max(0, Math.round((now - prev.enteredAt) / 1000))
      : undefined
    trackPageView(path, duration)
    prevRef.current = { path, enteredAt: now }
  }, [location.pathname])
}
