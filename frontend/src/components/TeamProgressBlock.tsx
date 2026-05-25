/**
 * TeamProgressBlock — shared UAT progress dashboard.
 *
 * Backs the Settings → Test Administration → "Team Progress" tab.
 * Surfaces every team member's attestation progress so Michael, Bob,
 * and Molly can see each other's UAT status without asking. Read-only:
 * no one can attest a step for another member through this surface
 * (the backend gates attestation on the caller's own email).
 *
 * SCOPE — covers UAT issue priority "shared visibility across team
 * members":
 *   1. Each member's checklist progress (passed / failed / pending /
 *      re-test / no-test counts)
 *   2. Real-time status — polls every 15s so a fresh check-off
 *      surfaces within one window
 *   3. Overall completion % per section AND per member
 *   4. Re-test / skipped items highlighted in amber
 *   5. Read-only — no action buttons render in this view
 *
 * Data: GET /api/v1/testing/team-progress, view_uat_status gated
 * (every team_member carries it post-PR #131).
 *
 * Permission boundary: this component never gates itself on a
 * permission — it's always rendered inside TestAdminSections, which
 * is itself gated on canAccessTestPanel. So a viewer never reaches
 * this code; a team_member or sysadmin always does.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { Loader2, AlertCircle, Activity, AlertTriangle } from 'lucide-react'
import { TEST_SCRIPTS, type TestScript } from '../constants/testScripts'

interface ScriptProgress {
  passed: string[]
  failed: string[]
  skipped: string[]
  retest: string[]
  last_attested_at: string | null
}

interface MemberProgress {
  email: string
  display_name: string
  scripts: Record<string, ScriptProgress>
  failure_count: number
  last_activity_at: string | null
  currently_testing: boolean
}

interface TeamProgressResponse {
  team_emails: string[]
  members: Record<string, MemberProgress>
}

// Maps a team email to the TestScript.assignedTo bucket the user is
// the primary owner of. The "all" script (all_testers_v1) applies to
// every member regardless.
const PRIMARY_SCRIPT_BUCKET: Record<string, TestScript['assignedTo']> = {
  'ruurdsm@queens.edu':  'michael',
  'thaob@queens.edu':    'bob',
  'murdockm@queens.edu': 'molly',
}

// Scripts that apply to a given member email — always the "all" script
// plus the member's primary bucket.
function scriptsForMember(email: string): TestScript[] {
  const bucket = PRIMARY_SCRIPT_BUCKET[email]
  return TEST_SCRIPTS.filter((s) =>
    s.assignedTo === 'all' || s.assignedTo === bucket,
  )
}

interface ProgressTotals {
  total: number
  passed: number
  failed: number
  retest: number
  skipped: number
  pending: number
  percent: number
}

function totalsForMember(member: MemberProgress): {
  overall: ProgressTotals
  perScript: { script: TestScript; totals: ProgressTotals }[]
} {
  const scripts = scriptsForMember(member.email)
  const perScript = scripts.map((script) => {
    const sp = member.scripts[script.id] || {
      passed: [], failed: [], skipped: [], retest: [], last_attested_at: null,
    }
    const total = script.steps.length
    const passed = sp.passed.length
    const failed = sp.failed.length
    const retest = sp.retest.length
    const skipped = sp.skipped.length
    // Pending = steps with no attestation row at all. A step appears
    // in only ONE of passed/failed/retest/skipped per the backend
    // classification, so pending is the inventory minus all four.
    const pending = Math.max(
      0,
      total - passed - failed - retest - skipped,
    )
    const percent = total > 0 ? Math.round((passed / total) * 100) : 0
    return {
      script,
      totals: { total, passed, failed, retest, skipped, pending, percent },
    }
  })
  const overall: ProgressTotals = perScript.reduce((acc, x) => ({
    total:   acc.total + x.totals.total,
    passed:  acc.passed + x.totals.passed,
    failed:  acc.failed + x.totals.failed,
    retest:  acc.retest + x.totals.retest,
    skipped: acc.skipped + x.totals.skipped,
    pending: acc.pending + x.totals.pending,
    percent: 0,
  }), {
    total: 0, passed: 0, failed: 0, retest: 0, skipped: 0, pending: 0,
    percent: 0,
  })
  overall.percent = overall.total > 0
    ? Math.round((overall.passed / overall.total) * 100) : 0
  return { overall, perScript }
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'never'
  const now = Date.now()
  const then = new Date(iso).getTime()
  if (!Number.isFinite(then)) return 'never'
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

const POLL_INTERVAL_MS = 15_000

export function TeamProgressBlock() {
  const [data, setData] = useState<TeamProgressResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)

  const load = useCallback(() => {
    axios.get<TeamProgressResponse>('/api/v1/testing/team-progress')
      .then((res) => {
        setData(res.data)
        setError(null)
        setLastFetched(new Date())
      })
      .catch(() => setError('Could not load team progress.'))
      .finally(() => setLoading(false))
  }, [])

  // Initial fetch + 15s polling. Polling continues for the lifetime
  // of the mounted component — closing the Settings page (or
  // unmounting the tab) cleans up via the effect's return.
  useEffect(() => {
    load()
    const id = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [load])

  // Combined-team totals across every member's scripts. Used for the
  // top-of-block summary card.
  const teamOverall = useMemo<ProgressTotals | null>(() => {
    if (!data) return null
    const init: ProgressTotals = {
      total: 0, passed: 0, failed: 0, retest: 0, skipped: 0, pending: 0,
      percent: 0,
    }
    const sum = Object.values(data.members).reduce((acc, m) => {
      const t = totalsForMember(m).overall
      return {
        total:   acc.total + t.total,
        passed:  acc.passed + t.passed,
        failed:  acc.failed + t.failed,
        retest:  acc.retest + t.retest,
        skipped: acc.skipped + t.skipped,
        pending: acc.pending + t.pending,
        percent: 0,
      }
    }, init)
    sum.percent = sum.total > 0 ? Math.round((sum.passed / sum.total) * 100) : 0
    return sum
  }, [data])

  if (loading) {
    return (
      <p className="text-xs text-muted flex items-center gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin" /> Loading team progress…
      </p>
    )
  }
  if (error || !data) {
    return (
      <div className="rounded border border-danger/30 bg-danger/5 p-3
                      text-xs text-danger flex items-center gap-2">
        <AlertCircle className="w-4 h-4" />
        {error || 'No team progress data.'}
      </div>
    )
  }

  // Sort members by their primary bucket — Michael (engineering),
  // Bob (analyst), Molly (presenter) — so the dashboard order
  // matches how the team thinks about itself.
  const ordered = ['ruurdsm@queens.edu', 'thaob@queens.edu',
                   'murdockm@queens.edu']
    .filter((em) => data.members[em])
    .map((em) => data.members[em])

  return (
    <div className="space-y-3" data-testid="team-progress-block">
      {/* Top-of-block summary card — combined % + count callouts. */}
      {teamOverall && (
        <div className="rounded border border-electric/30 bg-electric/5 p-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">
                Team UAT Progress
              </div>
              <div className="mt-0.5 flex items-baseline gap-2">
                <span className="text-2xl font-bold text-white font-mono">
                  {teamOverall.percent}%
                </span>
                <span className="text-2xs text-slate-300">
                  {teamOverall.passed} of {teamOverall.total} steps
                </span>
              </div>
            </div>
            <div className="text-2xs text-muted">
              <div>Failed (open): <span className="text-danger font-mono">{teamOverall.failed}</span></div>
              <div>Re-test:       <span className="text-warning font-mono">{teamOverall.retest}</span></div>
              <div>Pending:       <span className="text-slate-300 font-mono">{teamOverall.pending}</span></div>
            </div>
            <div className="text-2xs text-muted text-right">
              <div className="flex items-center gap-1">
                <Activity className="w-3 h-3" /> Updates every 15s
              </div>
              {lastFetched && (
                <div>Last fetched {relativeTime(lastFetched.toISOString())}</div>
              )}
            </div>
          </div>
          {(teamOverall.retest > 0 || teamOverall.skipped > 0) && (
            <div className="mt-2 text-2xs text-warning flex items-center gap-1.5">
              <AlertTriangle className="w-3 h-3" />
              {teamOverall.retest > 0 && (
                <span>{teamOverall.retest} step{teamOverall.retest === 1 ? '' : 's'} pending re-test</span>
              )}
              {teamOverall.retest > 0 && teamOverall.skipped > 0 && <span>·</span>}
              {teamOverall.skipped > 0 && (
                <span>{teamOverall.skipped} step{teamOverall.skipped === 1 ? '' : 's'} skipped (no-test)</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Per-member cards. Read-only — no buttons, no action affordances. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {ordered.map((m) => {
          const { overall, perScript } = totalsForMember(m)
          return (
            <div key={m.email}
                 className="rounded border border-border bg-navy-900 p-3"
                 data-testid={`member-card-${m.email}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-white truncate">
                    {m.display_name}
                  </div>
                  <div className="text-2xs text-muted truncate">{m.email}</div>
                </div>
                {m.currently_testing && (
                  <span className="text-2xs px-1.5 py-0.5 rounded
                                   bg-success/10 text-success border border-success/30
                                   flex items-center gap-1"
                        data-testid={`currently-testing-${m.email}`}>
                    <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
                    Testing now
                  </span>
                )}
              </div>
              <div className="mt-2">
                <div className="flex items-baseline gap-2">
                  <span className="text-xl font-bold text-white font-mono">
                    {overall.percent}%
                  </span>
                  <span className="text-2xs text-slate-300">
                    {overall.passed} / {overall.total} steps
                  </span>
                </div>
                {/* Progress bar — green up to the passed segment, then
                    amber for re-test, red for open failures, muted
                    for pending. */}
                <div className="mt-1 h-1.5 rounded bg-navy-800 overflow-hidden flex">
                  {overall.total > 0 && (
                    <>
                      <div className="bg-success" style={{
                        width: `${(overall.passed / overall.total) * 100}%`,
                      }} />
                      <div className="bg-warning" style={{
                        width: `${(overall.retest / overall.total) * 100}%`,
                      }} />
                      <div className="bg-danger" style={{
                        width: `${(overall.failed / overall.total) * 100}%`,
                      }} />
                      <div className="bg-slate-600/40" style={{
                        width: `${(overall.skipped / overall.total) * 100}%`,
                      }} />
                    </>
                  )}
                </div>
              </div>
              {/* Per-script breakdown — Section 1 (All Testers) plus
                  the member's primary section. */}
              <div className="mt-2 space-y-1">
                {perScript.map(({ script, totals }) => (
                  <div key={script.id}
                       className="text-2xs flex items-center justify-between gap-2">
                    <div className="text-slate-300 truncate">
                      {script.title}
                    </div>
                    <div className="font-mono text-muted">
                      <span className="text-white">{totals.percent}%</span>
                      <span className="text-muted"> · </span>
                      <span className="text-success">{totals.passed}</span>
                      <span className="text-muted">/</span>
                      <span>{totals.total}</span>
                      {totals.retest > 0 && (
                        <span className="text-warning ml-1.5"
                              title="Pending re-test">
                          ↻{totals.retest}
                        </span>
                      )}
                      {totals.failed > 0 && (
                        <span className="text-danger ml-1.5"
                              title="Open failures">
                          ✗{totals.failed}
                        </span>
                      )}
                      {totals.skipped > 0 && (
                        <span className="text-warning ml-1.5"
                              title="Skipped (no-test)">
                          ⊘{totals.skipped}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 text-2xs text-muted flex items-center justify-between gap-2">
                <div>
                  Failures filed: <span className="font-mono text-slate-300">
                    {m.failure_count}
                  </span>
                </div>
                <div>
                  Last attestation: <span className="text-slate-300">
                    {relativeTime(m.last_activity_at)}
                  </span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
