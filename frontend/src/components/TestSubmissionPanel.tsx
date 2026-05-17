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
 */
import { useState } from 'react'
import axios from 'axios'
import { X, Upload, Loader2, AlertTriangle, Lightbulb } from 'lucide-react'
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

export default function TestSubmissionPanel({
  mode, step, scriptId, sourceRoute, onClose, onSubmitted,
}: Props) {
  // Shared
  const [files, setFiles] = useState<File[]>([])
  const [browser, setBrowser] = useState(navigator.userAgent)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Quality gate
  const [clarification, setClarification] = useState<string | null>(null)
  const [reevaluated, setReevaluated] = useState(false)
  // Failure fields
  const [whatHappened, setWhatHappened] = useState('')
  const [expected, setExpected] = useState(step?.expectedResult ?? '')
  const [actual, setActual] = useState('')
  const [severity, setSeverity] = useState<string>('major')
  // Feedback fields
  const [feedbackType, setFeedbackType] = useState<string>('observation')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<string>('should_have')

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
    <div className="fixed inset-0 z-[95] flex items-center justify-center
                    bg-black/60 p-4" role="presentation" onClick={onClose}>
      <div
        role="dialog"
        aria-label={heading}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md max-h-[88vh] flex flex-col rounded-lg
                   border border-border bg-navy-800 shadow-2xl"
      >
        <header className="flex items-center justify-between gap-3 px-4 py-3
                           border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Icon className={`w-4 h-4 ${accent}`} />
            <h2 className="text-sm font-semibold text-white">{heading}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="text-muted hover:text-white">
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
            <button
              type="button"
              onClick={() => void submit(false)}
              disabled={busy || !canSubmit}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs rounded
                         font-medium bg-electric text-white hover:bg-blue-500
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {busy && <Loader2 className="w-3 h-3 animate-spin" />}
              {mode === 'failure' ? 'Submit Failure Report' : 'Submit Feedback'}
            </button>
          )}
        </footer>
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
