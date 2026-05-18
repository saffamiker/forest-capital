/**
 * QAHub — the QA tab, restructured as a two-section quality-assurance hub:
 *   1. Methodology Review — the QA agent's checklist. Every authenticated
 *      user sees it (no permission change from the old QA tab).
 *   2. Statistical Audit — independent re-verification of every analytical
 *      figure by a separate model. The full findings panel is project-team
 *      only; viewers see a read-only summary of the latest audit run.
 *
 * The Statistical Audit was relocated here from Settings — the QA tab is
 * the single home for both audit types.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import { ShieldCheck, Loader2 } from 'lucide-react'
import QAAuditPanel from '../components/QAAuditPanel'
import AuditPanel from '../components/AuditPanel'
import { useIsTeamMember } from '../hooks/usePermissions'

interface LatestRun {
  status: string
  triggered_at: string | null
  total_checks: number
  passed: number
  failed: number
  warnings: number
}

function auditVerdict(r: LatestRun): { label: string; cls: string } {
  if (r.failed > 0) return { label: 'FAIL', cls: 'text-danger' }
  if (r.warnings > 0) return { label: 'WARN', cls: 'text-warning' }
  return { label: 'PASS', cls: 'text-success' }
}

/**
 * Read-only audit summary shown to non-team viewers — the latest run's
 * verdict and counts only, never the findings or the run controls.
 */
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
  const v = auditVerdict(run)
  const date = run.triggered_at
    ? new Date(run.triggered_at).toLocaleDateString() : 'unknown date'
  return (
    <div className="rounded border border-border bg-navy-800 p-3">
      <div className="flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-muted shrink-0" />
        <span className="text-sm text-white">
          Last audit:{' '}
          <span className={`font-semibold ${v.cls}`}>{v.label}</span>
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

export default function QAHub() {
  const isTeam = useIsTeamMember()
  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-white">Quality Assurance</h1>
        <p className="text-sm text-muted mt-1">
          Methodology review and independent statistical audit — the two
          ways every analytical result on this platform is verified.
        </p>
      </div>

      {/* Section 1 — Methodology Review (every authenticated user). */}
      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-white">Methodology Review</h2>
          <p className="text-xs text-muted mt-0.5">
            The QA agent's methodology checklist — backtesting assumptions,
            statistical integrity, cross-validation and presentation rigour.
          </p>
        </div>
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
        {isTeam ? <AuditPanel /> : <AuditViewerSummary />}
      </section>
    </div>
  )
}
