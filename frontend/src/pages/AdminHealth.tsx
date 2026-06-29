/**
 * AdminHealth — /admin/health
 *
 * A read-only health panel surfacing the platform's runtime validation
 * status:
 *
 *   1. Invariant Framework        — the latest warm's deterministic
 *                                   verdict from
 *                                   `/api/v1/admin/invariants` (PR #244).
 *   2. Layer 4 Display Fixtures   — Category 2 ("Time Basis
 *                                   Consistency") violations from the
 *                                   same payload, surfaced separately
 *                                   because Cat 2 is the F3-class
 *                                   display-layer audit at runtime.
 *   3. Cache Warm History         — last seven warms from
 *                                   `/api/v1/admin/invariants/history`,
 *                                   each row carrying the verdict PR #252
 *                                   persists to analytics_metrics_cache.
 *
 * No new backend logic beyond a single thin history-read endpoint —
 * every figure on this page comes from a row that already exists in
 * the cache.
 *
 * Auth: any authenticated user can read this page; no sysadmin gate.
 * The data the panel surfaces is purely operational.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { ArrowLeft, RefreshCw, CheckCircle, XCircle } from 'lucide-react'


// ── API shapes ────────────────────────────────────────────────────────────

interface InvariantViolation {
  code: string
  severity: string         // "hard" | "soft"
  category: number         // 1=math, 2=time-basis (Layer 4), 3=external, 4=directional, 5=temporal
  entity?: string | null
  metric?: string | null
  expected?: string | null
  actual?: string | null
  detail?: string | null
}

interface InvariantsPayload {
  available: boolean
  passed?: boolean
  checks_run?: number
  hard_failures?: number
  soft_warnings?: number
  violations?: InvariantViolation[]
  ran_at?: string | null
  note?: string
}

interface HistoryRow {
  computed_at: string
  data_hash: string
  passed: boolean
  checks_run: number
  hard_failures: number
  soft_warnings: number
  ran_at?: string | null
}

interface HistoryPayload {
  available: boolean
  rows: HistoryRow[]
}


// ── Helpers ───────────────────────────────────────────────────────────────

function fmtTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
  } catch {
    return iso
  }
}

function StatusPill({ passed }: { passed: boolean | undefined }) {
  if (passed === undefined) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded
                       text-2xs font-mono uppercase tracking-wide
                       border border-border text-muted">
        unknown
      </span>
    )
  }
  return passed ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded
                     text-2xs font-mono uppercase tracking-wide
                     border border-success/40 bg-success/10 text-success">
      <CheckCircle className="w-3 h-3" /> Passed
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded
                     text-2xs font-mono uppercase tracking-wide
                     border border-danger/40 bg-danger/10 text-danger">
      <XCircle className="w-3 h-3" /> Failed
    </span>
  )
}

function CountBadge({
  label, count, tone,
}: { label: string; count: number; tone: 'success' | 'warning' | 'danger' | 'muted' }) {
  const toneCls = {
    success: 'border-success/40 bg-success/10 text-success',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    danger:  'border-danger/40 bg-danger/10 text-danger',
    muted:   'border-border text-muted',
  }[tone]
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded
                       text-xs font-mono ${toneCls}`}>
      <span className="font-semibold">{count}</span>
      <span className="opacity-80">{label}</span>
    </span>
  )
}


// ── Section 1 — Invariant Framework ──────────────────────────────────────

function InvariantSection({
  data, lastRunIso,
}: { data: InvariantsPayload | null; lastRunIso: string | null | undefined }) {
  if (!data) return <div className="text-sm text-muted">Loading invariant status…</div>
  if (!data.available) {
    return (
      <div className="text-sm text-muted">
        {data.note
          || 'No invariant run has landed yet — the framework fires on '
             + 'the next analytics warm.'}
      </div>
    )
  }
  const checksPassed = (data.checks_run ?? 0) - (data.violations?.length ?? 0)
  const total = data.checks_run ?? 0
  const hard = data.hard_failures ?? 0
  const soft = data.soft_warnings ?? 0
  const violations = data.violations ?? []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill passed={data.passed} />
        <CountBadge label="checks passed"
          count={checksPassed} tone={total > 0 ? 'success' : 'muted'} />
        <span className="text-2xs text-muted">of {total || '—'}</span>
        <CountBadge label="hard failures"
          count={hard} tone={hard > 0 ? 'danger' : 'muted'} />
        <CountBadge label="soft warnings"
          count={soft} tone={soft > 0 ? 'warning' : 'muted'} />
      </div>
      <div className="text-2xs text-muted font-mono">
        Last run: {fmtTimestamp(lastRunIso)}
      </div>
      {violations.length > 0 && (
        <ViolationsTable violations={violations} />
      )}
      {violations.length === 0 && (
        <p className="text-2xs text-muted leading-relaxed border-t border-border pt-2">
          All deterministic checks passed on the latest warm. The invariant
          framework runs at the end of every analytics warm —
          see <code className="text-electric">docs/INVARIANTS.md</code> for
          the assertion catalogue and severity tiers.
        </p>
      )}
    </div>
  )
}


function ViolationsTable({ violations }: { violations: InvariantViolation[] }) {
  return (
    <div className="overflow-x-auto border border-border rounded">
      <table className="w-full text-2xs font-mono">
        <thead className="bg-navy-800 text-muted uppercase tracking-wide">
          <tr>
            <th className="text-left px-2 py-1.5">Code</th>
            <th className="text-left px-2 py-1.5">Severity</th>
            <th className="text-left px-2 py-1.5">Entity</th>
            <th className="text-left px-2 py-1.5">Metric</th>
            <th className="text-left px-2 py-1.5">Expected</th>
            <th className="text-left px-2 py-1.5">Actual</th>
          </tr>
        </thead>
        <tbody className="text-white">
          {violations.map((v, i) => (
            <tr
              key={`${v.code}-${v.entity ?? ''}-${i}`}
              className="border-t border-border align-top">
              <td className="px-2 py-1.5">{v.code}</td>
              <td className="px-2 py-1.5">
                {v.severity === 'hard'
                  ? <span className="text-danger">HARD</span>
                  : <span className="text-warning">soft</span>}
              </td>
              <td className="px-2 py-1.5 text-white/80">{v.entity || '—'}</td>
              <td className="px-2 py-1.5 text-white/80">{v.metric || '—'}</td>
              <td className="px-2 py-1.5 text-muted">{v.expected || '—'}</td>
              <td className="px-2 py-1.5 text-white">{v.actual || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


// ── Section 2 — Layer 4 Display Fixtures (Category 2) ────────────────────
//
// The four-layer validation stack (see docs/INVARIANTS.md): Layer 1
// data sanity → Layer 2 recomputation → Layer 3 cross-platform → Layer 4
// display-layer math audit. Layer 4 lives in two places:
//
//   - tests/test_display_layer_fixtures.py — pytest fixtures, run in CI
//   - the invariant framework's Category 2 ("Time Basis Consistency")
//     checks (2a, 2b, 2c, 2d) — same class of bug, runtime detection
//
// The runtime category-2 checks are what the F3 incident proved were
// load-bearing (the CAGR-vs-cumulative bug was caught by 1a, 1h, and 2a
// per docs/INVARIANTS.md). This section surfaces those four runtime
// checks as the operationally-relevant Layer 4 status.

const _LAYER_4_CODES = ['2a', '2b', '2c', '2d'] as const
const _LAYER_4_DESCRIPTIONS: Record<string, string> = {
  '2a': 'Crisis cumulative_return matches a fresh recompute',
  '2b': 'Sharpe is annualised (sqrt(12)), not raw monthly',
  '2c': 'Factor-loadings rows share ≤ 4 estimation windows',
  '2d': 'Stored CAGR matches recompute within 0.5%',
}

function Layer4Section({ data }: { data: InvariantsPayload | null }) {
  if (!data) return <div className="text-sm text-muted">Loading…</div>
  if (!data.available) {
    return (
      <div className="text-sm text-muted">
        Layer 4 status is reported alongside the invariant framework —
        run an analytics warm to populate this section.
      </div>
    )
  }
  const layer4Failures = (data.violations ?? []).filter(
    (v) => v.category === 2)
  const total = _LAYER_4_CODES.length
  const passed = total - layer4Failures.length
  const allPassed = layer4Failures.length === 0
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill passed={allPassed} />
        <CountBadge label="fixtures passed"
          count={passed} tone={allPassed ? 'success' : 'warning'} />
        <span className="text-2xs text-muted">of {total}</span>
      </div>
      <ul className="text-2xs font-mono space-y-1.5">
        {_LAYER_4_CODES.map((code) => {
          const failed = layer4Failures.find((v) => v.code === code)
          return (
            <li key={code} className="flex items-start gap-2">
              {failed
                ? <XCircle className="w-3 h-3 text-danger shrink-0 mt-0.5" />
                : <CheckCircle className="w-3 h-3 text-success shrink-0 mt-0.5" />}
              <div className="min-w-0 flex-1">
                <span className="text-white">{code}</span>
                <span className="text-muted"> · {_LAYER_4_DESCRIPTIONS[code]}</span>
                {failed && (
                  <div className="text-danger mt-0.5">
                    {failed.entity ? `${failed.entity}: ` : ''}
                    expected <span className="text-white">{failed.expected || '—'}</span>,
                    got <span className="text-white">{failed.actual || '—'}</span>
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}


// ── Section 3 — Cache Warm History ───────────────────────────────────────

function WarmHistorySection({ data }: { data: HistoryPayload | null }) {
  if (!data) return <div className="text-sm text-muted">Loading warm history…</div>
  if (!data.available) {
    return (
      <div className="text-sm text-muted">
        Warm history is unavailable (the database is unreachable or no
        warms have landed since the invariant_summary cache row started
        persisting).
      </div>
    )
  }
  if (data.rows.length === 0) {
    return (
      <div className="text-sm text-muted">
        No warms recorded yet. The invariant summary persists on every
        analytics warm — the first warm after a deploy will populate
        this strip.
      </div>
    )
  }
  return (
    <div className="overflow-x-auto border border-border rounded">
      <table className="w-full text-2xs font-mono">
        <thead className="bg-navy-800 text-muted uppercase tracking-wide">
          <tr>
            <th className="text-left px-2 py-1.5">When</th>
            <th className="text-left px-2 py-1.5">Hash</th>
            <th className="text-left px-2 py-1.5">Verdict</th>
            <th className="text-right px-2 py-1.5">Checks</th>
            <th className="text-right px-2 py-1.5">Hard</th>
            <th className="text-right px-2 py-1.5">Soft</th>
          </tr>
        </thead>
        <tbody className="text-white">
          {data.rows.map((row, i) => (
            <tr
              key={`${row.data_hash}-${row.computed_at}-${i}`}
              className="border-t border-border">
              <td className="px-2 py-1.5">{fmtTimestamp(row.computed_at)}</td>
              <td className="px-2 py-1.5 text-muted">{row.data_hash}</td>
              <td className="px-2 py-1.5">
                <StatusPill passed={row.passed} />
              </td>
              <td className="px-2 py-1.5 text-right">{row.checks_run}</td>
              <td className={`px-2 py-1.5 text-right ${
                row.hard_failures > 0 ? 'text-danger' : 'text-muted'
              }`}>{row.hard_failures}</td>
              <td className={`px-2 py-1.5 text-right ${
                row.soft_warnings > 0 ? 'text-warning' : 'text-muted'
              }`}>{row.soft_warnings}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


// ── Page shell ────────────────────────────────────────────────────────────

interface SectionShellProps {
  id: string
  title: string
  description: string
  children: React.ReactNode
}

function Section({ id, title, description, children }: SectionShellProps) {
  return (
    <section id={id} className="scroll-mt-20">
      <h2 className="text-base font-semibold text-white">{title}</h2>
      <p className="text-xs text-muted mt-0.5">{description}</p>
      <div className="border-t border-border mt-3 pt-4">{children}</div>
    </section>
  )
}


export default function AdminHealth() {
  const navigate = useNavigate()
  const [invariants, setInvariants] = useState<InvariantsPayload | null>(null)
  const [history, setHistory] = useState<HistoryPayload | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = () => {
    setRefreshing(true)
    Promise.all([
      axios.get<InvariantsPayload>('/api/v1/admin/invariants'),
      axios.get<HistoryPayload>('/api/v1/admin/invariants/history'),
    ])
      .then(([inv, hist]) => {
        setInvariants(inv.data)
        setHistory(hist.data)
      })
      .catch((err) => {
        console.error('admin-health load failed', err)
      })
      .finally(() => setRefreshing(false))
  }

  useEffect(() => {
    load()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The most-recent warm's timestamp powers Section 1's "Last run". The
  // in-memory invariants payload doesn't carry it directly (the result
  // dict was defined before the persistence layer existed), so we read
  // it off the freshest history row.
  const lastRunIso = useMemo(() => {
    if (history?.rows && history.rows.length > 0) {
      return history.rows[0].computed_at
    }
    return invariants?.ran_at ?? null
  }, [history, invariants])

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-8">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <button
            type="button"
            onClick={() => navigate('/settings')}
            className="text-muted hover:text-white text-xs flex items-center
                       gap-1"
            aria-label="Back to Settings">
            <ArrowLeft className="w-3.5 h-3.5" /> Settings
          </button>
          <span className="text-muted">·</span>
          <h1 className="text-lg font-semibold text-white">Health</h1>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={refreshing}
          className="text-xs px-2.5 py-1 rounded border border-border
                     hover:bg-navy-800 text-muted hover:text-white
                     disabled:opacity-50 inline-flex items-center gap-1">
          <RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <Section
        id="invariant-framework"
        title="Invariant Framework"
        description={
          'Deterministic checks the platform runs at the end of every '
          + 'analytics warm. Every assertion is a pure mathematical '
          + 'comparison — no LLM in any detection path.'
        }>
        <InvariantSection data={invariants} lastRunIso={lastRunIso} />
      </Section>

      <Section
        id="layer-4-fixtures"
        title="Layer 4 Display Fixtures"
        description={
          'The runtime Category 2 ("Time Basis Consistency") checks — '
          + 'the F3-class display-layer math audit. Each fixture compares '
          + 'a displayed metric against a fresh recompute from the '
          + 'monthly series within a tight tolerance.'
        }>
        <Layer4Section data={invariants} />
      </Section>

      <Section
        id="warm-history"
        title="Cache Warm History"
        description={
          'Last seven analytics warms, newest first. Each row is the '
          + 'invariant verdict the warm wrote to '
          + 'analytics_metrics_cache. The data_hash anchors every row to '
          + 'a specific strategy_results_cache row.'
        }>
        <WarmHistorySection data={history} />
      </Section>

      <p className="text-2xs text-muted leading-relaxed border-t border-border pt-3">
        Data sources: <code className="text-electric">GET /api/v1/admin/invariants</code>
        {' '}(Sections 1 + 2) and{' '}
        <code className="text-electric">GET /api/v1/admin/invariants/history</code>
        {' '}(Section 3). Both read pre-computed rows from
        {' '}<code className="text-electric">analytics_metrics_cache</code> —
        no recomputation, no fan-out.
      </p>
    </div>
  )
}
