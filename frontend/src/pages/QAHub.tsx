/**
 * QAHub — the QA tab, a two-section quality-assurance hub:
 *   1. Methodology Review — the QA agent's checklist. Every authenticated
 *      user sees it (no permission change from the old QA tab).
 *   2. Statistical Audit — independent re-verification of every analytical
 *      figure by a separate model. The full findings panel is project-team
 *      only; viewers see a read-only summary of the latest audit run.
 *
 * A "Run Full QA" button triggers both at once (project team only) with
 * unified progress. A "Presentation View" shows a clean QA certificate
 * for screen-sharing during the final presentation. The Statistical Audit
 * was relocated here from Settings — the QA tab is the single home for
 * both audit types.
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  ShieldCheck, Loader2, CheckCircle, XCircle, AlertTriangle, PlayCircle,
  Presentation, Download,
} from 'lucide-react'
import QAAuditPanel from '../components/QAAuditPanel'
import AuditPanel from '../components/AuditPanel'
import TeamGate from '../components/TeamGate'
import { useIsTeamMember } from '../hooks/usePermissions'
import { useQAStore } from '../stores/qaStore'
import type { QAAuditResult } from '../types/agents'

interface LatestRun {
  id?: number
  status: string
  triggered_by?: string
  triggered_at: string | null
  completed_at: string | null
  total_checks: number
  passed: number
  failed: number
  warnings: number
  layer_1_status: string | null
  layer_2_status: string | null
  layer_3_status: string | null
}

interface LatestRunResponse {
  run: LatestRun | null
  is_current?: boolean
  statistical_current?: boolean
  qa_current?: boolean
  current_data_hash?: string
  last_hash?: string | null
}

type Verdict = 'PASS' | 'WARN' | 'FAIL'
type Phase = 'idle' | 'running' | 'done' | 'error'

// Smart audit caching — an audit re-runs only when the data it verifies
// has changed. relTime renders the "✓ Verified 2h ago" line.
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

// Poll the statistical audit at the same 10s cadence AuditPanel uses,
// capped so a never-completing run cannot poll forever.
const AUDIT_POLL_MS = 10_000
const AUDIT_POLL_MAX = 36   // 6 minutes

function runVerdict(r: LatestRun): Verdict {
  if (r.failed > 0) return 'FAIL'
  if (r.warnings > 0) return 'WARN'
  return 'PASS'
}

const VERDICT_CLS: Record<Verdict, string> = {
  PASS: 'text-success',
  WARN: 'text-warning',
  FAIL: 'text-danger',
}

function worstVerdict(vs: Verdict[]): Verdict {
  if (vs.includes('FAIL')) return 'FAIL'
  if (vs.includes('WARN')) return 'WARN'
  return 'PASS'
}

// ── Read-only audit summary for non-team viewers ──────────────────────────────

function AuditViewerSummary() {
  const [run, setRun] = useState<LatestRun | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<{ run: LatestRun | null }>('/api/v1/audit/runs/latest')
      .then((res) => { if (!cancelled) setRun(res.data.run) })
      .catch(() => { if (!cancelled) setRun(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  if (loading) {
    return (
      <p className="text-xs text-muted flex items-center gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin" /> Loading audit status…
      </p>
    )
  }
  if (!run) {
    return (
      <div className="rounded border border-border bg-navy-800 p-3 text-xs text-muted italic">
        No statistical audit has been run yet.
        {' '}Full results are available to project team members.
      </div>
    )
  }
  const v = runVerdict(run)
  const date = run.triggered_at
    ? new Date(run.triggered_at).toLocaleDateString() : 'unknown date'
  return (
    <div className="rounded border border-border bg-navy-800 p-3">
      <div className="flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-muted shrink-0" />
        <span className="text-sm text-white">
          Last audit: <span className={`font-semibold ${VERDICT_CLS[v]}`}>{v}</span>
        </span>
      </div>
      <div className="text-2xs text-muted mt-1 font-mono">
        {date} · {run.total_checks} checks · {run.passed} passed
      </div>
      <div className="text-2xs text-muted mt-1 italic">
        Full results available to project team members.
      </div>
    </div>
  )
}

// ── Unified-run progress card ─────────────────────────────────────────────────

function PhaseRow({ label, phase, detail }: {
  label: string; phase: Phase; detail: string
}) {
  const Icon = phase === 'running' ? Loader2
    : phase === 'error' ? AlertTriangle
      : phase === 'done' ? CheckCircle : PlayCircle
  const cls = phase === 'error' ? 'text-warning'
    : phase === 'done' ? 'text-success' : 'text-muted'
  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon className={`w-4 h-4 shrink-0 ${cls} ${phase === 'running' ? 'animate-spin' : ''}`} />
      <span className="text-white w-40 shrink-0">{label}</span>
      <span className={`text-xs ${cls}`}>{detail}</span>
    </div>
  )
}

// ── Presentation View — the QA certificate ────────────────────────────────────

function layerIcon(status: string | null): string {
  if (status === 'pass') return '✅'
  if (status === 'fail') return '❌'
  if (status === 'warning') return '⚠️'
  if (status === 'skip') return '—'
  return '—'
}

function QACertificate({ onExit }: { onExit: () => void }) {
  const qaResult = useQAStore((s) => s.result)
  const [run, setRun] = useState<LatestRun | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<{ run: LatestRun | null }>('/api/v1/audit/runs/latest')
      .then((res) => { if (!cancelled) setRun(res.data.run) })
      .catch(() => { if (!cancelled) setRun(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  // Overall verdict across both audit types.
  const verdicts: Verdict[] = []
  if (qaResult) verdicts.push(qaResult.verdict)
  if (run) verdicts.push(runVerdict(run))
  const overall: Verdict = verdicts.length ? worstVerdict(verdicts) : 'WARN'
  const OverallIcon = overall === 'FAIL' ? XCircle
    : overall === 'WARN' ? AlertTriangle : CheckCircle

  const stamp = run?.completed_at ?? run?.triggered_at ?? null
  const stampText = stamp ? new Date(stamp).toLocaleString() : 'no full QA run recorded yet'

  return (
    <div className="p-6 md:p-10 max-w-2xl mx-auto">
      <div className="flex justify-end mb-4">
        <button
          type="button"
          onClick={onExit}
          className="text-xs px-2.5 py-1.5 rounded border border-border
                     text-muted hover:text-white transition-colors"
        >
          Exit Presentation View
        </button>
      </div>

      <div className="text-center space-y-1 mb-8">
        <div className="text-2xs uppercase tracking-[0.2em] text-electric">
          Quality Assurance Certificate
        </div>
        <h1 className="text-xl font-semibold text-white">
          Forest Capital Portfolio Intelligence System
        </h1>
        <p className="text-xs text-muted">
          FNA 670 — McColl School of Business
        </p>
        <p className="text-2xs text-muted font-mono pt-1">
          Last full QA run: {stampText}
        </p>
      </div>

      {loading ? (
        <p className="text-center text-muted text-sm flex items-center justify-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading certificate…
        </p>
      ) : (
        <div className="space-y-4">
          {/* Methodology Review box */}
          <div className="rounded-lg border border-border bg-navy-800 p-5">
            <div className="text-2xs uppercase tracking-wide text-muted mb-2">
              Methodology Review
            </div>
            {qaResult ? (
              <div className="flex items-center gap-3">
                <CheckCircle className={`w-6 h-6 ${VERDICT_CLS[qaResult.verdict]}`} />
                <div>
                  <div className="text-lg font-mono text-white">
                    {qaResult.checks_passed}/{qaResult.checks_total} checks passed
                  </div>
                  <div className="text-2xs text-muted">
                    {qaResult.checks_failed} failures · {qaResult.checks_warned} warnings
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted italic">
                The methodology checklist has not been run yet.
              </p>
            )}
          </div>

          {/* Statistical Audit box */}
          <div className="rounded-lg border border-border bg-navy-800 p-5">
            <div className="text-2xs uppercase tracking-wide text-muted mb-2">
              Statistical Audit
            </div>
            {run ? (
              <div className="space-y-1.5">
                <div className="text-sm text-white flex justify-between">
                  <span>Layer 1: Raw data</span><span>{layerIcon(run.layer_1_status)}</span>
                </div>
                <div className="text-sm text-white flex justify-between">
                  <span>Layer 2: Recomputation</span><span>{layerIcon(run.layer_2_status)}</span>
                </div>
                <div className="text-sm text-white flex justify-between">
                  <span>Layer 3: Consistency</span><span>{layerIcon(run.layer_3_status)}</span>
                </div>
                <div className="text-2xs text-muted pt-1.5 border-t border-border/50 mt-1.5">
                  {run.total_checks} checks · {run.passed} passed ·
                  {' '}Independent model: Opus
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted italic">
                No statistical audit has been run yet.
              </p>
            )}
          </div>

          {/* Overall box */}
          <div className={`rounded-lg border p-5 ${
            overall === 'FAIL' ? 'border-danger/30 bg-danger/5'
              : overall === 'WARN' ? 'border-warning/30 bg-warning/5'
                : 'border-success/30 bg-success/5'}`}>
            <div className="flex items-center gap-3">
              <OverallIcon className={`w-7 h-7 ${VERDICT_CLS[overall]}`} />
              <div>
                <div className={`text-lg font-bold ${VERDICT_CLS[overall]}`}>
                  OVERALL: {overall}
                </div>
                <div className="text-xs text-muted">
                  {overall === 'PASS'
                    ? 'All analytical results have been independently verified.'
                    : overall === 'WARN'
                      ? 'Results verified — review the disclosed warnings.'
                      : 'One or more checks failed — review before presenting.'}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Hub ───────────────────────────────────────────────────────────────────────

export default function QAHub() {
  const isTeam = useIsTeamMember()
  const qaResult: QAAuditResult | null = useQAStore((s) => s.result)
  const qaReload = useQAStore((s) => s.reload)

  const [methodPhase, setMethodPhase] = useState<Phase>('idle')
  const [auditPhase, setAuditPhase] = useState<Phase>('idle')
  const [auditRun, setAuditRun] = useState<LatestRun | null>(null)
  // Bumped when a full run completes — remounts AuditPanel so it re-fetches.
  const [auditRefreshKey, setAuditRefreshKey] = useState(0)
  const [showProgress, setShowProgress] = useState(false)
  const [presentMode, setPresentMode] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)
  // Smart audit caching — is the last audit still current for this data?
  const [isCurrent, setIsCurrent] = useState<boolean | null>(null)
  const [statisticalCurrent, setStatisticalCurrent] = useState(false)
  const [qaCurrent, setQaCurrent] = useState(false)
  const [currencyRunAt, setCurrencyRunAt] = useState<string | null>(null)
  const [showDemoConfirm, setShowDemoConfirm] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current)
  }, [])

  // On mount, ask whether the cached audit still reflects the current
  // data — drives the "verified, no re-run needed" state of the button.
  const loadCurrency = () => {
    void axios.get<LatestRunResponse>('/api/v1/audit/runs/latest')
      .then((res) => {
        setIsCurrent(res.data.is_current ?? false)
        setStatisticalCurrent(res.data.statistical_current ?? false)
        setQaCurrent(res.data.qa_current ?? false)
        setCurrencyRunAt(res.data.run?.triggered_at ?? null)
      })
      .catch(() => { setIsCurrent(null) })
  }
  useEffect(() => { loadCurrency() }, [])

  const fullRunActive = methodPhase === 'running' || auditPhase === 'running'

  const runFullQA = (demo = false) => {
    if (fullRunActive) return
    setShowProgress(true)
    setMethodPhase('running')
    setAuditPhase('running')
    setAuditRun(null)

    // Methodology — qaStore.reload() re-runs the checklist; QAAuditPanel
    // reads the same store and updates on its own.
    void qaReload()
      .then(() => setMethodPhase(useQAStore.getState().error ? 'error' : 'done'))
      .catch(() => setMethodPhase('error'))

    // Statistical audit — fire, then poll the latest run until it settles.
    // A demo run forces a fresh audit regardless of cache currency and is
    // tagged triggered_by="demo" so the audit history can mark it.
    void axios.post('/api/v1/audit/run',
      demo ? { reason: 'demo' } : { triggered_by: 'manual' })
      .then(() => {
        let polls = 0
        pollRef.current = setInterval(() => {
          polls += 1
          void axios.get<{ run: LatestRun | null }>('/api/v1/audit/runs/latest')
            .then((res) => {
              const run = res.data.run
              if (run && run.status !== 'running') {
                if (pollRef.current) clearInterval(pollRef.current)
                setAuditRun(run)
                setAuditPhase('done')
                setAuditRefreshKey((k) => k + 1)
                loadCurrency()   // the fresh run is now the current one
              } else if (!run || polls >= AUDIT_POLL_MAX) {
                // No run row (the audit could not start — e.g. no
                // database) or the cap was hit — stop and report.
                if (pollRef.current) clearInterval(pollRef.current)
                setAuditPhase('error')
              }
            })
            .catch(() => {
              if (polls >= AUDIT_POLL_MAX && pollRef.current) {
                clearInterval(pollRef.current)
                setAuditPhase('error')
              }
            })
        }, AUDIT_POLL_MS)
      })
      .catch(() => setAuditPhase('error'))
  }

  // Download the Methodology Audit Report PDF (GET /api/v1/qa/export).
  const downloadMethodologyReport = () => {
    setReportError(null)
    void axios.get('/api/v1/qa/export', { responseType: 'blob' })
      .then((res) => {
        const url = URL.createObjectURL(res.data as Blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `forest_capital_methodology_audit_${
          new Date().toISOString().slice(0, 10)}.pdf`
        a.click()
        URL.revokeObjectURL(url)
      })
      .catch(() => setReportError('Could not download the methodology report.'))
  }

  if (presentMode) {
    return <QACertificate onExit={() => setPresentMode(false)} />
  }

  // Per-section progress detail.
  const methodDetail = methodPhase === 'running' ? 'Running…'
    : methodPhase === 'error' ? 'Could not complete'
      : methodPhase === 'done' && qaResult
        ? `${qaResult.checks_passed}/${qaResult.checks_total} passed`
        : ''
  const auditDetail = auditPhase === 'running' ? 'Running…'
    : auditPhase === 'error' ? 'Could not complete'
      : auditPhase === 'done' && auditRun
        ? `${auditRun.passed}/${auditRun.total_checks} passed`
          + (auditRun.warnings > 0 ? ` · ${auditRun.warnings} warning(s)` : '')
        : ''

  // Overall verdict — only once both have settled.
  const bothDone = methodPhase !== 'idle' && methodPhase !== 'running'
    && auditPhase !== 'idle' && auditPhase !== 'running'
  let overall: Verdict | null = null
  if (bothDone) {
    const verdicts: Verdict[] = []
    if (methodPhase === 'error') verdicts.push('FAIL')
    else if (qaResult) verdicts.push(qaResult.verdict)
    if (auditPhase === 'error') verdicts.push('FAIL')
    else if (auditRun) verdicts.push(runVerdict(auditRun))
    overall = verdicts.length ? worstVerdict(verdicts) : 'WARN'
  }
  const OverallIcon = overall === 'FAIL' ? XCircle
    : overall === 'WARN' ? AlertTriangle : CheckCircle

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-8">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">Quality Assurance</h1>
          <p className="text-sm text-muted mt-1">
            Methodology review and independent statistical audit — the two
            ways every analytical result on this platform is verified.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TeamGate permission="team_member">
            <button
              type="button"
              onClick={() => setPresentMode(true)}
              className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium
                         border border-warning/40 bg-warning/10 text-warning
                         hover:bg-warning/20 transition-colors"
            >
              <Presentation className="w-4 h-4" /> Presentation View
            </button>
          </TeamGate>
          <TeamGate permission="manage_users">
            <button
              type="button"
              onClick={() => runFullQA(false)}
              disabled={fullRunActive}
              className={`flex items-center gap-1.5 px-3 py-2 rounded text-sm
                         font-medium transition-colors disabled:opacity-50 ${
                isCurrent
                  ? 'border border-border text-muted hover:bg-navy-700'
                  : 'bg-electric/10 border border-electric/30 text-electric '
                    + 'hover:bg-electric/20'}`}
            >
              {fullRunActive
                ? <><Loader2 className="w-4 h-4 animate-spin" /> Running…</>
                : isCurrent
                  ? <><PlayCircle className="w-4 h-4" /> Re-run Audit</>
                  : <><PlayCircle className="w-4 h-4" /> Run Full QA</>}
            </button>
          </TeamGate>
        </div>
      </div>

      {/* Smart audit caching — currency banner. */}
      {isCurrent !== null && (
        <div className={`rounded border p-3 flex items-center justify-between
                         gap-3 flex-wrap ${
          isCurrent
            ? 'border-success/30 bg-success/5'
            : 'border-warning/30 bg-warning/5'}`}>
          {isCurrent ? (
            <span className="text-xs text-success flex items-center gap-1.5">
              <CheckCircle className="w-4 h-4 shrink-0" />
              Verified {relTime(currencyRunAt)} · Data unchanged ·
              {' '}No re-run needed
            </span>
          ) : (
            <div className="text-xs text-warning space-y-0.5">
              <div className="flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                Data has changed since the last audit — run a full QA to
                re-verify.
              </div>
              <div className="pl-6 font-mono text-2xs">
                Statistical audit:{' '}
                <span className={statisticalCurrent ? 'text-success' : 'text-warning'}>
                  {statisticalCurrent ? '✓ current' : '⚠️ stale'}
                </span>
              </div>
              <div className="pl-6 font-mono text-2xs">
                Methodology audit:{' '}
                <span className={qaCurrent ? 'text-success' : 'text-warning'}>
                  {qaCurrent ? '✓ current' : '⚠️ stale'}
                </span>
              </div>
            </div>
          )}
          {isCurrent && (
            <TeamGate permission="manage_users">
              <button
                type="button"
                onClick={() => setShowDemoConfirm(true)}
                disabled={fullRunActive}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded
                           text-xs font-medium border border-warning/40
                           bg-warning/10 text-warning hover:bg-warning/20
                           transition-colors disabled:opacity-50"
              >
                <PlayCircle className="w-3.5 h-3.5" /> Run Live Demo
              </button>
            </TeamGate>
          )}
        </div>
      )}

      {/* Demo-run confirmation. */}
      {showDemoConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center
                        bg-black/60 p-4"
             onClick={() => setShowDemoConfirm(false)}>
          <div className="card p-5 max-w-md w-full space-y-3"
               onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-white">
              Run a live demo audit?
            </h3>
            <p className="text-xs text-muted leading-relaxed">
              This forces a fresh three-layer statistical audit even though
              the data has not changed — the independent Opus model
              recomputes every metric from scratch. Use it to show the
              audit running live during a presentation. It costs an Opus
              run and takes a few minutes.
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDemoConfirm(false)}
                className="px-3 py-1.5 rounded text-xs border border-border
                           text-muted hover:text-white transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => { setShowDemoConfirm(false); runFullQA(true) }}
                className="px-3 py-1.5 rounded text-xs font-medium
                           bg-warning/10 border border-warning/40 text-warning
                           hover:bg-warning/20 transition-colors"
              >
                Run Live Demo
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Unified-run progress — shown once a full run has been triggered. */}
      {showProgress && (
        <div className="card p-4 space-y-2">
          <div className="text-2xs uppercase tracking-wide text-muted">
            Full QA run
          </div>
          <PhaseRow label="Methodology Review" phase={methodPhase} detail={methodDetail} />
          <PhaseRow label="Statistical Audit" phase={auditPhase} detail={auditDetail} />
          {overall && (
            <div className="flex items-center gap-2 pt-2 mt-1 border-t border-border/50">
              <OverallIcon className={`w-4 h-4 shrink-0 ${VERDICT_CLS[overall]}`} />
              <span className="text-sm text-white">
                Overall: <span className={`font-semibold ${VERDICT_CLS[overall]}`}>{overall}</span>
              </span>
              <button
                type="button"
                onClick={() => setShowProgress(false)}
                className="ml-auto text-xs text-electric hover:underline"
              >
                View Full Results
              </button>
            </div>
          )}
        </div>
      )}

      {/* Section 1 — Methodology Review (every authenticated user). */}
      <section className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-white">Methodology Review</h2>
            <p className="text-xs text-muted mt-0.5">
              The QA agent's methodology checklist — backtesting assumptions,
              statistical integrity, cross-validation and presentation rigour.
            </p>
          </div>
          <button
            type="button"
            onClick={downloadMethodologyReport}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs
                       border border-border text-slate-300 hover:bg-navy-700
                       transition-colors shrink-0"
          >
            <Download className="w-3.5 h-3.5" /> Download Methodology Report
          </button>
        </div>
        {reportError && (
          <p className="text-2xs text-danger">{reportError}</p>
        )}
        <QAAuditPanel />
      </section>

      {/* Section 2 — Statistical Audit (full panel: team only). */}
      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-white">Statistical Audit</h2>
          <p className="text-xs text-muted mt-0.5">
            Independent re-verification of every analytical figure by a
            separate model — raw data, metric recomputation and
            cross-platform consistency.
          </p>
        </div>
        {isTeam ? <AuditPanel key={auditRefreshKey} /> : <AuditViewerSummary />}
      </section>
    </div>
  )
}
