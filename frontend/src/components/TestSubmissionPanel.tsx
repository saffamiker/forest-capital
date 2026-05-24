/**
 * TestSubmissionPanel — the modal behind the test runner's ❌ Fail and
 * 💡 Feedback actions, and the free-form "Suggest an enhancement" button.
 *
 * Both a failure report and a feedback submission pass through the
 * quality gate (POST /api/v1/testing/quality-check) before they are
 * stored. A low-quality submission shows the evaluator's clarification
 * question — the tester can revise (one re-evaluation) or submit as-is.
 * The tester never sees a score, only the question; the interaction is
 * helpful, never critical.
 *
 *   failure  → POST /api/v1/testing/results  (result = "fail")
 *   feedback → POST /api/v1/testing/feedback (step stays pending)
 *
 * A free-form feedback submission (step === null) carries no script/step
 * — only the captured source route.
 *
 * May 24 2026 — UAT-blocking refactor per user spec:
 *   1. Draggable — title-bar drag moves the window so the user can
 *      reposition it over the content they're describing.
 *   2. Non-locking — no backdrop; the page behind stays scrollable
 *      so the tester can reference the UI they're documenting.
 *   3. Resizable — bottom-right corner drag-handle resizes the
 *      window (min 360x240, max viewport).
 *   4. Persistent — backdrop click does NOT close. Only X, Cancel,
 *      Escape, or Submit dismiss the panel.
 *   5. Auto-save — in-progress text is keyed by (mode, scriptId,
 *      stepId) in localStorage and restored on next open. Cleared
 *      on successful submit.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { X, Upload, Loader2, AlertTriangle, Lightbulb,
         Move, Maximize2 } from 'lucide-react'
import type { TestStep } from '../constants/testScripts'

type Mode = 'failure' | 'feedback'

interface Props {
  mode: Mode
  /** The current test step, or null for a free-form suggestion. */
  step: TestStep | null
  scriptId: string | null
  /** Route captured for a free-form suggestion. */
  sourceRoute?: string
  onClose: () => void
  /** Called after a successful store. `categorization` is set for feedback. */
  onSubmitted: (info: { categorization?: FeedbackCategorization }) => void
}

export interface FeedbackCategorization {
  ai_category: string | null
  ai_effort_estimate: string | null
}

const MAX_FILES = 3
const SEVERITIES = ['blocking', 'major', 'minor', 'cosmetic'] as const
const FEEDBACK_TYPES = [
  { value: 'feature_request', label: 'Feature Request' },
  { value: 'question', label: 'Question' },
  { value: 'observation', label: 'Observation' },
  { value: 'confusion', label: 'Confusion' },
] as const
const PRIORITIES = [
  { value: 'must_have', label: 'Must Have' },
  { value: 'should_have', label: 'Should Have' },
  { value: 'nice_to_have', label: 'Nice to Have' },
] as const

// Auto-save key — keyed on the (mode, scriptId, stepId) tuple so two
// concurrent test sessions on different steps don't overwrite each
// other. A single 24-hour TTL guard wraps every saved blob so stale
// drafts from a prior week don't reappear.
const _AUTOSAVE_KEY_PREFIX = 'fc_test_submission_draft_'
const _AUTOSAVE_TTL_MS = 24 * 60 * 60 * 1000


interface AutosaveBlob {
  ts: number
  whatHappened?: string
  expected?: string
  actual?: string
  severity?: string
  feedbackType?: string
  title?: string
  description?: string
  priority?: string
  browser?: string
}


function _autosaveKey(
  mode: Mode, scriptId: string | null, stepId: string | null,
): string {
  return `${_AUTOSAVE_KEY_PREFIX}${mode}_${scriptId ?? 'free'}_${stepId ?? 'free'}`
}


function _readAutosave(key: string): AutosaveBlob | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const blob = JSON.parse(raw) as AutosaveBlob
    if (!blob.ts || Date.now() - blob.ts > _AUTOSAVE_TTL_MS) {
      // Stale draft — drop it so a week-old in-progress note
      // doesn't ghost the next test run for the same step.
      localStorage.removeItem(key)
      return null
    }
    return blob
  } catch {
    return null
  }
}


function _writeAutosave(key: string, blob: AutosaveBlob): void {
  try {
    localStorage.setItem(key, JSON.stringify({ ...blob, ts: Date.now() }))
  } catch {
    // Storage quota / disabled — silent no-op. Auto-save is a
    // convenience, not a contract.
  }
}


function _clearAutosave(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch { /* noop */ }
}


export default function TestSubmissionPanel({
  mode, step, scriptId, sourceRoute, onClose, onSubmitted,
}: Props) {
  // Auto-save key for the current (mode, script, step) tuple.
  // Restore the in-progress text on mount; save on every keystroke
  // (debounced via the useEffect at end); clear on successful
  // submit.
  const autosaveKey = _autosaveKey(mode, scriptId, step?.id ?? null)
  const restored = _readAutosave(autosaveKey)

  // Shared
  const [files, setFiles] = useState<File[]>([])
  const [browser, setBrowser] = useState(
    restored?.browser ?? navigator.userAgent)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Quality gate
  const [clarification, setClarification] = useState<string | null>(null)
  const [reevaluated, setReevaluated] = useState(false)
  // Failure fields
  const [whatHappened, setWhatHappened] = useState(
    restored?.whatHappened ?? '')
  const [expected, setExpected] = useState(
    restored?.expected ?? (step?.expectedResult ?? ''))
  const [actual, setActual] = useState(restored?.actual ?? '')
  const [severity, setSeverity] = useState<string>(
    restored?.severity ?? 'major')
  // Feedback fields
  const [feedbackType, setFeedbackType] = useState<string>(
    restored?.feedbackType ?? 'observation')
  const [title, setTitle] = useState(restored?.title ?? '')
  const [description, setDescription] = useState(
    restored?.description ?? '')
  const [priority, setPriority] = useState<string>(
    restored?.priority ?? 'should_have')

  // Auto-save on every keystroke — the writer is cheap (localStorage
  // synchronous write of a small JSON blob) so we don't bother
  // debouncing. The effect's dep array makes React batch updates
  // naturally.
  useEffect(() => {
    _writeAutosave(autosaveKey, {
      ts: Date.now(),
      whatHappened, expected, actual, severity,
      feedbackType, title, description, priority, browser,
    })
  }, [
    autosaveKey, whatHappened, expected, actual, severity,
    feedbackType, title, description, priority, browser,
  ])

  // Escape closes — keyboard convention. Click-outside does NOT
  // close (per user spec) so a stray click doesn't dump 5 minutes
  // of typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // ── Drag + resize state ─────────────────────────────────────────────────
  //
  // The modal is a fixed-position floating window. The user can:
  //   - drag it by the title bar to reposition (no overlap clamp;
  //     bounded by viewport so it never goes fully off-screen)
  //   - resize from the bottom-right corner (min 360x320, max
  //     viewport - 32px)
  //
  // Both drag and resize use the pointer-event API so trackpad,
  // mouse, and touch all work. The handlers attach to the WINDOW
  // (not the modal) so the user can drag fast without losing the
  // pointer when it moves off the title bar.
  const _initialPos = (): { x: number; y: number } => {
    const w = window.innerWidth
    const h = window.innerHeight
    // Default to the right side, vertically centred-ish.
    return {
      x: Math.max(16, w - 460),
      y: Math.max(16, Math.round(h * 0.15)),
    }
  }
  const [pos, setPos] = useState(_initialPos)
  const [size, setSize] = useState({ width: 420, height: 540 })
  const dragRef = useRef<{ startX: number; startY: number;
                            originX: number; originY: number } | null>(null)
  const resizeRef = useRef<{ startX: number; startY: number;
                              startW: number; startH: number } | null>(null)

  const onDragStart = useCallback((e: React.PointerEvent) => {
    // Only respond to primary button (left mouse / single touch).
    if (e.button !== 0 && e.pointerType !== 'touch') return
    e.preventDefault()
    dragRef.current = {
      startX: e.clientX, startY: e.clientY,
      originX: pos.x, originY: pos.y,
    }
    const onMove = (m: PointerEvent) => {
      if (!dragRef.current) return
      const dx = m.clientX - dragRef.current.startX
      const dy = m.clientY - dragRef.current.startY
      // Keep at least 80px of the title bar inside the viewport so
      // the user can always grab it back even if they drag past
      // an edge.
      const nextX = Math.min(
        window.innerWidth - 80,
        Math.max(-(size.width - 80), dragRef.current.originX + dx))
      const nextY = Math.min(
        window.innerHeight - 40,
        Math.max(0, dragRef.current.originY + dy))
      setPos({ x: nextX, y: nextY })
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [pos.x, pos.y, size.width])

  const onResizeStart = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0 && e.pointerType !== 'touch') return
    e.preventDefault()
    e.stopPropagation()
    resizeRef.current = {
      startX: e.clientX, startY: e.clientY,
      startW: size.width, startH: size.height,
    }
    const onMove = (m: PointerEvent) => {
      if (!resizeRef.current) return
      const dw = m.clientX - resizeRef.current.startX
      const dh = m.clientY - resizeRef.current.startY
      setSize({
        width: Math.min(
          window.innerWidth - 32,
          Math.max(360, resizeRef.current.startW + dw)),
        height: Math.min(
          window.innerHeight - 32,
          Math.max(320, resizeRef.current.startH + dh)),
      })
    }
    const onUp = () => {
      resizeRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [size.width, size.height])

  const addFiles = (list: FileList | null) => {
    if (!list) return
    setFiles((prev) => [...prev, ...Array.from(list)].slice(0, MAX_FILES))
  }

  // The text the quality gate scores.
  const gateDescription = mode === 'failure' ? whatHappened : description

  const canSubmit = mode === 'failure'
    ? whatHappened.trim().length > 0 && actual.trim().length > 0
    : title.trim().length > 0 && description.trim().length > 0

  /** Runs the quality gate, then stores. lowQuality is set when the
   *  tester chooses "Submit as-is anyway" past a failed gate. */
  const submit = async (lowQuality: boolean) => {
    setBusy(true)
    setError(null)
    try {
      // ── Quality gate (skipped when already submitting as-is) ──────────
      if (!lowQuality) {
        const stepContext = step
          ? `${step.title} — expected: ${step.expectedResult}`
          : `Free-form suggestion${sourceRoute ? ` on ${sourceRoute}` : ''}`
        const gate = await axios.post<{
          passed: boolean; clarification_request: string
        }>('/api/v1/testing/quality-check', {
          type: mode,
          step_context: stepContext,
          description: gateDescription,
          actual_result: mode === 'failure' ? actual : undefined,
        })
        if (!gate.data.passed && !reevaluated) {
          // First failed gate — ask the tester to clarify.
          setClarification(gate.data.clarification_request
            || 'Could you add a little more detail?')
          setReevaluated(true)
          setBusy(false)
          return
        }
      }

      // ── Store ─────────────────────────────────────────────────────────
      const form = new FormData()
      files.slice(0, MAX_FILES).forEach((f) => form.append('screenshots', f))
      form.append('browser_info', browser)
      form.append('low_quality', String(lowQuality))

      if (mode === 'failure') {
        form.append('script_id', scriptId ?? '')
        form.append('step_id', step?.id ?? '')
        form.append('result', 'fail')
        form.append('failure_description', whatHappened)
        form.append('expected_result', expected)
        form.append('actual_result', actual)
        form.append('severity', severity)
        await axios.post('/api/v1/testing/results', form)
        // May 24 2026 — clear autosave on successful submit so a
        // follow-up open on the same step starts clean.
        _clearAutosave(autosaveKey)
        onSubmitted({})
      } else {
        form.append('feedback_type', feedbackType)
        form.append('title', title)
        form.append('description', description)
        if (step && scriptId) {
          form.append('script_id', scriptId)
          form.append('step_id', step.id)
        } else {
          form.append('source_route', sourceRoute ?? window.location.pathname)
        }
        if (feedbackType === 'feature_request') form.append('priority', priority)
        const res = await axios.post<{
          ai_category: string | null; ai_effort_estimate: string | null
        }>('/api/v1/testing/feedback', form)
        _clearAutosave(autosaveKey)
        onSubmitted({ categorization: {
          ai_category: res.data.ai_category,
          ai_effort_estimate: res.data.ai_effort_estimate,
        } })
      }
    } catch (err) {
      setError(axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Submission failed')
      setBusy(false)
    }
  }

  const heading = mode === 'failure' ? 'Describe the failure' : 'Share feedback'
  const Icon = mode === 'failure' ? AlertTriangle : Lightbulb
  const accent = mode === 'failure' ? 'text-danger' : 'text-electric'

  return (
    // May 24 2026 — non-locking, draggable, resizable floating panel.
    // No backdrop element (the page behind stays interactive); the
    // panel itself is fixed-positioned at the user's chosen
    // coordinates. Pointer events on the panel are isolated by the
    // panel's own root element — the user can scroll the page
    // outside the panel by clicking elsewhere.
    <div
      role="dialog"
      aria-label={heading}
      data-testid="test-submission-panel"
      style={{
        position: 'fixed',
        left:    `${pos.x}px`,
        top:     `${pos.y}px`,
        width:   `${size.width}px`,
        height:  `${size.height}px`,
        zIndex:  95,
      }}
      className="relative flex flex-col rounded-lg
                 border border-border bg-navy-800 shadow-2xl
                 overflow-hidden">
      <header
        onPointerDown={onDragStart}
        data-testid="test-submission-drag-handle"
        className="flex items-center justify-between gap-3 px-4 py-3
                   border-b border-border shrink-0
                   cursor-move select-none
                   bg-navy-900/60">
        <div className="flex items-center gap-2 min-w-0">
          <Move className="w-3 h-3 text-muted shrink-0"
                aria-hidden="true" />
          <Icon className={`w-4 h-4 ${accent} shrink-0`} />
          <h2 className="text-sm font-semibold text-white truncate">
            {heading}
          </h2>
        </div>
        <button type="button" onClick={onClose} aria-label="Close"
                data-testid="test-submission-close"
                className="text-muted hover:text-white shrink-0">
          <X className="w-4 h-4" />
        </button>
      </header>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {step === null && mode === 'feedback' && (
            <p className="text-2xs text-muted italic">
              This suggestion will be logged independently of your current
              test step.
            </p>
          )}

          {/* Quality-gate clarification */}
          {clarification && (
            <div className="rounded border border-electric/30 bg-electric/5
                            px-3 py-2 text-xs text-slate-200">
              <p className="font-medium text-electric mb-1">
                Before we record this, could you help us understand a bit
                more?
              </p>
              <p>{clarification}</p>
            </div>
          )}

          {mode === 'failure' ? (
            <>
              <Field label="What happened?" required>
                <textarea
                  value={whatHappened}
                  onChange={(e) => setWhatHappened(e.target.value)}
                  rows={3} className={inputCls} />
              </Field>
              <Field label="Expected">
                <textarea value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                  rows={2} className={inputCls} />
              </Field>
              <Field label="Actual result" required>
                <textarea value={actual}
                  onChange={(e) => setActual(e.target.value)}
                  rows={2} className={inputCls} />
              </Field>
              <Field label="Severity">
                <select value={severity}
                  onChange={(e) => setSeverity(e.target.value)}
                  className={inputCls}>
                  {SEVERITIES.map((s) => (
                    <option key={s} value={s}>
                      {s[0].toUpperCase() + s.slice(1)}
                    </option>
                  ))}
                </select>
              </Field>
            </>
          ) : (
            <>
              <Field label="Type">
                <select value={feedbackType}
                  onChange={(e) => setFeedbackType(e.target.value)}
                  className={inputCls}>
                  {FEEDBACK_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="Title" required>
                <input value={title} onChange={(e) => setTitle(e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Description" required>
                <textarea value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3} className={inputCls} />
              </Field>
              {feedbackType === 'feature_request' && (
                <Field label="Priority">
                  <select value={priority}
                    onChange={(e) => setPriority(e.target.value)}
                    className={inputCls}>
                    {PRIORITIES.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </select>
                </Field>
              )}
            </>
          )}

          {/* Screenshots */}
          <Field label={`Screenshot(s) — optional, max ${MAX_FILES}`}>
            <label className="flex items-center gap-2 px-2.5 py-1.5 rounded
                              border border-border text-xs text-muted
                              hover:text-white cursor-pointer w-fit">
              <Upload className="w-3 h-3" />
              Add image
              <input type="file" accept=".png,.jpg,.jpeg,.gif" multiple
                hidden onChange={(e) => addFiles(e.target.files)} />
            </label>
            {files.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {files.map((f, i) => (
                  <span key={i} className="text-2xs px-1.5 py-0.5 rounded
                                  bg-navy-700 text-slate-300">
                    {f.name.slice(0, 24)}
                  </span>
                ))}
              </div>
            )}
          </Field>

          <Field label="Browser / device">
            <input value={browser} onChange={(e) => setBrowser(e.target.value)}
              className={inputCls} />
          </Field>

          {error && (
            <div className="text-2xs text-danger">{error}</div>
          )}
        </div>

        <footer className="px-4 py-3 border-t border-border shrink-0
                           flex items-center justify-end gap-2">
          {clarification ? (
            <>
              <button type="button" onClick={() => setClarification(null)}
                disabled={busy}
                className="px-3 py-1.5 text-xs rounded border border-border
                           text-slate-300 hover:bg-navy-700">
                Update my description
              </button>
              <button type="button" onClick={() => void submit(true)}
                disabled={busy}
                className="px-3 py-1.5 text-xs rounded bg-electric/15
                           text-electric border border-electric/30">
                Submit as-is anyway
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                data-testid="test-submission-cancel"
                className="px-3 py-1.5 text-xs rounded
                           border border-border text-slate-300
                           hover:bg-navy-700
                           disabled:opacity-50 disabled:cursor-not-allowed">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submit(false)}
                disabled={busy || !canSubmit}
                data-testid="test-submission-submit"
                className="flex items-center gap-1.5 px-4 py-1.5 text-xs rounded
                           font-medium bg-electric text-white hover:bg-blue-500
                           disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy && <Loader2 className="w-3 h-3 animate-spin" />}
                {mode === 'failure' ? 'Submit Failure Report' : 'Submit Feedback'}
              </button>
            </>
          )}
        </footer>

        {/* Resize handle — bottom-right corner. Pointer-events
            isolated so a drag here resizes, not moves. */}
        <div
          onPointerDown={onResizeStart}
          data-testid="test-submission-resize-handle"
          aria-label="Resize"
          className="absolute right-0 bottom-0 w-4 h-4 cursor-se-resize
                     flex items-end justify-end pr-1 pb-1
                     text-muted hover:text-white">
          <Maximize2 className="w-3 h-3 rotate-90" aria-hidden="true" />
        </div>
    </div>
  )
}

const inputCls = 'w-full rounded border border-border bg-navy-900 px-2 py-1.5 '
  + 'text-xs text-white focus:border-electric focus:outline-none'

function Field({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode
}) {
  return (
    <div>
      <label className="text-2xs uppercase tracking-wide text-muted">
        {label}{required && <span className="text-danger"> *</span>}
      </label>
      <div className="mt-1">{children}</div>
    </div>
  )
}
