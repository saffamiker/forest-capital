/**
 * TestRunner — the guided UAT test runner. Mounted once in MainLayout
 * alongside SiteTour; never auto-starts (only the Settings "Start Test
 * Pass" button and the login notifications trigger it, via testRunnerBus).
 *
 * It walks the tester through one TestScript step by step: navigating to
 * each step's route, highlighting its target element, and recording an
 * attested Pass / Fail / Skip — plus a 💡 Feedback action that leaves the
 * step pending. Results persist server-side, so a run can be paused and
 * resumed.
 *
 * Highlighting note: the runner uses a lightweight pointer-events:none
 * spotlight rather than a second react-joyride instance. Joyride's
 * overlay gates page interaction — fine for a passive tour, wrong for a
 * UAT runner where the tester must freely exercise the real app. The
 * controlled-step + cross-route navigation PATTERN is reused from
 * SiteTour; the overlay is not.
 */
import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  X, HelpCircle, Minus, ChevronUp, Check, AlertTriangle, Lightbulb,
  SkipForward, ClipboardList,
} from 'lucide-react'
import { TEST_SCRIPTS, scriptForEmail } from '../constants/testScripts'
import type { TestScript, TestStep } from '../constants/testScripts'
import { registerTestRunner } from '../lib/testRunnerBus'
import type { StartTestRunOptions } from '../lib/testRunnerBus'
import { useAuth } from '../App'
import TestSubmissionPanel from './TestSubmissionPanel'
import type { FeedbackCategorization } from './TestSubmissionPanel'

type Phase = 'idle' | 'selecting' | 'primer' | 'resume' | 'running' | 'complete'
type StepResult = 'pass' | 'fail' | 'skip'

const PRIMER_KEY = 'fc_test_primer_seen'

// ── Lightweight spotlight ─────────────────────────────────────────────────────

function Spotlight({ target }: { target: string | null }) {
  const [rect, setRect] = useState<DOMRect | null>(null)

  useEffect(() => {
    if (!target || target === 'body') { setRect(null); return }
    let frame = 0
    const measure = () => {
      const el = document.querySelector(target)
      setRect(el ? el.getBoundingClientRect() : null)
    }
    measure()
    // Re-measure on scroll/resize while the step is shown.
    const onChange = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure) }
    window.addEventListener('scroll', onChange, true)
    window.addEventListener('resize', onChange)
    const poll = window.setInterval(measure, 600)  // catch late-rendering targets
    return () => {
      window.removeEventListener('scroll', onChange, true)
      window.removeEventListener('resize', onChange)
      window.clearInterval(poll)
      cancelAnimationFrame(frame)
    }
  }, [target])

  if (!rect) return null
  const pad = 6
  // pointer-events:none on every layer — the tester interacts with the
  // real app freely; the spotlight is purely a visual cue.
  return (
    <div className="fixed inset-0 z-[88]" style={{ pointerEvents: 'none' }}>
      <div
        style={{
          position: 'fixed',
          left: rect.left - pad, top: rect.top - pad,
          width: rect.width + pad * 2, height: rect.height + pad * 2,
          borderRadius: 6,
          boxShadow: '0 0 0 9999px rgba(10,14,26,0.55)',
          border: '2px solid #f59e0b',
          pointerEvents: 'none',
        }}
      />
    </div>
  )
}

// ── Small modal shell ─────────────────────────────────────────────────────────

function Modal({ children, onClose }: {
  children: React.ReactNode; onClose?: () => void
}) {
  return (
    <div className="fixed inset-0 z-[96] flex items-center justify-center
                    bg-black/60 p-4" role="presentation"
         onClick={onClose}>
      <div role="dialog" onClick={(e) => e.stopPropagation()}
           className="w-full max-w-md rounded-lg border border-border
                      bg-navy-800 shadow-2xl">
        {children}
      </div>
    </div>
  )
}

// ── TestRunner ────────────────────────────────────────────────────────────────

export default function TestRunner() {
  const navigate = useNavigate()
  const location = useLocation()
  const { session } = useAuth()
  const email = session?.email ?? ''

  const [phase, setPhase] = useState<Phase>('idle')
  const [script, setScript] = useState<TestScript | null>(null)
  const [stepIndex, setStepIndex] = useState(0)
  const [minimised, setMinimised] = useState(false)
  const [results, setResults] = useState<Record<string, StepResult>>({})
  const [feedbackCount, setFeedbackCount] = useState(0)
  const [submission, setSubmission] = useState<'failure' | 'feedback' | null>(null)
  const [freeForm, setFreeForm] = useState(false)
  const [showPrimer, setShowPrimer] = useState(false)
  const [resumeIndex, setResumeIndex] = useState(0)
  const [toast, setToast] = useState<string | null>(null)

  const step: TestStep | null = script ? script.steps[stepIndex] ?? null : null

  // ── Existing results — used for resume detection ─────────────────────
  const loadExisting = useCallback(async (s: TestScript) => {
    try {
      const res = await axios.get<{
        results: Record<string, Array<{ step_id: string; result: StepResult
          resolved_at: string | null }>>
      }>('/api/v1/testing/results')
      const rows = res.data.results[s.id] ?? []
      const map: Record<string, StepResult> = {}
      for (const r of rows) {
        // A resolved failure is pending re-test — not a recorded result.
        if (r.resolved_at == null) map[r.step_id] = r.result
      }
      return map
    } catch {
      return {}
    }
  }, [])

  const firstPending = useCallback(
    (s: TestScript, done: Record<string, StepResult>) =>
      s.steps.findIndex((st) => !(st.id in done)), [])

  // ── Begin a chosen script ────────────────────────────────────────────
  const beginScript = useCallback(async (
    s: TestScript, jumpToStepId?: string,
  ) => {
    const done = await loadExisting(s)
    setScript(s)
    setResults(done)
    setFeedbackCount(0)
    if (jumpToStepId) {
      const idx = s.steps.findIndex((st) => st.id === jumpToStepId)
      setStepIndex(idx >= 0 ? idx : 0)
      setPhase('running')
      return
    }
    const pending = firstPending(s, done)
    const doneCount = Object.keys(done).length
    if (doneCount > 0 && pending >= 0) {
      // Partial run — offer resume.
      setResumeIndex(pending)
      setPhase('resume')
    } else if (localStorage.getItem(PRIMER_KEY) !== '1') {
      setStepIndex(pending >= 0 ? pending : 0)
      setShowPrimer(true)
      setPhase('primer')
    } else {
      setStepIndex(pending >= 0 ? pending : 0)
      setPhase('running')
    }
  }, [loadExisting, firstPending])

  // ── testRunnerBus entry point ────────────────────────────────────────
  const start = useCallback((opts?: StartTestRunOptions) => {
    setSubmission(null)
    setFreeForm(false)
    setMinimised(false)
    if (opts?.scriptId) {
      const s = TEST_SCRIPTS.find((x) => x.id === opts.scriptId)
      if (s) { void beginScript(s, opts.stepId); return }
    }
    setPhase('selecting')
  }, [beginScript])

  useEffect(() => {
    registerTestRunner(start)
    return () => registerTestRunner(null)
  }, [start])

  // ── Cross-route navigation: when the active step lives on another
  //    route, navigate there and re-resolve the spotlight after a beat. ──
  useEffect(() => {
    if (phase !== 'running' || !step) return
    if (step.route && step.route !== location.pathname) {
      navigate(step.route)
    }
  }, [phase, step, location.pathname, navigate])

  // ── Record a pass/skip and advance ───────────────────────────────────
  const recordAndAdvance = useCallback(async (result: StepResult) => {
    if (!script || !step) return
    const form = new FormData()
    form.append('script_id', script.id)
    form.append('step_id', step.id)
    form.append('result', result)
    form.append('browser_info', navigator.userAgent)
    // Signal a completed test pass to the backend triage hook — the
    // client holds the testScripts.ts step inventory, so it is the
    // authoritative judge of "all steps attested".
    if (stepIndex + 1 >= script.steps.length) {
      form.append('script_complete', 'true')
    }
    try {
      await axios.post('/api/v1/testing/results', form)
    } catch { /* fail-open — the UI must not stall on a logging error */ }
    setResults((r) => ({ ...r, [step.id]: result }))
    advance()
  }, [script, step])  // eslint-disable-line react-hooks/exhaustive-deps

  const advance = useCallback(() => {
    if (!script) return
    setStepIndex((i) => {
      const next = i + 1
      if (next >= script.steps.length) { setPhase('complete'); return i }
      return next
    })
  }, [script])

  // ── Submission panel outcomes ────────────────────────────────────────
  const onFailureSubmitted = () => {
    if (step) setResults((r) => ({ ...r, [step.id]: 'fail' }))
    setSubmission(null)
    advance()
  }
  const onFeedbackSubmitted = (info: { categorization?: FeedbackCategorization }) => {
    setSubmission(null)
    setFreeForm(false)
    setFeedbackCount((c) => c + 1)
    const cat = info.categorization
    if (cat?.ai_category) {
      setToast(`Recorded as: ${cat.ai_category}`
        + (cat.ai_effort_estimate ? ` (${cat.ai_effort_estimate} effort)` : '')
        + ' — Michael will review and respond.')
      window.setTimeout(() => setToast(null), 6000)
    }
  }

  const closeRunner = () => {
    setPhase('idle'); setScript(null); setSubmission(null); setFreeForm(false)
  }

  // ── Render ───────────────────────────────────────────────────────────
  if (phase === 'idle') return null

  // Script selector
  if (phase === 'selecting') {
    const mine = scriptForEmail(email)
    return (
      <Modal onClose={closeRunner}>
        <div className="px-5 py-4">
          <h2 className="text-white font-semibold text-sm">
            Which test section would you like to run?
          </h2>
          <div className="mt-4 space-y-2">
            <button type="button"
              onClick={() => void beginScript(TEST_SCRIPTS[0])}
              className="w-full text-left px-3 py-2.5 rounded border
                         border-border hover:border-electric/40
                         hover:bg-navy-700 transition-colors">
              <div className="text-white text-sm font-medium">All Testers</div>
              <div className="text-2xs text-muted">
                {TEST_SCRIPTS[0].steps.length} steps · core navigation and
                platform basics
              </div>
            </button>
            {mine && (
              <button type="button" onClick={() => void beginScript(mine)}
                className="w-full text-left px-3 py-2.5 rounded border
                           border-border hover:border-electric/40
                           hover:bg-navy-700 transition-colors">
                <div className="text-white text-sm font-medium">My Section</div>
                <div className="text-2xs text-muted">
                  {mine.title} · {mine.steps.length} steps
                </div>
              </button>
            )}
          </div>
          <button type="button" onClick={closeRunner}
            className="mt-3 text-2xs text-muted hover:text-white">
            Cancel
          </button>
        </div>
      </Modal>
    )
  }

  // Resume prompt
  if (phase === 'resume' && script) {
    const done = Object.keys(results).length
    return (
      <Modal>
        <div className="px-5 py-4">
          <h2 className="text-white font-semibold text-sm">
            Resume your test pass?
          </h2>
          <p className="text-xs text-muted mt-1.5">
            You have an incomplete test pass ({done} of {script.steps.length}
            {' '}steps complete).
          </p>
          <div className="mt-4 flex gap-2">
            <button type="button"
              onClick={() => { setStepIndex(resumeIndex); setPhase('running') }}
              className="flex-1 px-3 py-2 rounded text-xs font-medium
                         bg-electric text-white hover:bg-blue-500">
              Resume
            </button>
            <button type="button"
              onClick={() => { setResults({}); setStepIndex(0); setPhase('running') }}
              className="flex-1 px-3 py-2 rounded text-xs border border-border
                         text-slate-300 hover:bg-navy-700">
              Start over
            </button>
          </div>
        </div>
      </Modal>
    )
  }

  // Primer
  if (phase === 'primer' && showPrimer) {
    return (
      <Modal>
        <div className="px-5 py-4">
          <div className="flex items-center gap-2">
            <ClipboardList className="w-4 h-4 text-electric" />
            <h2 className="text-white font-semibold text-sm">
              Welcome to the Guided Test Pass
            </h2>
          </div>
          <div className="mt-3 space-y-2 text-xs text-slate-300 leading-relaxed">
            <p>This tool walks you through each test step one at a time.
              For each step you will be taken to the right screen, see the
              element highlighted, read what to do, and record a result:</p>
            <ul className="space-y-1 pl-1">
              <li>✅ <strong>Pass</strong> — it worked as expected.</li>
              <li>❌ <strong>Fail</strong> — something went wrong (you will
                describe it and can attach a screenshot).</li>
              <li>💡 <strong>Feedback</strong> — a suggestion or question;
                the step stays pending, so you still pass or skip it.</li>
              <li>⏭ <strong>Skip</strong> — not applicable right now.</li>
            </ul>
            <p>Your results save automatically — pause and resume any time.
              Testing Mode must be active (the amber pill in the nav bar).
              If a step is unclear, use 💡 Feedback to ask.</p>
          </div>
          <button type="button"
            onClick={() => {
              localStorage.setItem(PRIMER_KEY, '1')
              setShowPrimer(false); setPhase('running')
            }}
            className="mt-4 w-full px-3 py-2 rounded text-xs font-medium
                       bg-electric text-white hover:bg-blue-500">
            Got it — Start Testing
          </button>
        </div>
      </Modal>
    )
  }

  // Completion summary
  if (phase === 'complete' && script) {
    const counts = Object.values(results)
    const passed = counts.filter((r) => r === 'pass').length
    const failed = counts.filter((r) => r === 'fail').length
    const skipped = counts.filter((r) => r === 'skip').length
    return (
      <Modal onClose={closeRunner}>
        <div className="px-5 py-4">
          <h2 className="text-white font-semibold text-sm">
            Test pass complete
          </h2>
          <p className="text-xs text-slate-300 mt-2">
            {passed} passed · {failed} failed · {skipped} skipped ·
            {' '}{feedbackCount} feedback item{feedbackCount === 1 ? '' : 's'}
          </p>
          <div className="mt-4 flex gap-2">
            <button type="button"
              onClick={() => { closeRunner(); navigate('/settings') }}
              className="flex-1 px-3 py-2 rounded text-xs font-medium
                         bg-electric text-white hover:bg-blue-500">
              View Results in Settings
            </button>
            <button type="button" onClick={closeRunner}
              className="flex-1 px-3 py-2 rounded text-xs border border-border
                         text-slate-300 hover:bg-navy-700">
              Done
            </button>
          </div>
        </div>
      </Modal>
    )
  }

  // ── Running ──────────────────────────────────────────────────────────
  if (phase !== 'running' || !script || !step) {
    // Free-form / submission panels can still be open over a non-running
    // phase only transiently; nothing to render here otherwise.
    return null
  }

  const total = script.steps.length

  return (
    <>
      <Spotlight target={step.target} />

      {/* Free-form "Suggest an enhancement" — bottom-left, clears the
          test panel at bottom-right. */}
      {/* Free-form Suggest — bottom-left on desktop; on mobile it moves
          to the right and sits above the full-width control panel so the
          two never overlap. */}
      <button
        type="button"
        onClick={() => setFreeForm(true)}
        title="Suggest an enhancement"
        aria-label="Suggest an enhancement"
        className="fixed z-[90] w-11 h-11 rounded-full
                   flex items-center justify-center bg-navy-800
                   border border-electric/40 text-electric shadow-lg
                   hover:bg-electric/15 transition-colors
                   bottom-[calc(50vh+1rem)] right-4
                   sm:bottom-4 sm:left-4 sm:right-auto"
      >
        <Lightbulb className="w-4 h-4" />
      </button>

      {/* Floating control panel — a full-width bottom sheet on mobile
          (capped at 50vh, scrollable, clear of the home-bar safe area);
          a bottom-right card with an amber border from sm: up. */}
      <div
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
        className="fixed z-[90] border-2 border-warning bg-navy-800 shadow-2xl
                   overflow-y-auto inset-x-0 bottom-0 max-h-[50vh] rounded-t-lg
                   max-sm:landscape:max-h-[40vh]
                   sm:inset-x-auto sm:bottom-4 sm:right-4 sm:w-[360px]
                   sm:max-w-[92vw] sm:max-h-none sm:rounded-lg sm:pb-0"
      >
        {minimised ? (
          <button type="button" onClick={() => setMinimised(false)}
            className="w-full flex items-center justify-between px-4 py-2.5
                       text-xs text-white">
            <span className="flex items-center gap-2">
              <ClipboardList className="w-3.5 h-3.5 text-warning" />
              Test step {stepIndex + 1} of {total}
            </span>
            <ChevronUp className="w-3.5 h-3.5 text-muted" />
          </button>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2 px-4 pt-3">
              <span className="text-2xs uppercase tracking-wide text-warning">
                Step {stepIndex + 1} of {total}
              </span>
              <div className="flex items-center gap-1.5">
                <button type="button" onClick={() => setShowPrimer(true)}
                  aria-label="How this works"
                  className="text-muted hover:text-white">
                  <HelpCircle className="w-3.5 h-3.5" />
                </button>
                <button type="button" onClick={() => setMinimised(true)}
                  aria-label="Minimise"
                  className="text-muted hover:text-white">
                  <Minus className="w-3.5 h-3.5" />
                </button>
                <button type="button" onClick={closeRunner}
                  aria-label="Close test runner"
                  className="text-muted hover:text-white">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <div className="px-4 py-2">
              <h3 className="text-white font-semibold text-sm">{step.title}</h3>
              <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                {step.instruction}
              </p>
              <div className="mt-2 rounded bg-navy-900 border border-border
                              px-2.5 py-1.5">
                <div className="text-2xs uppercase tracking-wide text-muted">
                  Expected result
                </div>
                <p className="text-xs text-slate-300 mt-0.5">
                  {step.expectedResult}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-1.5 px-4 pb-3 pt-1">
              <button type="button" onClick={() => void recordAndAdvance('pass')}
                className="flex items-center justify-center gap-1.5 px-2 py-2
                           rounded text-xs font-medium bg-success/15
                           text-success border border-success/30
                           hover:bg-success/25">
                <Check className="w-3.5 h-3.5" /> Pass
              </button>
              <button type="button" onClick={() => setSubmission('failure')}
                className="flex items-center justify-center gap-1.5 px-2 py-2
                           rounded text-xs font-medium bg-danger/15
                           text-danger border border-danger/30
                           hover:bg-danger/25">
                <AlertTriangle className="w-3.5 h-3.5" /> Fail
              </button>
              <button type="button" onClick={() => setSubmission('feedback')}
                className="flex items-center justify-center gap-1.5 px-2 py-2
                           rounded text-xs font-medium bg-electric/15
                           text-electric border border-electric/30
                           hover:bg-electric/25">
                <Lightbulb className="w-3.5 h-3.5" /> Feedback
              </button>
              <button type="button"
                disabled={!step.allowSkip}
                onClick={() => void recordAndAdvance('skip')}
                className="flex items-center justify-center gap-1.5 px-2 py-2
                           rounded text-xs font-medium border border-border
                           text-slate-300 hover:bg-navy-700
                           disabled:opacity-40 disabled:cursor-not-allowed">
                <SkipForward className="w-3.5 h-3.5" /> Skip
              </button>
            </div>
          </>
        )}
      </div>

      {/* AI-categorisation toast after feedback */}
      {toast && (
        <div className="fixed right-4 z-[91] w-[360px] max-w-[92vw]
                        bottom-[calc(50vh+1rem)] sm:bottom-[5.5rem]
                        rounded border border-electric/30
                        bg-navy-800 px-3 py-2 text-2xs text-slate-200 shadow-lg">
          {toast}
        </div>
      )}

      {/* The How-this-works primer, re-openable via the ? button */}
      {showPrimer && phase === 'running' && (
        <Modal onClose={() => setShowPrimer(false)}>
          <div className="px-5 py-4">
            <h2 className="text-white font-semibold text-sm">
              How the Guided Test Pass works
            </h2>
            <ul className="mt-3 space-y-1 text-xs text-slate-300">
              <li>✅ <strong>Pass</strong> — it worked as expected.</li>
              <li>❌ <strong>Fail</strong> — describe what went wrong.</li>
              <li>💡 <strong>Feedback</strong> — a suggestion; the step
                stays pending.</li>
              <li>⏭ <strong>Skip</strong> — not applicable right now.</li>
            </ul>
            <button type="button" onClick={() => setShowPrimer(false)}
              className="mt-4 w-full px-3 py-2 rounded text-xs font-medium
                         bg-electric text-white hover:bg-blue-500">
              Close
            </button>
          </div>
        </Modal>
      )}

      {/* Per-step failure / feedback submission */}
      {submission && (
        <TestSubmissionPanel
          mode={submission}
          step={step}
          scriptId={script.id}
          onClose={() => setSubmission(null)}
          onSubmitted={submission === 'failure'
            ? onFailureSubmitted : onFeedbackSubmitted}
        />
      )}

      {/* Free-form suggestion — no step association */}
      {freeForm && (
        <TestSubmissionPanel
          mode="feedback"
          step={null}
          scriptId={null}
          sourceRoute={location.pathname}
          onClose={() => setFreeForm(false)}
          onSubmitted={onFeedbackSubmitted}
        />
      )}
    </>
  )
}
