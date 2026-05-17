/**
 * AcademicAnalytics — the analytics layer that backs the midpoint paper.
 *
 * Five components, all driven by one bundled call to
 * GET /api/v1/analytics/academic (no pipeline recompute — the endpoint
 * reads market_data_monthly, strategy_results_cache and ff_factors_monthly):
 *   1. Summary statistics table (equity / IG / HY / BENCHMARK)
 *   2. 12-month rolling correlation chart with the 2022 regime-break marker
 *   3. Regime-conditional performance table (pre/post-2022 split)
 *   4. Drawdown comparison table
 *   5. Fama-French factor loadings table
 *
 * The turnover column lives on the Dashboard strategy table, not here.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import TableExportButton from '../components/TableExportButton'

// Purple accent — analytics sits alongside the academic-rigour screens.
const ACCENT = '#7c3aed'

// ── Types (mirror the /api/v1/analytics/academic payload) ─────────────────────

interface SummaryRow {
  asset: string
  cagr: number
  excess_return: number | null
  ann_volatility: number
  sharpe_ratio: number
  information_ratio: number | null
  max_drawdown: number
  skewness: number
  n_months: number
}

type RollingExcessPoint = { date: string } & Record<string, number | null>

interface RollingExcess {
  strategies: string[]
  points: RollingExcessPoint[]
  window_months: number
}

// Sensitivity analysis comes from its own endpoint (/api/v1/analytics/
// sensitivity), not the bundled /academic payload — it is a ~23-backtest
// compute that must not run on every analytics page load.
interface SensitivityPoint {
  value: number
  sharpe: number | null
}

interface SensitivityStrategy {
  strategy: string
  parameter: string
  current_value: number
  points: SensitivityPoint[]
}

interface SensitivityPayload {
  available: boolean
  strategies: SensitivityStrategy[]
}

interface CorrPoint {
  date: string
  equity_ig: number | null
  equity_hy: number | null
}

interface RollingCorrelation {
  window_months: number
  regime_break: string
  points: CorrPoint[]
  pre_2022: { equity_ig: number | null; equity_hy: number | null }
  post_2022: { equity_ig: number | null; equity_hy: number | null }
}

interface RegimeRow {
  strategy: string
  pre_2022_sharpe: number | null
  post_2022_sharpe: number | null
  pre_2022_cagr: number | null
  post_2022_cagr: number | null
  pre_2022_months: number
  post_2022_months: number
}

interface DrawdownRow {
  strategy: string
  max_drawdown: number
  recovery_months: number | null
}

interface FactorRow {
  strategy: string
  alpha_annualized: number
  alpha_significant: boolean
  mkt_rf: number
  mkt_rf_significant: boolean
  smb: number
  smb_significant: boolean
  hml: number
  hml_significant: boolean
  r_squared: number
  n_months: number
}

type CumulativePoint = { date: string } & Record<string, number | null>

interface CumulativeReturns {
  strategies: string[]
  points: CumulativePoint[]
}

interface AnalyticsPayload {
  available: boolean
  note?: string
  study_period?: { start: string; end: string; n_months: number }
  cumulative_returns?: CumulativeReturns
  summary_statistics?: SummaryRow[]
  rolling_correlation?: RollingCorrelation
  rolling_excess_return?: RollingExcess
  regime_conditional?: RegimeRow[]
  drawdown_comparison?: DrawdownRow[]
  factor_loadings?: FactorRow[]
}

// Distinct line colours assigned by index; the benchmark is rendered as a
// bold light-grey reference line (detected by name).
const SERIES_COLORS = [
  '#3b82f6', '#22c55e', '#f59e0b', '#a78bfa', '#06b6d4',
  '#ec4899', '#84cc16', '#f97316', '#14b8a6', '#ef4444',
]
const BENCHMARK_COLOR = '#e5e7eb'
const isBenchmark = (name: string): boolean => /benchmark/i.test(name)

// ── Formatting helpers ────────────────────────────────────────────────────────

const pct = (x: number | null | undefined): string =>
  x == null ? '—' : `${(x * 100).toFixed(2)}%`
const num = (x: number | null | undefined, dp = 2): string =>
  x == null ? '—' : x.toFixed(dp)

// Percentage with green/red sign colouring — used for excess return.
function SignedPct({ x }: { x: number | null | undefined }) {
  if (x == null) return <>—</>
  const cls = x > 0 ? 'text-success' : x < 0 ? 'text-danger' : 'text-muted'
  return <span className={cls}>{`${(x * 100).toFixed(2)}%`}</span>
}

// ── Shared table chrome ───────────────────────────────────────────────────────

function SectionCard({
  title, subtitle, exportButton, children,
}: {
  title: string
  subtitle: string
  exportButton?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="card p-5" style={{ borderLeft: `3px solid ${ACCENT}` }}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-white">{title}</h2>
          <p className="text-xs text-muted mt-0.5">{subtitle}</p>
        </div>
        {exportButton}
      </div>
      {children}
    </div>
  )
}

const TH = ({ children, right = false }: { children: React.ReactNode; right?: boolean }) => (
  <th className={`px-3 py-2 text-xs font-medium uppercase tracking-wider text-muted
                  ${right ? 'text-right' : 'text-left'}`}>
    {children}
  </th>
)
const TD = ({ children, right = false, mono = false }:
  { children: React.ReactNode; right?: boolean; mono?: boolean }) => (
  <td className={`px-3 py-2 text-sm text-white ${right ? 'text-right' : 'text-left'}
                  ${mono ? 'font-mono' : ''}`}>
    {children}
  </td>
)

// ── 1. Summary statistics table ───────────────────────────────────────────────

function SummaryStatisticsTable({ rows }: { rows: SummaryRow[] }) {
  const headers = ['Asset', 'CAGR', 'Excess Return (ann.)', 'Ann. Volatility',
                   'Sharpe', 'Information Ratio', 'Max Drawdown', 'Skewness']
  const exportRows = rows.map((r) => [
    r.asset, pct(r.cagr), pct(r.excess_return), pct(r.ann_volatility),
    num(r.sharpe_ratio), num(r.information_ratio), pct(r.max_drawdown),
    num(r.skewness),
  ])
  return (
    <SectionCard
      title="Summary Statistics"
      subtitle="Full study period — equity, investment-grade bonds, high-yield bonds, and the benchmark. Excess return is annualised CAGR minus the benchmark CAGR; information ratio is excess return over tracking error."
      exportButton={<TableExportButton tableId="summary_statistics" headers={headers} rows={exportRows} />}
    >
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH>Asset</TH><TH right>CAGR</TH><TH right>Excess Return (ann.)</TH>
          <TH right>Ann. Volatility</TH><TH right>Sharpe</TH>
          <TH right>Information Ratio</TH><TH right>Max Drawdown</TH><TH right>Skewness</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.asset} className="border-b border-border/50">
              <TD>{r.asset}</TD>
              <TD right mono>{pct(r.cagr)}</TD>
              <TD right mono><SignedPct x={r.excess_return} /></TD>
              <TD right mono>{pct(r.ann_volatility)}</TD>
              <TD right mono>{num(r.sharpe_ratio)}</TD>
              <TD right mono>{r.information_ratio == null ? 'N/A' : num(r.information_ratio)}</TD>
              <TD right mono>{pct(r.max_drawdown)}</TD>
              <TD right mono>{num(r.skewness)}</TD>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  )
}

// ── Rolling excess return chart ───────────────────────────────────────────────

function RollingExcessReturnChart({ data }: { data: RollingExcess }) {
  const breakX = data.points.find((p) => p.date >= '2022-01-01')?.date
  // Numeric bounds for the above/below-zero shading.
  const vals = data.points.flatMap((p) =>
    data.strategies.map((s) => p[s]).filter((v): v is number => v != null))
  const ymax = vals.length ? Math.max(0, ...vals) : 0
  const ymin = vals.length ? Math.min(0, ...vals) : 0

  const headers = ['Date', ...data.strategies]
  const exportRows = data.points.map((p) => [
    p.date, ...data.strategies.map((s) => p[s] ?? ''),
  ])

  return (
    <SectionCard
      title="Rolling Excess Return vs Benchmark"
      subtitle={`${data.window_months}-month rolling total return of each strategy minus the 100% equity benchmark. Above zero is outperformance, below zero is underperformance.`}
      exportButton={<TableExportButton tableId="rolling_excess_return" headers={headers} rows={exportRows} />}
    >
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data.points} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} minTickGap={56} />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{ background: '#1a2438', border: '1px solid #1e3a5c',
                            borderRadius: 8, fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {/* Outperformance / underperformance half-plane shading. */}
          <ReferenceArea y1={0} y2={ymax} fill="#10b981" fillOpacity={0.05} />
          <ReferenceArea y1={ymin} y2={0} fill="#ef4444" fillOpacity={0.05} />
          <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={1.5} />
          {breakX && (
            <ReferenceLine x={breakX} stroke={ACCENT} strokeDasharray="4 4"
              label={{ value: 'Correlation Regime Break', fill: ACCENT, fontSize: 11,
                       position: 'insideTopRight' }} />
          )}
          {data.strategies.map((s, i) => (
            <Line key={s} type="monotone" dataKey={s} name={s}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  strokeWidth={1.5} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </SectionCard>
  )
}

// ── 2. Rolling correlation chart ──────────────────────────────────────────────

function RollingCorrelationChart({ data }: { data: RollingCorrelation }) {
  // The vertical regime marker must land on an actual x value — snap it to
  // the first plotted month at or after the 2022 break.
  const breakX = data.points.find((p) => p.date >= data.regime_break)?.date
    ?? data.regime_break
  const avg = (x: number | null) => (x == null ? '—' : x.toFixed(2))

  return (
    <SectionCard
      title="Rolling Correlation — Equity vs Bonds"
      subtitle={`${data.window_months}-month rolling correlation. The 2022 hiking cycle is where equity-bond diversification broke down.`}
    >
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data.points} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }}
                 minTickGap={48} />
          <YAxis domain={[-1, 1]} tick={{ fill: '#64748b', fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: '#1a2438', border: '1px solid #1e3a5c',
                            borderRadius: 8, fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <ReferenceLine y={0} stroke="#475569" />
          <ReferenceLine x={breakX} stroke={ACCENT} strokeDasharray="4 4"
            label={{ value: 'Correlation Regime Break', fill: ACCENT, fontSize: 11,
                     position: 'insideTopRight' }} />
          <Line type="monotone" dataKey="equity_ig" name="Equity vs IG"
                stroke="#3b82f6" dot={false} strokeWidth={2} connectNulls />
          <Line type="monotone" dataKey="equity_hy" name="Equity vs HY"
                stroke="#f59e0b" dot={false} strokeWidth={2} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      <div className="grid grid-cols-2 gap-3 mt-3 text-xs">
        <div className="bg-navy-800 rounded p-2.5">
          <div className="text-muted uppercase tracking-wider mb-1">Equity vs IG</div>
          <div className="text-white font-mono">
            Pre-2022 avg <span className="text-electric">{avg(data.pre_2022.equity_ig)}</span>
            {'   '}·{'   '}
            Post-2022 avg <span className="text-warning">{avg(data.post_2022.equity_ig)}</span>
          </div>
        </div>
        <div className="bg-navy-800 rounded p-2.5">
          <div className="text-muted uppercase tracking-wider mb-1">Equity vs HY</div>
          <div className="text-white font-mono">
            Pre-2022 avg <span className="text-electric">{avg(data.pre_2022.equity_hy)}</span>
            {'   '}·{'   '}
            Post-2022 avg <span className="text-warning">{avg(data.post_2022.equity_hy)}</span>
          </div>
        </div>
      </div>
    </SectionCard>
  )
}

// ── 3. Regime-conditional performance table ───────────────────────────────────

function RegimeConditionalTable({ rows }: { rows: RegimeRow[] }) {
  const headers = ['Strategy', 'Pre-2022 Sharpe', 'Post-2022 Sharpe', 'Pre-2022 CAGR', 'Post-2022 CAGR']
  const exportRows = rows.map((r) => [
    r.strategy, num(r.pre_2022_sharpe), num(r.post_2022_sharpe),
    pct(r.pre_2022_cagr), pct(r.post_2022_cagr),
  ])
  return (
    <SectionCard
      title="Regime-Conditional Performance"
      subtitle="Each strategy split at the 2022 break. Sorted by post-2022 Sharpe — which strategies held up once diversification stopped working."
      exportButton={<TableExportButton tableId="regime_conditional" headers={headers} rows={exportRows} />}
    >
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH>Strategy</TH><TH right>Pre-2022 Sharpe</TH><TH right>Post-2022 Sharpe</TH>
          <TH right>Pre-2022 CAGR</TH><TH right>Post-2022 CAGR</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} className="border-b border-border/50">
              <TD>{r.strategy}</TD>
              <TD right mono>{num(r.pre_2022_sharpe)}</TD>
              <TD right mono>{num(r.post_2022_sharpe)}</TD>
              <TD right mono>{pct(r.pre_2022_cagr)}</TD>
              <TD right mono>{pct(r.post_2022_cagr)}</TD>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  )
}

// ── 4. Drawdown comparison table ──────────────────────────────────────────────

function DrawdownComparisonTable({ rows }: { rows: DrawdownRow[] }) {
  const headers = ['Strategy', 'Max Drawdown', 'Recovery (months)']
  const exportRows = rows.map((r) => [
    r.strategy, pct(r.max_drawdown),
    r.recovery_months == null ? 'not recovered' : r.recovery_months,
  ])
  return (
    <SectionCard
      title="Drawdown Comparison"
      subtitle="Max peak-to-trough loss and months to a new equity high. Sorted by max drawdown — deepest loss first."
      exportButton={<TableExportButton tableId="drawdown_comparison" headers={headers} rows={exportRows} />}
    >
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH>Strategy</TH><TH right>Max Drawdown</TH><TH right>Recovery (months)</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} className="border-b border-border/50">
              <TD>{r.strategy}</TD>
              <TD right mono>{pct(r.max_drawdown)}</TD>
              <TD right mono>
                {r.recovery_months == null
                  ? <span className="text-warning">not recovered</span>
                  : r.recovery_months}
              </TD>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  )
}

// ── 5. Fama-French factor loadings table ──────────────────────────────────────

function FactorLoadingsTable({ rows }: { rows: FactorRow[] }) {
  const headers = ['Strategy', 'Alpha (annualized)', 'MKT-RF', 'SMB', 'HML', 'R-squared']
  const exportRows = rows.map((r) => [
    r.strategy, pct(r.alpha_annualized), num(r.mkt_rf), num(r.smb), num(r.hml),
    num(r.r_squared),
  ])
  // A loading is rendered bold + with a * suffix when p < 0.05.
  const Beta = ({ v, sig }: { v: number; sig: boolean }) => (
    <span className={sig ? 'text-electric font-semibold' : ''}>
      {num(v)}{sig ? ' *' : ''}
    </span>
  )
  return (
    <SectionCard
      title="Fama-French Factor Loadings"
      subtitle="OLS regression of each strategy's monthly excess return on the three-factor model. * marks loadings significant at p < 0.05. Momentum is not in the dataset — this is a three-factor, not Carhart four-factor, regression."
      exportButton={<TableExportButton tableId="factor_loadings" headers={headers} rows={exportRows} />}
    >
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH>Strategy</TH><TH right>Alpha (annualized)</TH><TH right>MKT-RF</TH>
          <TH right>SMB</TH><TH right>HML</TH><TH right>R²</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} className="border-b border-border/50">
              <TD>{r.strategy}</TD>
              <TD right mono>
                <span className={r.alpha_significant ? 'text-electric font-semibold' : ''}>
                  {pct(r.alpha_annualized)}{r.alpha_significant ? ' *' : ''}
                </span>
              </TD>
              <TD right mono><Beta v={r.mkt_rf} sig={r.mkt_rf_significant} /></TD>
              <TD right mono><Beta v={r.smb} sig={r.smb_significant} /></TD>
              <TD right mono><Beta v={r.hml} sig={r.hml_significant} /></TD>
              <TD right mono>{num(r.r_squared)}</TD>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

// ── Cumulative total return chart ─────────────────────────────────────────────

function CumulativeReturnChart({ data }: { data: CumulativeReturns }) {
  const [logScale, setLogScale] = useState(false)

  // Snap the 2022 regime marker to the first plotted month at/after the break.
  const breakX = data.points.find((p) => p.date >= '2022-01-01')?.date

  const headers = ['Date', ...data.strategies]
  const exportRows = data.points.map((p) => [
    p.date, ...data.strategies.map((s) => p[s] ?? ''),
  ])

  return (
    <SectionCard
      title="Cumulative Total Return"
      subtitle="Growth of $1 invested in each strategy over the full study period. The benchmark (100% equity) is the bold grey reference line."
      exportButton={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setLogScale((v) => !v)}
            className="text-xs px-2 py-1 rounded border border-border text-muted
                       hover:text-white transition-colors"
          >
            {logScale ? 'Linear scale' : 'Log scale'}
          </button>
          <TableExportButton tableId="cumulative_returns" headers={headers} rows={exportRows} />
        </div>
      }
    >
      <ResponsiveContainer width="100%" height={340}>
        <LineChart data={data.points} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} minTickGap={56} />
          <YAxis
            scale={logScale ? 'log' : 'linear'}
            domain={logScale ? ['auto', 'auto'] : [0, 'auto']}
            allowDataOverflow
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickFormatter={(v: number) => `${v.toFixed(1)}x`}
          />
          <Tooltip
            contentStyle={{ background: '#1a2438', border: '1px solid #1e3a5c',
                            borderRadius: 8, fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {breakX && (
            <ReferenceLine x={breakX} stroke={ACCENT} strokeDasharray="4 4"
              label={{ value: 'Correlation Regime Break', fill: ACCENT, fontSize: 11,
                       position: 'insideTopRight' }} />
          )}
          {data.strategies.map((s, i) => {
            const bench = isBenchmark(s)
            return (
              <Line
                key={s}
                type="monotone"
                dataKey={s}
                name={s}
                stroke={bench ? BENCHMARK_COLOR : SERIES_COLORS[i % SERIES_COLORS.length]}
                strokeWidth={bench ? 2.5 : 1.5}
                dot={false}
                connectNulls
              />
            )
          })}
        </LineChart>
      </ResponsiveContainer>
    </SectionCard>
  )
}

// ── Sensitivity analysis ──────────────────────────────────────────────────────

function SensitivityChart({ s }: { s: SensitivityStrategy }) {
  return (
    <div className="bg-navy-800 rounded p-3">
      <div className="text-sm text-white font-medium">{s.strategy}</div>
      <div className="text-2xs text-muted mb-2">{s.parameter}</div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={s.points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="value" type="number" domain={['dataMin', 'dataMax']}
                 tick={{ fill: '#64748b', fontSize: 10 }} />
          <YAxis tick={{ fill: '#64748b', fontSize: 10 }}
                 tickFormatter={(v: number) => v.toFixed(2)}
                 label={{ value: 'Sharpe', angle: -90, position: 'insideLeft',
                          fill: '#64748b', fontSize: 10 }} />
          <Tooltip
            contentStyle={{ background: '#1a2438', border: '1px solid #1e3a5c',
                            borderRadius: 8, fontSize: 12 }}
          />
          <ReferenceLine x={s.current_value} stroke={ACCENT} strokeDasharray="4 4"
            label={{ value: 'current', fill: ACCENT, fontSize: 10,
                     position: 'top' }} />
          <Line type="monotone" dataKey="sharpe" stroke="#3b82f6"
                strokeWidth={2} dot={{ r: 3 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function SensitivityAnalysis() {
  const [data, setData] = useState<SensitivityPayload | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<SensitivityPayload>('/api/v1/analytics/sensitivity')
      .then((res) => { if (!cancelled) setData(res.data) })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const strategies = data?.strategies ?? []
  const headers = ['Strategy', 'Parameter', 'Value', 'Sharpe']
  const exportRows = strategies.flatMap((s) =>
    s.points.map((p) => [s.strategy, s.parameter, p.value, p.sharpe ?? '']))

  return (
    <SectionCard
      title="Sensitivity Analysis"
      subtitle="How sensitive is each dynamic strategy's risk-adjusted performance to its key parameter? The vertical line marks the current setting."
      exportButton={strategies.length > 0
        ? <TableExportButton tableId="sensitivity_analysis" headers={headers} rows={exportRows} />
        : undefined}
    >
      {loading ? (
        <p className="text-xs text-muted">
          Computing sensitivity — runs ~23 backtests, first load only…
        </p>
      ) : strategies.length === 0 ? (
        <p className="text-xs text-muted italic">Sensitivity analysis unavailable.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {strategies.map((s) => <SensitivityChart key={s.strategy} s={s} />)}
        </div>
      )}
    </SectionCard>
  )
}

export default function AcademicAnalytics() {
  const [data, setData] = useState<AnalyticsPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    axios.get<AnalyticsPayload>('/api/v1/analytics/academic')
      .then((res) => { if (!cancelled) setData(res.data) })
      .catch(() => { if (!cancelled) setError('Could not load analytics.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Academic Analytics</h1>
        <p className="text-sm text-muted mt-1">
          Summary statistics, the equity-bond correlation regime break, regime-conditional
          performance, drawdowns, and Fama-French factor loadings — the analytical backbone
          of the midpoint paper. Every table exports to CSV.
        </p>
        {data?.study_period && (
          <p className="text-xs text-muted mt-1 font-mono">
            Study period: {data.study_period.start} → {data.study_period.end}
            {' '}({data.study_period.n_months} months)
          </p>
        )}
      </div>

      {loading && <div className="card p-8 text-center text-muted text-sm">Loading…</div>}

      {!loading && error && (
        <div className="card p-6 text-center text-sm text-warning">{error}</div>
      )}

      {!loading && !error && data && !data.available && (
        <div className="card p-6 text-center text-sm text-muted">
          Analytics not available yet. {data.note ?? 'Load the dashboard once to warm the caches.'}
        </div>
      )}

      {!loading && !error && data?.available && (
        <>
          {data.cumulative_returns && data.cumulative_returns.points.length > 0 &&
            <CumulativeReturnChart data={data.cumulative_returns} />}
          {data.summary_statistics && data.summary_statistics.length > 0 &&
            <SummaryStatisticsTable rows={data.summary_statistics} />}
          {data.rolling_correlation && data.rolling_correlation.points.length > 0 &&
            <RollingCorrelationChart data={data.rolling_correlation} />}
          {data.rolling_excess_return && data.rolling_excess_return.points.length > 0 &&
            <RollingExcessReturnChart data={data.rolling_excess_return} />}
          {data.regime_conditional && data.regime_conditional.length > 0 &&
            <RegimeConditionalTable rows={data.regime_conditional} />}
          {data.drawdown_comparison && data.drawdown_comparison.length > 0 &&
            <DrawdownComparisonTable rows={data.drawdown_comparison} />}
          {data.factor_loadings && data.factor_loadings.length > 0 &&
            <FactorLoadingsTable rows={data.factor_loadings} />}
          <SensitivityAnalysis />
        </>
      )}
    </div>
  )
}
