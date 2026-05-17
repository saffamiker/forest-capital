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
import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  Check, X, SkipForward, Circle, RefreshCw, Download, AlertTriangle,
  Loader2,
} from 'lucide-react'
import { TEST_SCRIPTS, getTestScript } from '../constants/testScripts'
import { startTestRun } from '../lib/testRunnerBus'
import { csvBlob } from '../lib/csv'

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
          <div key={script.id} className="rounded border border-border bg-navy-900 p-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-white text-sm font-medium">{script.title}</h3>
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

function FailureReportsBlock() {
  const [failures, setFailures] = useState<FailureRow[]>([])
  const [loading, setLoading] = useState(true)
  const [resolvingId, setResolvingId] = useState<number | null>(null)
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    axios.get<{ failures: FailureRow[] }>('/api/v1/testing/failures')
      .then((res) => setFailures(res.data.failures ?? []))
      .catch(() => setFailures([]))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const resolve = async (id: number) => {
    try {
      await axios.post(`/api/v1/testing/failures/${id}/resolve`,
        { resolution_note: note })
      setResolvingId(null)
      setNote('')
      load()
    } catch { /* surfaced by the row staying put */ }
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
            'Expected', 'Actual', 'Attested', 'Resolved'],
          failures.map((f) => [f.user_email, f.script_id,
            stepTitle(f.script_id, f.step_id), f.severity ?? '',
            f.failure_description ?? '', f.expected_result ?? '',
            f.actual_result ?? '', f.attested_at ?? '',
            f.resolved_at ?? ''])),
          'test-failures.csv')}
        className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                   border border-border text-slate-300 hover:bg-navy-700">
        <Download className="w-3 h-3" /> Download All Failures
      </button>
      {failures.map((f) => (
        <div key={f.id} className={`rounded border p-2.5 ${
          f.resolved_at ? 'border-border bg-navy-900 opacity-60'
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
              {f.resolved_at && (
                <p className="text-2xs text-success mt-1">
                  Resolved{f.resolved_by ? ` by ${f.resolved_by}` : ''}
                  {f.resolution_note ? ` — ${f.resolution_note}` : ''}
                </p>
              )}
            </div>
            {!f.resolved_at && (
              <button type="button" onClick={() => setResolvingId(f.id)}
                className="text-2xs text-electric hover:underline shrink-0">
                Mark Resolved
              </button>
            )}
          </div>
          {resolvingId === f.id && (
            <div className="mt-2 flex gap-2">
              <input value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="Resolution note"
                className="flex-1 rounded border border-border bg-navy-900
                           px-2 py-1 text-2xs text-white" />
              <button type="button" onClick={() => void resolve(f.id)}
                className="text-2xs px-2 py-1 rounded bg-electric text-white">
                Save
              </button>
            </div>
          )}
        </div>
      ))}
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
        <div key={f.id} className="rounded border border-border bg-navy-900 p-2.5">
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

// ── Exported sections ─────────────────────────────────────────────────────────

export function TestResultsSection() {
  return <TestResultsBlock />
}

export function TestAdminSections() {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 text-danger" />
          Failure Reports
        </h3>
        <p className="text-2xs text-muted mt-0.5 mb-2">
          Every failed step across all testers.
        </p>
        <FailureReportsBlock />
      </div>
      <div>
        <h3 className="text-sm font-semibold text-white">Feedback Backlog</h3>
        <p className="text-2xs text-muted mt-0.5 mb-2">
          Tester feedback with AI categorisation — step-linked and free-form.
        </p>
        <FeedbackBacklogBlock />
      </div>
    </div>
  )
}
