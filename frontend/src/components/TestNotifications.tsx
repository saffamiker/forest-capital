/**
 * TestNotifications — operational login notifications for the guided UAT
 * test runner. Mounted in MainLayout; shown once after login.
 *
 * Three notification types, deliberately separate from the changelog
 * What's New modal (those are informational; these are operational):
 *
 *   🧪 New tests available  — un-attested test steps exist.
 *   ✅ Failure resolved      — a failure you reported was resolved; re-test.
 *   💬 Feedback responded    — your feedback was reviewed.
 *   🔍 Triage report ready   — a new triage report (sysadmin only).
 *   ⚠️ Audit found issues    — a statistical audit flagged discrepancies
 *                              (sysadmin only).
 *
 * Notifications are derived server-side (no notifications table) — see
 * GET /api/v1/testing/notifications and /unseen. They are dismissible
 * per session (sessionStorage); a failure-resolved notification also
 * self-clears once the step is re-attested.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  FlaskConical, CheckCircle, MessageSquare, Search, ShieldAlert, X,
} from 'lucide-react'
import { TEST_SCRIPTS, scriptForEmail, getTestScript } from '../constants/testScripts'
import { startTestRun } from '../lib/testRunnerBus'
import { useSession } from '../context/SessionContext'
import { useAuth } from '../App'

const DISMISS_KEY = 'fc_test_notif_dismissed'

interface UnseenResponse {
  scripts: Record<string, { attested_step_ids: string[] }>
}
interface NotificationsResponse {
  resolved_failures: Array<{
    script_id: string; step_id: string; resolution_note: string | null
  }>
  responded_feedback: Array<{
    id: number; title: string; status: string; resolution_note: string | null
  }>
}
interface TriageLatestResponse {
  report: {
    id: number
    triggered_at: string | null
    items_assessed: number
    github_issues_created: number
    status: string
    metadata: { immediate_count?: number }
  } | null
}
interface AuditLatestResponse {
  run: {
    id: number
    triggered_at: string | null
    status: string
    failed: number
  } | null
}

interface Notice {
  key: string
  kind: 'new_tests' | 'failure_resolved' | 'feedback_responded'
    | 'triage_ready' | 'audit_failed'
  title: string
  body: string
  actionLabel: string
  onAction: () => void
}

function readDismissed(): string[] {
  try {
    const raw = sessionStorage.getItem(DISMISS_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

export default function TestNotifications() {
  const navigate = useNavigate()
  const { setTestingMode } = useSession()
  const { session } = useAuth()
  const email = session?.email ?? ''

  const [notices, setNotices] = useState<Notice[] | null>(null)
  const [index, setIndex] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [unseenRes, notifRes] = await Promise.all([
          axios.get<UnseenResponse>('/api/v1/testing/unseen'),
          axios.get<NotificationsResponse>('/api/v1/testing/notifications'),
        ])
        if (cancelled) return

        const built: Notice[] = []

        // ── New tests available — diff testScripts against attested ────
        const relevant = [TEST_SCRIPTS[0]]
        const mine = scriptForEmail(email)
        if (mine) relevant.push(mine)
        const attested = unseenRes.data.scripts ?? {}
        let newSteps = 0
        for (const sc of relevant) {
          const done = new Set(attested[sc.id]?.attested_step_ids ?? [])
          newSteps += sc.steps.filter((st) => !done.has(st.id)).length
        }
        if (newSteps > 0) {
          built.push({
            key: 'new_tests',
            kind: 'new_tests',
            title: '🧪 New test cases available',
            body: `There ${newSteps === 1 ? 'is' : 'are'} ${newSteps} test `
              + `step${newSteps === 1 ? '' : 's'} that need your attestation.`,
            actionLabel: 'Run Tests Now',
            onAction: () => { setTestingMode(true); startTestRun() },
          })
        }

        // ── Failure resolved ───────────────────────────────────────────
        for (const f of notifRes.data.resolved_failures ?? []) {
          const title = getTestScript(f.script_id)?.steps
            .find((s) => s.id === f.step_id)?.title ?? f.step_id
          built.push({
            key: `failure:${f.script_id}:${f.step_id}`,
            kind: 'failure_resolved',
            title: '✅ A test failure you reported has been resolved',
            body: `"${title}" has been marked resolved. Please re-run this `
              + `step.${f.resolution_note ? ` — ${f.resolution_note}` : ''}`,
            actionLabel: 'Re-test Now',
            onAction: () => {
              setTestingMode(true)
              startTestRun({ scriptId: f.script_id, stepId: f.step_id })
            },
          })
        }

        // ── Feedback responded ─────────────────────────────────────────
        for (const fb of notifRes.data.responded_feedback ?? []) {
          built.push({
            key: `feedback:${fb.id}:${fb.status}`,
            kind: 'feedback_responded',
            title: '💬 Your feedback has been reviewed',
            body: `"${fb.title}" — ${fb.status}`
              + (fb.resolution_note ? `. ${fb.resolution_note}` : ''),
            actionLabel: 'View in Settings',
            onAction: () => navigate('/settings#test-results'),
          })
        }

        // ── Triage report ready — sysadmin only ────────────────────────
        // Its own request: /triage/latest is sysadmin-gated, so a 403 for
        // a non-sysadmin must not abort the other notifications above.
        try {
          const triage = await axios.get<TriageLatestResponse>(
            '/api/v1/testing/triage/latest')
          const rep = triage.data.report
          if (rep && rep.status !== 'running' && rep.triggered_at) {
            const ageH = (Date.now() - new Date(rep.triggered_at).getTime())
              / 3600000
            if (ageH < 24) {
              built.push({
                key: `triage:${rep.id}`,
                kind: 'triage_ready',
                title: '🔍 Triage report ready',
                body: `${rep.items_assessed} item`
                  + `${rep.items_assessed === 1 ? '' : 's'} assessed · `
                  + `${rep.metadata.immediate_count ?? 0} immediate action`
                  + `${(rep.metadata.immediate_count ?? 0) === 1 ? '' : 's'} · `
                  + `${rep.github_issues_created} GitHub issue`
                  + `${rep.github_issues_created === 1 ? '' : 's'} created.`,
                actionLabel: 'View Report',
                onAction: () => navigate('/settings#test-administration'),
              })
            }
          }
        } catch { /* non-sysadmin (403) or no report — no triage notice */ }

        // ── Audit found discrepancies — sysadmin only ──────────────────
        // Its own request, like the triage notice — a 403 for a
        // non-sysadmin must not abort the notices already built.
        try {
          const audit = await axios.get<AuditLatestResponse>(
            '/api/v1/audit/runs/latest')
          const run = audit.data.run
          if (run && run.status !== 'running' && run.failed > 0
              && run.triggered_at) {
            const ageH = (Date.now() - new Date(run.triggered_at).getTime())
              / 3600000
            if (ageH < 24) {
              built.push({
                key: `audit:${run.id}`,
                kind: 'audit_failed',
                title: '⚠️ Statistical audit found discrepancies',
                body: `The latest audit flagged ${run.failed} `
                  + `discrepanc${run.failed === 1 ? 'y' : 'ies'} requiring `
                  + 'attention. Review the audit findings before presenting.',
                actionLabel: 'View Audit Report',
                onAction: () => navigate('/settings#audit'),
              })
            }
          }
        } catch { /* non-sysadmin (403) or no run — no audit notice */ }

        const dismissed = new Set(readDismissed())
        setNotices(built.filter((n) => !dismissed.has(n.key)))
      } catch {
        if (!cancelled) setNotices([])
      }
    }
    void load()
    return () => { cancelled = true }
  }, [email, navigate, setTestingMode])

  const current = useMemo(
    () => (notices && index < notices.length ? notices[index] : null),
    [notices, index])

  if (!current) return null

  const dismiss = () => {
    try {
      sessionStorage.setItem(DISMISS_KEY,
        JSON.stringify([...readDismissed(), current.key]))
    } catch { /* sessionStorage best-effort */ }
    setIndex((i) => i + 1)
  }

  const Icon = current.kind === 'new_tests' ? FlaskConical
    : current.kind === 'failure_resolved' ? CheckCircle
      : current.kind === 'triage_ready' ? Search
        : current.kind === 'audit_failed' ? ShieldAlert : MessageSquare
  const accent = current.kind === 'failure_resolved'
    ? 'text-success'
    : current.kind === 'audit_failed' ? 'text-warning' : 'text-electric'

  return (
    <div className="fixed inset-0 z-[82] flex items-center justify-center
                    bg-black/50 p-4" role="presentation" onClick={dismiss}>
      <div role="dialog" aria-label={current.title}
           onClick={(e) => e.stopPropagation()}
           className="w-full max-w-md rounded-lg border border-border
                      bg-navy-800 shadow-2xl">
        <div className="flex items-start justify-between gap-3 px-5 py-4
                        border-b border-border">
          <div className="flex items-center gap-2">
            <Icon className={`w-4 h-4 ${accent}`} />
            <h2 className="text-white font-semibold text-sm">{current.title}</h2>
          </div>
          <button type="button" onClick={dismiss} aria-label="Dismiss"
                  className="text-muted hover:text-white shrink-0">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-5 py-4">
          <p className="text-xs text-slate-300 leading-relaxed">{current.body}</p>
          {notices && notices.length > 1 && (
            <p className="text-2xs text-muted mt-2">
              {index + 1} of {notices.length}
            </p>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3
                        border-t border-border">
          <button type="button" onClick={dismiss}
            className="px-3 py-1.5 text-xs text-muted hover:text-white">
            Later
          </button>
          <button type="button"
            onClick={() => { current.onAction(); dismiss() }}
            className="px-4 py-1.5 rounded text-xs font-medium bg-electric/15
                       text-electric border border-electric/30
                       hover:bg-electric/25 transition-colors">
            {current.actionLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
