/**
 * TestRunnerSettings — the guided UAT test runner's Settings views.
 *
 * Three blocks, rendered as Settings sections:
 *   Test Results   — every tester: per-script progress, a step accordion,
 *                    re-test, and an attestation CSV export.
 *   Failure Reports — admin (ruurdsm@) only: every failed step across all
 *                    testers, with resolution.
 *   Feedback Backlog — admin only: every feedback item with its AI
 *                    categorisation, status, and filters.
 *
 * Test scripts are frontend config (testScripts.ts); results and
 * feedback come from /api/v1/testing/*. The total/pending counts are
 * derived here by diffing the script step inventory against the stored
 * results — the backend deliberately does not know the scripts.
 */
import {
  Fragment, useCallback, useEffect, useMemo, useRef, useState,
} from 'react'
import axios from 'axios'
import {
  Check, X, SkipForward, Circle, RefreshCw, Download, AlertTriangle,
  Loader2, ChevronDown, ChevronRight, ExternalLink, Search,
} from 'lucide-react'
import { TEST_SCRIPTS, getTestScript } from '../constants/testScripts'
import { startTestRun } from '../lib/testRunnerBus'
import { csvBlob } from '../lib/csv'
import Markdown from './Markdown'

type StepResult = 'pass' | 'fail' | 'skip'

interface ResultRow {
  script_id: string
  step_id: string
  result: StepResult
  severity: string | null
  failure_description: string | null
  screenshot_paths: string[]
  attested_at: string | null
  overridden: boolean
  resolved_at: string | null
  resolution_note: string | null
  low_quality: boolean
}

interface FailureRow {
  id: number
  user_email: string
  script_id: string
  step_id: string
  failure_description: string | null
  expected_result: string | null
  actual_result: string | null
  severity: string | null
  screenshot_paths: string[]
  low_quality: boolean
  attested_at: string | null
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string | null
  // Migration 025 — resolution-gate metadata. All three optional at
  // the row level: an Open failure carries no resolution; a resolved
  // failure carries resolution_type plus root cause; only a
  // code_fix_deployed row carries fix_reference + remediation_note.
  resolution_type?: 'no_bug_detected' | 'code_fix_deployed' | 'wont_fix' | null
  fix_reference?: string | null
  remediation_note?: string | null
}

// Resolution-type labels, kept here so every surface that renders a
// badge (Failure Reports row, expand card, Issue Tracker) reads from
// one source. Mirrors RESOLUTION_TYPES in tools/test_runner.py.
export const RESOLUTION_TYPE_LABEL: Record<string, string> = {
  no_bug_detected:    'No bug detected',
  code_fix_deployed:  'Code fix deployed',
  wont_fix:           "Won't fix",
}

// Fix-reference validators — must stay in sync with backend
// main._is_valid_fix_reference. A regression on either side
// breaks the Submit gate.
const SHA_RE = /^[0-9a-fA-F]{7,40}$/
const PR_RE = /^#\d{1,6}$/
const GH_URL_RE = /^https?:\/\/(?:www\.)?github\.com\/[^/]+\/[^/]+\/(?:commit|pull|issues)\/.+$/

export function isValidFixReference(s: string): boolean {
  const t = s.trim()
  return SHA_RE.test(t) || PR_RE.test(t) || GH_URL_RE.test(t)
}

interface FeedbackRow {
  id: number
  user_email: string
  script_id: string | null
  step_id: string | null
  source_route: string | null
  feedback_type: string
  title: string
  description: string
  ai_category: string | null
  ai_severity: string | null
  ai_effort_estimate: string | null
  ai_tags: string[]
  ai_summary: string | null
  ai_confidence: number | null
  low_quality: boolean
  status: string
  resolution_note: string | null
}

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const stepTitle = (scriptId: string, stepId: string): string =>
  getTestScript(scriptId)?.steps.find((s) => s.id === stepId)?.title ?? stepId

// ── Test Results — per-script progress for the current user ───────────────────

function TestResultsBlock() {
  const [results, setResults] = useState<Record<string, ResultRow[]>>({})
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    axios.get<{ results: Record<string, ResultRow[]> }>('/api/v1/testing/results')
      .then((res) => setResults(res.data.results ?? {}))
      .catch(() => setResults({}))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  if (loading) {
    return <p className="text-xs text-muted flex items-center gap-1.5">
      <Loader2 className="w-3 h-3 animate-spin" /> Loading test results…</p>
  }

  return (
    <div className="space-y-5">
      {TEST_SCRIPTS.map((script) => {
        const rows = results[script.id] ?? []
        const byStep = new Map(rows.map((r) => [r.step_id, r]))
        const attested = script.steps.filter((s) => {
          const r = byStep.get(s.id)
          return r && r.resolved_at == null
        }).length
        const total = script.steps.length
        const failedRows = rows.filter(
          (r) => r.result === 'fail' && r.resolved_at == null)
        const pct = total ? Math.round((attested / total) * 100) : 0

        return (
          <div key={script.id} className="card p-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-white text-base font-medium">{script.title}</h3>
              <span className="text-2xs text-muted">{attested}/{total} steps</span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-navy-700 overflow-hidden">
              <div className="h-full bg-electric rounded-full"
                   style={{ width: `${pct}%` }} />
            </div>

            <div className="mt-2.5 space-y-1">
              {script.steps.map((s) => {
                const r = byStep.get(s.id)
                const pending = !r || r.resolved_at != null
                return (
                  <div key={s.id} className="text-2xs">
                    <div className="flex items-center gap-2">
                      {pending ? <Circle className="w-3 h-3 text-muted shrink-0" />
                        : r!.result === 'pass'
                          ? <Check className="w-3 h-3 text-success shrink-0" />
                          : r!.result === 'fail'
                            ? <X className="w-3 h-3 text-danger shrink-0" />
                            : <SkipForward className="w-3 h-3 text-muted shrink-0" />}
                      <span className="text-slate-300 flex-1">{s.title}</span>
                      {r?.resolved_at != null && (
                        <span className="text-warning">Resolved — re-test</span>
                      )}
                      {!pending && r!.result === 'fail' && (
                        <button type="button"
                          onClick={() => startTestRun({ scriptId: script.id, stepId: s.id })}
                          className="text-electric hover:underline">
                          Re-test
                        </button>
                      )}
                    </div>
                    {r && r.result === 'fail' && r.failure_description && (
                      <p className="ml-5 text-muted italic mt-0.5">
                        {r.failure_description}
                        {r.low_quality && ' ⚠'}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>

            <div className="mt-2.5 flex flex-wrap gap-2">
              {failedRows.length > 0 && (
                <button type="button"
                  onClick={() => startTestRun({
                    scriptId: script.id, stepId: failedRows[0].step_id })}
                  className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                             border border-border text-slate-300 hover:bg-navy-700">
                  <RefreshCw className="w-3 h-3" /> Re-test Failed Steps
                </button>
              )}
              <button type="button"
                onClick={() => download(csvBlob(
                  ['Step', 'Result', 'Severity', 'Attested at', 'Overridden'],
                  rows.map((r) => [stepTitle(r.script_id, r.step_id), r.result,
                    r.severity ?? '', r.attested_at ?? '',
                    r.overridden ? 'yes' : 'no'])),
                  `attestation-${script.id}.csv`)}
                className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                           border border-border text-slate-300 hover:bg-navy-700">
                <Download className="w-3 h-3" /> Download Attestation
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Failure Reports — admin only ──────────────────────────────────────────────

// ── ResolutionCard — the read-only card rendered when a resolved row
// expands. Same shape is reused by the Issue Tracker tab.
export function ResolutionCard({ f }: { f: FailureRow }) {
  const rtype = f.resolution_type ?? null
  return (
    <div className="mt-2 rounded border border-border bg-navy-900/60
                    p-2.5 text-2xs space-y-1.5">
      {rtype && (
        <div>
          <span className="text-muted">Resolution type: </span>
          <ResolutionBadge type={rtype} />
        </div>
      )}
      {f.resolution_note && (
        <div>
          <span className="text-muted">Root cause: </span>
          <span className="text-slate-200">{f.resolution_note}</span>
        </div>
      )}
      {rtype === 'code_fix_deployed' && f.fix_reference && (
        <div>
          <span className="text-muted">Fix reference: </span>
          <FixReferenceLink reference={f.fix_reference} />
        </div>
      )}
      {rtype === 'code_fix_deployed' && f.remediation_note && (
        <div>
          <span className="text-muted">What changed: </span>
          <span className="text-slate-200">{f.remediation_note}</span>
        </div>
      )}
      {f.resolved_by && f.resolved_at && (
        <div className="text-muted">
          Resolved by {f.resolved_by} at{' '}
          {new Date(f.resolved_at).toLocaleString()}
        </div>
      )}
    </div>
  )
}


// ── ResolutionBadge — type pill, colour-coded so a scan of the
// Failure Reports list reads as "what came of each report" at a glance.
export function ResolutionBadge({ type }: { type: string }) {
  const colours: Record<string, string> = {
    no_bug_detected:    'bg-amber-900/40 text-amber-300 border-amber-700/50',
    code_fix_deployed:  'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
    wont_fix:           'bg-slate-700/40 text-slate-300 border-slate-600/50',
  }
  const cls = colours[type] ?? 'bg-navy-700 text-muted border-border'
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded border text-2xs
                      whitespace-nowrap ${cls}`}>
      {RESOLUTION_TYPE_LABEL[type] ?? type}
    </span>
  )
}


// ── FixReferenceLink — renders a commit SHA / #NNN / GH URL as a
// clickable link to GitHub. SHA & PR shortcuts assume the project
// repo (saffamiker/forest-capital); a bare URL is used as-is.
export function FixReferenceLink({ reference }: { reference: string }) {
  const r = reference.trim()
  let href: string | null = null
  let label = r
  if (SHA_RE.test(r)) {
    href = `https://github.com/saffamiker/forest-capital/commit/${r}`
    label = r.slice(0, 8)
  } else if (PR_RE.test(r)) {
    href = `https://github.com/saffamiker/forest-capital/pull/${r.slice(1)}`
  } else if (GH_URL_RE.test(r)) {
    href = r
  }
  if (!href) return <span className="text-slate-200">{r}</span>
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className="text-electric hover:underline inline-flex items-center gap-1">
      <span className="font-mono">{label}</span>
      <ExternalLink className="w-2.5 h-2.5" />
    </a>
  )
}


// ── ResolutionModal — replaces the legacy inline note input.
// Submit stays disabled until the required fields for the chosen
// resolution type are populated and the fix-reference shape validates.
export function ResolutionModal({
  failure, onClose, onResolved,
}: {
  failure: FailureRow
  onClose: () => void
  onResolved: () => void
}) {
  const [resolutionType, setResolutionType] = useState<
    'no_bug_detected' | 'code_fix_deployed' | 'wont_fix' | ''
  >('')
  const [rootCause, setRootCause] = useState('')
  const [fixReference, setFixReference] = useState('')
  const [remediation, setRemediation] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Submit-disabled gate. Mirrors the backend validator so the button
  // stays inert until the body would pass server-side validation.
  const isCodeFix = resolutionType === 'code_fix_deployed'
  const fixOk = !isCodeFix || isValidFixReference(fixReference)
  const remedOk = !isCodeFix || remediation.trim().length > 0
  const canSubmit =
    resolutionType !== '' && rootCause.trim().length > 0
    && fixOk && remedOk && !submitting

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await axios.post(`/api/v1/testing/failures/${failure.id}/resolve`, {
        resolution_type: resolutionType,
        resolution_note: rootCause.trim(),
        fix_reference: isCodeFix ? fixReference.trim() : null,
        remediation_note: isCodeFix ? remediation.trim() : null,
      })
      onResolved()
    } catch (exc) {
      const detail = (exc as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setError(detail ?? 'Could not save the resolution. Please retry.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center
                    bg-black/60 p-4" role="presentation" onClick={onClose}>
      <div role="dialog" aria-label="Mark failure resolved"
           onClick={(e) => e.stopPropagation()}
           className="w-full max-w-lg rounded-lg border border-border
                      bg-navy-800 shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3
                        border-b border-border">
          <h2 className="text-sm font-semibold text-white">
            Mark Resolved — {stepTitle(failure.script_id, failure.step_id)}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-4 py-3 space-y-3 max-h-[70vh] overflow-y-auto">
          {/* Resolution type — required radio. */}
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted
                              block mb-1.5">
              Resolution type
            </label>
            <div className="space-y-1.5">
              {(['no_bug_detected', 'code_fix_deployed', 'wont_fix'] as const)
                .map((t) => (
                  <label key={t}
                         className="flex items-start gap-2 text-xs text-slate-200
                                    cursor-pointer">
                    <input type="radio" name="resolution_type" value={t}
                      checked={resolutionType === t}
                      onChange={() => setResolutionType(t)}
                      className="mt-0.5 accent-electric" />
                    <span>
                      <span className="font-medium">
                        {RESOLUTION_TYPE_LABEL[t]}
                      </span>
                      <span className="block text-2xs text-muted">
                        {t === 'no_bug_detected'
                          && 'User error, env issue, or misread test step.'}
                        {t === 'code_fix_deployed'
                          && 'A change has landed that addresses the failure.'}
                        {t === 'wont_fix'
                          && "Closed by design or as out of scope; no re-test."}
                      </span>
                    </span>
                  </label>
                ))}
            </div>
          </div>

          {/* Root cause — required for every type. */}
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted
                              block mb-1">
              What caused this failure?
            </label>
            <textarea value={rootCause}
              onChange={(e) => setRootCause(e.target.value)}
              rows={3} placeholder="Root cause…"
              className="w-full rounded border border-border bg-navy-900
                         px-2 py-1.5 text-xs text-white" />
          </div>

          {/* Code-fix-only fields. Always present in the DOM so the
              transition reads cleanly; visibility is gated on the type. */}
          {isCodeFix && (
            <>
              <div>
                <label className="text-2xs uppercase tracking-wider text-muted
                                  block mb-1">
                  Fix reference
                </label>
                <input value={fixReference}
                  onChange={(e) => setFixReference(e.target.value)}
                  placeholder="Commit SHA, #PR-number, or GitHub URL"
                  className={`w-full rounded border bg-navy-900 px-2 py-1.5
                              text-xs text-white font-mono ${
                                fixReference && !fixOk
                                  ? 'border-danger/60'
                                  : 'border-border'}`} />
                <p className="text-2xs text-muted mt-1">
                  {fixReference && !fixOk
                    ? 'Must be 7+ hex characters, #NNN, or a GitHub URL.'
                    : 'Paste a commit SHA or PR number to confirm a fix has '
                      + 'landed before notifying the tester.'}
                </p>
              </div>
              <div>
                <label className="text-2xs uppercase tracking-wider text-muted
                                  block mb-1">
                  What was changed and how does it address the failure?
                </label>
                <textarea value={remediation}
                  onChange={(e) => setRemediation(e.target.value)}
                  rows={3} placeholder="Remediation note…"
                  className="w-full rounded border border-border bg-navy-900
                             px-2 py-1.5 text-xs text-white" />
              </div>
            </>
          )}

          {error && (
            <p className="text-2xs text-danger" role="alert">{error}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3
                        border-t border-border">
          <button type="button" onClick={onClose}
            className="px-3 py-1.5 text-xs text-muted hover:text-white">
            Cancel
          </button>
          <button type="button"
            onClick={() => void submit()}
            disabled={!canSubmit}
            className="px-4 py-1.5 rounded text-xs font-medium
                       bg-electric text-white
                       disabled:bg-navy-700 disabled:text-muted
                       disabled:cursor-not-allowed">
            {submitting ? 'Saving…' : 'Submit'}
          </button>
        </div>
      </div>
    </div>
  )
}


function FailureReportsBlock() {
  const [failures, setFailures] = useState<FailureRow[]>([])
  const [loading, setLoading] = useState(true)
  const [resolvingFailure, setResolvingFailure] = useState<FailureRow | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const load = useCallback(() => {
    setLoading(true)
    axios.get<{ failures: FailureRow[] }>('/api/v1/testing/failures')
      .then((res) => setFailures(res.data.failures ?? []))
      .catch(() => setFailures([]))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const toggleExpanded = (id: number) => {
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  if (loading) {
    return <p className="text-xs text-muted flex items-center gap-1.5">
      <Loader2 className="w-3 h-3 animate-spin" /> Loading failure reports…</p>
  }
  if (failures.length === 0) {
    return <p className="text-xs text-muted italic">No failure reports.</p>
  }

  return (
    <div className="space-y-2">
      <button type="button"
        onClick={() => download(csvBlob(
          ['Tester', 'Script', 'Step', 'Severity', 'Description',
            'Expected', 'Actual', 'Attested', 'Resolved',
            'Resolution type', 'Fix reference'],
          failures.map((f) => [f.user_email, f.script_id,
            stepTitle(f.script_id, f.step_id), f.severity ?? '',
            f.failure_description ?? '', f.expected_result ?? '',
            f.actual_result ?? '', f.attested_at ?? '',
            f.resolved_at ?? '',
            f.resolution_type
              ? (RESOLUTION_TYPE_LABEL[f.resolution_type] ?? '') : '',
            f.fix_reference ?? ''])),
          'test-failures.csv')}
        className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                   border border-border text-slate-300 hover:bg-navy-700">
        <Download className="w-3 h-3" /> Download All Failures
      </button>
      {failures.map((f) => {
        const isResolved = !!f.resolved_at
        const isExpanded = expanded.has(f.id)
        return (
          <div key={f.id} className={`rounded border p-2.5 ${
            isResolved ? 'border-border bg-navy-900 opacity-60'
              : 'border-danger/30 bg-danger/5'}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap text-2xs">
                  <span className="text-white font-medium">
                    {stepTitle(f.script_id, f.step_id)}
                  </span>
                  <span className="px-1 py-0.5 rounded bg-navy-700 text-muted">
                    {f.severity ?? 'major'}
                  </span>
                  <span className="text-muted">{f.user_email}</span>
                  {f.low_quality && (
                    <span className="text-warning" title="Low-quality submission">⚠</span>
                  )}
                  {isResolved && f.resolution_type && (
                    <ResolutionBadge type={f.resolution_type} />
                  )}
                </div>
                <p className="text-2xs text-slate-300 mt-1">
                  {f.failure_description}
                </p>
                {f.actual_result && (
                  <p className="text-2xs text-muted mt-0.5">
                    Actual: {f.actual_result}
                  </p>
                )}
                {f.screenshot_paths.length > 0 && (
                  <div className="flex gap-1.5 mt-1.5">
                    {f.screenshot_paths.map((p) => (
                      <a key={p} href={`/uploads/${p}`} target="_blank"
                         rel="noopener noreferrer">
                        <img src={`/uploads/${p}`} alt="screenshot"
                             className="h-12 rounded border border-border" />
                      </a>
                    ))}
                  </div>
                )}
              </div>
              {isResolved ? (
                <button type="button"
                  onClick={() => toggleExpanded(f.id)}
                  aria-label={isExpanded ? 'Collapse resolution' : 'Expand resolution'}
                  className="text-muted hover:text-white shrink-0
                             min-h-[24px] min-w-[24px] inline-flex
                             items-center justify-center">
                  {isExpanded
                    ? <ChevronDown className="w-3.5 h-3.5" />
                    : <ChevronRight className="w-3.5 h-3.5" />}
                </button>
              ) : (
                <button type="button"
                  onClick={() => setResolvingFailure(f)}
                  className="text-2xs text-electric hover:underline shrink-0">
                  Mark Resolved
                </button>
              )}
            </div>
            {isResolved && isExpanded && <ResolutionCard f={f} />}
          </div>
        )
      })}
      {resolvingFailure && (
        <ResolutionModal
          failure={resolvingFailure}
          onClose={() => setResolvingFailure(null)}
          onResolved={() => { setResolvingFailure(null); load() }}
        />
      )}
    </div>
  )
}

// ── Feedback Backlog — admin only ─────────────────────────────────────────────

const FB_STATUSES = ['new', 'noted', 'planned', 'wont_do', 'resolved']

function FeedbackBacklogBlock() {
  const [feedback, setFeedback] = useState<FeedbackRow[]>([])
  const [loading, setLoading] = useState(true)
  const [linkFilter, setLinkFilter] = useState<'all' | 'step' | 'free'>('all')
  const [statusFilter, setStatusFilter] = useState('')
  const [notes, setNotes] = useState<Record<number, string>>({})

  const load = useCallback(() => {
    setLoading(true)
    axios.get<{ feedback: FeedbackRow[] }>('/api/v1/testing/feedback',
      { params: statusFilter ? { status: statusFilter } : {} })
      .then((res) => setFeedback(res.data.feedback ?? []))
      .catch(() => setFeedback([]))
      .finally(() => setLoading(false))
  }, [statusFilter])
  useEffect(load, [load])

  const shown = useMemo(() => feedback.filter((f) => {
    if (linkFilter === 'step') return f.script_id != null
    if (linkFilter === 'free') return f.script_id == null
    return true
  }), [feedback, linkFilter])

  const updateStatus = async (id: number, status: string) => {
    if (status === 'new') return
    try {
      await axios.post(`/api/v1/testing/feedback/${id}/resolve`,
        { status, resolution_note: notes[id] ?? '' })
      load()
    } catch { /* row stays */ }
  }

  if (loading) {
    return <p className="text-xs text-muted flex items-center gap-1.5">
      <Loader2 className="w-3 h-3 animate-spin" /> Loading feedback…</p>
  }

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2 text-2xs">
        <span className="text-muted">Show:</span>
        {(['all', 'step', 'free'] as const).map((v) => (
          <button key={v} type="button" onClick={() => setLinkFilter(v)}
            className={`px-2 py-0.5 rounded border ${
              linkFilter === v ? 'border-electric text-electric'
                : 'border-border text-muted'}`}>
            {v === 'all' ? 'All' : v === 'step' ? 'Step-linked' : 'Free-form'}
          </button>
        ))}
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded border border-border bg-navy-900 px-1.5 py-0.5
                     text-2xs text-white">
          <option value="">All statuses</option>
          {FB_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="button"
          onClick={() => download(csvBlob(
            ['Tester', 'Context', 'Type', 'Title', 'Description', 'Category',
              'Severity', 'Effort', 'Tags', 'Status'],
            shown.map((f) => [f.user_email,
              f.script_id ? `Step: ${stepTitle(f.script_id, f.step_id ?? '')}`
                : `Route: ${f.source_route ?? '—'}`,
              f.feedback_type, f.title, f.description, f.ai_category ?? '',
              f.ai_severity ?? '', f.ai_effort_estimate ?? '',
              f.ai_tags.join('; '), f.status])),
            'test-feedback-backlog.csv')}
          className="flex items-center gap-1 px-2 py-0.5 rounded border
                     border-border text-slate-300 hover:bg-navy-700">
          <Download className="w-3 h-3" /> Export Backlog
        </button>
      </div>

      {shown.length === 0 && (
        <p className="text-xs text-muted italic">No feedback.</p>
      )}

      {shown.map((f) => (
        <div key={f.id} className="card p-2.5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Tester said */}
            <div className="min-w-0">
              <div className="text-2xs uppercase tracking-wide text-muted">
                Tester said
              </div>
              <div className="text-2xs text-electric mt-0.5">
                {f.script_id
                  ? `[Step: ${stepTitle(f.script_id, f.step_id ?? '')}]`
                  : `[${f.source_route ?? 'free-form'}]`}
              </div>
              <p className="text-xs text-white font-medium mt-0.5">{f.title}</p>
              <p className="text-2xs text-slate-300 mt-0.5">{f.description}</p>
              <p className="text-2xs text-muted mt-0.5">
                {f.user_email} · {f.feedback_type}
                {f.low_quality && ' · ⚠ low quality'}
              </p>
            </div>
            {/* AI categorized */}
            <div className="min-w-0">
              <div className="text-2xs uppercase tracking-wide text-muted">
                AI categorized
              </div>
              <div className="flex flex-wrap gap-1 mt-0.5">
                {f.ai_category && (
                  <span className="text-2xs px-1.5 py-0.5 rounded bg-navy-700
                                   text-slate-200">{f.ai_category}</span>
                )}
                {f.ai_severity && (
                  <span className="text-2xs px-1.5 py-0.5 rounded bg-navy-700
                                   text-slate-200">{f.ai_severity}</span>
                )}
                {f.ai_effort_estimate && (
                  <span className="text-2xs px-1.5 py-0.5 rounded bg-navy-700
                                   text-slate-200">{f.ai_effort_estimate} effort</span>
                )}
                {f.ai_confidence != null && f.ai_confidence < 0.7 && (
                  <span className="text-2xs text-warning" title="Low AI confidence">⚠</span>
                )}
              </div>
              {f.ai_tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {f.ai_tags.map((t) => (
                    <span key={t} className="text-2xs px-1 py-0.5 rounded
                                    bg-electric/10 text-electric">{t}</span>
                  ))}
                </div>
              )}
              {f.ai_summary && (
                <p className="text-2xs text-slate-300 mt-1 italic">{f.ai_summary}</p>
              )}
            </div>
          </div>
          {/* Status control */}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select value={f.status}
              onChange={(e) => void updateStatus(f.id, e.target.value)}
              className="rounded border border-border bg-navy-800 px-1.5 py-0.5
                         text-2xs text-white">
              {FB_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            {(f.status === 'resolved' || f.status === 'wont_do') && (
              <input
                value={notes[f.id] ?? f.resolution_note ?? ''}
                onChange={(e) => setNotes((n) => ({ ...n, [f.id]: e.target.value }))}
                onBlur={() => void updateStatus(f.id, f.status)}
                placeholder="Resolution note"
                className="flex-1 rounded border border-border bg-navy-800
                           px-2 py-0.5 text-2xs text-white" />
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Triage Reports ────────────────────────────────────────────────────────────

interface TriageIssue {
  item_type: string
  item_id: number
  number: number
  url: string
}

interface TriageReport {
  id: number
  triggered_by: string
  triggered_at: string | null
  items_assessed: number
  report_text: string
  github_issues_created: number
  status: string
  metadata: {
    immediate_count?: number
    github_issues?: TriageIssue[]
    sections?: Record<string, boolean>
  }
}

function relTime(iso: string | null): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'never'
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

// Best-effort count of the bullet items under a "## SECTION" heading —
// the engine stores deterministic immediate / issue counts, but quick
// wins live only in the agent prose.
function countSection(reportText: string, heading: string): number {
  const start = reportText.indexOf(heading)
  if (start === -1) return 0
  const rest = reportText.slice(start + heading.length)
  const next = rest.indexOf('\n## ')
  const body = next === -1 ? rest : rest.slice(0, next)
  return body.split('\n').filter((l) => /^\s*[-*]\s+\S/.test(l)).length
}

function TriageSummaryLine({ r }: { r: TriageReport }) {
  const immediate = r.metadata.immediate_count ?? 0
  const quickWins = countSection(r.report_text, '## QUICK WINS')
  return (
    <div className="text-2xs text-muted">
      {r.items_assessed} item{r.items_assessed === 1 ? '' : 's'} assessed
      {' · '}{immediate} immediate
      {' · '}{quickWins} quick win{quickWins === 1 ? '' : 's'}
      {' · '}{r.github_issues_created} GitHub issue
      {r.github_issues_created === 1 ? '' : 's'}
    </div>
  )
}

function TriageIssueLinks({ issues }: { issues: TriageIssue[] }) {
  if (issues.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {issues.map((iss) => (
        <a
          key={`${iss.item_type}-${iss.item_id}`}
          href={iss.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-2xs px-1.5 py-0.5
                     rounded border border-electric/30 bg-electric/10
                     text-electric hover:bg-electric/20 transition-colors"
        >
          <ExternalLink className="w-2.5 h-2.5" />
          #{iss.number}
        </a>
      ))}
    </div>
  )
}

// ── Per-item resolution UI ────────────────────────────────────────────────────
//
// triage_report_items (migration 023) — one row per finding parsed
// out of a triage run's verdict. The sysadmin marks items resolved
// inline; the form stamps resolved_at / resolution_note / fix_commit
// and (when requires_retest=true) retest_requested_at so the reporter
// sees a "Fix ready for retest" notification.

interface TriageItem {
  id: number
  report_id: number
  item_type: 'immediate' | 'quick_win' | 'pattern' | 'backlog'
  item_title: string
  item_body: string | null
  github_issue_number: number | null
  github_issue_url: string | null
  source_item_type: 'failure' | 'feedback' | null
  source_item_id: number | null
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string | null
  fix_commit: string | null
  requires_retest: boolean
  retest_requested_at: string | null
  retest_completed_at: string | null
  created_at: string | null
}

const ITEM_TYPE_LABEL: Record<TriageItem['item_type'], string> = {
  immediate: 'Immediate', quick_win: 'Quick Win',
  pattern: 'Pattern', backlog: 'Backlog',
}

const ITEM_TYPE_COLOUR: Record<TriageItem['item_type'], string> = {
  immediate: 'bg-danger/15 text-danger border-danger/30',
  quick_win: 'bg-success/15 text-success border-success/30',
  pattern: 'bg-warning/15 text-warning border-warning/30',
  backlog: 'bg-navy-700 text-muted border-border',
}

/** Defaults: requires_retest ON for immediate / quick_win items
 *  (those typically change behaviour), OFF for pattern / backlog
 *  (those typically don't have a single test to re-run). */
function defaultRetestForType(t: TriageItem['item_type']): boolean {
  return t === 'immediate' || t === 'quick_win'
}

function shortSha(commit: string | null): string {
  if (!commit) return ''
  return commit.length > 7 ? commit.slice(0, 7) : commit
}

function TriageItemRow({
  item, onResolve, onUnresolve,
}: {
  item: TriageItem
  onResolve: (id: number, body: {
    resolution_note: string
    fix_commit: string
    requires_retest: boolean
  }) => Promise<void>
  onUnresolve: (id: number) => Promise<void>
}) {
  // Resolved items collapsed by default — the resolution summary is
  // the headline; unresolved items expanded so the action is one click.
  const [bodyOpen, setBodyOpen] = useState(item.resolved_at === null)
  const [formOpen, setFormOpen] = useState(false)
  const [note, setNote] = useState('')
  const [commit, setCommit] = useState('')
  const [retest, setRetest] = useState(defaultRetestForType(item.item_type))
  const [saving, setSaving] = useState(false)
  const resolved = item.resolved_at !== null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!note.trim()) return
    setSaving(true)
    try {
      await onResolve(item.id, {
        resolution_note: note.trim(),
        fix_commit: commit.trim(),
        requires_retest: retest,
      })
      setFormOpen(false)
      setNote('')
      setCommit('')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`rounded border p-3 ${
      resolved ? 'border-border/60 bg-navy-900/30'
                : 'border-border bg-navy-800'}`}>
      <div className="flex items-start gap-2 flex-wrap">
        <span className={`text-2xs px-1.5 py-0.5 rounded-full border ${
          ITEM_TYPE_COLOUR[item.item_type]}`}>
          {ITEM_TYPE_LABEL[item.item_type]}
        </span>
        <span className="text-xs text-white font-medium flex-1 min-w-0">
          {item.item_title}
        </span>
        {resolved && (
          <span className="text-2xs px-1.5 py-0.5 rounded-full border
                            bg-success/15 text-success border-success/30">
            Resolved
          </span>
        )}
        {item.requires_retest && item.retest_completed_at === null
          && resolved && (
          <span className="text-2xs px-1.5 py-0.5 rounded-full border
                            bg-warning/15 text-warning border-warning/30">
            Retest pending
          </span>
        )}
        {item.requires_retest && item.retest_completed_at !== null && (
          <span className="text-2xs px-1.5 py-0.5 rounded-full border
                            bg-success/15 text-success border-success/30">
            Retest complete
          </span>
        )}
        {item.github_issue_number && item.github_issue_url && (
          <a href={item.github_issue_url} target="_blank"
             rel="noopener noreferrer"
             className="inline-flex items-center gap-1 text-2xs
                        px-1.5 py-0.5 rounded border border-electric/30
                        bg-electric/10 text-electric
                        hover:bg-electric/20 transition-colors">
            <ExternalLink className="w-2.5 h-2.5" />
            #{item.github_issue_number}
          </a>
        )}
      </div>

      {/* Resolution summary — visible whenever the item is resolved. */}
      {resolved && (
        <div className="mt-1.5 text-2xs text-muted space-y-0.5">
          <div>
            <span className="text-slate-300">{item.resolution_note}</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span>by {item.resolved_by || 'unknown'}</span>
            {item.fix_commit && (
              <span className="font-mono">commit {shortSha(item.fix_commit)}</span>
            )}
            {/* Unresolve link — sysadmin recovery for an item resolved
                in error. Tucked to the right so it does not invite
                accidental clicks. */}
            <button type="button"
                    onClick={() => void onUnresolve(item.id)}
                    className="ml-auto text-muted hover:text-danger
                               underline underline-offset-2">
              Undo resolve
            </button>
          </div>
        </div>
      )}

      {/* Body — collapsed by default for resolved items. */}
      {item.item_body && (
        <div className="mt-1.5">
          <button type="button"
                  onClick={() => setBodyOpen((o) => !o)}
                  className="text-2xs text-electric hover:underline">
            {bodyOpen ? 'Hide detail' : 'Show detail'}
          </button>
          {bodyOpen && (
            <div className="mt-1 text-2xs text-slate-300 whitespace-pre-wrap
                            break-words [overflow-wrap:anywhere]">
              {item.item_body}
            </div>
          )}
        </div>
      )}

      {/* Mark Resolved form — unresolved items only. */}
      {!resolved && !formOpen && (
        <button type="button"
                onClick={() => setFormOpen(true)}
                className="mt-2 text-2xs px-2 py-1 rounded
                           border border-electric/30 bg-electric/10
                           text-electric hover:bg-electric/20
                           transition-colors">
          Mark Resolved
        </button>
      )}
      {!resolved && formOpen && (
        <form onSubmit={(e) => void submit(e)}
              className="mt-2 space-y-1.5">
          <textarea value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder="Resolution note — what was fixed"
                    rows={2} required
                    className="w-full bg-navy-900 border border-border
                               rounded px-2 py-1 text-2xs text-slate-200
                               placeholder-muted focus:outline-none
                               focus:border-electric" />
          <input value={commit} onChange={(e) => setCommit(e.target.value)}
                 placeholder="Fix commit SHA (optional)"
                 className="w-full bg-navy-900 border border-border
                            rounded px-2 py-1 text-2xs font-mono
                            text-slate-200 placeholder-muted
                            focus:outline-none focus:border-electric" />
          <label className="flex items-center gap-1.5 text-2xs text-muted">
            <input type="checkbox" checked={retest}
                   onChange={(e) => setRetest(e.target.checked)}
                   className="w-3 h-3" />
            Requires retest — notify the reporter
          </label>
          <div className="flex items-center gap-1.5">
            <button type="submit" disabled={saving || !note.trim()}
                    className="text-2xs px-2 py-1 rounded
                               border border-electric/30 bg-electric/10
                               text-electric hover:bg-electric/20
                               disabled:opacity-50 transition-colors">
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button type="button" onClick={() => setFormOpen(false)}
                    className="text-2xs px-2 py-1 text-muted
                               hover:text-white">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  )
}

function TriageItemsBlock({ reportId }: { reportId: number }) {
  const [items, setItems] = useState<TriageItem[]>([])
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    try {
      const res = await axios.get<{ items: TriageItem[] }>(
        '/api/v1/testing/triage/items', { params: { report_id: reportId } })
      setItems(res.data.items ?? [])
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [reportId])

  useEffect(() => { void reload() }, [reload])

  const resolve = useCallback(async (id: number, body: {
    resolution_note: string
    fix_commit: string
    requires_retest: boolean
  }) => {
    try {
      await axios.patch(`/api/v1/testing/triage/items/${id}/resolve`, body)
      await reload()
    } catch { /* surfaced as the unchanged-state */ }
  }, [reload])

  const unresolve = useCallback(async (id: number) => {
    try {
      await axios.patch(`/api/v1/testing/triage/items/${id}/unresolve`)
      await reload()
    } catch { /* same */ }
  }, [reload])

  if (loading) return (
    <p className="mt-3 text-2xs text-muted italic flex items-center gap-1.5">
      <Loader2 className="w-3 h-3 animate-spin" /> Loading items…
    </p>
  )
  if (items.length === 0) return null

  const total = items.length
  const resolved = items.filter((it) => it.resolved_at !== null).length
  const awaitingRetest = items.filter(
    (it) => it.resolved_at !== null
        && it.requires_retest && it.retest_completed_at === null).length

  return (
    <div className="mt-3 pt-3 border-t border-border/50">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <span className="text-2xs text-muted uppercase tracking-wide">
          Items
        </span>
        <span className="text-2xs text-muted">
          {resolved} of {total} resolved
          {awaitingRetest > 0 && ` · ${awaitingRetest} awaiting retest`}
        </span>
      </div>
      <div className="space-y-2">
        {items.map((it) => (
          <TriageItemRow key={it.id} item={it}
                          onResolve={resolve} onUnresolve={unresolve} />
        ))}
      </div>
    </div>
  )
}


function TriageReportsBlock() {
  const [reports, setReports] = useState<TriageReport[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await axios.get<{ reports: TriageReport[] }>(
        '/api/v1/testing/triage')
      setReports(res.data.reports ?? [])
      setError(null)
    } catch {
      setError('Could not load triage reports.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // While a run is in flight, poll the latest report every 5s until its
  // status leaves 'running'.
  useEffect(() => {
    if (!running) return
    pollRef.current = setInterval(() => {
      void axios.get<{ report: TriageReport | null }>(
        '/api/v1/testing/triage/latest')
        .then((res) => {
          const latest = res.data.report
          if (latest && latest.status !== 'running') {
            setRunning(false)
            void load()
          }
        })
        .catch(() => { /* keep polling — a transient error is not fatal */ })
    }, 5000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [running, load])

  const runTriage = async () => {
    setError(null)
    try {
      await axios.post('/api/v1/testing/triage')
      setRunning(true)
    } catch {
      setError('Could not start a triage run.')
    }
  }

  const latest = reports[0] ?? null
  const previous = reports.slice(1)

  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-2">
        <p className="text-2xs text-muted">
          Automated triage of the feedback and failure backlog — runs on a
          5-item threshold, on a completed test pass, or on demand.
        </p>
        <button
          type="button"
          onClick={() => void runTriage()}
          disabled={running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                     font-medium bg-electric/10 border border-electric/30
                     text-electric hover:bg-electric/20 transition-colors
                     disabled:opacity-50 shrink-0"
        >
          {running
            ? <><Loader2 className="w-3 h-3 animate-spin" /> Triage running…</>
            : <><Search className="w-3 h-3" /> Run Triage Now</>}
        </button>
      </div>

      {error && (
        <div className="text-2xs text-danger mb-2">{error}</div>
      )}

      {loading ? (
        <p className="text-xs text-muted flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading triage reports…
        </p>
      ) : latest === null ? (
        <p className="text-xs text-muted italic">
          No triage run yet — it runs automatically as the backlog grows, or
          start one now.
        </p>
      ) : (
        <div className="space-y-3">
          {/* Latest report — shown in full. */}
          <div className="rounded border border-border bg-navy-800 p-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <span className="text-xs text-white font-medium">
                Last triage {relTime(latest.triggered_at)}
              </span>
              <span className="text-2xs text-muted">
                triggered by {latest.triggered_by} · {latest.status}
              </span>
            </div>
            <TriageSummaryLine r={latest} />
            <TriageIssueLinks issues={latest.metadata.github_issues ?? []} />
            {latest.report_text && (
              <div className="mt-2 pt-2 border-t border-border/50">
                <Markdown content={latest.report_text} />
              </div>
            )}
            {/* Per-item resolution UI. Renders below the verdict prose
                inside the latest report card so the sysadmin can mark
                items resolved without leaving the report. UAT triage
                resolution workflow (Item 3 Commit 5). */}
            <TriageItemsBlock reportId={latest.id} />
          </div>

          {/* Previous reports — collapsible history. */}
          {previous.length > 0 && (
            <div className="rounded border border-border overflow-hidden">
              <button
                type="button"
                onClick={() => setHistoryOpen((o) => !o)}
                className="w-full flex items-center gap-2 px-3 py-2 min-h-[44px]
                           text-xs text-white hover:bg-navy-700 transition-colors"
              >
                {historyOpen
                  ? <ChevronDown className="w-3.5 h-3.5 text-muted" />
                  : <ChevronRight className="w-3.5 h-3.5 text-muted" />}
                Previous reports ({previous.length})
              </button>
              {historyOpen && (
                <div className="border-t border-border divide-y divide-border">
                  {previous.map((r) => (
                    <div key={r.id} className="px-3 py-2.5">
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <span className="text-2xs text-white">
                          {relTime(r.triggered_at)} · {r.triggered_by} · {r.status}
                        </span>
                      </div>
                      <TriageSummaryLine r={r} />
                      <TriageIssueLinks issues={r.metadata.github_issues ?? []} />
                      {r.report_text && (
                        <details className="mt-1.5">
                          <summary className="text-2xs text-electric cursor-pointer">
                            View report
                          </summary>
                          <div className="mt-1.5">
                            <Markdown content={r.report_text} />
                          </div>
                        </details>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Exported sections ─────────────────────────────────────────────────────────

// ── Issue Tracker (Prompt B) ──────────────────────────────────────────────────
// Lifecycle view of every test failure ever reported: Open → Pending
// re-test → Passed | Closed. Sysadmin-only via the view_admin gate on
// the GET /api/v1/testing/issue-tracker endpoint.

interface IssueRow {
  id: number
  user_email: string
  script_id: string
  step_id: string
  result: 'pass' | 'fail' | 'skip'
  failure_description: string | null
  severity: string | null
  attested_at: string | null
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string | null
  resolution_type: 'no_bug_detected' | 'code_fix_deployed' | 'wont_fix' | null
  fix_reference: string | null
  remediation_note: string | null
  github_issue_number: number | null
  github_issue_url: string | null
  status: 'open' | 'pending_retest' | 'passed' | 'closed'
}

// Route → Feature label. Every test step in testScripts.ts is anchored
// to one of five routes; the tracker derives a coarse-grained feature
// pill from that (Council, Analytics, Reports, Settings, Dashboard).
// The mapping is intentionally coarse — finer feature taxonomy would
// require a new field on TestStep we don't have today.
const ROUTE_TO_FEATURE: Record<string, string> = {
  '/':          'Dashboard',
  '/analytics': 'Analytics',
  '/council':   'Council',
  '/reports':   'Reports',
  '/settings':  'Settings',
}

function featureForStep(scriptId: string, stepId: string): string {
  const route = getTestScript(scriptId)?.steps
    .find((s) => s.id === stepId)?.route
  return route ? (ROUTE_TO_FEATURE[route] ?? route) : '—'
}


// Status badge — colour-coded per Prompt B Part 6.
const STATUS_LABEL: Record<string, string> = {
  open:           'Open',
  pending_retest: 'Pending re-test',
  passed:         'Passed',
  closed:         'Closed',
}
const STATUS_COLOUR: Record<string, string> = {
  open:           'bg-danger/20 text-danger border-danger/40',
  pending_retest: 'bg-amber-900/40 text-amber-300 border-amber-700/50',
  passed:         'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
  closed:         'bg-slate-700/40 text-slate-300 border-slate-600/50',
}
function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLOUR[status]
    ?? 'bg-navy-700 text-muted border-border'
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded border text-2xs
                      whitespace-nowrap ${cls}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

// Reported-at "3 days ago" relative formatting with full timestamp on
// hover. Pure to keep the renderer trivial — falsy input renders "—".
function relativeAge(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms)) return iso
  const days = Math.floor(ms / 86_400_000)
  if (days < 1) {
    const hours = Math.floor(ms / 3_600_000)
    if (hours < 1) return 'just now'
    return `${hours}h ago`
  }
  if (days === 1) return '1 day ago'
  if (days < 30) return `${days} days ago`
  if (days < 365) return `${Math.floor(days / 30)}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

// Days from reported to resolved (for resolved rows) or open-age (for
// Open rows). Used for the Age and Resolution age columns.
function daysBetween(start: string | null, end: string | null): number | null {
  if (!start) return null
  const a = new Date(start).getTime()
  const b = end ? new Date(end).getTime() : Date.now()
  if (Number.isNaN(a) || Number.isNaN(b)) return null
  return Math.max(0, Math.floor((b - a) / 86_400_000))
}

function ownerForIssue(row: IssueRow): string {
  // Open: ruurdsm@ (Michael — awaiting triage/resolution)
  // Pending re-test: original tester's email
  // Passed / Closed: none (the lifecycle is terminal)
  if (row.status === 'open') return 'ruurdsm@queens.edu'
  if (row.status === 'pending_retest') return row.user_email
  return '—'
}


// Sort keys the table supports. Default sort: Status (Open first),
// then Reported (oldest first) — surfaces the items most needing
// attention at the top.
type SortKey =
  | 'id' | 'feature' | 'step' | 'tester' | 'reported' | 'age'
  | 'status' | 'resolution_type' | 'owner' | 'resolution_age'

const STATUS_ORDER: Record<string, number> = {
  open: 0, pending_retest: 1, passed: 2, closed: 3,
}


function IssueTrackerBlock() {
  const [issues, setIssues] = useState<IssueRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  // Filter state. Status defaults to "all except Closed" so the busy
  // lifecycle states are visible without history clutter.
  const [statusFilter, setStatusFilter] = useState<Set<string>>(
    new Set(['open', 'pending_retest', 'passed']))
  const [featureFilter, setFeatureFilter] = useState<Set<string>>(new Set())
  const [testerFilter, setTesterFilter] = useState<Set<string>>(new Set())
  const [resTypeFilter, setResTypeFilter] = useState<Set<string>>(new Set())
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const [sortKey, setSortKey] = useState<SortKey>('status')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    axios.get<{ issues: IssueRow[] }>('/api/v1/testing/issue-tracker')
      .then((res) => setIssues(res.data.issues ?? []))
      .catch(() => setError('Could not load the Issue Tracker.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  // Filter universe — driven by the loaded data so the dropdowns
  // never list values the dataset doesn't contain.
  const allFeatures = useMemo(() => {
    const s = new Set<string>()
    for (const r of issues) s.add(featureForStep(r.script_id, r.step_id))
    return Array.from(s).sort()
  }, [issues])
  const allTesters = useMemo(() => {
    const s = new Set<string>()
    for (const r of issues) s.add(r.user_email)
    return Array.from(s).sort()
  }, [issues])

  // Apply filters, then sort. Both pure derivations of `issues` +
  // filter state so the result is memoised cheaply.
  const visible = useMemo(() => {
    let out = issues
    if (statusFilter.size > 0) {
      out = out.filter((r) => statusFilter.has(r.status))
    }
    if (featureFilter.size > 0) {
      out = out.filter((r) => featureFilter.has(
        featureForStep(r.script_id, r.step_id)))
    }
    if (testerFilter.size > 0) {
      out = out.filter((r) => testerFilter.has(r.user_email))
    }
    if (resTypeFilter.size > 0) {
      out = out.filter((r) =>
        r.resolution_type && resTypeFilter.has(r.resolution_type))
    }
    if (fromDate) {
      out = out.filter((r) =>
        r.attested_at !== null && r.attested_at >= fromDate)
    }
    if (toDate) {
      // Inclusive: any time on the toDate counts. Add a "Z" sentinel
      // so '2026-05-22' matches the full day, not just midnight.
      const endZ = toDate + 'T23:59:59Z'
      out = out.filter((r) =>
        r.attested_at !== null && r.attested_at <= endZ)
    }

    const sorted = [...out].sort((a, b) => {
      let cmp = 0
      switch (sortKey) {
        case 'id': cmp = a.id - b.id; break
        case 'feature':
          cmp = featureForStep(a.script_id, a.step_id)
            .localeCompare(featureForStep(b.script_id, b.step_id))
          break
        case 'step':
          cmp = stepTitle(a.script_id, a.step_id)
            .localeCompare(stepTitle(b.script_id, b.step_id))
          break
        case 'tester': cmp = a.user_email.localeCompare(b.user_email); break
        case 'reported':
          // Oldest first when ascending — the items that have been
          // open longest read at the top by default.
          cmp = (a.attested_at ?? '').localeCompare(b.attested_at ?? '')
          break
        case 'age':
          cmp = (daysBetween(a.attested_at, a.resolved_at) ?? 0)
            - (daysBetween(b.attested_at, b.resolved_at) ?? 0)
          break
        case 'status':
          // Status sort is by lifecycle order, not alphabetical —
          // a string sort would put Closed before Open.
          cmp = (STATUS_ORDER[a.status] ?? 99)
            - (STATUS_ORDER[b.status] ?? 99)
          // Tie-break by reported (oldest first) so default ordering
          // is predictable.
          if (cmp === 0) {
            cmp = (a.attested_at ?? '').localeCompare(b.attested_at ?? '')
          }
          break
        case 'resolution_type':
          cmp = (a.resolution_type ?? '').localeCompare(
            b.resolution_type ?? '')
          break
        case 'owner':
          cmp = ownerForIssue(a).localeCompare(ownerForIssue(b))
          break
        case 'resolution_age':
          cmp = (a.resolved_at && daysBetween(a.attested_at, a.resolved_at)
            ? daysBetween(a.attested_at, a.resolved_at)! : 0)
            - (b.resolved_at && daysBetween(b.attested_at, b.resolved_at)
              ? daysBetween(b.attested_at, b.resolved_at)! : 0)
          break
      }
      return sortDir === 'asc' ? cmp : -cmp
    })
    return sorted
  }, [issues, statusFilter, featureFilter, testerFilter, resTypeFilter,
      fromDate, toDate, sortKey, sortDir])

  const toggleExpanded = (id: number) => {
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(k)
      setSortDir('asc')
    }
  }

  const toggleFilter = (
    setter: React.Dispatch<React.SetStateAction<Set<string>>>,
    value: string,
  ) => setter((s) => {
    const next = new Set(s)
    if (next.has(value)) next.delete(value); else next.add(value)
    return next
  })

  const exportCsv = () => {
    download(csvBlob(
      ['ID', 'Feature', 'Step', 'Tester', 'Reported',
       'Age (days)', 'Status', 'Resolution type', 'Fix reference',
       'Current owner', 'Resolution age (days)', 'Root cause',
       'What changed', 'Resolved by'],
      visible.map((r) => [
        String(r.id),
        featureForStep(r.script_id, r.step_id),
        stepTitle(r.script_id, r.step_id),
        r.user_email,
        r.attested_at ?? '',
        String(daysBetween(r.attested_at, r.resolved_at) ?? ''),
        STATUS_LABEL[r.status] ?? r.status,
        r.resolution_type
          ? (RESOLUTION_TYPE_LABEL[r.resolution_type] ?? '')
          : '',
        r.fix_reference ?? '',
        ownerForIssue(r),
        r.resolved_at
          ? String(daysBetween(r.attested_at, r.resolved_at) ?? '')
          : '',
        r.resolution_note ?? '',
        r.remediation_note ?? '',
        r.resolved_by ?? '',
      ])),
      'issue-tracker.csv')
  }

  if (loading) {
    return <p className="text-xs text-muted flex items-center gap-1.5">
      <Loader2 className="w-3 h-3 animate-spin" /> Loading issue tracker…</p>
  }
  if (error) {
    return <p className="text-xs text-danger">{error}</p>
  }
  if (issues.length === 0) {
    return <p className="text-xs text-muted italic">
      No issues recorded yet.</p>
  }

  return (
    <div className="space-y-3">
      {/* Filter bar */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3
                      p-3 rounded border border-border bg-navy-900">
        <FilterMultiSelect label="Status"
          options={(['open', 'pending_retest', 'passed', 'closed'] as const)
            .map((v) => ({ value: v, label: STATUS_LABEL[v] }))}
          selected={statusFilter}
          onToggle={(v) => toggleFilter(setStatusFilter, v)} />
        <FilterMultiSelect label="Feature"
          options={allFeatures.map((v) => ({ value: v, label: v }))}
          selected={featureFilter}
          onToggle={(v) => toggleFilter(setFeatureFilter, v)} />
        <FilterMultiSelect label="Tester"
          options={allTesters.map((v) => ({ value: v, label: v }))}
          selected={testerFilter}
          onToggle={(v) => toggleFilter(setTesterFilter, v)} />
        <FilterMultiSelect label="Resolution type"
          options={(['no_bug_detected', 'code_fix_deployed', 'wont_fix'] as const)
            .map((v) => ({ value: v, label: RESOLUTION_TYPE_LABEL[v] }))}
          selected={resTypeFilter}
          onToggle={(v) => toggleFilter(setResTypeFilter, v)} />
        <div className="text-2xs">
          <label className="block text-muted uppercase tracking-wider mb-1">
            Reported from
          </label>
          <input type="date" value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="w-full rounded border border-border bg-navy-800
                       px-2 py-1 text-xs text-white" />
        </div>
        <div className="text-2xs">
          <label className="block text-muted uppercase tracking-wider mb-1">
            Reported to
          </label>
          <input type="date" value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="w-full rounded border border-border bg-navy-800
                       px-2 py-1 text-xs text-white" />
        </div>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-2xs text-muted">
          Showing {visible.length} of {issues.length} issues
        </p>
        <button type="button" onClick={exportCsv}
          className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                     border border-border text-slate-300 hover:bg-navy-700">
          <Download className="w-3 h-3" /> Download Issue Tracker
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-2xs">
          <thead className="text-muted uppercase tracking-wider text-left">
            <tr className="border-b border-border">
              <SortHeader k="id" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>ID</SortHeader>
              <SortHeader k="feature" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Feature</SortHeader>
              <SortHeader k="step" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Step</SortHeader>
              <SortHeader k="tester" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Original tester</SortHeader>
              <SortHeader k="reported" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Reported</SortHeader>
              <SortHeader k="age" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Age</SortHeader>
              <SortHeader k="status" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Status</SortHeader>
              <SortHeader k="resolution_type" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Resolution type</SortHeader>
              <th className="px-2 py-1.5">Fix reference</th>
              <SortHeader k="owner" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Current owner</SortHeader>
              <SortHeader k="resolution_age" sortKey={sortKey} dir={sortDir}
                onToggle={toggleSort}>Resolution age</SortHeader>
              <th />
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const isExpanded = expanded.has(r.id)
              const age = daysBetween(r.attested_at, r.resolved_at)
              const resAge = r.resolved_at
                ? daysBetween(r.attested_at, r.resolved_at) : null
              return (
                <Fragment key={r.id}>
                  <tr className="border-b border-border hover:bg-navy-900/50">
                    <td className="px-2 py-1.5 font-mono text-muted">
                      {r.id}
                    </td>
                    <td className="px-2 py-1.5 text-slate-200">
                      {featureForStep(r.script_id, r.step_id)}
                    </td>
                    <td className="px-2 py-1.5 text-slate-200">
                      {stepTitle(r.script_id, r.step_id)}
                    </td>
                    <td className="px-2 py-1.5 text-muted">{r.user_email}</td>
                    <td className="px-2 py-1.5 text-muted"
                        title={r.attested_at ?? ''}>
                      {relativeAge(r.attested_at)}
                    </td>
                    <td className="px-2 py-1.5 text-muted">
                      {age !== null ? `${age}d` : '—'}
                    </td>
                    <td className="px-2 py-1.5">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-2 py-1.5">
                      {r.resolution_type
                        ? <ResolutionBadge type={r.resolution_type} />
                        : <span className="text-muted">—</span>}
                    </td>
                    <td className="px-2 py-1.5">
                      {r.fix_reference
                        ? <FixReferenceLink reference={r.fix_reference} />
                        : <span className="text-muted">—</span>}
                    </td>
                    <td className="px-2 py-1.5 text-muted">
                      {ownerForIssue(r)}
                    </td>
                    <td className="px-2 py-1.5 text-muted">
                      {resAge !== null ? `${resAge}d` : '—'}
                    </td>
                    <td className="px-2 py-1.5">
                      <button type="button" onClick={() => toggleExpanded(r.id)}
                        aria-label={isExpanded ? 'Collapse' : 'Expand'}
                        className="text-muted hover:text-white">
                        {isExpanded
                          ? <ChevronDown className="w-3.5 h-3.5" />
                          : <ChevronRight className="w-3.5 h-3.5" />}
                      </button>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="border-b border-border">
                      <td colSpan={12} className="px-2 py-2 bg-navy-900/30">
                        <ResolutionCard f={{
                          // ResolutionCard expects FailureRow shape;
                          // IssueRow is a superset for the fields the
                          // card reads, so the cast is safe.
                          ...r,
                          expected_result: null,
                          actual_result: null,
                          screenshot_paths: [],
                          low_quality: false,
                        } as unknown as FailureRow} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}


// Reusable sortable-column header. The sort arrow is intentionally
// subtle — it indicates the active column without competing with the
// data for visual weight.
function SortHeader({
  k, sortKey, dir, onToggle, children,
}: {
  k: SortKey
  sortKey: SortKey
  dir: 'asc' | 'desc'
  onToggle: (k: SortKey) => void
  children: React.ReactNode
}) {
  const active = sortKey === k
  return (
    <th className="px-2 py-1.5 font-medium">
      <button type="button" onClick={() => onToggle(k)}
        className={`inline-flex items-center gap-1 ${
          active ? 'text-white' : 'hover:text-white'}`}>
        {children}
        {active && (
          <span className="text-electric">
            {dir === 'asc' ? '↑' : '↓'}
          </span>
        )}
      </button>
    </th>
  )
}


// Multi-select dropdown for the filter bar. Compact enough to fit in
// the grid; expands on click. A pure controlled component so the
// IssueTrackerBlock owns the selection state.
function FilterMultiSelect({
  label, options, selected, onToggle,
}: {
  label: string
  options: Array<{ value: string; label: string }>
  selected: Set<string>
  onToggle: (v: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const summary = selected.size === 0
    ? 'All'
    : selected.size === 1
      ? (options.find((o) => o.value === Array.from(selected)[0])?.label
         ?? Array.from(selected)[0])
      : `${selected.size} selected`

  return (
    <div className="text-2xs relative" ref={ref}>
      <label className="block text-muted uppercase tracking-wider mb-1">
        {label}
      </label>
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="w-full rounded border border-border bg-navy-800
                   px-2 py-1 text-xs text-white text-left
                   flex items-center justify-between">
        <span className={selected.size === 0 ? 'text-muted' : ''}>
          {summary}
        </span>
        <ChevronDown className={`w-3 h-3 text-muted shrink-0
          transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute z-10 mt-1 left-0 right-0 max-h-60
                        overflow-y-auto rounded border border-border
                        bg-navy-900 shadow-lg p-1.5 space-y-1">
          {options.map((o) => (
            <label key={o.value}
              className="flex items-center gap-1.5 px-1 py-0.5 cursor-pointer
                         hover:bg-navy-800 rounded">
              <input type="checkbox" checked={selected.has(o.value)}
                onChange={() => onToggle(o.value)}
                className="accent-electric" />
              <span className="text-xs text-slate-200">{o.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}


export function TestResultsSection() {
  return <TestResultsBlock />
}


// ── Three-tab container (Prompt B Part 1) ────────────────────────────────────
// Failure Reports / Feedback Backlog / Issue Tracker share the same
// admin audience and are deliberately scoped together here. Each tab
// content lives in its own component, mounted only when active so a
// tab switch never refetches the others' data unnecessarily.
function FailureFeedbackTabs() {
  type Tab = 'failures' | 'feedback' | 'tracker'
  const [tab, setTab] = useState<Tab>('failures')

  const tabButton = (key: Tab, label: string, icon: React.ReactNode) => (
    <button key={key} type="button" onClick={() => setTab(key)}
      className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px
                  inline-flex items-center gap-1.5 ${
        tab === key
          ? 'border-electric text-white'
          : 'border-transparent text-muted hover:text-slate-200'
      }`}>
      {icon} {label}
    </button>
  )

  return (
    <div>
      <div className="flex items-center gap-1 border-b border-border">
        {tabButton('failures', 'Failure Reports',
          <AlertTriangle className="w-3.5 h-3.5 text-danger" />)}
        {tabButton('feedback', 'Feedback Backlog', null)}
        {tabButton('tracker', 'Issue Tracker',
          <Search className="w-3.5 h-3.5 text-electric" />)}
      </div>
      <div className="pt-3">
        {tab === 'failures' && (
          <div>
            <p className="text-2xs text-muted mb-2">
              Every failed step across all testers — open Mark Resolved
              to record the resolution.
            </p>
            <FailureReportsBlock />
          </div>
        )}
        {tab === 'feedback' && (
          <div>
            <p className="text-2xs text-muted mb-2">
              Tester feedback with AI categorisation — step-linked and
              free-form.
            </p>
            <FeedbackBacklogBlock />
          </div>
        )}
        {tab === 'tracker' && (
          <div>
            <p className="text-2xs text-muted mb-2">
              Lifecycle of every reported failure: Open → Pending re-test
              → Passed or Closed.
            </p>
            <IssueTrackerBlock />
          </div>
        )}
      </div>
    </div>
  )
}


export function TestAdminSections() {
  return (
    <div className="space-y-6">
      <FailureFeedbackTabs />
      <div>
        <h3 className="text-base font-semibold text-white flex items-center gap-1.5">
          <Search className="w-3.5 h-3.5 text-electric" />
          Triage Reports
        </h3>
        <p className="text-2xs text-muted mt-0.5 mb-2">
          AI triage of the backlog into immediate actions, quick wins and
          patterns — with GitHub issues for the urgent items.
        </p>
        <TriageReportsBlock />
      </div>
    </div>
  )
}
