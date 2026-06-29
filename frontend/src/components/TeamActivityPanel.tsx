/**
 * TeamActivityPanel — the Team Activity section of the Reports view.
 *
 * The objective record of how the practicum team engaged with the
 * platform: a per-member summary, then a unified timeline interleaving
 * commits, council runs, academic reviews, QA audits, document uploads
 * and page views. Filterable by member, activity type, date range and
 * session band; every visible record exports to CSV.
 *
 * Analytical sessions only by default — Testing Mode activity is opt-in
 * via the "Include testing activity" toggle.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  GitCommit, Users, GraduationCap, ShieldCheck, FileText, Eye,
  Loader2, AlertCircle, RefreshCw, ClipboardCheck, Bug, CheckCircle2,
  Lightbulb, DollarSign,
} from 'lucide-react'
import type {
  ActivityEvent, ActivityKind, ActivitySummary, CostSummary,
  TeamActivityResponse,
} from '../types/activity'
import TableExportButton from './TableExportButton'
import TeamActivityCharts from './TeamActivityCharts'

const PAGE_LIMIT = 100

// Every kind except page_view shows by default — page views are the
// high-volume, low-signal rows, opt-in via the filter.
const DEFAULT_KINDS: ActivityKind[] = [
  'commit', 'council', 'academic_review', 'qa', 'document_upload',
  'test_pass', 'test_failure', 'test_failure_resolved', 'test_feedback',
]
const ALL_KINDS: ActivityKind[] = [...DEFAULT_KINDS, 'page_view']

const KIND_META: Record<ActivityKind, { label: string; icon: typeof GitCommit; color: string }> = {
  commit:           { label: 'Development Entry', icon: GitCommit,   color: '#6366f1' },
  council:          { label: 'Council',         icon: Users,         color: '#3b82f6' },
  academic_review:  { label: 'Academic Review', icon: GraduationCap, color: '#f59e0b' },
  qa:               { label: 'QA Audit',        icon: ShieldCheck,   color: '#be123c' },
  document_upload:  { label: 'Upload',          icon: FileText,      color: '#0d9488' },
  page_view:        { label: 'Page View',       icon: Eye,           color: '#64748b' },
  test_pass:             { label: 'Test Attestations', icon: ClipboardCheck, color: '#10b981' },
  test_failure:          { label: 'Test Failure',      icon: Bug,            color: '#ef4444' },
  test_failure_resolved: { label: 'Test Resolved',     icon: CheckCircle2,   color: '#10b981' },
  test_feedback:         { label: 'Test Feedback',     icon: Lightbulb,      color: '#3b82f6' },
}

// Council agent id → short label for the timeline / "most active agents".
const AGENT_LABELS: Record<string, string> = {
  equity_analyst: 'Equity Analyst',
  fixed_income_analyst: 'Fixed Income Analyst',
  risk_manager: 'Risk Manager',
  quant_backtester: 'Quant Backtester',
  independent_analyst: 'Independent (Gemini)',
  contrarian_analyst: 'Contrarian (Grok)',
  cio: 'CIO',
  academic_advisor: 'Academic Advisor',
}
const agentLabel = (id: string): string => AGENT_LABELS[id] ?? id

const RATING_STYLE: Record<string, string> = {
  Strong: 'bg-success/15 text-success border-success/30',
  Developing: 'bg-warning/15 text-warning border-warning/30',
  'Needs Work': 'bg-danger/15 text-danger border-danger/30',
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      })
}

function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds}s`
  return `${Math.round(seconds / 60)}m`
}

// AI spend is fractions of a cent per call — four decimal places keeps a
// single council run legible rather than rounding it to $0.00.
function fmtCost(usd: number | null | undefined): string {
  if (usd == null) return '$0.0000'
  return `$${usd.toFixed(4)}`
}

function fmtTokens(n: number | null | undefined): string {
  if (!n) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

// agent_interactions.interaction_type → a readable label for the
// cost-by-type breakdown.
const TYPE_LABELS: Record<string, string> = {
  council: 'Council',
  academic_review: 'Academic Review',
  qa: 'QA Audit',
  document_upload: 'Document Upload',
  explain: 'Metric Explainer',
  explain_data: 'Data Explainer',
  export: 'Export',
  test_quality_eval: 'Test Quality Check',
}
const typeLabel = (t: string): string => TYPE_LABELS[t] ?? t

// ── Cost panel ────────────────────────────────────────────────────────────────

function CostPanel({ cost }: { cost: CostSummary }) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <DollarSign className="w-3.5 h-3.5 text-success" />
        <span className="text-2xs text-muted uppercase tracking-wide">
          AI token spend{cost.analytical_sessions_only
            ? ' · analytical sessions' : ' · all sessions'}
        </span>
      </div>

      {/* Grand total */}
      <div className="grid grid-cols-3 gap-3 mb-3">
        <div>
          <div className="font-mono text-success text-2xl leading-none">
            {fmtCost(cost.total_cost_usd)}
          </div>
          <div className="text-2xs text-muted mt-1">estimated spend</div>
        </div>
        <div>
          <div className="font-mono text-white text-2xl leading-none">
            {fmtTokens(cost.total_input_tokens)}
          </div>
          <div className="text-2xs text-muted mt-1">input tokens</div>
        </div>
        <div>
          <div className="font-mono text-white text-2xl leading-none">
            {fmtTokens(cost.total_output_tokens)}
          </div>
          <div className="text-2xs text-muted mt-1">output tokens</div>
        </div>
      </div>

      {cost.total_interactions === 0 ? (
        <p className="text-xs text-muted italic">
          No AI spend recorded yet — cost tracking began with the
          token-logging release; earlier interactions carry no cost.
        </p>
      ) : cost.total_cost_usd === 0 ? (
        // Historical-only case: interactions exist but every row's
        // estimated_cost_usd is NULL because the rows landed before
        // migration 020 added the token columns. Without this branch
        // the panel showed "$0.0000" with a populated by_type table,
        // which read as a live-zero spend (misleading). The empty-
        // state copy clarifies this is a pre-fix historical tail.
        // UAT FIX A.
        <p className="text-xs text-muted italic">
          Cost tracking began May 2026. Earlier interactions carry
          no cost data.
        </p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {/* By interaction type */}
          <div>
            <div className="text-2xs text-muted uppercase tracking-wide mb-1.5">
              By interaction
            </div>
            <div className="space-y-1">
              {cost.by_type.map((r) => (
                <div key={r.interaction_type}
                     className="flex items-center justify-between text-xs">
                  <span className="text-slate-300">
                    {typeLabel(r.interaction_type ?? '')}
                    <span className="text-2xs text-muted"> · {r.interactions}</span>
                  </span>
                  <span className="font-mono text-success">{fmtCost(r.cost_usd)}</span>
                </div>
              ))}
            </div>
          </div>
          {/* By member */}
          <div>
            <div className="text-2xs text-muted uppercase tracking-wide mb-1.5">
              By team member
            </div>
            <div className="space-y-1">
              {cost.by_member.map((r) => (
                <div key={r.user}
                     className="flex items-center justify-between text-xs">
                  <span className="text-slate-300 truncate">{r.user_name}</span>
                  <span className="font-mono text-success">{fmtCost(r.cost_usd)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Summary panel ─────────────────────────────────────────────────────────────

function SummaryPanel({ summary }: { summary: ActivitySummary }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      {/* Per-member interaction counts */}
      <div className="card p-4 lg:col-span-2">
        <div className="text-2xs text-muted uppercase tracking-wide mb-2">
          Per-member engagement{summary.analytical_sessions_only
            ? ' · analytical sessions' : ' · all sessions'}
        </div>
        {summary.per_member.length === 0 ? (
          <p className="text-xs text-muted italic">No team activity recorded yet.</p>
        ) : (
          <div className="space-y-2">
            {summary.per_member.map((m) => {
              const interactions = m.council_interactions
                + m.academic_review_sessions + m.document_uploads + m.qa_audits
              return (
                <div key={m.user} className="flex items-center justify-between gap-3
                                              border-b border-border/50 pb-2 last:border-0">
                  <div className="min-w-0">
                    <div className="text-sm text-white truncate">{m.user_name}</div>
                    <div className="text-2xs text-muted">
                      {m.council_interactions} council · {m.academic_review_sessions} review
                      {' '}· {m.document_uploads} upload · {m.qa_audits} QA
                      {' '}· {m.page_views} views
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="font-mono text-electric text-lg leading-none">
                      {interactions}
                    </div>
                    <div className="text-2xs text-muted mt-0.5">interactions</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Commits + agents + last review */}
      <div className="space-y-3">
        <div className="card p-4">
          <div className="text-2xs text-muted uppercase tracking-wide">Development Entries this week</div>
          <div className="font-mono text-electric text-2xl mt-1">
            {summary.commits.this_week}
          </div>
          <div className="text-2xs text-muted mt-0.5">
            {summary.commits.total} total recorded
          </div>
        </div>
        {summary.platform_releases != null && (
          <div className="card p-4">
            <div className="text-2xs text-muted uppercase tracking-wide">Platform Releases</div>
            <div className="font-mono text-electric text-2xl mt-1">
              {summary.platform_releases}
            </div>
            <div className="text-2xs text-muted mt-0.5">
              merged to the live platform
            </div>
          </div>
        )}
        <div className="card p-4">
          <div className="text-2xs text-muted uppercase tracking-wide mb-1.5">
            Most active agents
          </div>
          {summary.most_active_agents.length === 0 ? (
            <p className="text-2xs text-muted italic">No agent activity yet.</p>
          ) : (
            <div className="space-y-1">
              {summary.most_active_agents.map((a) => (
                <div key={a.agent} className="flex items-center justify-between text-xs">
                  <span className="text-slate-300">{agentLabel(a.agent)}</span>
                  <span className="font-mono text-muted">{a.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card p-4">
          <div className="text-2xs text-muted uppercase tracking-wide mb-1">
            Last academic review
          </div>
          {summary.last_academic_review ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-slate-300">
                {fmtTime(summary.last_academic_review.timestamp)}
              </span>
              {summary.last_academic_review.overall_rating && (
                <span className={`text-2xs px-2 py-0.5 rounded-full border ${
                  RATING_STYLE[summary.last_academic_review.overall_rating]
                  ?? 'bg-navy-700 text-muted border-border'
                }`}>
                  {summary.last_academic_review.overall_rating}
                </span>
              )}
            </div>
          ) : (
            <p className="text-2xs text-muted italic">No review run yet.</p>
          )}
          {summary.test_coverage && (
            <p className="text-2xs text-muted mt-2 pt-2 border-t border-border/50">
              Test coverage: {summary.test_coverage.steps_attested} step
              {summary.test_coverage.steps_attested === 1 ? '' : 's'} attested
              {' '}across {summary.test_coverage.testers} team member
              {summary.test_coverage.testers === 1 ? '' : 's'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Filter bar ────────────────────────────────────────────────────────────────

interface Filters {
  userId: string
  kinds: ActivityKind[]
  dateFrom: string
  dateTo: string
  includeTesting: boolean
}

function FilterBar({
  filters, members, onChange,
}: {
  filters: Filters
  members: { user: string; user_name: string }[]
  onChange: (f: Filters) => void
}) {
  const toggleKind = (k: ActivityKind) => {
    const next = filters.kinds.includes(k)
      ? filters.kinds.filter((x) => x !== k)
      : [...filters.kinds, k]
    onChange({ ...filters, kinds: next })
  }
  return (
    <div className="card p-3 flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-3">
      {/* Team member */}
      <select
        value={filters.userId}
        onChange={(e) => onChange({ ...filters, userId: e.target.value })}
        aria-label="Filter by team member"
        className="w-full sm:w-auto bg-navy-800 border border-border rounded text-xs text-white px-2 py-1.5"
      >
        <option value="">All members</option>
        {members.map((m) => (
          <option key={m.user} value={m.user}>{m.user_name}</option>
        ))}
      </select>

      {/* Activity type multiselect — chips */}
      <div className="flex items-center gap-1 flex-wrap">
        {ALL_KINDS.map((k) => {
          const on = filters.kinds.includes(k)
          return (
            <button
              key={k}
              type="button"
              onClick={() => toggleKind(k)}
              className={`text-2xs px-2 py-1 rounded border transition-colors ${
                on
                  ? 'border-electric/40 bg-electric/10 text-electric'
                  : 'border-border text-muted hover:text-white'
              }`}
            >
              {KIND_META[k].label}
            </button>
          )
        })}
      </div>

      {/* Date range — full-width on mobile, the two inputs sharing the row */}
      <div className="flex items-center gap-1 text-2xs text-muted w-full sm:w-auto">
        <input
          type="date"
          value={filters.dateFrom}
          onChange={(e) => onChange({ ...filters, dateFrom: e.target.value })}
          aria-label="From date"
          className="flex-1 sm:flex-none bg-navy-800 border border-border rounded text-xs text-white px-1.5 py-1.5"
        />
        <span>→</span>
        <input
          type="date"
          value={filters.dateTo}
          onChange={(e) => onChange({ ...filters, dateTo: e.target.value })}
          aria-label="To date"
          className="flex-1 sm:flex-none bg-navy-800 border border-border rounded text-xs text-white px-1.5 py-1.5"
        />
      </div>

      {/* Include testing */}
      <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
        <input
          type="checkbox"
          checked={filters.includeTesting}
          onChange={(e) => onChange({ ...filters, includeTesting: e.target.checked })}
          className="accent-warning"
        />
        Include testing activity
      </label>
    </div>
  )
}

// ── Per-query council cost ────────────────────────────────────────────────────

interface AgentCost {
  input_tokens?: number
  output_tokens?: number
  estimated_cost_usd?: number
  calls?: number
}

function CouncilCost({ ev }: { ev: ActivityEvent }) {
  const [open, setOpen] = useState(false)
  const cost = ev.estimated_cost_usd
  // A council row predating the token-logging release has no cost — emit
  // nothing rather than a misleading $0.0000.
  if (cost == null) return null

  const perAgent = (ev.metadata?.per_agent_cost as
    Record<string, AgentCost> | undefined) ?? {}
  const rows = Object.entries(perAgent)
    .sort(([, a], [, b]) =>
      (b.estimated_cost_usd ?? 0) - (a.estimated_cost_usd ?? 0))

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={rows.length === 0}
        className="text-2xs text-success font-mono hover:underline
                   disabled:no-underline disabled:cursor-default"
      >
        {fmtCost(cost)}
        <span className="text-muted">
          {' '}· {fmtTokens(ev.input_tokens)} in / {fmtTokens(ev.output_tokens)} out
        </span>
        {rows.length > 0 && (
          <span className="text-muted"> · {open ? 'hide' : 'per agent'}</span>
        )}
      </button>
      {open && rows.length > 0 && (
        <div className="mt-1 pl-2 border-l border-border/60 space-y-0.5">
          {rows.map(([label, a]) => (
            <div key={label}
                 className="flex items-center justify-between text-2xs gap-3">
              <span className="text-slate-300">{agentLabel(label)}</span>
              <span className="font-mono text-muted">
                {fmtCost(a.estimated_cost_usd)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Timeline row ──────────────────────────────────────────────────────────────

function TimelineRow({ ev }: { ev: ActivityEvent }) {
  const meta = KIND_META[ev.kind]
  const Icon = meta.icon
  return (
    <div className="flex items-start gap-3 px-3 py-2.5 border-b border-border/50 last:border-0">
      <div
        className="w-7 h-7 rounded flex items-center justify-center shrink-0 mt-0.5"
        style={{ background: `${meta.color}1a`, border: `1px solid ${meta.color}44` }}
      >
        <Icon className="w-3.5 h-3.5" style={{ color: meta.color }} />
      </div>
      <div className="min-w-0 flex-1">
        <RowBody ev={ev} />
        <div className="text-2xs text-muted mt-1 flex items-center gap-2 flex-wrap">
          <span>{ev.user_name}</span>
          <span>·</span>
          <span>{fmtTime(ev.timestamp)}</span>
          {ev.session_type === 'testing' && (
            <span className="text-warning">· testing</span>
          )}
        </div>
      </div>
    </div>
  )
}

function RowBody({ ev }: { ev: ActivityEvent }) {
  if (ev.kind === 'commit') {
    const technical = (ev.message ?? '').split('\n')[0]
    // Plain-English summary is the primary label; the technical git message
    // is shown muted beneath it for developer context (Issue 2).
    const primary = ev.plain_summary || technical
    return (
      <div>
        <div className="text-sm text-white leading-snug">
          {primary}
        </div>
        {ev.plain_summary && technical && (
          <div className="text-2xs text-muted/80 font-mono mt-0.5 leading-snug">
            {technical}
          </div>
        )}
        <div className="text-2xs text-muted mt-0.5 flex items-center gap-2 flex-wrap">
          {ev.github_url ? (
            <a href={ev.github_url} target="_blank" rel="noopener noreferrer"
               className="font-mono text-electric hover:underline">
              {(ev.sha ?? '').slice(0, 7)}
            </a>
          ) : (
            <span className="font-mono">{(ev.sha ?? '').slice(0, 7)}</span>
          )}
          {ev.files_changed != null && <span>{ev.files_changed} files</span>}
          {ev.insertions != null && (
            <span className="text-success">+{ev.insertions}</span>
          )}
          {ev.deletions != null && (
            <span className="text-danger">−{ev.deletions}</span>
          )}
        </div>
      </div>
    )
  }
  if (ev.kind === 'council') {
    const q = (ev.question_text ?? '').trim()
    return (
      <div>
        <div className="text-sm text-white leading-snug">
          {q ? (q.length > 120 ? q.slice(0, 120) + '…' : q) : 'Council deliberation'}
        </div>
        {ev.agents_involved && ev.agents_involved.length > 0 && (
          <div className="text-2xs text-muted mt-0.5">
            {ev.agents_involved.map(agentLabel).join(' · ')}
          </div>
        )}
        <CouncilCost ev={ev} />
      </div>
    )
  }
  if (ev.kind === 'academic_review') {
    const rating = (ev.metadata?.overall_rating as string | undefined) ?? null
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm text-white">Academic Review Session</span>
        {rating && (
          <span className={`text-2xs px-2 py-0.5 rounded-full border ${
            RATING_STYLE[rating] ?? 'bg-navy-700 text-muted border-border'
          }`}>
            {rating}
          </span>
        )}
      </div>
    )
  }
  if (ev.kind === 'qa') {
    const verdict = (ev.metadata?.verdict as string | undefined) ?? null
    return (
      <div className="text-sm text-white">
        QA Audit{verdict ? ` — ${verdict}` : ''}
      </div>
    )
  }
  if (ev.kind === 'document_upload') {
    const fn = (ev.metadata?.filename as string | undefined) ?? 'document'
    const dt = (ev.metadata?.document_type as string | undefined) ?? ''
    return (
      <div className="text-sm text-white">
        Uploaded <span className="font-mono text-slate-300">{fn}</span>
        {dt && <span className="text-2xs text-muted"> · {dt}</span>}
      </div>
    )
  }
  if (ev.kind === 'test_pass') {
    const m = ev.metadata ?? {}
    return (
      <div>
        <div className="text-sm text-white">Test Pass Completed</div>
        <div className="text-2xs text-muted mt-0.5">
          {String(m.script_id ?? '')}: {Number(m.passed ?? 0)} passed,
          {' '}{Number(m.failed ?? 0)} failed, {Number(m.skipped ?? 0)} skipped
        </div>
      </div>
    )
  }
  if (ev.kind === 'test_failure') {
    const m = ev.metadata ?? {}
    return (
      <div>
        <div className="text-sm text-white">
          Test Failure Reported
          {m.severity ? <span className="text-2xs text-danger"> · {String(m.severity)}</span> : null}
        </div>
        {m.failure_description ? (
          <div className="text-2xs text-muted mt-0.5">
            {String(m.failure_description)}
          </div>
        ) : null}
      </div>
    )
  }
  if (ev.kind === 'test_failure_resolved') {
    return (
      <div className="text-sm text-white">
        Test Failure Resolved
        <span className="text-2xs text-muted">
          {' '}· {String(ev.metadata?.step_id ?? '')}
        </span>
      </div>
    )
  }
  if (ev.kind === 'test_feedback') {
    const m = ev.metadata ?? {}
    return (
      <div className="text-sm text-white">
        Feedback Submitted — {String(m.title ?? 'feedback')}
        {m.ai_category ? (
          <span className="text-2xs text-muted"> · {String(m.ai_category)}</span>
        ) : null}
      </div>
    )
  }
  // page_view
  return (
    <div className="text-sm text-slate-300">
      Visited <span className="font-mono">{ev.page}</span>
      {ev.duration_seconds != null && (
        <span className="text-2xs text-muted"> · {fmtDuration(ev.duration_seconds)}</span>
      )}
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function TeamActivityPanel() {
  const [summary, setSummary] = useState<ActivitySummary | null>(null)
  const [cost, setCost] = useState<CostSummary | null>(null)
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [presentMode, setPresentMode] = useState(false)
  const [filters, setFilters] = useState<Filters>({
    userId: '', kinds: DEFAULT_KINDS, dateFrom: '', dateTo: '', includeTesting: false,
  })

  // Server params — activity_type is always 'all'; the kind multiselect is
  // applied client-side so it can be a true multiselect against a single
  // endpoint parameter.
  const serverParams = useCallback((nextOffset: number) => {
    const p: Record<string, string | number> = {
      activity_type: 'all',
      session_type: filters.includeTesting ? 'all' : 'analytical',
      limit: PAGE_LIMIT,
      offset: nextOffset,
    }
    if (filters.userId) p.user_id = filters.userId
    if (filters.dateFrom) p.date_from = filters.dateFrom
    if (filters.dateTo) p.date_to = filters.dateTo
    return p
  }, [filters])

  const loadSummary = useCallback(() => {
    axios.get<ActivitySummary>('/api/v1/activity/summary', {
      params: { include_testing: filters.includeTesting },
    })
      .then((res) => setSummary(res.data))
      .catch(() => setSummary(null))
    axios.get<CostSummary>('/api/v1/activity/cost-summary', {
      params: { include_testing: filters.includeTesting },
    })
      .then((res) => setCost(res.data))
      .catch(() => setCost(null))
  }, [filters.includeTesting])

  const loadTimeline = useCallback((reset: boolean) => {
    const nextOffset = reset ? 0 : offset
    if (reset) setLoading(true)
    else setLoadingMore(true)
    axios.get<TeamActivityResponse>('/api/v1/activity/team', {
      params: serverParams(nextOffset),
    })
      .then((res) => {
        const batch = res.data.events ?? []
        setEvents((prev) => (reset ? batch : [...prev, ...batch]))
        setHasMore(batch.length === PAGE_LIMIT)
        setOffset(nextOffset + batch.length)
        setError(null)
      })
      .catch(() => setError('Could not load team activity.'))
      .finally(() => { setLoading(false); setLoadingMore(false) })
  // offset intentionally excluded — reset path always uses 0, more path
  // reads the latest offset via the ref-free closure on click.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverParams])

  // Refetch whenever a server-side filter changes.
  useEffect(() => {
    loadSummary()
    loadTimeline(true)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.userId, filters.dateFrom, filters.dateTo, filters.includeTesting])

  // Client-side kind multiselect.
  const visible = useMemo(
    () => events.filter((e) => filters.kinds.includes(e.kind)),
    [events, filters.kinds],
  )

  // CSV export — every visible record, all fields incl. session_type.
  const exportRows = useMemo(
    () => visible.map((e) => [
      e.timestamp ?? '',
      e.kind,
      e.user,
      e.session_type ?? '',
      e.kind === 'commit'
        ? (e.message ?? '').split('\n')[0]
        : e.kind === 'council'
          ? (e.question_text ?? '')
          : e.kind === 'page_view'
            ? (e.page ?? '')
            : (e.response_summary ?? ''),
      e.sha ?? '',
      e.files_changed ?? '',
      e.insertions ?? '',
      e.deletions ?? '',
    ]),
    [visible],
  )

  return (
    <section data-tour="team-activity">
      <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-baseline gap-3">
          <h2 className="text-white font-semibold text-sm">Team Activity</h2>
          <span className="text-2xs text-muted uppercase tracking-wide">
            Platform engagement · AI use evidence
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setPresentMode((p) => !p)}
            className={`text-2xs px-2 py-1 rounded border transition-colors ${
              presentMode
                ? 'border-warning/40 bg-warning/10 text-warning'
                : 'border-border text-muted hover:text-white'
            }`}
          >
            {presentMode ? 'Exit Presentation View' : 'Presentation View'}
          </button>
          <button
            type="button"
            onClick={() => { loadSummary(); loadTimeline(true) }}
            aria-label="Refresh team activity"
            className="text-muted hover:text-white p-1 rounded transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 rounded border border-danger/30
                        bg-danger/5 text-danger text-xs mb-3">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="card p-8 text-center text-muted text-sm">
          <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
          Loading team activity…
        </div>
      ) : (
        <div className="space-y-3">
          {/* Summary + cost panels — hidden in Presentation View, which
              shows only the three charts full-width. */}
          {summary && !presentMode && <SummaryPanel summary={summary} />}
          {cost && !presentMode && <CostPanel cost={cost} />}

          {/* Visualisation dashboard. */}
          <TeamActivityCharts
            events={events}
            summary={summary}
            presentMode={presentMode}
          />

          {/* Filters + timeline — suppressed in Presentation View. */}
          {!presentMode && (
            <>
              <FilterBar
                filters={filters}
                members={summary?.per_member ?? []}
                onChange={setFilters}
              />

              <div className="card overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2
                                border-b border-border">
                  <span className="text-2xs text-muted uppercase tracking-wide">
                    Timeline · {visible.length} shown
                  </span>
                  <TableExportButton
                    tableId="team_activity"
                    headers={['Timestamp', 'Kind', 'User', 'Session Type', 'Summary',
                              'SHA', 'Files Changed', 'Insertions', 'Deletions']}
                    rows={exportRows}
                  />
                </div>
                {visible.length === 0 ? (
                  <p className="px-3 py-6 text-center text-xs text-muted italic">
                    No activity matches the current filters.
                  </p>
                ) : (
                  visible.map((ev, i) => (
                    <TimelineRow key={`${ev.kind}-${ev.timestamp}-${i}`} ev={ev} />
                  ))
                )}
                {hasMore && (
                  <button
                    type="button"
                    onClick={() => loadTimeline(false)}
                    disabled={loadingMore}
                    className="w-full py-2 min-h-[44px] text-xs text-electric
                               hover:bg-navy-700 transition-colors border-t
                               border-border disabled:opacity-50"
                  >
                    {loadingMore
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin inline" />
                      : 'Load more'}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}
