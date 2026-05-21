/**
 * TestNotifications — operational login notifications for the guided UAT
 * test runner. Mounted in MainLayout; shown once after login.
 *
 * Three notification types, deliberately separate from the changelog
 * What's New modal (those are informational; these are operational):
 *
 *   🧪 New tests available  — un-attested test steps exist.
 *   ✅ Failure resolved      — a failure you reported was resolved; re-test.
 *   🔁 Fix ready             — a triage item you sourced was resolved and
 *                              flagged requires_retest=true.
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
  CalendarClock, RotateCw,
} from 'lucide-react'
import { TEST_SCRIPTS, scriptForEmail, getTestScript } from '../constants/testScripts'
import { startTestRun } from '../lib/testRunnerBus'
import { useSession } from '../context/SessionContext'
import { useAuth } from '../App'
import { SUBMISSION_DEADLINES, deadlineCountdown } from './SubmissionGuides'

const DISMISS_KEY = 'fc_test_notif_dismissed'

interface UnseenResponse {
  scripts: Record<string, { attested_step_ids: string[] }>
}
interface NotificationsResponse {
  resolved_failures: Array<{
    script_id: string; step_id: string; resolution_note: string | null
    // Migration 025 — resolution-gate metadata. resolution_type
    // drives the three-variant card below. Legacy rows resolved
    // before the migration carry null; the renderer falls back to
    // the original "please re-run this step" wording in that case.
    resolution_type?: 'no_bug_detected' | 'code_fix_deployed' | 'wont_fix' | null
    fix_reference?: string | null
    remediation_note?: string | null
  }>
  responded_feedback: Array<{
    id: number; title: string; status: string; resolution_note: string | null
  }>
  retest_requested?: Array<{
    item_id: number
    item_title: string
    resolution_note: string | null
    fix_commit: string | null
    retest_requested_at: string | null
    source_item_type: 'failure' | 'feedback'
    source_item_id: number
    script_id: string | null
    step_id: string | null
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
    | 'retest_requested' | 'triage_ready' | 'audit_failed' | 'deadline'
  title: string
  /** String for the legacy bare-text notices; ReactNode for the
   *  structured failure_resolved card (Migration 025) which renders
   *  badge + root cause + fix reference + remediation block. */
  body: string | import('react').ReactNode
  /** Optional — omitted for the wont_fix variant of failure_resolved.
   *  The Won't fix card is informational only with no CTA. */
  actionLabel?: string
  onAction?: () => void
  /** Overrides the kind-based accent — used by the deadline notice to
   *  match the guide panel's amber/red urgency colours. */
  accentClass?: string
}


// Resolution-type vocabulary — kept in sync with the TestRunnerSettings
// RESOLUTION_TYPE_LABEL map (one source of truth would mean a circular
// import, so this is intentional duplication with a regression test
// pinning the two in sync).
const RESOLUTION_TYPE_LABEL: Record<string, string> = {
  no_bug_detected:    'No bug detected',
  code_fix_deployed:  'Code fix deployed',
  wont_fix:           "Won't fix",
}


function ResolutionBadge({ type }: { type: string }) {
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


// Render a SHA / #PR / GH URL as a clickable link inline. Mirrors the
// FixReferenceLink in TestRunnerSettings; duplicated here to avoid a
// cross-component import cycle.
function FixReferenceLink({ reference }: { reference: string }) {
  const r = reference.trim()
  let href: string | null = null
  let label = r
  if (/^[0-9a-fA-F]{7,40}$/.test(r)) {
    href = `https://github.com/saffamiker/forest-capital/commit/${r}`
    label = r.slice(0, 8)
  } else if (/^#\d{1,6}$/.test(r)) {
    href = `https://github.com/saffamiker/forest-capital/pull/${r.slice(1)}`
  } else if (/^https?:\/\/(?:www\.)?github\.com\//.test(r)) {
    href = r
  }
  if (!href) return <span className="text-slate-200 font-mono">{r}</span>
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className="text-electric hover:underline font-mono">
      {label}
    </a>
  )
}


// Three-variant card body for a resolved failure. Mirrors PART 3 of
// the resolution-gate spec — root cause is universal; what-changed +
// fix-reference appear only for code_fix_deployed; wont_fix carries
// no CTA (the parent Notice omits actionLabel for that variant).
function ResolvedFailureBody({
  stepName, rootCause, resolutionType, fixReference, remediation,
}: {
  stepName: string
  rootCause: string | null
  resolutionType: string | null
  fixReference: string | null
  remediation: string | null
}) {
  const type = resolutionType ?? ''
  return (
    <div className="space-y-2 text-2xs text-slate-200">
      <div className="flex items-start gap-2 flex-wrap">
        <span className="text-muted">Step: </span>
        <span className="text-slate-100">"{stepName}"</span>
        {type && <ResolutionBadge type={type} />}
      </div>
      {rootCause && (
        <div>
          <span className="text-muted">Root cause: </span>
          <span>{rootCause}</span>
        </div>
      )}
      {type === 'code_fix_deployed' && remediation && (
        <div>
          <span className="text-muted">What changed: </span>
          <span>{remediation}</span>
        </div>
      )}
      {type === 'code_fix_deployed' && fixReference && (
        <div>
          <span className="text-muted">Fix reference: </span>
          <FixReferenceLink reference={fixReference} />
        </div>
      )}
      {type === 'no_bug_detected' && (
        <p className="text-muted italic">
          No code change was required. Please re-run this step — the
          expected behaviour is described in the test guide.
        </p>
      )}
      {type === 'wont_fix' && (
        <p className="text-muted italic">
          No re-test is required. This step has been marked closed.
        </p>
      )}
    </div>
  )
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

        // ── Failure resolved (Migration 025 — three variants) ──────────
        // No bug detected / Code fix deployed → re-test CTA. Won't fix →
        // informational only, no CTA, step stays at its current attested
        // state. A legacy row with resolution_type == null falls back to
        // the original "please re-run this step" UX.
        for (const f of notifRes.data.resolved_failures ?? []) {
          const stepName = getTestScript(f.script_id)?.steps
            .find((s) => s.id === f.step_id)?.title ?? f.step_id
          const rtype = f.resolution_type ?? null
          const isWontFix = rtype === 'wont_fix'
          const titlePrefix = isWontFix
            ? '🔒 A failure you reported has been closed'
            : rtype === 'no_bug_detected'
              ? '✅ A failure you reported was not a bug'
              : rtype === 'code_fix_deployed'
                ? '✅ A failure you reported has been fixed'
                : '✅ A test failure you reported has been resolved'
          built.push({
            key: `failure:${f.script_id}:${f.step_id}`,
            kind: 'failure_resolved',
            title: titlePrefix,
            body: (
              <ResolvedFailureBody
                stepName={stepName}
                rootCause={f.resolution_note ?? null}
                resolutionType={rtype}
                fixReference={f.fix_reference ?? null}
                remediation={f.remediation_note ?? null}
              />
            ),
            // wont_fix: no CTA — leaving actionLabel undefined keeps
            // the renderer from showing the action button.
            ...(isWontFix ? {} : {
              actionLabel: 'Re-test This Step',
              onAction: () => {
                setTestingMode(true)
                startTestRun({ scriptId: f.script_id, stepId: f.step_id })
              },
            }),
          })
        }

        // ── Fix ready (triage item resolved with requires_retest=true) ─
        // Sourced from a failure → deep-link the tester into the test
        // runner at that step (same UX as failure_resolved). Sourced
        // from feedback → send them to the Test Results settings view,
        // where the feedback thread + resolution note are visible.
        for (const r of notifRes.data.retest_requested ?? []) {
          const noteSuffix = r.resolution_note ? ` — ${r.resolution_note}` : ''
          const fromFailure = r.source_item_type === 'failure'
            && r.script_id && r.step_id
          built.push({
            key: `retest:${r.item_id}`,
            kind: 'retest_requested',
            title: '🔁 Fix ready — please retest',
            body: `"${r.item_title}" has been resolved and is awaiting `
              + `your verification.${noteSuffix}`,
            actionLabel: fromFailure ? 'Re-test Now' : 'View in Settings',
            onAction: () => {
              if (fromFailure) {
                setTestingMode(true)
                startTestRun({
                  scriptId: r.script_id as string,
                  stepId: r.step_id as string,
                })
              } else {
                navigate('/settings#test-results')
              }
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

        // ── Deliverable deadline countdown — Bob / Molly only.
        //    Bob has TWO deadlines (May 27 midpoint paper + July 1
        //    executive brief); SUBMISSION_DEADLINES is therefore flat
        //    with one entry per deliverable. The notification surfaces
        //    the nearest UNPASSED deadline per owner so a Bob who has
        //    submitted the midpoint paper next sees the exec-brief
        //    countdown, not a stuck "deadline passed" notice.
        const myDeadlines = SUBMISSION_DEADLINES
          .filter((d) => d.ownerEmail === email)
          .map((d) => ({ d, cd: deadlineCountdown(d.deadline, d.noun) }))
          .filter((x) => x.cd.tone !== 'passed')
          .sort((a, b) => a.d.deadline.localeCompare(b.d.deadline))
        const nearest = myDeadlines.length > 0 ? myDeadlines[0]! : null
        if (nearest) {
          built.push({
            key: `deadline:${nearest.d.deadline}`,
            kind: 'deadline',
            title: '📋 Deliverable deadline',
            body: `${nearest.d.label}: ${nearest.cd.label.toLowerCase()}. `
              + 'Open the Submission Guide on the Reports screen for the '
              + 'step-by-step workflow.',
            actionLabel: 'Open Reports',
            onAction: () => navigate('/reports'),
            accentClass: nearest.cd.tone === 'red' ? 'text-danger'
              : nearest.cd.tone === 'amber' ? 'text-warning' : 'text-electric',
          })
        }

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
      : current.kind === 'retest_requested' ? RotateCw
        : current.kind === 'triage_ready' ? Search
          : current.kind === 'audit_failed' ? ShieldAlert
            : current.kind === 'deadline' ? CalendarClock : MessageSquare
  const accent = current.accentClass
    ?? (current.kind === 'failure_resolved' ? 'text-success'
      : current.kind === 'retest_requested' ? 'text-success'
        : current.kind === 'audit_failed' ? 'text-warning' : 'text-electric')

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
          {typeof current.body === 'string'
            ? (
              <p className="text-xs text-slate-300 leading-relaxed">
                {current.body}
              </p>
            )
            : (
              <div className="text-xs text-slate-300 leading-relaxed">
                {current.body}
              </div>
            )}
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
            {current.actionLabel ? 'Later' : 'Close'}
          </button>
          {current.actionLabel && current.onAction && (
            <button type="button"
              onClick={() => { current.onAction!(); dismiss() }}
              className="px-4 py-1.5 rounded text-xs font-medium bg-electric/15
                         text-electric border border-electric/30
                         hover:bg-electric/25 transition-colors">
              {current.actionLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
