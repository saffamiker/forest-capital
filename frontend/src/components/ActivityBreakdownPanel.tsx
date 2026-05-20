/**
 * ActivityBreakdownPanel — the Settings → Users → Platform Engagement
 * surface. Sits below the user-management table and shows a per-user
 * breakdown of agent_interactions over the last 30 days, plus a
 * session-type (analytical / testing) page-view split.
 *
 * Data:        GET /api/v1/admin/users/activity-breakdown
 * Gate:        manage_users (the endpoint refuses anything else)
 * Colours:     consistent with TeamActivityCharts on the Reports page
 *              (council = navy, academic_review = amber, …) so a
 *              sysadmin glancing between the two pages reads the same
 *              signal both places.
 *
 * Each user with any activity renders a horizontal stacked bar
 * (recharts) of their interaction counts by type; below the chart, a
 * two-column summary lists the per-type counts on the left and the
 * session-type page-view split on the right. AI spend appears only
 * when the cost is non-zero — a viewer's $0 row stays uncluttered.
 *
 * A user with zero interactions in the 30-day window shows a muted
 * "No activity in the last 30 days" state instead of an empty bar.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell,
} from 'recharts'
import { AlertCircle, Loader2 } from 'lucide-react'

interface UserBreakdown {
  email: string
  display_name: string | null
  role: string | null
  breakdown: Record<string, number>
  session_breakdown: { analytical?: number; testing?: number }
  total_interactions: number
  total_cost_usd: number
  first_seen: string | null
  last_seen: string | null
}

interface ApiResponse {
  users: UserBreakdown[]
  period_days: number
  generated_at: string
}

// Interaction-type colours — kept in sync with TeamActivityCharts on
// the Reports page; a sysadmin scanning both surfaces should not have
// to relearn the palette.
const INTERACTION_COLOURS: Record<string, string> = {
  council:              '#1e3a8a',  // navy
  academic_review:      '#f59e0b',  // amber
  writing_assistant:    '#3b82f6',  // electric blue
  explain:              '#10b981',  // green
  explain_data:         '#059669',  // green (slightly darker)
  qa:                   '#7c3aed',  // purple
  export:               '#0d9488',  // teal
  test_quality_eval:    '#475569',  // grey
  document_upload:      '#6366f1',  // indigo (matches Commits colour)
}

const INTERACTION_LABELS: Record<string, string> = {
  council:              'Council',
  academic_review:      'Academic Review',
  writing_assistant:    'Writing Assistant',
  explain:              'Explain',
  explain_data:         'Data Explain',
  qa:                   'QA',
  export:               'Export',
  test_quality_eval:    'Test Eval',
  document_upload:      'Document Upload',
}

// Stable ordering — the segments inside a stacked bar render in the
// same sequence per user, so a glance comparison reads cleanly. Any
// unknown type a future migration introduces falls through to grey.
const TYPE_ORDER = [
  'council', 'academic_review', 'writing_assistant',
  'explain', 'explain_data', 'qa', 'export',
  'test_quality_eval', 'document_upload',
]

function colourFor(t: string): string {
  return INTERACTION_COLOURS[t] ?? '#64748b'
}
function labelFor(t: string): string {
  return INTERACTION_LABELS[t] ?? t.replace(/_/g, ' ')
}

function displayName(u: UserBreakdown): string {
  return u.display_name ?? u.email
}

/**
 * Horizontal stacked bar for one user. Each segment is one
 * interaction_type; tooltip reveals the per-type label and count.
 * Recharts wants the data as a single row with one key per series.
 */
function UserBar({ user }: { user: UserBreakdown }) {
  // Data row — one key per interaction_type with a count > 0. Recharts
  // skips a series whose value is undefined, so this naturally hides
  // empty segments from the legend.
  const row: Record<string, number | string> = { name: 'total' }
  const present: string[] = []
  for (const t of TYPE_ORDER) {
    const n = user.breakdown[t]
    if (n && n > 0) {
      row[t] = n
      present.push(t)
    }
  }
  // Catch unknown types the backend might add — fold into a single
  // "other" segment so a new interaction_type doesn't silently vanish.
  let otherCount = 0
  for (const [t, n] of Object.entries(user.breakdown)) {
    if (!TYPE_ORDER.includes(t) && n > 0) {
      otherCount += n
    }
  }
  if (otherCount > 0) {
    row['other'] = otherCount
    present.push('other')
  }

  return (
    <ResponsiveContainer width="100%" height={32}>
      <BarChart data={[row]} layout="vertical"
                margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" hide />
        <Tooltip
          contentStyle={{
            background: '#0a0e1a', border: '1px solid #1f2937',
            borderRadius: 4, fontSize: 11, color: '#f9fafb',
          }}
          labelStyle={{ display: 'none' }}
          formatter={(value: number, name: string) =>
            [`${value} interactions`, labelFor(String(name))]}
        />
        {present.map((t) => (
          <Bar key={t} dataKey={t} stackId="a" isAnimationActive={false}>
            <Cell fill={colourFor(t)} />
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

/** Per-type list — only types with count > 0 appear. */
function TypeBreakdownList({ user }: { user: UserBreakdown }) {
  const entries = Object.entries(user.breakdown)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
  if (entries.length === 0) {
    return <div className="text-2xs text-muted italic">No interactions</div>
  }
  return (
    <ul className="text-2xs text-slate-300 space-y-0.5">
      {entries.map(([t, n]) => (
        <li key={t} className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-sm shrink-0"
                style={{ background: colourFor(t) }} />
          <span className="flex-1 truncate">{labelFor(t)}</span>
          <span className="font-mono text-slate-200 shrink-0">{n}</span>
        </li>
      ))}
    </ul>
  )
}

/** Session-type page-view split. */
function SessionBreakdownList({ user }: { user: UserBreakdown }) {
  const analytical = user.session_breakdown.analytical ?? 0
  const testing = user.session_breakdown.testing ?? 0
  if (analytical === 0 && testing === 0) {
    return <div className="text-2xs text-muted italic">No page views</div>
  }
  return (
    <ul className="text-2xs text-slate-300 space-y-0.5">
      <li className="flex items-center justify-between gap-2">
        <span>Analytical</span>
        <span className="font-mono">{analytical} page views</span>
      </li>
      <li className="flex items-center justify-between gap-2">
        <span>Testing</span>
        <span className="font-mono">{testing} page views</span>
      </li>
    </ul>
  )
}

/** One user's full breakdown card — bar + two-column summary + cost. */
function UserBreakdownCard({ user }: { user: UserBreakdown }) {
  const zero = user.total_interactions === 0
  return (
    <div className="border border-border rounded p-3 space-y-2"
         data-testid={`activity-breakdown-${user.email}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm text-white truncate">
            {displayName(user)}
          </div>
          {user.display_name && (
            <div className="text-2xs text-muted truncate">{user.email}</div>
          )}
        </div>
        <div className="text-2xs font-mono text-slate-300 shrink-0">
          {user.total_interactions} interactions
        </div>
      </div>

      {zero ? (
        <div className="text-2xs text-muted py-1.5"
             data-testid={`activity-zero-${user.email}`}>
          No activity in the last 30 days
        </div>
      ) : (
        <>
          <UserBar user={user} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <TypeBreakdownList user={user} />
            <SessionBreakdownList user={user} />
          </div>
          {user.total_cost_usd > 0 && (
            <div className="text-2xs text-muted pt-1 border-t
                            border-border/40">
              AI spend: <span className="font-mono text-slate-200">
                ${user.total_cost_usd.toFixed(2)}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  )
}


export default function ActivityBreakdownPanel() {
  const [data, setData] = useState<ApiResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    axios.get<ApiResponse>('/api/v1/admin/users/activity-breakdown')
      .then((res) => { if (!cancelled) { setData(res.data); setError(null) } })
      .catch((err) => {
        if (cancelled) return
        setError(axios.isAxiosError(err)
          ? (err.response?.data?.detail ?? err.message)
          : 'Failed to load activity breakdown')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-white font-semibold text-sm"
            data-testid="activity-breakdown-header">
          Platform Engagement
        </h3>
        <p className="text-2xs text-muted mt-0.5">
          Last 30 days — analytical sessions only
        </p>
      </div>

      {loading && (
        <div className="text-2xs text-muted flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading…
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 px-3 py-2 rounded border
                        border-danger/30 bg-danger/5 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {!loading && !error && data && (
        <div className="space-y-2">
          {data.users.length === 0 ? (
            <p className="text-2xs text-muted italic">No users.</p>
          ) : (
            data.users.map((u) => (
              <UserBreakdownCard key={u.email} user={u} />
            ))
          )}
        </div>
      )}
    </div>
  )
}
