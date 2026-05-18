/**
 * SiteTour — the guided platform walkthrough, mounted once in
 * MainLayout so it persists across every route.
 *
 * The tour spans multiple pages, so Joyride runs CONTROLLED: this
 * component owns `run` and `stepIndex`. When the next step lives on a
 * different route the handler navigates there, pauses, and a
 * location-change effect resumes the tour once the new page's targets
 * have rendered.
 *
 * Trigger logic (once per login session):
 *   - On mount it reads /api/v1/changelog/unseen for has_tour_update.
 *   - If a tour update is pending and the user has not skipped this
 *     session: it auto-starts — but only directly when no What's New
 *     modal is showing. When the modal IS showing, auto-start is
 *     suppressed so the tour never opens on top of it; the modal's
 *     "View updated site tour" button calls startTour() instead, and a
 *     later session auto-starts directly once the modal is dismissed.
 *   - The Settings "Retake" button and the modal's "View updated tour"
 *     button both call startTour() (via tourBus), which force-starts
 *     regardless of seen state.
 *
 * Completion and skip both POST /api/v1/changelog/mark-seen with the
 * current tour_version, so the tour does not re-trigger until a new
 * TOUR_VERSION ships.
 *
 * react-joyride v3 note: v3 is a ground-up rewrite. The single
 * `callback` of v2 is now an `onEvent` handler receiving `EventData`
 * (with `.type` the event); per-step `disableBeacon` and the
 * `disableOverlayClose`/`showSkipButton` props are gone — beacon and
 * overlay behaviour now live in the `options` prop.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import axios from 'axios'
import { Joyride, ACTIONS, EVENTS, STATUS } from 'react-joyride'
import type { EventData, Step, TooltipRenderProps } from 'react-joyride'
import { TOUR_STEPS } from '../constants/tourSteps'
import type { TourStep } from '../constants/tourSteps'
import { registerTourStarter } from '../lib/tourBus'
import type { UnseenChangelogResponse } from '../types/changelog'

const SKIP_KEY = 'fc_tour_skipped'
const RESUME_DELAY_MS = 400   // lets a freshly-navigated page render its targets

// ── Custom dark-theme tooltip ─────────────────────────────────────────────────

function TourTooltip({
  index, size, step, backProps, primaryProps, skipProps, tooltipProps,
  isLastStep,
}: TooltipRenderProps) {
  // The TourStep payload travels on step.data.
  const data = (step.data ?? {}) as TourStep
  return (
    <div
      {...tooltipProps}
      className="w-[380px] max-w-[90vw] rounded-lg border border-border
                 bg-navy-800 shadow-2xl p-4"
    >
      <h3 className="text-white font-semibold text-sm">{data.title}</h3>
      <div className="mt-2 space-y-2">
        {(data.body ?? []).map((para, i) => (
          <p key={i} className="text-xs text-slate-300 leading-relaxed">{para}</p>
        ))}
      </div>
      {data.relevantFor && (
        <p className="mt-2 text-2xs text-muted italic">
          Most relevant for: {data.relevantFor}
        </p>
      )}
      <div className="mt-3 pt-2.5 border-t border-border flex items-center
                      justify-between gap-2 flex-wrap">
        <span className="text-2xs text-muted">Step {index + 1} of {size}</span>
        <div className="flex items-center gap-1.5">
          <button
            {...skipProps}
            className="inline-flex items-center justify-center px-2.5 py-1.5
                       min-h-[44px] sm:min-h-0 text-xs text-muted
                       hover:text-white transition-colors"
          >
            Skip
          </button>
          {index > 0 && (
            <button
              {...backProps}
              className="inline-flex items-center justify-center px-3 py-1.5
                         min-h-[44px] sm:min-h-0 text-xs rounded border
                         border-border text-slate-300 hover:bg-navy-700
                         transition-colors"
            >
              Back
            </button>
          )}
          <button
            {...primaryProps}
            className="inline-flex items-center justify-center px-3.5 py-1.5
                       min-h-[44px] sm:min-h-0 text-xs rounded font-medium
                       bg-electric/15 text-electric border border-electric/30
                       hover:bg-electric/25 transition-colors"
          >
            {isLastStep ? 'Start Exploring' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── SiteTour ──────────────────────────────────────────────────────────────────

export default function SiteTour() {
  const navigate = useNavigate()
  const location = useLocation()
  const [run, setRun] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)
  // On a phone the tooltip is centred on screen rather than pinned to the
  // step target — a target-anchored popover is unreliable at 320–640px.
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < 640)
  // Set when a cross-route step needs the page to change before resuming.
  const pendingIndex = useRef<number | null>(null)
  // tour_version from /unseen — recorded on completion / skip.
  const tourVersion = useRef<number | null>(null)
  // Guards endTour against the double-fire when STEP_AFTER ends the tour
  // and TOUR_END then fires for the same run — keeps mark-seen to one POST.
  const settled = useRef(false)

  const joyrideSteps: Step[] = TOUR_STEPS.map((s) => ({
    target: s.target,
    content: s.title,                   // satisfies the type; the custom
    // Centre every tooltip on mobile; pin to the target from sm: up.
    placement: isMobile ? 'center' : (s.placement ?? 'bottom'),
    data: s,
  }))

  const markTourSeen = useCallback(() => {
    const body = tourVersion.current != null
      ? { tour_version_seen: tourVersion.current }
      : {}
    void axios.post('/api/v1/changelog/mark-seen', body).catch(() => { /* silent */ })
  }, [])

  // Ends the run exactly once: stops Joyride, resets to step 0, records
  // the skip flag when dismissed early, and marks the tour version seen.
  const endTour = useCallback((opts: { skipped: boolean }) => {
    if (settled.current) return
    settled.current = true
    setRun(false)
    setStepIndex(0)
    if (opts.skipped) sessionStorage.setItem(SKIP_KEY, '1')
    markTourSeen()
  }, [markTourSeen])

  // The starter other components invoke via tourBus — a forced start
  // from step 1, regardless of seen/skip state.
  const beginTour = useCallback(() => {
    sessionStorage.removeItem(SKIP_KEY)
    pendingIndex.current = null
    settled.current = false
    setStepIndex(0)
    navigate('/')
    setRun(true)
  }, [navigate])

  useEffect(() => {
    registerTourStarter(beginTour)
    return () => registerTourStarter(null)
  }, [beginTour])

  // Track the viewport so the tooltip placement flips between centred
  // (mobile) and target-anchored (sm+) on a resize or rotation.
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 640)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Auto-start once per login session.
  useEffect(() => {
    let cancelled = false
    axios.get<UnseenChangelogResponse>('/api/v1/changelog/unseen')
      .then((res) => {
        if (cancelled) return
        tourVersion.current = res.data.tour_version ?? null
        const pending = !!res.data.has_tour_update
        const skipped = sessionStorage.getItem(SKIP_KEY) === '1'
        // Auto-start directly only when no What's New modal will show
        // (no unseen changelog entries). When the modal shows, it calls
        // startTour() on close so the tour never overlaps it.
        const modalWillShow = (res.data.entries ?? []).length > 0
        if (pending && !skipped && !modalWillShow) {
          settled.current = false
          setStepIndex(0)
          setRun(true)
        }
      })
      .catch(() => { /* silent — no tour on failure */ })
    return () => { cancelled = true }
  }, [])

  // Resume the tour after a cross-route navigation, once the new page
  // has had a moment to render the next step's target.
  useEffect(() => {
    if (pendingIndex.current == null) return
    const idx = pendingIndex.current
    pendingIndex.current = null
    const t = setTimeout(() => {
      setStepIndex(idx)
      setRun(true)
    }, RESUME_DELAY_MS)
    return () => clearTimeout(t)
  }, [location.pathname])

  const handleEvent = (data: EventData) => {
    const { type, action, index, status } = data

    if (type === EVENTS.STEP_AFTER || type === EVENTS.TARGET_NOT_FOUND) {
      // Skip button, or Esc (dismissKeyAction 'close' fires a CLOSE
      // action) — both end the tour and record it as skipped.
      if (action === ACTIONS.SKIP || action === ACTIONS.CLOSE) {
        endTour({ skipped: true })
        return
      }
      const dir = action === ACTIONS.PREV ? -1 : 1
      const next = index + dir
      // Past the last step — the user clicked "Start Exploring".
      if (next >= TOUR_STEPS.length) {
        endTour({ skipped: false })
        return
      }
      if (next < 0) return
      const nextRoute = TOUR_STEPS[next].route
      if (nextRoute && nextRoute !== location.pathname) {
        // Cross-route step — pause, navigate, resume via the effect above.
        setRun(false)
        pendingIndex.current = next
        navigate(nextRoute)
      } else {
        setStepIndex(next)
      }
      return
    }

    // Backstop — covers any status-driven end the STEP_AFTER path missed.
    if (type === EVENTS.TOUR_END) {
      endTour({ skipped: status === STATUS.SKIPPED })
    }
  }

  return (
    <Joyride
      steps={joyrideSteps}
      run={run}
      stepIndex={stepIndex}
      continuous
      scrollToFirstStep
      onEvent={handleEvent}
      tooltipComponent={TourTooltip}
      options={{
        overlayColor: 'rgba(0,0,0,0.6)',
        arrowColor: '#1a2438',
        zIndex: 90,
        skipBeacon: true,          // controlled tour — show the tooltip directly
        overlayClickAction: false, // an overlay click never dismisses the tour
        dismissKeyAction: 'close', // Esc fires CLOSE, handled above as a skip
      }}
    />
  )
}
