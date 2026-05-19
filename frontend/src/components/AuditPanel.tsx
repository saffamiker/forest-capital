/**
 * AuditPanel — the Settings → Statistical Audit section (sysadmin only).
 *
 * Independent verification of every analytical calculation by a separate
 * AI model (claude-opus-4-7). Shows the latest audit's status and
 * findings, runs a full or pre-submission audit (polling while it runs),
 * and downloads the formatted audit report for the Analytical Appendix.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  ShieldCheck, Loader2, ChevronDown, ChevronRight, Download, FileSearch,
  CheckCircle2,
} from 'lucide-react'
import TeamGate from './TeamGate'

interface AuditFinding {
  id: number
  layer: number
  check_name: string
  metric: string
  strategy: string | null
  severity: string
  status: string
  platform_value: string | null
  auditor_value: string | null
  discrepancy: string | null
  auditor_reasoning: string | null
  resolved?: boolean
  resolution_note?: string | null
}

// A demo run (the QA tab's "Run Live Demo" button) is marked 🎯 in the
// history so a forced presentation run is not mistaken for a real audit.
function triggerLabel(triggeredBy: string): string {
  return triggeredBy === 'demo' ? '🎯 demo' : triggeredBy
}

interface AuditRun {
  id: number
  triggered_by: string
  triggered_at: string | null
  triggered_by_email: string | null
  status: string
  layer_1_status: string | null
  layer_2_status: string | null
  layer_3_status: string | null
  total_checks: number
  passed: number
  failed: number
  warnings: number
  completed_at: string | null
  findings?: {
    layer_1: AuditFinding[]
    layer_2: AuditFinding[]
    layer_3: AuditFinding[]
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

function overallStatus(r: AuditRun): { label: string; cls: string } {
  if (r.failed > 0) return { label: 'FAIL ❌', cls: 'text-danger' }
  if (r.warnings > 0) return { label: 'WARN ⚠️', cls: 'text-warning' }
  return { label: 'PASS ✅', cls: 'text-success' }
}

function allFindings(r: AuditRun): AuditFinding[] {
  const f = r.findings
  if (!f) return []
  return [...f.layer_1, ...f.layer_2, ...f.layer_3]
}

// ── One finding row ───────────────────────────────────────────────────────────

export function FindingRow({ f }: { f: AuditFinding }) {
  const [open, setOpen] = useState(false)
  // WARN acknowledge/resolve — local so a save needs no full reload.
  const [resolved, setResolved] = useState(Boolean(f.resolved))
  const [note, setNote] = useState(f.resolution_note ?? '')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [ackError, setAckError] = useState<string | null>(null)

  const isWarn = f.status === 'warning'
  const dot = f.status === 'fail' ? 'bg-danger'
    : isWarn ? 'bg-warning' : 'bg-success'
  // A WARN finding is always expandable so its acknowledge control is
  // reachable even when it carries no platform value or reasoning.
  const expandable = Boolean(f.auditor_reasoning || f.platform_value || isWarn)

  const saveAck = async () => {
    setSaving(true)
    setAckError(null)
    try {
      await axios.post(`/api/v1/audit/findings/${f.id}/resolve`,
        { resolution_note: draft.trim() })
      setResolved(true)
      setNote(draft.trim())
      setEditing(false)
    } catch {
      setAckError('Could not save the acknowledgement.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border-b border-border/40 last:border-0 py-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-start gap-2 text-left min-h-[44px] sm:min-h-0"
      >
        <span className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${dot}`} />
        <span className="flex-1 min-w-0">
          <span className="text-xs text-white">
            L{f.layer} · {f.check_name}
            {resolved && (
              <span className="ml-1.5 inline-flex items-center gap-0.5
                               text-2xs text-success align-middle">
                <CheckCircle2 className="w-3 h-3" /> Acknowledged
              </span>
            )}
          </span>
          <span className="text-2xs text-muted block">
            {f.metric}{f.strategy ? ` · ${f.strategy}` : ''}
            {f.discrepancy ? ` · ${f.discrepancy}` : ''}
          </span>
        </span>
        {expandable && (
          open ? <ChevronDown className="w-3.5 h-3.5 text-muted shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-muted shrink-0" />
        )}
      </button>
      {open && (
        <div className="pl-4 pr-2 pt-1 space-y-0.5 text-2xs text-slate-300">
          {f.platform_value && (
            <div><span className="text-muted">platform:</span> {f.platform_value}</div>
          )}
          {f.auditor_value && (
            <div><span className="text-muted">auditor:</span> {f.auditor_value}</div>
          )}
          {f.auditor_reasoning && (
            <div className="leading-relaxed">{f.auditor_reasoning}</div>
          )}

          {/* WARN acknowledge/resolve — a recorded response to the
              limitation. It does not change the audit's verdict. */}
          {isWarn && (
            <div className="pt-1.5 mt-1 border-t border-border/40">
              {resolved && !editing && (
                <div className="space-y-1">
                  <div className="flex items-center gap-1 text-success">
                    <CheckCircle2 className="w-3 h-3" /> Acknowledged
                  </div>
                  {note && (
                    <div className="text-slate-300 leading-relaxed">{note}</div>
                  )}
                  <button type="button"
                    onClick={() => { setDraft(note); setEditing(true) }}
                    className="text-electric hover:underline">
                    Edit
                  </button>
                </div>
              )}
              {!resolved && !editing && (
                <button type="button"
                  onClick={() => { setDraft(note); setEditing(true) }}
                  className="text-electric hover:underline">
                  Acknowledge
                </button>
              )}
              {editing && (
                <div className="space-y-1">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={3}
                    placeholder="Describe how you have addressed or accepted
                                 this limitation…"
                    className="w-full bg-navy-800 border border-border rounded
                               text-2xs text-white px-2 py-1.5 resize-y"
                  />
                  <div className="flex items-center gap-2">
                    <button type="button" onClick={() => void saveAck()}
                      disabled={saving || !draft.trim()}
                      className="px-2 py-1 rounded bg-electric/15 text-electric
                                 border border-electric/30 hover:bg-electric/25
                                 disabled:opacity-50">
                      {saving ? 'Saving…' : 'Save acknowledgement'}
                    </button>
                    <button type="button" onClick={() => setEditing(false)}
                      className="text-muted hover:text-white">
                      Cancel
                    </button>
                  </div>
                  {ackError && <p className="text-danger">{ackError}</p>}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FindingGroup({ title, findings, defaultOpen }: {
  title: string; findings: AuditFinding[]; defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (findings.length === 0) return null
  return (
    <div className="rounded border border-border overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 min-h-[44px]
                   text-xs text-white hover:bg-navy-700 transition-colors"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5 text-muted" />
          : <ChevronRight className="w-3.5 h-3.5 text-muted" />}
        {title} ({findings.length})
      </button>
      {open && (
        <div className="border-t border-border px-3">
          {findings.map((f, i) => <FindingRow key={i} f={f} />)}
        </div>
      )}
    </div>
  )
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export default function AuditPanel() {
  const [latest, setLatest] = useState<AuditRun | null>(null)
  const [runs, setRuns] = useState<AuditRun[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      const [latestRes, runsRes] = await Promise.all([
        axios.get<{ run: AuditRun | null }>('/api/v1/audit/runs/latest'),
        axios.get<{ runs: AuditRun[] }>('/api/v1/audit/runs'),
      ])
      setLatest(latestRes.data.run)
      setRuns(runsRes.data.runs ?? [])
      setError(null)
    } catch {
      setError('Could not load audit runs.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // Poll while an audit is in flight — every 10s until status leaves running.
  useEffect(() => {
    if (!running) return
    pollRef.current = setInterval(() => {
      void axios.get<{ run: AuditRun | null }>('/api/v1/audit/runs/latest')
        .then((res) => {
          const r = res.data.run
          if (r && r.status !== 'running') {
            setRunning(false)
            void load()
          }
        })
        .catch(() => { /* keep polling — a transient error is not fatal */ })
    }, 10000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [running, load])

  const runAudit = async (triggeredBy: 'manual' | 'pre_submission') => {
    setError(null)
    try {
      await axios.post('/api/v1/audit/run', { triggered_by: triggeredBy })
      setRunning(true)
    } catch {
      setError('Could not start the audit.')
    }
  }

  const downloadReport = async (id: number) => {
    try {
      const res = await axios.get(`/api/v1/audit/runs/${id}/export`,
        { responseType: 'blob' })
      const url = URL.createObjectURL(res.data as Blob)
      const a = document.createElement('a')
      a.href = url
      // The endpoint returns the formatted Statistical Audit Report PDF.
      a.download = `forest_capital_statistical_audit_${
        new Date().toISOString().slice(0, 10)}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setError('Could not download the audit report.')
    }
  }

  const findings = latest ? allFindings(latest) : []
  const fails = findings.filter((f) => f.status === 'fail')
  const warns = findings.filter((f) => f.status === 'warning')
  const passes = findings.filter((f) => f.status === 'pass')
  const previous = runs.filter((r) => r.id !== latest?.id)

  return (
    <div>
      <p className="text-xs text-muted mb-3">
        Independent verification of every analytical calculation by a
        separate AI model (claude-opus-4-7) — three layers: raw-data
        verification, metric-by-metric recomputation, and cross-platform
        consistency.
      </p>

      {/* Actions — triggering an audit run is sysadmin-only (the backend
          gates POST /api/v1/audit/run on manage_users); the gate keeps
          the UI honest for a team_member who can see this panel. */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <TeamGate permission="manage_users"
          tooltip="Running an audit is restricted to the platform sysadmin">
        <button
          type="button"
          onClick={() => void runAudit('manual')}
          disabled={running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                     font-medium bg-electric/10 border border-electric/30
                     text-electric hover:bg-electric/20 transition-colors
                     disabled:opacity-50"
        >
          {running
            ? <><Loader2 className="w-3 h-3 animate-spin" /> Audit running…</>
            : <><ShieldCheck className="w-3 h-3" /> Run Full Audit</>}
        </button>
        </TeamGate>
        <TeamGate permission="manage_users"
          tooltip="Running an audit is restricted to the platform sysadmin">
        <button
          type="button"
          onClick={() => void runAudit('pre_submission')}
          disabled={running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                     font-medium border border-warning/30 bg-warning/10
                     text-warning hover:bg-warning/20 transition-colors
                     disabled:opacity-50"
        >
          <FileSearch className="w-3 h-3" /> Run Pre-Submission Audit
        </button>
        </TeamGate>
        {latest && latest.status !== 'running' && (
          <button
            type="button"
            onClick={() => void downloadReport(latest.id)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs
                       border border-border text-slate-300 hover:bg-navy-700
                       transition-colors"
          >
            <Download className="w-3 h-3" /> Download Audit Report
          </button>
        )}
      </div>

      {error && <div className="text-2xs text-danger mb-2">{error}</div>}

      {loading ? (
        <p className="text-xs text-muted flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading audit history…
        </p>
      ) : latest === null ? (
        <p className="text-xs text-muted italic">
          No audit has been run yet. Run a full audit to independently
          verify every analytical figure.
        </p>
      ) : (
        <div className="space-y-3">
          {/* Latest run summary */}
          <div className="rounded border border-border bg-navy-800 p-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <span className="text-sm font-semibold">
                Last audit {relTime(latest.triggered_at)}
              </span>
              <span className={`text-sm font-semibold ${
                overallStatus(latest).cls}`}>
                {latest.status === 'running'
                  ? 'Running…' : overallStatus(latest).label}
              </span>
            </div>
            <div className="text-2xs text-muted mt-1">
              {latest.total_checks} checks · {latest.passed} passed ·{' '}
              {latest.warnings} warnings · {latest.failed} failures ·{' '}
              triggered by {triggerLabel(latest.triggered_by)}
            </div>
            {/* Per-layer progress */}
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs">
              {([['Layer 1 — Raw data', latest.layer_1_status],
                 ['Layer 2 — Recomputation', latest.layer_2_status],
                 ['Layer 3 — Consistency', latest.layer_3_status],
                ] as const).map(([label, st]) => (
                <span key={label} className="text-muted">
                  {label}:{' '}
                  <span className={
                    st === 'pass' ? 'text-success'
                      : st === 'fail' ? 'text-danger'
                        : st === 'skip' ? 'text-muted' : 'text-warning'}>
                    {latest.status === 'running' && !st ? '⏳' : (st ?? '—')}
                  </span>
                </span>
              ))}
            </div>
          </div>

          {/* Findings — failures expanded, warnings/passes collapsed */}
          {findings.length > 0 && (
            <div className="space-y-2">
              <FindingGroup title="Critical failures" findings={fails}
                defaultOpen />
              <FindingGroup title="Warnings" findings={warns}
                defaultOpen={false} />
              <FindingGroup title="Passed checks" findings={passes}
                defaultOpen={false} />
            </div>
          )}

          {/* Previous runs */}
          {previous.length > 0 && (
            <div className="rounded border border-border overflow-hidden">
              <button
                type="button"
                onClick={() => setHistoryOpen((o) => !o)}
                className="w-full flex items-center gap-2 px-3 py-2 min-h-[44px]
                           text-xs text-white hover:bg-navy-700 transition-colors"
              >
                {historyOpen ? <ChevronDown className="w-3.5 h-3.5 text-muted" />
                  : <ChevronRight className="w-3.5 h-3.5 text-muted" />}
                Previous audits ({previous.length})
              </button>
              {historyOpen && (
                <div className="border-t border-border divide-y divide-border">
                  {previous.map((r) => (
                    <div key={r.id} className="px-3 py-2 flex items-center
                                                justify-between gap-2 flex-wrap">
                      <span className="text-2xs text-white">
                        {relTime(r.triggered_at)} · {triggerLabel(r.triggered_by)}
                        {' '}·{' '}
                        <span className={overallStatus(r).cls}>
                          {overallStatus(r).label}
                        </span>
                      </span>
                      <button
                        type="button"
                        onClick={() => void downloadReport(r.id)}
                        className="flex items-center gap-1 text-2xs text-electric
                                   hover:underline"
                      >
                        <Download className="w-3 h-3" /> Report
                      </button>
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
