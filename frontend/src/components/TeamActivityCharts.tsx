/**
 * TeamActivityCharts — the visualisation dashboard above the Team
 * Activity timeline (commit 8b).
 *
 * Three charts, all derived from the already-fetched timeline events
 * and the summary (so they honour the same session-type and date
 * filters the panel applied):
 *
 *   1. Activity over time — weekly stacked bars by activity type.
 *   2. Team contribution split — a donut of substantive interactions
 *      per member (council + academic review + uploads; commits and
 *      page views excluded).
 *   3. Agent engagement — how often each council agent was consulted.
 *
 * In Presentation View the panel hides its filters and timeline and
 * shows these three charts full-width — the visual evidence shown
 * during the AI-use narrative of the final presentation.
 */
import { useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import type { ActivityEvent, ActivityKind, ActivitySummary } from '../types/activity'
import type { ChartTheme } from '../lib/exportTheme'
import { DARK_CHART_THEME } from '../lib/exportTheme'

// Stacked activity types for chart 1 — page_view is the lightest layer.
// Test attestations are their own distinct stack colour.
const STACK_KINDS: { key: ActivityKind; label: string; color: string }[] = [
  { key: 'commit',          label: 'Commits',          color: '#6366f1' },
  { key: 'council',         label: 'Council',          color: '#3b82f6' },
  { key: 'academic_review', label: 'Academic Review',  color: '#f59e0b' },
  { key: 'document_upload', label: 'Uploads',          color: '#0d9488' },
  { key: 'test_pass',       label: 'Test Attestations', color: '#10b981' },
  { key: 'page_view',       label: 'Page Views',       color: '#475569' },
]

const MEMBER_COLORS = ['#3b82f6', '#f59e0b', '#10b981', '#8b5cf6', '#06b6d4']

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

/** Monday (ISO week start) of the given date, as YYYY-MM-DD. */
function weekStart(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'unknown'
  const day = (d.getUTCDay() + 6) % 7   // 0 = Monday
  d.setUTCDate(d.getUTCDate() - day)
  return d.toISOString().slice(0, 10)
}

interface ChartCardProps {
  title: string
  subtitle: string
  children: React.ReactNode
  // In Presentation View titles and padding scale up for projector legibility.
  presentMode?: boolean
  theme?: ChartTheme
}

function ChartCard({ title, subtitle, children, presentMode, theme = DARK_CHART_THEME }: ChartCardProps) {
  const light = theme.mode === 'light'
  return (
    <div
      className={light ? `${presentMode ? 'p-6' : 'p-4'} rounded-lg` : `card ${presentMode ? 'p-6' : 'p-4'}`}
      style={light ? { background: theme.background, border: `1px solid ${theme.border}` } : undefined}
    >
      <div className={presentMode ? 'mb-4' : 'mb-2'}>
        <h3 className={`font-semibold ${presentMode ? 'text-xl' : 'text-sm'}`}
            style={{ color: theme.textPrimary }}>
          {title}
        </h3>
        <p className={presentMode ? 'text-sm' : 'text-2xs'} style={{ color: theme.textSecondary }}>
          {subtitle}
        </p>
      </div>
      {children}
    </div>
  )
}

interface Props {
  events: ActivityEvent[]
  summary: ActivitySummary | null
  presentMode: boolean
  /** Light theme renders the charts on white for the academic export. */
  theme?: ChartTheme
}

export default function TeamActivityCharts({
  events, summary, presentMode, theme = DARK_CHART_THEME,
}: Props) {
  // Chart-1 stack visibility — clicking the legend toggles a type off.
  const [hidden, setHidden] = useState<Set<ActivityKind>>(new Set())
  const toggle = (k: ActivityKind) => {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  // ── Chart 1 — weekly stacked activity ───────────────────────────────────────
  const weekly = useMemo(() => {
    const buckets = new Map<string, Record<string, number>>()
    for (const ev of events) {
      if (!ev.timestamp) continue
      const wk = weekStart(ev.timestamp)
      const row = buckets.get(wk) ?? {}
      row[ev.kind] = (row[ev.kind] ?? 0) + 1
      buckets.set(wk, row)
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([week, counts]) => ({ week, ...counts }))
  }, [events])

  // ── Chart 2 — contribution split (substantive interactions only) ────────────
  const contribution = useMemo(() => {
    const members = summary?.per_member ?? []
    return members
      .map((m) => ({
        name: m.user_name,
        value: m.council_interactions + m.academic_review_sessions
          + m.document_uploads,
      }))
      .filter((m) => m.value > 0)
  }, [summary])

  // ── Chart 3 — agent engagement ──────────────────────────────────────────────
  const agentEngagement = useMemo(() => {
    const counts = new Map<string, number>()
    for (const ev of events) {
      if (ev.kind !== 'council' && ev.kind !== 'academic_review') continue
      for (const a of ev.agents_involved ?? []) {
        counts.set(a, (counts.get(a) ?? 0) + 1)
      }
    }
    return [...counts.entries()]
      .map(([agent, count]) => ({ agent: agentLabel(agent), count }))
      .sort((a, b) => b.count - a.count)
  }, [events])

  // Presentation View scales every chart up for 1920×1080 projector
  // legibility — taller charts, larger axis ticks/legends, thicker marks.
  const chartHeight = presentMode ? 480 : 240
  const axisFontSize = presentMode ? 16 : 11
  const legendFontSize = presentMode ? 16 : 11
  const barLabelFontSize = presentMode ? 15 : 11

  const axisProps = {
    tick: { fill: theme.axisTick.fill, fontSize: axisFontSize },
    stroke: theme.gridStroke,
  }
  const tooltipStyle = {
    contentStyle: {
      background: theme.tooltipContentStyle.backgroundColor as string,
      border: `1px solid ${theme.border}`,
      borderRadius: 8, fontSize: presentMode ? 15 : 12,
      color: theme.textPrimary,
    },
    labelStyle: { color: theme.textPrimary },
  }
  const legendProps = {
    wrapperStyle: { fontSize: legendFontSize },
  }

  const chartActivity = (
        <ChartCard
          title="Activity over time"
          subtitle="Weekly platform activity by type — analytical sessions unless testing is included"
          presentMode={presentMode}
        >
          {weekly.length === 0 ? (
            <EmptyChart />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={chartHeight}>
                <BarChart data={weekly}>
                  <XAxis dataKey="week" {...axisProps} />
                  <YAxis allowDecimals={false} {...axisProps} />
                  <Tooltip {...tooltipStyle} cursor={{ fill: '#ffffff08' }} />
                  {/* Present mode adds a recharts Legend; normal mode relies
                      on the interactive custom legend below the chart. */}
                  {presentMode && <Legend {...legendProps} />}
                  {STACK_KINDS.filter((s) => !hidden.has(s.key)).map((s) => (
                    <Bar key={s.key} dataKey={s.key} stackId="a"
                         name={s.label} fill={s.color} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
              {/* Custom legend — click to show/hide a stack */}
              <div className={`flex items-center flex-wrap ${
                presentMode ? 'gap-5 mt-4' : 'gap-3 mt-2'
              }`}>
                {STACK_KINDS.map((s) => {
                  const off = hidden.has(s.key)
                  return (
                    <button
                      key={s.key}
                      type="button"
                      onClick={() => toggle(s.key)}
                      className={`flex items-center transition-opacity ${
                        presentMode ? 'gap-2 text-base' : 'gap-1.5 text-2xs'
                      } ${off ? 'opacity-40' : 'opacity-100'}`}
                    >
                      <span className={`rounded-sm ${
                        presentMode ? 'w-4 h-4' : 'w-2.5 h-2.5'
                      }`} style={{ background: s.color }} />
                      <span style={{ color: theme.textSecondary }}>{s.label}</span>
                    </button>
                  )
                })}
              </div>
            </>
          )}
        </ChartCard>
  )

  const chartContribution = (
      <ChartCard
        title="Team contribution split"
        subtitle="Share of substantive interactions — council, academic review, uploads"
        presentMode={presentMode}
        theme={theme}
      >
        {contribution.length === 0 ? (
          <EmptyChart />
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <PieChart>
              <Pie
                data={contribution}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                innerRadius={presentMode ? 110 : 50}
                outerRadius={presentMode ? 175 : 80}
                stroke="#0a0e1a"
                strokeWidth={presentMode ? 4 : 2}
                label={(e) => `${e.name}: ${e.value}`}
                labelLine={false}
                style={{ fontSize: barLabelFontSize }}
              >
                {contribution.map((_, i) => (
                  <Cell key={i} fill={MEMBER_COLORS[i % MEMBER_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip {...tooltipStyle} />
              <Legend {...legendProps} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
  )

  const chartAgent = (
      <ChartCard
        title="Agent engagement"
        subtitle="Times each council agent was consulted across all sessions"
        presentMode={presentMode}
        theme={theme}
      >
        {agentEngagement.length === 0 ? (
          <EmptyChart />
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart data={agentEngagement} layout="vertical"
                      margin={{ left: presentMode ? 40 : 24 }}>
              <XAxis type="number" allowDecimals={false} {...axisProps} />
              <YAxis type="category" dataKey="agent"
                     width={presentMode ? 180 : 120} {...axisProps} />
              <Tooltip {...tooltipStyle} cursor={{ fill: '#ffffff08' }} />
              {presentMode && <Legend {...legendProps} />}
              <Bar dataKey="count" name="Times consulted" fill="#3b82f6"
                   {...(presentMode ? { barSize: 36 } : {})} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
  )

  // Presentation View — a one-at-a-time swipeable carousel on mobile, the
  // three charts stacked full-width from sm: up.
  if (presentMode) {
    return <PresentationCarousel charts={[chartActivity, chartContribution, chartAgent]} />
  }

  // Normal mode — chart 1 spans both columns, charts 2 and 3 side by side
  // from lg: up; all three stack on a narrow screen.
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div className="lg:col-span-2">{chartActivity}</div>
      {chartContribution}
      {chartAgent}
    </div>
  )
}

/**
 * PresentationCarousel — Presentation View chart container. On a phone
 * the three charts are shown one at a time, swipeable left/right, with a
 * ●○○ index indicator; from sm: up they stack full-width.
 */
function PresentationCarousel({ charts }: { charts: React.ReactNode[] }) {
  const [i, setI] = useState(0)
  const touchX = useRef<number | null>(null)
  const go = (d: number) => setI((p) => (p + d + charts.length) % charts.length)

  return (
    <>
      {/* Mobile — one chart, swipeable. */}
      <div className="sm:hidden">
        <div
          onTouchStart={(e) => { touchX.current = e.touches[0]?.clientX ?? null }}
          onTouchEnd={(e) => {
            if (touchX.current == null) return
            const dx = (e.changedTouches[0]?.clientX ?? 0) - touchX.current
            if (dx > 40) go(-1)
            else if (dx < -40) go(1)
            touchX.current = null
          }}
        >
          {charts[i]}
        </div>
        <div className="flex items-center justify-center gap-4 mt-3">
          <button
            type="button" onClick={() => go(-1)} aria-label="Previous chart"
            className="w-11 h-11 flex items-center justify-center
                       text-muted hover:text-white"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="flex gap-2" aria-hidden="true">
            {charts.map((_, n) => (
              <span
                key={n}
                className={`w-2 h-2 rounded-full ${
                  n === i ? 'bg-electric' : 'bg-border'}`}
              />
            ))}
          </div>
          <button
            type="button" onClick={() => go(1)} aria-label="Next chart"
            className="w-11 h-11 flex items-center justify-center
                       text-muted hover:text-white"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      </div>
      {/* sm and up — all three stacked full-width. */}
      <div className="hidden sm:block space-y-4">
        {charts.map((c, n) => <div key={n}>{c}</div>)}
      </div>
    </>
  )
}

function EmptyChart() {
  return (
    <div className="h-[200px] flex items-center justify-center text-xs text-muted italic">
      No activity in this range yet.
    </div>
  )
}
