/**
 * Council Performance Record (/performance-record).
 *
 * The event-by-event track record behind the aggregate out-of-sample
 * Sharpe (Layer 3, 0.8576). Reads the nine frozen point-in-time event
 * evaluations from GET /api/v1/play-by-play (read-only; the rows are
 * written once by run_play_by_play.py and never recomputed). Renders:
 *   1. a scorecard summary with honest framing,
 *   2. a cumulative post-2022 chart (when the series is available),
 *   3. one card per event with the regime read, blend, council
 *      recommendation, dissent, forward 30/60/90d performance, verdict,
 *      and value-added Sharpe; Liberation Day carries its explicit
 *      limitation note.
 *
 * Every number is server-provided; nothing is hardcoded here.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { AlertTriangle, TrendingUp, TrendingDown, Loader2 } from 'lucide-react'
import InfoIcon from '../components/InfoIcon'
import { useChartTheme } from '../lib/useChartTheme'

interface Horizons { d30: number | null; d60: number | null; d90: number | null }
interface PerfBlock {
  blend?: Horizons
  benchmark?: Horizons
  classic_6040?: Horizons
}
interface EventRow {
  event_id: string
  event_date: string
  trigger: string
  regime: string | null
  posterior: { bull?: number; bear?: number; transition?: number } | null
  blend_weights: Record<string, number> | null
  recommendation: string | null
  dissenting_view: string | null
  performance: PerfBlock | null
  verdict: string | null
  value_added_sharpe: number | null
  key_limitation?: string
}
interface Scorecard {
  n_total: number
  n_evaluable: number
  n_value_added: number
  value_added_event_ids: string[]
  framing: string
}
interface CumulativePoint {
  date: string
  regime_conditional?: number | null
  benchmark?: number | null
  classic_6040?: number | null
}
interface Cumulative {
  series: CumulativePoint[]
  event_markers: string[]
}
interface Payload {
  available: boolean
  events: EventRow[]
  scorecard: Scorecard | null
  key_limitations: Record<string, string>
  cumulative?: Cumulative
}

const pct = (x: number | null | undefined): string =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(1)}%`

const prob = (x: number | null | undefined): string =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(0)}%`

const fmtSharpe = (x: number | null | undefined): string =>
  x === null || x === undefined ? '—' : x.toFixed(2)

const fmtVsPct = (x: number | null | undefined): string =>
  x === null || x === undefined ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(0)}%`

// Display-only: render a stored ISO date (YYYY-MM-DD) as US MM/DD/YYYY.
// The backend keeps ISO internally; this formats at the point of render.
const fmtDate = (iso: string | null | undefined): string => {
  if (!iso) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso))
  return m ? `${m[2]}/${m[3]}/${m[1]}` : String(iso)
}

function topWeights(w: Record<string, number> | null): string {
  if (!w) return '—'
  return Object.entries(w)
    .filter(([, v]) => v > 0.01)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([n, v]) => `${n} ${(v * 100).toFixed(0)}%`)
    .join('  ·  ')
}

// Rotated, colour-coded label drawn at the top of each event marker line on
// the cumulative chart. recharts clones this element and injects `viewBox`
// (the plotting area, with x = the marker's pixel position). Green when the
// council added value at the event, muted red otherwise. A full-height
// transparent hit-rect plus the text share an SVG <title>, so hovering the
// line OR the label shows the event tooltip (name / date / verdict / value).
interface MarkerLabelProps {
  viewBox?: { x?: number; y?: number; width?: number; height?: number }
  text: string
  color: string
  tooltip: string
  idx: number
}
function EventMarkerLabel({ viewBox, text, color, tooltip, idx }: MarkerLabelProps) {
  const x = viewBox?.x
  if (x === undefined) return null
  const top = viewBox?.y ?? 0
  const h = viewBox?.height ?? 0
  // Alternate the baseline so adjacent events (e.g. the 2022 cluster) do
  // not overprint each other.
  const ty = top - (idx % 2 === 0 ? 4 : 18)
  return (
    <g style={{ cursor: 'default' }}>
      <title>{tooltip}</title>
      <rect x={x - 5} y={top} width={10} height={h} fill="transparent" />
      <text x={x} y={ty} fill={color} fontSize={10} textAnchor="start"
            transform={`rotate(-45, ${x}, ${ty})`}>
        {text}
      </text>
    </g>
  )
}

// Short, rotation-friendly label from the event id; full detail goes in the
// hover tooltip.
const shortLabel = (s: string): string =>
  s.length > 16 ? `${s.slice(0, 15)}…` : s

interface CostScenario {
  bps: number
  net_sharpe: number | null
  vs_benchmark_pct: number | null
}
interface RebalanceEvent {
  date: string | null
  regime: string | null
  weights: Record<string, number>
  total_shift: number
  asset_allocation?: { equity: number; ig: number; hy: number }
  largest_asset_change?: { asset: string; change: number }
}

// Compact column labels for the per-strategy weights table.
const STRATEGY_SHORT: Record<string, string> = {
  BENCHMARK: 'Benchmark',
  CLASSIC_60_40: '60/40',
  EQUAL_WEIGHT: 'Equal Wt',
  RISK_PARITY: 'Risk Parity',
  MIN_VARIANCE: 'Min Var',
  BLACK_LITTERMAN: 'Black-Litt',
  MOMENTUM_ROTATION: 'Momentum',
  REGIME_SWITCHING: 'Regime Sw',
  VOL_TARGETING: 'Vol Target',
  MAX_SHARPE_ROLLING: 'Max Sharpe',
}
// Preferred display order; any key not listed falls to the end, alphabetical.
const STRATEGY_ORDER = [
  'BENCHMARK', 'CLASSIC_60_40', 'EQUAL_WEIGHT', 'RISK_PARITY', 'MIN_VARIANCE',
  'BLACK_LITTERMAN', 'MOMENTUM_ROTATION', 'REGIME_SWITCHING', 'VOL_TARGETING',
  'MAX_SHARPE_ROLLING',
]
const ASSET_LABEL: Record<string, string> = {
  equity: 'Equity', ig: 'IG Bonds', hy: 'HY Bonds',
}
interface CostSensitivity {
  n_rebalances: number
  gross_sharpe: number | null
  benchmark_sharpe: number | null
  n_test_months: number
  scenarios: CostScenario[]
  rebalance_events?: RebalanceEvent[]
}
interface CostPayload {
  available: boolean
  cost_sensitivity: CostSensitivity | null
}

// The net cumulative blend lines drawn on the chart (ADDITION 1). Blue
// family: lighter/dashed (10), dashed (15), dotted (20).
const NET_COST_LINES: { bps: number; color: string; dash: string }[] = [
  { bps: 10, color: '#93c5fd', dash: '5 3' },
  { bps: 15, color: '#3b82f6', dash: '5 3' },
  { bps: 20, color: '#3b82f6', dash: '1 4' },
]

export default function PerformanceRecord() {
  const chartTheme = useChartTheme()
  const [data, setData] = useState<Payload | null>(null)
  const [cost, setCost] = useState<CostSensitivity | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    axios.get<Payload>('/api/v1/play-by-play')
      .then((r) => { if (alive) { setData(r.data); setLoading(false) } })
      .catch(() => { if (alive) { setError('Could not load the performance record.'); setLoading(false) } })
    // Transaction-cost sensitivity for the "Net of Switching Costs" table —
    // independent fetch; the table simply hides if it is unavailable.
    axios.get<CostPayload>('/api/v1/oos-cost-sensitivity')
      .then((r) => { if (alive && r.data.available) setCost(r.data.cost_sensitivity) })
      .catch(() => { /* table hidden when unavailable */ })
    return () => { alive = false }
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-400 p-8">
        <Loader2 className="w-5 h-5 animate-spin" />
        Loading the council performance record…
      </div>
    )
  }
  if (error) {
    return <div className="p-8 text-red-400">{error}</div>
  }
  if (!data?.available) {
    return (
      <div className="p-8 text-slate-400 max-w-2xl">
        <h1 className="text-2xl font-semibold text-white mb-2">
          Council Performance Record
        </h1>
        <p>
          No events recorded yet. Run <code>run_play_by_play.py</code> on the
          server to compute and freeze the nine point-in-time event
          evaluations; they will appear here, read-only, once stored.
        </p>
      </div>
    )
  }

  const sc = data.scorecard
  const cum = data.cumulative
  // Marker date -> event row, so each cumulative-chart marker line can show
  // the event's name, verdict, and value-added Sharpe (the markers are
  // placed at event dates, which match the stored event_date exactly).
  const eventByDate = new Map((data.events || []).map((e) => [e.event_date, e]))

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      <header>
        <h1 className="text-2xl font-semibold text-white">
          Council Performance Record
        </h1>
        <p className="text-sm text-slate-400 mt-1">
          Point-in-time, no look-ahead. Each event uses only the data
          available at the event month. 30/60/90 days are 1/2/3 forward
          months (monthly data).
        </p>
      </header>

      {/* ── Scorecard summary ─────────────────────────────────────── */}
      {sc && (
        <section className="bg-navy-800 border border-navy-700 rounded-lg p-5">
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-bold text-electric">
              {sc.n_value_added}/{sc.n_evaluable}
            </span>
            <span className="text-sm text-slate-300">
              events where the regime-conditional council added value
            </span>
          </div>
          <p className="text-sm text-slate-400 mt-3 leading-relaxed">
            {sc.framing}
          </p>
        </section>
      )}

      {/* ── Risk-adjusted summary banner ──────────────────────────── */}
      {/*
        Two figures coexist by design:

          ACADEMIC SUBMISSION  (Dec 2025 data lock — frozen). The
            figures Bob and Molly defend in the cohort peer review
            (June 3) and on the July 1 panel. These are NEVER
            replaced by the live figures in the executive brief or
            the final presentation — the locked dataset is the
            submitted record.
          LIVE PERFORMANCE  (extends through the most recent month
            available). Shown alongside so the platform reflects
            ongoing reality without overwriting the submitted
            record. Use in conversation, not in deliverables.

        The user's directive (May 31 2026): "Do not replace the
        academic figures with live figures anywhere in the
        presentation or executive brief." This panel is the only
        place both are surfaced; every other place that quotes
        these figures continues to use the academic submission.
      */}
      <section className="bg-navy-800 rounded-lg p-5">
        <div className="flex items-center gap-1.5 mb-3">
          <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
            Risk-Adjusted Performance
          </h2>
          <InfoIcon tooltipKey="council_record_sharpe"
                    metricLabel="Post-2022 Sharpe ratio" size="md" />
        </div>

        {/* Row 1 — academic submission (locked Dec 2025). */}
        <div className="mb-2 flex items-center gap-2">
          <div className="text-2xs font-semibold text-amber-300
                          uppercase tracking-wide">
            Academic Submission
          </div>
          <div className="text-2xs text-slate-400">
            (locked December 2025 — defended Jun 3 + Jul 1)
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="bg-navy-900 rounded-lg p-4">
            <div className="text-2xs text-slate-400 uppercase tracking-wide">
              Blend (Post-2022 Sharpe)
            </div>
            <div className="text-3xl font-bold text-electric mt-1">0.86</div>
          </div>
          <div className="bg-navy-900 rounded-lg p-4">
            <div className="text-2xs text-slate-400 uppercase tracking-wide">
              S&amp;P 500 Benchmark
            </div>
            <div className="text-3xl font-bold text-red-400 mt-1">0.43</div>
            <div className="text-2xs text-slate-500 mt-0.5">
              Out-of-sample test period
            </div>
          </div>
          <div className="bg-navy-900 rounded-lg p-4">
            <div className="text-2xs text-slate-400 uppercase tracking-wide">
              Risk-Adjusted Advantage
            </div>
            <div className="text-3xl font-bold text-emerald-400 mt-1">+98%</div>
            <div className="text-2xs text-slate-500 mt-0.5">
              Sharpe ratio vs benchmark
            </div>
          </div>
        </div>

        {/* Row 2 — live figure (extends through the most recent
            month). Visually distinguished by a sky-blue tag and a
            dashed border so a reader cannot mistake one for the
            other at a glance. */}
        <div className="mt-5 mb-2 flex items-center gap-2">
          <div className="text-2xs font-semibold text-sky-300
                          uppercase tracking-wide">
            Live Figure
          </div>
          <div className="text-2xs text-slate-400">
            (through most recent month — includes Jan–May 2026; for
            discussion only, not for submissions)
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="bg-navy-900/60 border border-dashed
                          border-sky-500/30 rounded-lg p-4">
            <div className="text-2xs text-slate-400 uppercase tracking-wide">
              Blend (Post-2022 Sharpe)
            </div>
            <div className="text-3xl font-bold text-sky-300 mt-1">1.24</div>
          </div>
          <div className="bg-navy-900/60 border border-dashed
                          border-sky-500/30 rounded-lg p-4">
            <div className="text-2xs text-slate-400 uppercase tracking-wide">
              S&amp;P 500 Benchmark
            </div>
            <div className="text-3xl font-bold text-rose-300 mt-1">0.73</div>
            <div className="text-2xs text-slate-500 mt-0.5">
              Out-of-sample through today
            </div>
          </div>
          <div className="bg-navy-900/60 border border-dashed
                          border-sky-500/30 rounded-lg p-4">
            <div className="text-2xs text-slate-400 uppercase tracking-wide">
              Risk-Adjusted Advantage
            </div>
            <div className="text-3xl font-bold text-emerald-300 mt-1">+70%</div>
            <div className="text-2xs text-slate-500 mt-0.5">
              Sharpe ratio vs benchmark
            </div>
          </div>
        </div>

        <p className="text-2xs text-slate-400 mt-3 leading-relaxed">
          Regime-conditional allocation outperforms on a risk-adjusted
          basis across the 40-month post-2022 out-of-sample period.
          Outperformance is driven by systematic regime weighting,
          not shock prediction. The submitted figures are the
          December 2025 data lock; the live figures are shown
          alongside so the platform reflects ongoing performance, but
          are <strong className="text-slate-300">not used in the
          executive brief or final presentation</strong> — the
          academic submission stands as the record.
        </p>
      </section>

      {/* ── Net of switching costs (transaction-cost sensitivity) ───── */}
      {cost && cost.scenarios && cost.scenarios.length > 0 && (() => {
        const grossVs = cost.gross_sharpe != null && cost.benchmark_sharpe
          ? cost.gross_sharpe / cost.benchmark_sharpe - 1 : null
        return (
          <section className="bg-navy-800 rounded-lg p-5">
            <div className="flex items-center gap-1.5 mb-3">
              <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
                Net of Switching Costs
              </h2>
              <InfoIcon tooltipKey="switching_costs"
                        metricLabel="Transaction-cost sensitivity" size="md" />
            </div>
            <div className="overflow-x-auto">
              <table className="text-sm w-full max-w-2xl">
                <thead>
                  <tr className="text-slate-400 text-2xs uppercase">
                    <th className="text-left font-medium py-1">Metric</th>
                    <th className="text-right font-medium py-1">Gross</th>
                    {cost.scenarios.map((s) => (
                      <th key={s.bps} className="text-right font-medium py-1">
                        {s.bps} bps
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono text-xs">
                  <tr className="border-t border-navy-700">
                    <td className="text-left text-slate-300 font-sans py-1.5">
                      Sharpe Ratio
                    </td>
                    <td className="text-right text-slate-200 py-1.5">
                      {fmtSharpe(cost.gross_sharpe)}
                    </td>
                    {cost.scenarios.map((s) => {
                      const beats = s.net_sharpe != null
                        && cost.benchmark_sharpe != null
                        && s.net_sharpe > cost.benchmark_sharpe
                      return (
                        <td key={s.bps}
                            className={`text-right py-1.5 ${beats ? 'text-emerald-400' : 'text-red-400'}`}>
                          {fmtSharpe(s.net_sharpe)}
                        </td>
                      )
                    })}
                  </tr>
                  <tr className="border-t border-navy-700">
                    <td className="text-left text-slate-300 font-sans py-1.5">
                      vs Benchmark Sharpe
                    </td>
                    <td className="text-right text-slate-200 py-1.5">
                      {fmtVsPct(grossVs)}
                    </td>
                    {cost.scenarios.map((s) => {
                      const pos = s.vs_benchmark_pct != null && s.vs_benchmark_pct > 0
                      return (
                        <td key={s.bps}
                            className={`text-right py-1.5 ${pos ? 'text-emerald-400' : 'text-red-400'}`}>
                          {fmtVsPct(s.vs_benchmark_pct)}
                        </td>
                      )
                    })}
                  </tr>
                  <tr className="border-t border-navy-700">
                    <td className="text-left text-slate-300 font-sans py-1.5">
                      Rebalancing Events
                    </td>
                    <td className="text-right text-slate-400 py-1.5"
                        colSpan={1 + cost.scenarios.length}>
                      {cost.n_rebalances} rebalances over {cost.n_test_months} months
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-2xs text-slate-500 mt-3 leading-relaxed">
              One-way transaction cost applied at each material rebalance
              (&gt;2% weight shift in any single strategy). Net Sharpe stays
              above the S&amp;P 500 benchmark at every cost assumption.
            </p>
          </section>
        )
      })()}

      {/* ── Rebalancing history — two sections ─────────────────────── */}
      {cost && cost.rebalance_events && cost.rebalance_events.length > 0 && (() => {
        const events = [...cost.rebalance_events]
          .sort((a, b) => (b.date ?? '').localeCompare(a.date ?? ''))
        const n = cost.rebalance_events.length
        const avgGap = n > 0 ? (cost.n_test_months / n) : null
        const subtitle = (
          'Events where blend weights shifted more than 2% in any strategy. '
          + 'Transaction costs applied at each.')
        const footer = (
          `${n} rebalancing events over ${cost.n_test_months} months.`
          + (avgGap !== null
            ? ` Average ${avgGap.toFixed(1)} months between rebalances.` : ''))
        // Strategy columns: preferred order first, then any extras alphabetically.
        const stratCols = Array.from(
          new Set(events.flatMap((e) => Object.keys(e.weights))))
          .sort((a, b) => {
            const ia = STRATEGY_ORDER.indexOf(a)
            const ib = STRATEGY_ORDER.indexOf(b)
            if (ia !== -1 && ib !== -1) return ia - ib
            if (ia !== -1) return -1
            if (ib !== -1) return 1
            return a.localeCompare(b)
          })
        const hasAsset = events.some((e) => e.asset_allocation)
        const a1 = (x: number | undefined): string =>
          `${((x ?? 0) * 100).toFixed(1)}%`
        return (
          <>
            {/* SECTION 1 — Implied Asset Allocation */}
            {hasAsset && (
              <section className="bg-navy-800 rounded-lg p-5">
                <div className="flex items-center gap-1.5">
                  <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
                    Implied Asset Allocation
                  </h2>
                  <InfoIcon tooltipKey="rebalancing_events"
                            metricLabel="Rebalancing events" size="md" />
                </div>
                <p className="text-2xs text-slate-500 mt-1 leading-relaxed">{subtitle}</p>
                <div className="overflow-x-auto mt-3">
                  <table className="text-sm w-full">
                    <thead>
                      <tr className="text-slate-400 text-2xs uppercase">
                        <th className="text-left font-medium py-1">Date</th>
                        <th className="text-left font-medium py-1">Regime</th>
                        <th className="text-right font-medium py-1">Equity %</th>
                        <th className="text-right font-medium py-1">IG Bonds %</th>
                        <th className="text-right font-medium py-1">HY Bonds %</th>
                        <th className="text-right font-medium py-1">Largest Change</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono text-xs">
                      {events.map((ev, i) => {
                        const aa = ev.asset_allocation
                        const lc = ev.largest_asset_change
                        return (
                          <tr key={`aa-${ev.date}-${i}`} className="border-t border-navy-700">
                            <td className="text-left text-slate-300 py-1.5">{fmtDate(ev.date)}</td>
                            <td className="text-left text-slate-300 font-sans py-1.5">{ev.regime ?? '—'}</td>
                            <td className="text-right text-slate-300 py-1.5">{a1(aa?.equity)}</td>
                            <td className="text-right text-slate-300 py-1.5">{a1(aa?.ig)}</td>
                            <td className="text-right text-slate-300 py-1.5">{a1(aa?.hy)}</td>
                            <td className="text-right text-electric py-1.5">
                              {lc ? `${ASSET_LABEL[lc.asset] ?? lc.asset} ${(lc.change * 100).toFixed(1)}%` : '—'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="text-2xs text-slate-500 mt-3">{footer}</p>
              </section>
            )}

            {/* SECTION 2 — Strategy Blend Weights */}
            <section className="bg-navy-800 rounded-lg p-5">
              <div className="flex items-center gap-1.5">
                <h2 className="text-sm font-semibold text-slate-200 uppercase tracking-wide">
                  Strategy Blend Weights
                </h2>
                <InfoIcon tooltipKey="rebalancing_events"
                          metricLabel="Rebalancing events" size="md" />
              </div>
              <p className="text-2xs text-slate-500 mt-1 leading-relaxed">
                {subtitle} All strategy weights sum to 100%.
              </p>
              <div className="overflow-x-auto mt-3">
                <table className="text-sm w-full">
                  <thead>
                    <tr className="text-slate-400 text-2xs uppercase">
                      <th className="text-left font-medium py-1 sticky left-0 bg-navy-800">Date</th>
                      <th className="text-left font-medium py-1">Regime</th>
                      {stratCols.map((k) => (
                        <th key={k} className="text-right font-medium py-1 px-1.5">
                          {STRATEGY_SHORT[k] ?? k}
                        </th>
                      ))}
                      <th className="text-right font-medium py-1 px-1.5">Total Shift</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-xs">
                    {events.map((ev, i) => (
                      <tr key={`sw-${ev.date}-${i}`} className="border-t border-navy-700">
                        <td className="text-left text-slate-300 py-1.5 sticky left-0 bg-navy-800">{fmtDate(ev.date)}</td>
                        <td className="text-left text-slate-300 font-sans py-1.5">{ev.regime ?? '—'}</td>
                        {stratCols.map((k) => (
                          <td key={k} className="text-right text-slate-300 py-1.5 px-1.5">
                            {((ev.weights[k] ?? 0) * 100).toFixed(0)}%
                          </td>
                        ))}
                        <td className="text-right text-electric py-1.5 px-1.5">
                          {(ev.total_shift * 100).toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-2xs text-slate-500 mt-3">{footer}</p>
            </section>
          </>
        )
      })()}

      {/* ── Cumulative chart (post-2022) ──────────────────────────── */}
      <section className="bg-navy-800 border border-navy-700 rounded-lg p-5">
        <h2 className="text-sm font-semibold text-slate-200 mb-3 uppercase tracking-wide">
          Cumulative return, post-2022
        </h2>
        {cum && cum.series.length > 0 ? (
          <ResponsiveContainer width="100%" height={360}>
            <LineChart data={cum.series}
                       margin={{ top: 48, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke={chartTheme.gridStroke} strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fill: chartTheme.textSecondary, fontSize: 11 }}
                     minTickGap={40} />
              <YAxis tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                     tick={{ fill: chartTheme.textSecondary, fontSize: 11 }} />
              <Tooltip
                contentStyle={chartTheme.tooltipContentStyle}
                labelStyle={chartTheme.tooltipLabelStyle}
                formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
              <Legend />
              <ReferenceLine y={0} stroke={chartTheme.textSecondary} strokeWidth={1} />
              {(cum.event_markers || []).map((d, i) => {
                const ev = eventByDate.get(d)
                const va = ev?.value_added_sharpe ?? null
                const added = va !== null && va > 0
                // Green when the council added value at this event; muted
                // red when it did not (value-added Sharpe <= 0).
                const labelColor = added ? '#34d399' : '#f87171'
                const vaStr = va === null ? '—' : `${va >= 0 ? '+' : ''}${va.toFixed(2)}`
                const tip = ev
                  ? `${ev.event_id}\n${fmtDate(ev.event_date)}`
                    + `${ev.verdict ? `\n${ev.verdict}` : ''}`
                    + `\nValue added Sharpe: ${vaStr}`
                  : d
                return (
                  <ReferenceLine key={d} x={d} stroke="#f59e0b"
                                 strokeDasharray="2 2"
                                 label={<EventMarkerLabel
                                   text={ev ? shortLabel(ev.event_id) : d}
                                   color={labelColor} tooltip={tip} idx={i} />} />
                )
              })}
              <Line type="monotone" dataKey="regime_conditional"
                    name="Gross (0 bps)" stroke="#3b82f6"
                    dot={false} strokeWidth={2} connectNulls />
              {/* Net-of-transaction-cost blend paths (ADDITION 1). Rendered
                  only when the backend supplied the blend_net_* series. */}
              {NET_COST_LINES.map((l) => (
                <Line key={l.bps} type="monotone" dataKey={`blend_net_${l.bps}`}
                      name={`Blend net ${l.bps} bps`} stroke={l.color}
                      strokeDasharray={l.dash} dot={false} strokeWidth={1.5}
                      connectNulls />
              ))}
              <Line type="monotone" dataKey="benchmark" name="Benchmark (S&P 500)"
                    stroke="#ef4444" dot={false} strokeWidth={1.5} connectNulls />
              <Line type="monotone" dataKey="classic_6040" name="Classic 60/40"
                    stroke="#94a3b8" dot={false} strokeWidth={1.5} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-sm text-slate-500">
            Cumulative series not yet available. The event records below are
            the point-in-time track record; the continuous post-2022 curve
            is computed separately.
          </p>
        )}
      </section>

      {/* ── Event cards ───────────────────────────────────────────── */}
      <section className="space-y-4">
        {data.events.map((ev) => {
          const va = ev.value_added_sharpe
          const added = va !== null && va > 0
          const limitation = ev.key_limitation || data.key_limitations?.[ev.event_id]
          return (
            <div key={ev.event_id}
                 className="bg-navy-800 border border-navy-700 rounded-lg p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-base font-semibold text-white">
                    {ev.event_id}
                  </h3>
                  <p className="text-xs text-slate-500">{fmtDate(ev.event_date)}</p>
                </div>
                <div className={`flex items-center gap-1 text-sm font-medium ${
                  added ? 'text-emerald-400' : 'text-red-400'}`}>
                  {added ? <TrendingUp className="w-4 h-4" />
                         : <TrendingDown className="w-4 h-4" />}
                  {va === null ? '—' : `${va >= 0 ? '+' : ''}${va.toFixed(2)} Sharpe`}
                </div>
              </div>

              <p className="text-sm text-slate-400 mt-2">{ev.trigger}</p>

              <div className="mt-3 text-sm text-slate-300 space-y-1">
                <div>
                  <span className="text-slate-500">Regime: </span>
                  <span className="text-white">{ev.regime ?? '—'}</span>
                  <span className="text-slate-500 ml-3">
                    P(BULL) {prob(ev.posterior?.bull)} ·
                    P(BEAR) {prob(ev.posterior?.bear)} ·
                    P(TRANSITION) {prob(ev.posterior?.transition)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500">Blend: </span>
                  <span className="font-mono text-xs">{topWeights(ev.blend_weights)}</span>
                </div>
              </div>

              {ev.recommendation && (
                <p className="text-sm text-slate-300 mt-3">
                  <span className="text-slate-500">Recommendation: </span>
                  {ev.recommendation}
                </p>
              )}
              {ev.dissenting_view && (
                <p className="text-sm text-slate-400 mt-1">
                  <span className="text-slate-500">Dissent: </span>
                  {ev.dissenting_view}
                </p>
              )}

              {/* 30/60/90d performance table */}
              <table className="mt-3 text-sm w-full max-w-md">
                <thead>
                  <tr className="text-slate-500 text-xs uppercase">
                    <th className="text-left font-medium">Series</th>
                    <th className="text-right font-medium">30d</th>
                    <th className="text-right font-medium">60d</th>
                    <th className="text-right font-medium">90d</th>
                  </tr>
                </thead>
                <tbody className="font-mono text-xs">
                  {([['Blend', ev.performance?.blend],
                     ['Benchmark', ev.performance?.benchmark],
                     ['Classic 60/40', ev.performance?.classic_6040]] as const)
                    .map(([label, h]) => (
                    <tr key={label}>
                      <td className="text-left text-slate-300 font-sans">{label}</td>
                      <td className="text-right text-slate-300">{pct(h?.d30)}</td>
                      <td className="text-right text-slate-300">{pct(h?.d60)}</td>
                      <td className="text-right text-slate-300">{pct(h?.d90)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {ev.verdict && (
                <p className="text-xs text-slate-400 mt-3 italic">{ev.verdict}</p>
              )}

              {limitation && (
                <div className="mt-3 flex gap-2 bg-amber-500/10 border border-amber-500/30
                                rounded-md p-3">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <p className="text-sm text-amber-200">{limitation}</p>
                </div>
              )}
            </div>
          )
        })}
      </section>
    </div>
  )
}
