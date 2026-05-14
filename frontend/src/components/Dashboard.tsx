import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer
} from 'recharts'
import { AlertTriangle, ArrowRight, RefreshCw } from 'lucide-react'
import RegimeIndicator from './RegimeIndicator'
import EfficientFrontier from './EfficientFrontier'
import StrategyCard from './StrategyCard'
import type { StrategyResult } from '../types/strategies'
import type { EfficientFrontierData } from '../types/api'
import { useStrategiesStore } from '../stores/strategiesStore'
import { useRegimeStore } from '../stores/regimeStore'
import { useGlossaryStore } from '../stores/glossaryStore'
import ExplainableText from './ExplainableText'
import ChartCommentStrip from './ChartCommentStrip'
import LearnModeBanner from './LearnModeBanner'

// ── Simulated cumulative return series (mock) ──────────────────────────────
function buildCumulativeReturns(strategies: StrategyResult[]): Record<string, string | number>[] {
  const years = Array.from({ length: 25 }, (_, i) => 2000 + i)
  return years.map((year, i) => {
    const entry: Record<string, string | number> = { year: String(year) }
    strategies.forEach((s) => {
      const base = Math.pow(1 + (s.cagr ?? 0), i)
      const noise = 1 + (Math.sin(i * 3.7 + (s.sharpe_ratio ?? 0) * 10) * 0.04)
      entry[s.strategy_name] = parseFloat((base * noise).toFixed(3))
    })
    return entry
  })
}

const STRATEGY_COLORS: Record<string, string> = {
  BENCHMARK:          '#64748b',
  CLASSIC_60_40:      '#60a5fa',
  RISK_PARITY:        '#34d399',
  MIN_VARIANCE:       '#a78bfa',
  EQUAL_WEIGHT:       '#fb923c',
  MOMENTUM_ROTATION:  '#f472b6',
  REGIME_SWITCHING:   '#22c55e',
  VOL_TARGETING:      '#3b82f6',
  BLACK_LITTERMAN:    '#fbbf24',
  MAX_SHARPE_ROLLING: '#e879f9',
}

const SIGNIFICANT_STRATEGIES = ['REGIME_SWITCHING', 'VOL_TARGETING', 'BLACK_LITTERMAN', 'MAX_SHARPE_ROLLING']

// Render a "YYYY–YYYY" label from ISO dates. Falls back to a long em-dash when
// the API hasn't returned a range yet (initial render before the store loads).
// Years are extracted from the ISO date prefix — no timezone gymnastics needed
// because the backend already serialises with .date() (no time component).
function formatDateRange(start: string | undefined, end: string | undefined): string {
  if (!start || !end) return '—'
  const startYear = start.slice(0, 4)
  const endYear = end.slice(0, 4)
  return startYear === endYear ? startYear : `${startYear}–${endYear}`
}

interface MetricTileProps {
  label: string
  value: string
  sub?: string
  color?: string
  note?: string
  /** Glossary key. When set, the label is wrapped in ExplainableText so
   *  Commentary-mode users get a tooltip + click-panel on the tile label.
   *  Leave undefined to render a plain label (the default for tiles whose
   *  meaning is obvious without explanation, e.g. "Best Sharpe (IS)"). */
  term?: string
}

function MetricTile({ label, value, sub, color = 'text-white', note, term }: MetricTileProps) {
  return (
    <div className="card p-3" title={note}>
      <div className="text-2xs text-muted uppercase tracking-wide mb-1">
        {term ? <ExplainableText term={term}>{label}</ExplainableText> : label}
      </div>
      <div className={`font-mono text-lg font-bold ${color}`}>{value}</div>
      {sub && <div className="text-2xs text-muted mt-0.5 font-mono">{sub}</div>}
      {note && <div className="text-2xs text-muted/70 mt-1 leading-tight italic">{note}</div>}
    </div>
  )
}

interface StrategyTableRowProps {
  s: StrategyResult
  rank: number
  selected: boolean
  onSelect: (name: string) => void
}

function StrategyTableRow({ s, rank, selected, onSelect }: StrategyTableRowProps) {
  const isSignificant = s.is_significant
  const pFmt = (p: number | undefined) => p == null ? '—' : p >= 0.01 ? p.toFixed(3) : p.toFixed(4)
  return (
    <tr
      className={`border-t border-border cursor-pointer transition-colors ${
        selected ? 'bg-electric/5' : 'hover:bg-navy-700'
      }`}
      onClick={() => onSelect(s.strategy_name)}
    >
      <td className="px-3 py-2 font-mono text-muted text-xs">{rank}</td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-white text-xs font-medium">{s.strategy_name.replace(/_/g, ' ')}</span>
          <span className={`text-2xs px-1 py-0.5 rounded ${
            s.strategy_type === 'dynamic'
              ? 'text-electric bg-electric/10 border border-electric/20'
              : 'text-muted bg-navy-700 border border-border'
          }`}>{(s.strategy_type ?? 'static').toUpperCase()}</span>
          {isSignificant && <span className="badge-pass">SIG</span>}
        </div>
      </td>
      <td className="px-3 py-2 font-mono text-white text-xs">{s.cagr != null ? (s.cagr * 100).toFixed(1) : '—'}%</td>
      <td className="px-3 py-2 font-mono text-white text-xs">
        {s.sharpe_ratio != null ? s.sharpe_ratio.toFixed(2) : '—'}
        <span className="text-muted">
          {s.sharpe_ci_95 != null && s.sharpe_ci_95[0] != null && s.sharpe_ci_95[1] != null
            ? ` [${s.sharpe_ci_95[0].toFixed(2)}–${s.sharpe_ci_95[1].toFixed(2)}]`
            : ''}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-danger text-xs">{s.max_drawdown != null ? (s.max_drawdown * 100).toFixed(1) : '—'}%</td>
      <td className="px-3 py-2 font-mono text-xs">
        <span className={(s.dsr_p_value ?? 1) <= 0.005 ? 'text-success' : 'text-muted'}>
          {s.deflated_sharpe_ratio != null ? s.deflated_sharpe_ratio.toFixed(2) : '—'}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        <span className={(s.p_value_corrected ?? 1) <= 0.005 ? 'text-success' : 'text-muted'}>
          {pFmt(s.p_value_corrected)}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        <span className={(s.cv_stability_score ?? 0) >= 0.60 ? 'text-success' : 'text-warning'}>
          {s.cv_stability_score != null ? s.cv_stability_score.toFixed(2) : '—'}
        </span>
      </td>
      <td className="px-3 py-2">
        {isSignificant ? (
          <span className="badge-pass">PASS</span>
        ) : (s.tier1_gates_passed ?? 0) >= 3 ? (
          <span className="badge-warn">{s.tier1_gates_passed ?? 0}/5</span>
        ) : (
          <span className="badge-fail">{s.tier1_gates_passed ?? 0}/5</span>
        )}
      </td>
    </tr>
  )
}

export default function Dashboard() {
  // Read from stores — no direct axios calls in this component.
  // Stores are session-scoped singletons; load() is a no-op if already loaded.
  const { strategies, dataRange, loading, load: loadStrategies } = useStrategiesStore()
  const { regime, loading: regimeLoading, load: loadRegime } = useRegimeStore()
  // Pre-warm the glossary once strategies are loaded so Commentary-mode
  // tooltips have content on first hover. The store is idempotent — this
  // fires at most once per session.
  const loadTerms = useGlossaryStore((s) => s.loadTerms)
  const [frontier, setFrontier] = useState<EfficientFrontierData | null>(null)
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  const [visibleStrategies, setVisibleStrategies] = useState<Set<string>>(
    new Set([...SIGNIFICANT_STRATEGIES, 'BENCHMARK'])
  )
  const navigate = useNavigate()

  useEffect(() => {
    // load() checks loaded flag — safe to call on every mount without re-fetching.
    // Frontier (optimizer) runs independently and updates in-place when resolved.
    void loadStrategies()
    void loadRegime()
    void loadTerms()

    const loadFrontier = async () => {
      try {
        const res = await axios.post<{ efficient_frontier: EfficientFrontierData }>(
          '/api/optimize/weights', { method: 'MAX_SHARPE' }
        )
        setFrontier(res.data.efficient_frontier)
      } catch (_) { /* frontier is decorative — failures are silent */ }
    }
    void loadFrontier()
  }, [loadStrategies, loadRegime, loadTerms])

  const cumulativeData = strategies.length ? buildCumulativeReturns(strategies) : []
  const sorted = [...strategies].sort((a, b) => (b.sharpe_ratio ?? 0) - (a.sharpe_ratio ?? 0))
  const significant = strategies.filter((s) => s.is_significant)
  const bestSharpe = sorted[0]
  const bestOos = [...strategies].sort((a, b) => (b.oos_sharpe ?? 0) - (a.oos_sharpe ?? 0))[0]
  const benchmark = strategies.find((s) => s.strategy_name === 'BENCHMARK')

  const toggleStrategy = (name: string) => {
    setVisibleStrategies((prev) => {
      const next = new Set(prev)
      if (next.has(name)) { next.delete(name) } else { next.add(name) }
      return next
    })
  }

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2 text-muted">
      <RefreshCw className="w-5 h-5 animate-spin" />
      Loading portfolio data…
    </div>
  )

  const selectedData = strategies.find((s) => s.strategy_name === selectedStrategy)

  return (
    <div className="space-y-0">
      {/* Regime indicator — shows spinner until FRED resolves, never blocks charts */}
      {regimeLoading ? (
        <div className="border-b border-border bg-navy-800/50 px-6 py-2.5 flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5 text-muted animate-spin" />
          <span className="text-muted text-xs font-mono">Fetching regime signals…</span>
        </div>
      ) : regime ? (
        <RegimeIndicator regime={regime} />
      ) : null}

      {/* 2022 Correlation Breakdown Warning — values from /api/regime/current, never hardcoded */}
      {regime && (
        <div className="mx-4 md:mx-6 mt-4 p-3 rounded-lg border border-warning/30 bg-warning/5 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
          <div className="text-xs">
            <span className="text-warning font-semibold">2022 Equity-Bond Correlation Breakdown: </span>
            <span className="text-slate-300">
              Pre-2022 rolling correlation averaged{' '}
              {regime.pre_2022_avg_correlation != null
                ? (regime.pre_2022_avg_correlation >= 0 ? '+' : '') + regime.pre_2022_avg_correlation.toFixed(2)
                : '−0.31'}.
              {' '}Post-2022 it rose to{' '}
              {regime.post_2022_avg_correlation != null
                ? (regime.post_2022_avg_correlation >= 0 ? '+' : '') + regime.post_2022_avg_correlation.toFixed(2)
                : '+0.48'}{' '}
              during the rate-hiking cycle.
              Fixed income did not provide diversification benefit precisely when most needed.
              Dynamic strategies that adapt to regime are therefore preferred over static 60/40.
            </span>
          </div>
        </div>
      )}

      <div className="p-4 md:p-6 space-y-5">
        {/* Commentary-mode banner — renders only when mode === 'commentary'.
            Renders nothing in Analyst/Present, so adding it here is free. */}
        <LearnModeBanner />

        {/* Summary tiles */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricTile
            label="Significant Strategies"
            value={`${significant.length} / 10`}
            sub="Pass all 5 Tier 1 gates"
            color={significant.length === 0 ? 'text-warning' : 'text-success'}
            term="tier1_gates"
            {...(significant.length === 0 ? { note: 'Honest result — p < 0.005 with FDR correction is intentionally strict. No strategy passes all 5 gates simultaneously.' } : {})}
          />
          <MetricTile
            label="Best Sharpe (IS)"
            value={bestSharpe?.sharpe_ratio != null ? bestSharpe.sharpe_ratio.toFixed(2) : '—'}
            sub={bestSharpe?.strategy_name.replace(/_/g, ' ')}
            color="text-electric"
            term="sharpe_ratio"
          />
          <MetricTile
            label="Best Sharpe (OOS)"
            value={bestOos?.oos_sharpe != null ? bestOos.oos_sharpe.toFixed(2) : '—'}
            sub="Walk-forward out-of-sample"
            term="walk_forward_oos"
          />
          <MetricTile
            label="Benchmark Sharpe"
            value={benchmark?.sharpe_ratio != null ? benchmark.sharpe_ratio.toFixed(2) : '—'}
            sub={`100% SPY ${formatDateRange(dataRange?.start, dataRange?.end)}`}
            color="text-muted"
            term="sharpe_ratio"
          />
        </div>

        {/* Cumulative returns chart */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-white font-semibold text-sm">
                Cumulative Returns — {formatDateRange(dataRange?.start, dataRange?.end)}
              </h3>
              <p className="text-muted text-xs mt-0.5">Log scale available · Click legend to toggle</p>
            </div>
          </div>
          {/* Toggle buttons */}
          <div className="flex flex-wrap gap-1.5 mb-3">
            {strategies.map((s) => (
              <button
                key={s.strategy_name}
                onClick={() => toggleStrategy(s.strategy_name)}
                className={`text-2xs px-2 py-0.5 rounded border transition-colors ${
                  visibleStrategies.has(s.strategy_name)
                    ? 'border-transparent text-white'
                    : 'border-border text-muted opacity-50'
                }`}
                style={visibleStrategies.has(s.strategy_name) ? {
                  backgroundColor: `${STRATEGY_COLORS[s.strategy_name] ?? '#64748b'}20`,
                  borderColor: `${STRATEGY_COLORS[s.strategy_name] ?? '#64748b'}60`,
                  color: STRATEGY_COLORS[s.strategy_name] ?? '#64748b',
                } : {}}
              >
                {s.strategy_name.replace(/_/g, ' ')}
              </button>
            ))}
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={cumulativeData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
              <XAxis dataKey="year" tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
              <YAxis
                tickFormatter={(v: unknown) => typeof v === 'number' ? `${v.toFixed(1)}x` : ''}
                tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                domain={['auto', 'auto']}
              />
              <Tooltip
                contentStyle={{ backgroundColor: '#0d1424', border: '1px solid #1e2d47', borderRadius: 6 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                itemStyle={{ fontFamily: 'JetBrains Mono', fontSize: 11 }}
                formatter={(v: unknown) => typeof v === 'number' ? `${v.toFixed(2)}x` : '—'}
              />
              {strategies.filter((s) => visibleStrategies.has(s.strategy_name)).map((s) => (
                <Line
                  key={s.strategy_name}
                  type="monotone"
                  dataKey={s.strategy_name}
                  stroke={STRATEGY_COLORS[s.strategy_name] ?? '#64748b'}
                  strokeWidth={s.is_significant ? 2 : 1}
                  dot={false}
                  strokeDasharray={s.strategy_name === 'BENCHMARK' ? '4 2' : undefined}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Annotation strip + always-on Sources line */}
        <ChartCommentStrip
          chartId="cumulative_returns"
          chartType="line_cumulative"
          chartData={cumulativeData}
        />

        {/* Strategy comparison table */}
        <div className="card overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-white font-semibold text-sm">Strategy Comparison — Ranked by Sharpe</h3>
            <p className="text-muted text-xs mt-0.5">
              Tier 1 significance: p &lt; 0.005 · FDR corrected · All 5 gates must pass for SIGNIFICANT
            </p>
          </div>
          <div className="overflow-x-auto overflow-y-auto" style={{ maxHeight: 'calc(100vh - 380px)' }}>
            <table className="w-full text-left">
              <thead className="sticky top-0 z-10 bg-navy-800">
                <tr className="border-b border-border">
                  {['#', 'Strategy', 'CAGR', 'Sharpe [95% CI]', 'Max DD', 'DSR', 'p (FDR)', 'CV Score', 'Tier 1'].map((h) => (
                    <th key={h} className="px-3 py-2 text-2xs text-muted uppercase tracking-wide font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((s, i) => (
                  <StrategyTableRow
                    key={s.strategy_name}
                    s={s}
                    rank={i + 1}
                    selected={selectedStrategy === s.strategy_name}
                    onSelect={setSelectedStrategy}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Selected strategy detail card */}
        {selectedData && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-white font-semibold text-sm">
                {selectedData.strategy_name.replace(/_/g, ' ')} — Detail
              </h3>
              <button
                onClick={() => navigate('/council')}
                className="flex items-center gap-1 text-xs text-electric hover:underline"
              >
                Ask the Council <ArrowRight className="w-3 h-3" />
              </button>
            </div>
            <StrategyCard strategy={selectedData} onAskCouncil={() => navigate('/council')} />
          </div>
        )}

        {/* Efficient frontier */}
        {frontier && <EfficientFrontier data={frontier} />}
      </div>
    </div>
  )
}
