import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer
} from 'recharts'
import { AlertTriangle, RefreshCw, X } from 'lucide-react'
import RegimeIndicator from './RegimeIndicator'
import EfficientFrontier from './EfficientFrontier'
import StrategyCard from './StrategyCard'
import CIORecommendationCard from './CIORecommendationCard'
import ForwardConfidenceChart from './ForwardConfidenceChart'
import PerformanceRecordLink from './PerformanceRecordLink'
import type { StrategyResult } from '../types/strategies'
import { useStrategiesStore } from '../stores/strategiesStore'
import { useDashboardDataStore } from '../stores/dashboardDataStore'
import { useRegimeStore } from '../stores/regimeStore'
import { useGlossaryStore } from '../stores/glossaryStore'
import { useDataStatus, tableOf } from '../hooks/useDataStatus'
import {
  useCharacterisationsStore,
} from '../stores/strategyCharacterisationsStore'
import { PortfolioProfileModal } from './PortfolioProfileModal'
import ExplainableText from './ExplainableText'
import InfoIcon from './InfoIcon'
import ChartCommentStrip from './ChartCommentStrip'
import LearnModeBanner from './LearnModeBanner'
import DataCurrencyBar from './DataCurrencyBar'
import ChartExportButton from './ChartExportButton'
import TableExportButton from './TableExportButton'
import MacroResearchPanel from './MacroResearchPanel'
import FloatingSectionNav from './FloatingSectionNav'
import ContextFreshnessBadge from './ContextFreshnessBadge'
// Canonical strategy-colour map — one source of truth shared with every
// chart component (was duplicated locally in this file).
import { STRATEGY_COLORS } from '../lib/strategyColors'
import {
  STRATEGY_METADATA, strategyTooltipKey, strategyMetaSummary,
} from '../constants/strategyMetadata'

// ── Real cumulative-return series ──────────────────────────────────────────
// Type definitions now live in stores/dashboardDataStore alongside the
// load() helper that fetches them — Dashboard re-exports nothing of
// its own. The shape is { strategies: string[]; points: CumulativePoint[] }
// served by /api/v1/analytics/academic.cumulative_returns — computed
// from market_data_monthly. The Dashboard chart renders these
// verbatim; it never synthesises a curve.

// ── Data-freshness pill — mirrors Settings → Data and Study Period ─────────
type Staleness = 'green' | 'amber' | 'red' | 'unknown'
const STALENESS_PILL: Record<Staleness, { cls: string; label: string }> = {
  green:   { cls: 'bg-success/15 text-success border-success/30', label: 'Current' },
  amber:   { cls: 'bg-warning/15 text-warning border-warning/30', label: 'Ageing' },
  red:     { cls: 'bg-danger/15 text-danger border-danger/30',    label: 'Stale' },
  unknown: { cls: 'bg-navy-700 text-muted border-border',         label: 'Unknown' },
}

const SIGNIFICANT_STRATEGIES = ['REGIME_SWITCHING', 'VOL_TARGETING', 'BLACK_LITTERMAN', 'MAX_SHARPE_ROLLING']

// Strategy comparison table columns. `term` keys the glossary entry that
// the ExplainableText wrapper looks up — null means "structural column,
// no metric explanation needed". Keep the term IDs stable: they're the
// same identifiers the Explainer Agent prompt expects to emit.
interface StrategyTableColumn {
  label: string
  /** Key into explainerTooltips.ts — drives the column-header InfoIcon. */
  infoKey: string | null
  /** Hidden below lg unless the "More columns" toggle is on. The '#'
   *  rank column stays hidden on mobile even when expanded. */
  mobileHidden: boolean
}

const STRATEGY_TABLE_COLUMNS: StrategyTableColumn[] = [
  { label: '#',                infoKey: null,           mobileHidden: true },
  { label: 'Strategy',         infoKey: null,           mobileHidden: false },
  { label: 'CAGR',             infoKey: 'cagr',         mobileHidden: false },
  { label: 'Sharpe [95% CI]',  infoKey: 'sharpe_ci',    mobileHidden: false },
  { label: 'Max DD',           infoKey: 'max_drawdown', mobileHidden: true },
  { label: 'DSR',              infoKey: 'dsr',          mobileHidden: true },
  { label: 'p (FDR)',          infoKey: 'p_fdr',        mobileHidden: true },
  { label: 'CV Score',         infoKey: 'cv_score',     mobileHidden: true },
  { label: 'Turnover (ann.)',  infoKey: 'turnover',     mobileHidden: true },
  { label: 'Tier 1',           infoKey: 'tier',         mobileHidden: false },
]

// Below lg the table shows a reduced column set (Strategy, CAGR, Sharpe,
// Tier 1) so it fits a phone without horizontal scrolling; "More columns"
// reveals the rest. The '#' rank column stays hidden on mobile regardless
// — row order conveys rank. lg+ always shows every column.
function colVis(col: StrategyTableColumn, showAll: boolean): string {
  const stayHidden = col.mobileHidden && (col.label === '#' || !showAll)
  return stayHidden ? 'hidden lg:table-cell' : ''
}

// The Strategy column is frozen (sticky-left) so it stays visible while
// the metric columns scroll horizontally on a narrow screen.
const STICKY_NAME_CELL = 'sticky left-0 bg-navy-800 z-10'

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
  /** Drives the mobile column visibility — matches the table header. */
  showAll: boolean
  /** AI-generated short descriptor from the characterisations store
   *  (Item 9). Null when not yet loaded or unavailable. */
  behaviouralTag: string | null
  /** Opens the Portfolio Profile modal for this strategy. The host
   *  (Dashboard) owns the modal state; the tag click hands the
   *  strategy_id up. */
  onOpenProfile: (strategyId: string, displayName: string) => void
}

function StrategyTableRow({
  s, rank, selected, onSelect, showAll, behaviouralTag, onOpenProfile,
}: StrategyTableRowProps) {
  const isSignificant = s.is_significant
  const pFmt = (p: number | undefined) => p == null ? '—' : p >= 0.01 ? p.toFixed(3) : p.toFixed(4)
  // Per-column mobile visibility — indexes line up with STRATEGY_TABLE_COLUMNS.
  const c = (i: number) => colVis(STRATEGY_TABLE_COLUMNS[i], showAll)
  // Strategy-rules metadata behind the ⓘ icon on the strategy name.
  const meta = STRATEGY_METADATA[s.strategy_name]
  const displayName = s.strategy_name.replace(/_/g, ' ')
  return (
    <tr
      className={`border-t border-border cursor-pointer transition-colors ${
        selected ? 'bg-electric/5' : 'hover:bg-navy-700'
      }`}
      onClick={() => onSelect(s.strategy_name)}
    >
      <td className={`px-3 py-2 font-mono text-muted text-xs ${c(0)}`}>{rank}</td>
      <td className={`px-3 py-2 ${STICKY_NAME_CELL} ${c(1)}`}>
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center">
              <span className="text-white text-xs font-medium">{displayName}</span>
              {/* ⓘ — hover for the strategy type + one-line description,
                  click for the full rules explanation. Wrapped so the
                  click does not also select the table row. */}
              <span
                className="inline-flex"
                onClick={(e) => e.stopPropagation()}
              >
                <InfoIcon
                  tooltipKey={strategyTooltipKey(s.strategy_name)}
                  metricLabel={`${displayName} strategy`}
                  {...(meta ? { currentValue: strategyMetaSummary(meta) } : {})}
                />
              </span>
            </span>
            <span className={`text-2xs px-1 py-0.5 rounded ${
              s.strategy_type === 'dynamic'
                ? 'text-electric bg-electric/10 border border-electric/20'
                : 'text-muted bg-navy-700 border border-border'
            }`}>{(s.strategy_type ?? 'static').toUpperCase()}</span>
            {isSignificant && <span className="badge-pass">SIG</span>}
          </div>
          {/* Item 9 Commit 4 — AI-generated behavioural_tag below the
              name. Clickable: opens the Portfolio Profile modal for
              this strategy directly. The click stops propagation so
              the row's onSelect (which opens the inline strategy
              detail) does not also fire. Falls back to "Open Profile"
              before the characterisations store has loaded so the
              affordance is always there. */}
          <button
            type="button"
            data-testid={`behavioural-tag-${s.strategy_name}`}
            onClick={(e) => {
              e.stopPropagation()
              onOpenProfile(s.strategy_name, displayName)
            }}
            className="text-2xs text-electric/80 hover:text-electric
                        text-left max-w-[280px] truncate font-normal
                        leading-tight"
            title={behaviouralTag ?? 'Open Portfolio Profile'}
          >
            {behaviouralTag ?? 'Open Portfolio Profile →'}
          </button>
        </div>
      </td>
      <td className={`px-3 py-2 font-mono text-white text-xs ${c(2)}`}>{s.cagr != null ? (s.cagr * 100).toFixed(1) : '—'}%</td>
      <td className={`px-3 py-2 font-mono text-white text-xs ${c(3)}`}>
        {s.sharpe_ratio != null ? s.sharpe_ratio.toFixed(2) : '—'}
        <span className="text-muted">
          {s.sharpe_ci_95 != null && s.sharpe_ci_95[0] != null && s.sharpe_ci_95[1] != null
            ? ` [${s.sharpe_ci_95[0].toFixed(2)}–${s.sharpe_ci_95[1].toFixed(2)}]`
            : ' [—]'}
        </span>
      </td>
      <td className={`px-3 py-2 font-mono text-danger text-xs ${c(4)}`}>{s.max_drawdown != null ? (s.max_drawdown * 100).toFixed(1) : '—'}%</td>
      <td className={`px-3 py-2 font-mono text-xs ${c(5)}`}>
        <span className={(s.dsr_p_value ?? 1) <= 0.005 ? 'text-success' : 'text-muted'}>
          {s.deflated_sharpe_ratio != null ? s.deflated_sharpe_ratio.toFixed(2) : '—'}
        </span>
      </td>
      <td className={`px-3 py-2 font-mono text-xs ${c(6)}`}>
        <span className={(s.p_value_corrected ?? 1) <= 0.005 ? 'text-success' : 'text-muted'}>
          {pFmt(s.p_value_corrected)}
        </span>
      </td>
      <td className={`px-3 py-2 font-mono text-xs ${c(7)}`}>
        <span className={(s.cv_stability_score ?? 0) >= 0.60 ? 'text-success' : 'text-warning'}>
          {s.cv_stability_score != null ? s.cv_stability_score.toFixed(2) : '—'}
        </span>
      </td>
      <td
        className={`px-3 py-2 font-mono text-white text-xs ${c(8)}`}
        title="Genuine annualised portfolio turnover — one-way trading at each quarterly rebalance, including drift correction. The benchmark never rebalances, so its turnover is 0%."
      >
        {`${((s.true_turnover ?? 0) * 100).toFixed(0)}%`}
      </td>
      <td className={`px-3 py-2 ${c(9)}`}>
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
  // F3 (May 22 2026) — share the data-status fetch with DataCurrencyBar
  // and the Analytics page via the Zustand store rather than firing
  // a duplicate /api/v1/admin/data-status request from here.
  const { status: dataStatus } = useDataStatus()
  const strategyTable = tableOf(dataStatus, 'strategy_results_cache')
  const dataFreshness = strategyTable
    ? {
        last_updated: strategyTable.last_updated,
        staleness: (strategyTable.staleness as Staleness),
      }
    : null
  // dashboardDataStore caches cumulative + frontier across navigation
  // so a remount reads from the store immediately rather than firing
  // a fresh round-trip that races with the other dashboard fetches.
  // Before this store the user saw "Cumulative return series
  // unavailable" + an empty frontier on every navigation return
  // because component state was cleared on unmount (May 23 2026
  // production fire). load() is a no-op when loaded === true so the
  // useEffect below safely fires it on every visit.
  const {
    cumulative, frontier,
    cumulativeError, frontierError,
    warming: dashboardWarming,
    load: loadDashboardData,
  } = useDashboardDataStore()
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  // Item 9 Commit 4 — Portfolio Profile modal state. The tag below
  // each strategy name on the table opens this modal. The
  // characterisations store dedupes its fetch so loading it on Dashboard
  // mount is free — same store the inline panel reads from on Analytics.
  const [profileModalFor, setProfileModalFor] = useState<
    { strategyId: string; displayName: string } | null>(null)
  const charsById = useCharacterisationsStore((s) => s.byId)
  const loadCharacterisations = useCharacterisationsStore((s) => s.load)
  // Mobile only — reveals the columns hidden in the reduced phone view.
  const [showAllCols, setShowAllCols] = useState(false)
  const cumulativeChartRef = useRef<HTMLDivElement>(null)
  const [visibleStrategies, setVisibleStrategies] = useState<Set<string>>(
    new Set([...SIGNIFICANT_STRATEGIES, 'BENCHMARK'])
  )
  const navigate = useNavigate()

  useEffect(() => {
    // Every load() checks the store's `loaded` flag — safe to call on
    // every mount without re-fetching. This is the navigation-remount
    // contract: a user returning to the dashboard re-runs this effect,
    // each store recognises it's already loaded, and the cached data
    // renders immediately. Network only fires on the first visit of
    // the session OR an explicit refresh.
    void loadStrategies()
    void loadRegime()
    void loadTerms()
    // Item 9 — fetch per-strategy characterisations so the behavioural_tag
    // below each strategy name renders the AI text. The store dedupes,
    // so this is free for every consumer beyond the first.
    void loadCharacterisations()
    // Cumulative + frontier — same load() contract via the dashboardData
    // store. Previously these lived in component useState which CLEARED
    // on every navigation away, leaving the user with "Cumulative return
    // series unavailable" + empty frontier on every return until the
    // silent retry races completed (May 23 2026 production fire).
    void loadDashboardData()
  }, [
    loadStrategies, loadRegime, loadTerms,
    loadCharacterisations, loadDashboardData,
  ])

  const cumulativeData = cumulative?.points ?? []
  const sorted = [...strategies].sort((a, b) => (b.sharpe_ratio ?? 0) - (a.sharpe_ratio ?? 0))
  const significant = strategies.filter((s) => s.is_significant)
  const bestSharpe = sorted[0]
  const bestOos = [...strategies].sort((a, b) => (b.oos_sharpe ?? 0) - (a.oos_sharpe ?? 0))[0]
  const benchmark = strategies.find((s) => s.strategy_name === 'BENCHMARK')

  // CSV export of the strategy comparison table — the flagship table for
  // Forest Capital; every other data table already offers CSV export.
  const STRATEGY_EXPORT_HEADERS = [
    '#', 'Strategy', 'Type', 'CAGR %', 'Sharpe', 'Sharpe CI Low', 'Sharpe CI High',
    'Max Drawdown %', 'DSR p-value', 'P (FDR)', 'CV Score', 'Tier 1 Gates', 'Significant',
  ]
  const strategyExportRows = sorted.map((s, i) => [
    i + 1,
    s.strategy_name.replace(/_/g, ' '),
    (s.strategy_type ?? 'static').toUpperCase(),
    s.cagr != null ? (s.cagr * 100).toFixed(2) : '',
    s.sharpe_ratio != null ? s.sharpe_ratio.toFixed(3) : '',
    s.sharpe_ci_95?.[0] ?? '',
    s.sharpe_ci_95?.[1] ?? '',
    s.max_drawdown != null ? (s.max_drawdown * 100).toFixed(2) : '',
    s.dsr_p_value ?? '',
    s.p_value_corrected ?? '',
    s.cv_stability_score ?? '',
    s.tier1_gates_passed != null ? `${s.tier1_gates_passed}/5` : '',
    s.is_significant ? 'YES' : 'NO',
  ])

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
      {/* Landing-page past/present/future arc, above the fold:
          present (CIO live recommendation), future (forward confidence
          projection), past (Council Performance Record preview). Each is
          self-fetching and degrades to its own empty state. */}
      <CIORecommendationCard />
      <ForwardConfidenceChart />
      <PerformanceRecordLink />

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
            <span className="text-warning font-semibold">
              {/* Central project finding — wrap the heading so the
                  audience can click it for the academic context. The
                  underline inherits ExplainableText's electric-blue
                  dotted style but the surrounding `text-warning` keeps
                  the banner amber overall. */}
              <ExplainableText term="equity_bond_correlation_breakdown">
                2022 Equity-Bond Correlation Breakdown
              </ExplainableText>:{' '}
            </span>
            <span className="text-slate-300">
              {/* Correlation values are computed from market_data_monthly.
                  When absent (cold start / test env) render "—" — never a
                  hardcoded number that would read as a computed result. */}
              Pre-2022 rolling correlation averaged{' '}
              {regime.pre_2022_avg_correlation != null
                ? (regime.pre_2022_avg_correlation >= 0 ? '+' : '') + regime.pre_2022_avg_correlation.toFixed(2)
                : '—'}.
              {' '}Post-2022 it rose to{' '}
              {regime.post_2022_avg_correlation != null
                ? (regime.post_2022_avg_correlation >= 0 ? '+' : '') + regime.post_2022_avg_correlation.toFixed(2)
                : '—'}{' '}
              during the rate-hiking cycle.
              Fixed income did not provide diversification benefit precisely when most needed.
              Dynamic strategies that adapt to regime are therefore preferred over static 60/40.
            </span>
            {regime.as_of && (
              <div className="text-2xs text-muted mt-1">
                Regime signals as of {regime.as_of.slice(0, 16).replace('T', ' ')} UTC
              </div>
            )}
          </div>
        </div>
      )}

      <div className="p-4 md:p-6 space-y-5">
        {/* UAT 2026-05-24 — Dashboard now mounts the FloatingSectionNav
            (collapsed by default, click-to-expand). The data-section-id
            markers on the major sections below feed its discovery loop.
            minSections=3 — Dashboard has five tagged sections, well
            above the threshold. */}
        <FloatingSectionNav pageKey="dashboard" />
        {/* Page header — consistent with every other screen's title block. */}
        <div
          data-section-id="dashboard-overview"
          data-section-label="Overview">
          <h1 className="text-xl font-semibold text-white">Dashboard</h1>
          <p className="text-sm text-muted mt-1">
            Ten portfolio strategies ranked by risk-adjusted performance against
            the 100% equity benchmark.
          </p>
          <div className="mt-1"><DataCurrencyBar /></div>
        </div>

        {/* Commentary-mode banner — renders only when mode === 'commentary'.
            Renders nothing in Analyst/Present, so adding it here is free. */}
        <LearnModeBanner />

        {/* Context freshness badge — item 5 (May 23 2026). Surfaces
            the staleness of the three agent-prompt context caches
            (macro digest / analytics narrative / diversification
            metrics) so the user knows how current the prompts the
            council reads are. Right-aligned above the macro panel. */}
        <div className="flex justify-end">
          <ContextFreshnessBadge />
        </div>

        {/* FEATURE 2 — macro research digest (the same digest the
            council and academic_review prompts inject as a CURRENT
            MACRO CONDITIONS block). Sits above the summary tiles so
            the user reads "today's context" before scanning the
            strategy rankings. Sysadmin-only "Run now" trigger inside. */}
        <div
          data-section-id="dashboard-macro"
          data-section-label="Macro Conditions">
          <MacroResearchPanel />
        </div>

        {/* Summary tiles */}
        <div
          className="grid grid-cols-2 sm:grid-cols-4 gap-3"
          data-section-id="dashboard-summary"
          data-section-label="Summary Tiles">
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
        <div
          className="card p-4"
          ref={cumulativeChartRef}
          data-section-id="dashboard-cumulative-returns"
          data-section-label="Cumulative Returns">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="text-white font-semibold text-sm flex items-center">
                Cumulative Returns — {formatDateRange(dataRange?.start, dataRange?.end)}
                <InfoIcon
                  tooltipKey="cumulative_return_chart"
                  metricLabel="Cumulative Returns"
                  size="md"
                  {...(cumulative && cumulative.points.length > 0
                    ? { currentValue:
                        `Growth of $1 across ${cumulative.strategies.length} `
                        + `strategies over ${cumulative.points.length} months.` }
                    : {})}
                />
              </h3>
              <p className="text-muted text-xs mt-0.5">
                Growth of $1 invested · use the buttons below to show or hide a strategy
              </p>
            </div>
            <ChartExportButton chartId="cumulative_returns" containerRef={cumulativeChartRef} />
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
          {cumulativeData.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={cumulativeData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
              <XAxis
                dataKey="date"
                tickFormatter={(d: unknown) => typeof d === 'string' ? d.slice(0, 4) : ''}
                minTickGap={50}
                tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              />
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
          ) : (
            <div
              data-testid="cumulative-empty-state"
              className="h-[280px] flex flex-col items-center justify-center text-muted text-xs gap-1">
              {dashboardWarming ? (
                <>
                  <span data-testid="cumulative-warming"
                        className="text-electric">
                    Computing cumulative returns…
                  </span>
                  <span className="text-2xs italic">
                    The analytics cache is being built — this view
                    will refresh automatically within a few seconds.
                  </span>
                </>
              ) : (
                <>
                  <span>Cumulative return series unavailable</span>
                  {cumulativeError ? (
                    <span
                      data-testid="cumulative-error"
                      className="text-red-400 text-2xs italic max-w-[60ch] text-center">
                      {cumulativeError}
                    </span>
                  ) : null}
                </>
              )}
            </div>
          )}
        </div>

        {/* Annotation strip + always-on Sources line */}
        <ChartCommentStrip
          chartId="cumulative_returns"
          chartType="line_cumulative"
          chartData={cumulativeData}
        />

        {/* Strategy comparison table */}
        <div
          className="card overflow-hidden"
          data-tour="strategy-table"
          data-section-id="dashboard-strategy-table"
          data-section-label="Strategy Comparison">
          <div className="px-4 py-3 border-b border-border flex items-start justify-between gap-3">
            <div>
              <h3 className="text-white font-semibold text-sm">Strategy Comparison — Ranked by Sharpe</h3>
              <p className="text-muted text-xs mt-0.5">
                Tier 1 significance: p &lt; 0.005 · FDR corrected · All 5 gates must pass for SIGNIFICANT
              </p>
            </div>
            {/* Data freshness + CSV export — freshness mirrors Settings →
                Data and Study Period; CSV export matches every other table. */}
            <div className="flex items-center gap-2 shrink-0">
              {dataFreshness && (
                <>
                  {dataFreshness.last_updated && (
                    <span className="text-2xs text-muted font-mono">
                      computed {dataFreshness.last_updated.slice(0, 10)}
                    </span>
                  )}
                  <span className={`text-2xs px-2 py-0.5 rounded-full border ${
                    STALENESS_PILL[dataFreshness.staleness].cls}`}>
                    {STALENESS_PILL[dataFreshness.staleness].label}
                  </span>
                </>
              )}
              <TableExportButton
                tableId="strategy_comparison"
                headers={STRATEGY_EXPORT_HEADERS}
                rows={strategyExportRows}
              />
            </div>
          </div>
          {/* Mobile-only control row — the table shows a reduced column
              set on a phone; this toggles the rest and hints at the
              horizontal scroll. Hidden from lg up, where all columns fit. */}
          <div className="lg:hidden flex items-center justify-between gap-2
                          px-4 py-2 border-b border-border">
            <span className="text-2xs text-muted">← scroll table sideways →</span>
            <button
              type="button"
              onClick={() => setShowAllCols((v) => !v)}
              className="text-2xs text-electric hover:underline min-h-[44px] px-1"
            >
              {showAllCols ? 'Fewer columns' : 'More columns'}
            </button>
          </div>
          <div className="overflow-x-auto overflow-y-auto" style={{ maxHeight: 'calc(100vh - 380px)' }}>
            <table className="w-full text-left">
              <thead className="sticky top-0 z-10 bg-navy-800">
                <tr className="border-b border-border">
                  {/*
                    Metric columns carry an InfoIcon — hover for the static
                    tooltip, click for the live explainer. The '#' and
                    'Strategy' columns are structural, not metric labels, so
                    they have no infoKey. InfoIcon supersedes the old
                    Commentary-mode ExplainableText wrap here: it is always
                    on and needs no prior council session.
                  */}
                  {STRATEGY_TABLE_COLUMNS.map((col) => (
                    <th
                      key={col.label}
                      className={`px-3 py-2 text-2xs text-muted uppercase tracking-wide
                        font-medium whitespace-nowrap ${colVis(col, showAllCols)} ${
                        col.label === 'Strategy' ? 'sticky left-0 z-20 bg-navy-800' : ''}`}
                    >
                      <span className="inline-flex items-center">
                        {col.label}
                        {col.infoKey && (
                          <InfoIcon tooltipKey={col.infoKey} metricLabel={col.label} />
                        )}
                      </span>
                    </th>
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
                    showAll={showAllCols}
                    behaviouralTag={
                      charsById[s.strategy_name]?.behavioural_tag ?? null}
                    onOpenProfile={(strategyId, displayName) =>
                      setProfileModalFor({ strategyId, displayName })}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Selected strategy detail card. The single council hand-off is
            the strategy-specific link inside StrategyCard — the former
            generic top-right "Ask the Council" link was a duplicate and
            has been removed. */}
        {selectedData && (
          // Inline panel on desktop; a full-screen overlay on mobile so the
          // detail is not lost below the fold on a phone. `fixed lg:static`
          // collapses the overlay back into normal page flow from lg up.
          <div
            className="fixed inset-0 z-50 overflow-y-auto bg-navy-900 p-4
                       lg:static lg:z-auto lg:bg-transparent lg:p-0 lg:overflow-visible"
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-white font-semibold text-sm">
                {selectedData.strategy_name.replace(/_/g, ' ')} — Detail
              </h3>
              {/* Close — mobile overlay only; on desktop the panel is
                  inline and dismissed by selecting another row. */}
              <button
                type="button"
                onClick={() => setSelectedStrategy(null)}
                aria-label="Close strategy detail"
                className="lg:hidden flex items-center justify-center w-11 h-11
                           -mr-2 rounded text-muted hover:text-white hover:bg-navy-700"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <StrategyCard
              strategy={selectedData}
              onAskCouncil={(question) =>
                navigate('/council', { state: { prefillQuestion: question } })}
            />
          </div>
        )}

        {/* Efficient frontier */}
        {frontier && (frontier.frontier_points?.length ?? 0) > 0 ? (
          <div
            data-section-id="dashboard-frontier"
            data-section-label="Efficient Frontier">
            <EfficientFrontier data={frontier} />
          </div>
        ) : dashboardWarming ? (
          <div
            data-testid="frontier-warming"
            className="card p-4 text-center text-electric text-xs">
            Computing efficient frontier… the analytics cache is
            being built — this view will refresh automatically within
            a few seconds.
          </div>
        ) : frontierError ? (
          <div
            data-testid="frontier-error"
            className="card p-4 text-center text-red-400 text-xs italic">
            Efficient frontier unavailable — {frontierError}
          </div>
        ) : null}
      </div>

      {/* Item 9 Commit 4 — the Portfolio Profile modal, opened by the
          behavioural_tag click below each strategy name. Sits at the
          page root so its z-index dominates the page content; the
          modal owns its backdrop and Esc-to-close handler. */}
      <PortfolioProfileModal
        strategyId={profileModalFor?.strategyId ?? null}
        strategyName={profileModalFor?.displayName}
        onClose={() => setProfileModalFor(null)}
      />
    </div>
  )
}
