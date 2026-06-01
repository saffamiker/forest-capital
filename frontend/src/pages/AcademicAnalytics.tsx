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
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import { ChevronDown, ChevronRight } from 'lucide-react'
import TableExportButton from '../components/TableExportButton'
import InfoIcon from '../components/InfoIcon'
import ExplainableText from '../components/ExplainableText'
import DataExplainButton from '../components/DataExplainButton'
import DataCurrencyBar from '../components/DataCurrencyBar'
import { useDataStatus, tableOf } from '../hooks/useDataStatus'
import type { ChartTheme } from '../lib/exportTheme'
import { DARK_CHART_THEME } from '../lib/exportTheme'
// Diversification suite (item 8) — managed by its own hooks against
// /api/v1/analytics/correlation etc. (the analytics_metrics_cache hot
// path is sub-millisecond, so a per-mount fetch is fine here).
import FloatingSectionNav from '../components/FloatingSectionNav'
import ChartCommentStrip from '../components/ChartCommentStrip'
import { CorrelationHeatmap } from '../components/diversification/CorrelationHeatmap'
import { TailRiskTable } from '../components/diversification/TailRiskTable'
import { CaptureScatter } from '../components/diversification/CaptureScatter'
import { DrawdownDurationTable } from '../components/diversification/DrawdownDurationTable'
import { CrisisPerformanceTable } from '../components/diversification/CrisisPerformanceTable'
import { RiskContributionBar } from '../components/diversification/RiskContributionBar'
import { DistributionTable } from '../components/diversification/DistributionTable'
// Item 9 — per-strategy Portfolio Profile inside the methodology accordion.
import { PortfolioProfilePanel } from '../components/PortfolioProfilePanel'
import { formatDate } from '../lib/dateFormat'

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
  period_start: string | null
  period_end: string | null
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
  model: string
  alpha_annualized: number
  alpha_significant: boolean
  mkt_rf: number
  mkt_rf_significant: boolean
  smb: number
  smb_significant: boolean
  hml: number
  hml_significant: boolean
  mom: number | null
  mom_significant: boolean
  r_squared: number
  n_months: number
}

type CumulativePoint = { date: string } & Record<string, number | null>

interface CumulativeReturns {
  strategies: string[]
  points: CumulativePoint[]
  // First actual return month per strategy. The dynamic strategies start
  // later than the full study period — see the chart footnote.
  start_dates?: Record<string, string>
}

interface StrategyMeta {
  id: string
  name: string
  type: 'static' | 'dynamic'
  rebalancing: string
  weights: { equity: number; ig: number; hy: number } | null
  signal_logic: string | null
  economic_intuition: string | null
  key_parameter: string | null
  parameter_value: string | null
  rationale: string
}

/**
 * Per-strategy block-bootstrap Sharpe CI (length 12, 10,000 resamples,
 * seed=42). `sharpe` is the point estimate; `ci_low`/`ci_high` are the
 * 2.5%/97.5% percentiles of the bootstrap distribution. `samples` is
 * present only on the `bootstrap_ci_samples` payload — a down-sampled
 * (≤1000-point) distribution for the density-overlap visualisation.
 */
export interface BootstrapCIRow {
  strategy: string
  sharpe: number
  ci_low: number
  ci_high: number
  n_resamples: number
  block_size: number
  n_observations: number
  samples?: number[]
}

export interface AnalyticsPayload {
  available: boolean
  note?: string
  study_period?: { start: string; end: string; n_months: number }
  cumulative_returns?: CumulativeReturns
  summary_statistics?: SummaryRow[]
  bootstrap_ci_sharpe?: BootstrapCIRow[]
  bootstrap_ci_samples?: BootstrapCIRow[]
  rolling_correlation?: RollingCorrelation
  rolling_excess_return?: RollingExcess
  regime_conditional?: RegimeRow[]
  drawdown_comparison?: DrawdownRow[]
  factor_loadings?: FactorRow[]
  strategy_metadata?: StrategyMeta[]
}

// Series line colours and the benchmark colour now come from the active
// ChartTheme (theme.seriesColors / theme.benchmark) — see exportTheme.ts.
const isBenchmark = (name: string): boolean => /benchmark/i.test(name)

// ── Formatting helpers ────────────────────────────────────────────────────────

const pct = (x: number | null | undefined): string =>
  x == null ? '—' : `${(x * 100).toFixed(2)}%`
const num = (x: number | null | undefined, dp = 2): string =>
  x == null ? '—' : x.toFixed(dp)

// An ISO date → a "YYYY-MM" study-period label.
const monthLabel = (iso: string | null | undefined): string =>
  iso ? iso.slice(0, 7) : '—'
// A row's actual study period as "YYYY-MM to YYYY-MM".
const periodLabel = (start: string | null, end: string | null): string =>
  start && end ? `${monthLabel(start)} to ${monthLabel(end)}` : '—'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

// The calendar months strictly after `afterIso`'s month, through
// `throughIso`'s month — used to name the FF factor months Ken French
// has not yet posted ("April 2026").
function monthsBetween(afterIso: string, throughIso: string): string[] {
  let [y, m] = afterIso.slice(0, 7).split('-').map(Number)   // m is 1-indexed
  const [ty, tm] = throughIso.slice(0, 7).split('-').map(Number)
  const out: string[] = []
  m += 1
  if (m > 12) { m = 1; y += 1 }
  while (y < ty || (y === ty && m <= tm)) {
    out.push(`${MONTH_NAMES[m - 1]} ${y}`)
    m += 1
    if (m > 12) { m = 1; y += 1 }
  }
  return out
}

// Percentage with green/red sign colouring — used for excess return.
function SignedPct({ x }: { x: number | null | undefined }) {
  if (x == null) return <>—</>
  const cls = x > 0 ? 'text-success' : x < 0 ? 'text-danger' : 'text-muted'
  return <span className={cls}>{`${(x * 100).toFixed(2)}%`}</span>
}

// ── Shared table chrome ───────────────────────────────────────────────────────

function SectionCard({
  title, subtitle, exportButton, infoKey, tourId, theme = DARK_CHART_THEME,
  dataExplain, sectionId, children,
}: {
  title: string
  subtitle: string
  exportButton?: React.ReactNode
  /** When set, an InfoIcon is placed after the title — hover for the
   *  static tooltip, click for the live explainer. */
  infoKey?: string
  /** When set, the card carries a data-tour attribute the site tour
   *  anchors a step to. */
  tourId?: string
  /** Light mode is used by the off-screen academic-export renderer; the
   *  default (dark) leaves the live UI pixel-identical. */
  theme?: ChartTheme
  /** When set, an "Explain this data" button is placed in the header —
   *  a contextual reading of the chart's current values. Suppressed in
   *  light (export) mode, where interactive chrome must not render. */
  dataExplain?: { currentValue?: string; context?: string }
  /** May 24 2026 — when set, the card emits the data-section-id +
   *  data-section-label attributes the FloatingSectionNav picks up.
   *  Omit it for cards that should not appear in the page TOC (e.g.
   *  the methodology accordion's per-strategy sub-cards). */
  sectionId?: string
  children: React.ReactNode
}) {
  // In light mode the dark `card` class is bypassed entirely — the export
  // package needs a white background, so card chrome is set inline instead.
  const light = theme.mode === 'light'
  return (
    <div
      className={light ? 'p-5 rounded-lg' : 'card p-5'}
      style={
        light
          ? {
              borderLeft: `3px solid ${ACCENT}`,
              background: theme.background,
              border: `1px solid ${theme.border}`,
            }
          : { borderLeft: `3px solid ${ACCENT}` }
      }
      {...(tourId ? { 'data-tour': tourId } : {})}
      {...(sectionId
        ? { 'data-section-id': sectionId, 'data-section-label': title }
        : {})}
    >
      <div className="flex items-start justify-between mb-3 gap-2">
        <div className="min-w-0">
          <h2
            className={`text-base font-semibold flex items-center min-w-0 ${light ? '' : 'text-white'}`}
            {...(light ? { style: { color: theme.textPrimary } } : {})}
          >
            {/* truncate, never wrap, on a narrow header row */}
            <span className="truncate">{title}</span>
            {infoKey && (
              <InfoIcon
                tooltipKey={infoKey}
                metricLabel={title}
                size="md"
                {...(dataExplain?.currentValue
                  ? { currentValue: dataExplain.currentValue }
                  : {})}
              />
            )}
          </h2>
          <p
            className={`text-xs mt-0.5 ${light ? '' : 'text-muted'}`}
            {...(light ? { style: { color: theme.textSecondary } } : {})}
          >
            {subtitle}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {dataExplain && !light && (
            <DataExplainButton
              metric={title}
              {...(dataExplain.currentValue !== undefined
                ? { currentValue: dataExplain.currentValue } : {})}
              context={dataExplain.context ?? 'academic_project'}
            />
          )}
          {exportButton}
        </div>
      </div>
      {children}
    </div>
  )
}

/**
 * ResponsiveHeader — a column-header label that abbreviates on mobile
 * to keep the row to a single line in a narrow viewport. The full
 * label still appears in the InfoIcon tooltip (set via infoLabel on
 * the surrounding TH) so a tap surfaces the original wording.
 */
function ResponsiveHeader(
  { full, short }: { full: string; short: string },
) {
  return (
    <>
      <span className="hidden sm:inline">{full}</span>
      <span className="inline sm:hidden">{short}</span>
    </>
  )
}

const TH = ({ children, right = false, infoKey, infoLabel, term,
              sticky = false }: {
  children: React.ReactNode
  right?: boolean
  /** When set, an InfoIcon is placed after the header label. */
  infoKey?: string
  infoLabel?: string
  /** Glossary term ID — when set, the header label is wrapped in
   *  ExplainableText so Commentary mode explains the metric. */
  term?: string
  /** Freezes the column (sticky-left) so it stays visible while the
   *  table scrolls horizontally on a narrow screen. */
  sticky?: boolean
}) => (
  <th className={`px-3 py-2 text-xs font-medium uppercase tracking-wider text-muted
                  whitespace-nowrap ${right ? 'text-right' : 'text-left'}
                  ${sticky ? 'sticky left-0 z-10 bg-navy-800' : ''}`}>
    <span className={`inline-flex items-center ${right ? 'flex-row-reverse' : ''}`}>
      {term
        ? <ExplainableText term={term}>{children}</ExplainableText>
        : children}
      {/* No double affordance — InfoIcon (hover tooltip) is suppressed
          when the header is already wrapped in ExplainableText (dotted
          underline + click panel, the richer affordance). Headers that
          do not declare a glossary term keep the InfoIcon. */}
      {!term && infoKey && (
        <InfoIcon tooltipKey={infoKey} metricLabel={infoLabel ?? infoKey} />
      )}
    </span>
  </th>
)
const TD = ({ children, right = false, mono = false, sticky = false }:
  { children: React.ReactNode; right?: boolean; mono?: boolean; sticky?: boolean }) => (
  <td className={`px-3 py-2 text-sm text-white whitespace-nowrap
                  ${right ? 'text-right' : 'text-left'}
                  ${mono ? 'font-mono' : ''}
                  ${sticky ? 'sticky left-0 z-[5] bg-navy-800' : ''}`}>
    {children}
  </td>
)

// ── 1. Summary statistics table ───────────────────────────────────────────────

function SummaryStatisticsTable({ rows }: { rows: SummaryRow[] }) {
  const headers = ['Asset', 'Period', 'CAGR', 'Excess Return (ann.)',
                   'Ann. Volatility', 'Sharpe', 'Information Ratio',
                   'Max Drawdown', 'Skewness']
  const exportRows = rows.map((r) => [
    r.asset, periodLabel(r.period_start, r.period_end),
    pct(r.cagr), pct(r.excess_return), pct(r.ann_volatility),
    num(r.sharpe_ratio), num(r.information_ratio), pct(r.max_drawdown),
    num(r.skewness),
  ])
  return (
    <SectionCard
      title="Summary Statistics"
      sectionId="summary-statistics"
      subtitle="Full study period — equity, investment-grade bonds, high-yield bonds, and the benchmark. Excess return is annualised CAGR minus the benchmark CAGR; information ratio is excess return over tracking error."
      exportButton={<TableExportButton tableId="summary_statistics" headers={headers} rows={exportRows} />}
    >
      <div className="overflow-x-auto">
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH sticky>Asset</TH>
          <TH>Period</TH>
          <TH right infoKey="cagr" infoLabel="CAGR" term="cagr">CAGR</TH>
          <TH right infoKey="excess_return" infoLabel="Excess Return">
            <ResponsiveHeader full="Excess Return (ann.)" short="Ex. Ret." />
          </TH>
          <TH right infoKey="volatility" infoLabel="Annualised Volatility">
            <ResponsiveHeader full="Ann. Volatility" short="Vol." />
          </TH>
          <TH right infoKey="sharpe" infoLabel="Sharpe Ratio" term="sharpe_ratio">Sharpe</TH>
          <TH right infoKey="information_ratio" infoLabel="Information Ratio" term="info_ratio">
            <ResponsiveHeader full="Information Ratio" short="IR" />
          </TH>
          <TH right infoKey="max_drawdown" infoLabel="Maximum Drawdown" term="max_drawdown">
            <ResponsiveHeader full="Max Drawdown" short="Max DD" />
          </TH>
          <TH right infoKey="skewness" infoLabel="Skewness" term="skewness">Skewness</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.asset} className="border-b border-border/50 hover:bg-navy-800/40 transition-colors">
              <TD sticky>{r.asset}</TD>
              <TD mono>{periodLabel(r.period_start, r.period_end)}</TD>
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
      </div>
      <p className="text-2xs text-muted mt-2 leading-relaxed">
        Strategies with initialisation lookback windows have shorter study
        periods than the four asset series shown here. CAGR and every other
        metric is computed over each strategy's actual data period — see the
        cumulative return chart footnote for the dynamic-strategy start dates.
      </p>
    </SectionCard>
  )
}

// ── Bootstrap Confidence Intervals on Sharpe ──────────────────────────────────
//
// Block bootstrap (length 12, 10,000 resamples, seed=42) over each
// strategy's monthly returns. The table presents the point Sharpe
// alongside its 95% CI — `0.62 [0.41, 0.83]`. The density-overlap
// chart plots a smoothed histogram of the bootstrap distribution per
// strategy, with low opacity so the OVERLAP between strategies is
// visually obvious. Substantial overlap is the empirical motivation
// for regime-conditional construction: when historical-mean ranking
// cannot reliably distinguish strategies, the selection must come
// from current-regime signals.

function _kdeHistogram(
  samples: number[], n_bins: number = 40,
): { x: number; y: number }[] {
  if (!samples.length) return []
  const lo = Math.min(...samples)
  const hi = Math.max(...samples)
  if (hi - lo < 1e-9) return [{ x: lo, y: 1 }]
  const width = (hi - lo) / n_bins
  // Triangular kernel — modest smoothing, no scipy dependency on the
  // wire. Bins-of-width-h centred at each x; the count per bin /
  // (total * h) gives the density.
  const bins = new Array(n_bins).fill(0)
  for (const v of samples) {
    let i = Math.floor((v - lo) / width)
    if (i >= n_bins) i = n_bins - 1
    if (i < 0) i = 0
    bins[i] += 1
  }
  const total = samples.length
  return bins.map((c, i) => ({
    x: lo + width * (i + 0.5),
    y: c / (total * width),
  }))
}


function BootstrapCITable({ rows }: { rows: BootstrapCIRow[] }) {
  // The user-spec column header is "Sharpe [95% CI]" rendering
  // "0.62 [0.41, 0.83]" — point estimate followed by the bracketed
  // interval. Months / resamples / block size live alongside as a
  // disclosure of the methodology behind the CI.
  const headers = ['Strategy', 'Sharpe [95% CI]', 'Months',
                   'Resamples', 'Block (months)']
  const exportRows = rows.map((r) => [
    r.strategy,
    `${num(r.sharpe)} [${num(r.ci_low)}, ${num(r.ci_high)}]`,
    String(r.n_observations),
    String(r.n_resamples),
    String(r.block_size),
  ])
  return (
    <SectionCard
      title="Bootstrap Confidence Intervals on Sharpe"
      sectionId="bootstrap-ci-sharpe"
      subtitle={
        "Block bootstrap (length 12 months, 10,000 resamples, seed=42) "
        + "over each strategy's monthly returns. The 95% CI is the "
        + "2.5%/97.5% percentile of the bootstrap Sharpe distribution. "
        + "Block resampling preserves within-year autocorrelation; "
        + "individual-month resampling would break it."
      }
      exportButton={
        <TableExportButton
          tableId="bootstrap_ci_sharpe"
          headers={headers}
          rows={exportRows}
        />
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <TH sticky>Strategy</TH>
              <TH right infoKey="sharpe" infoLabel="Sharpe with 95% CI"
                  term="sharpe_ratio">Sharpe [95% CI]</TH>
              <TH right>Months</TH>
              <TH right>Resamples</TH>
              <TH right>
                <ResponsiveHeader full="Block (months)" short="Block" />
              </TH>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.strategy}
                  className="border-b border-border/50
                              hover:bg-navy-800/40 transition-colors">
                <TD sticky>{r.strategy}</TD>
                <TD right mono>
                  {num(r.sharpe)} [{num(r.ci_low)}, {num(r.ci_high)}]
                </TD>
                <TD right mono>{r.n_observations}</TD>
                <TD right mono>{r.n_resamples.toLocaleString()}</TD>
                <TD right mono>{r.block_size}</TD>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-2xs text-muted mt-2 leading-relaxed">
        <strong className="text-text-secondary">Limitation.</strong>{' '}
        Bootstrap 95% confidence intervals on Sharpe ratios show
        substantial overlap across strategies on the 286-observation
        sample. Static strategy selection cannot be made with
        statistical confidence from historical averages alone. This is
        the empirical motivation for regime-conditional construction:
        when historical ranking is unreliable, selection must be driven
        by current regime signals.
      </p>
    </SectionCard>
  )
}


function BootstrapCIDensityChart(
  { rows, theme = DARK_CHART_THEME }:
  { rows: BootstrapCIRow[]; theme?: ChartTheme },
) {
  // Recharts-friendly shape: one row per x-value, one column per
  // strategy. Densities are computed per strategy from `samples` and
  // re-indexed against a common x-grid so the overlap renders as
  // proper overlapping areas, not as side-by-side panels.
  const visible = rows.filter((r) => r.samples && r.samples.length > 0)
  if (visible.length === 0) {
    return null
  }
  // Build a unified x-grid spanning every strategy's sample range.
  const allSamples = visible.flatMap((r) => r.samples ?? [])
  const lo = Math.min(...allSamples)
  const hi = Math.max(...allSamples)
  const nBins = 60
  const width = (hi - lo) / nBins
  const xs = Array.from(
    { length: nBins },
    (_, i) => lo + width * (i + 0.5))
  // Per-strategy density on the unified grid.
  const perStrategy: Record<string, number[]> = {}
  for (const r of visible) {
    const hist = _kdeHistogram(r.samples ?? [], nBins)
    // Re-index hist (each strategy's own range) onto the unified xs.
    const lookup = new Map(hist.map((p) => [p.x.toFixed(6), p.y]))
    perStrategy[r.strategy] = xs.map((x) => {
      // Nearest-neighbour from the strategy's own bin centres.
      let best = 0
      let bestDist = Infinity
      for (const [k, v] of lookup.entries()) {
        const d = Math.abs(parseFloat(k) - x)
        if (d < bestDist) { bestDist = d; best = v }
      }
      return best
    })
  }
  const data = xs.map((x, i) => {
    const row: Record<string, number> = { sharpe: x }
    for (const name of Object.keys(perStrategy)) {
      row[name] = perStrategy[name][i]
    }
    return row
  })

  return (
    <SectionCard
      title="Bootstrap Sharpe Distributions — Overlap Visualisation"
      sectionId="bootstrap-ci-density"
      subtitle={
        "Smoothed density of each strategy's 10,000-resample bootstrap "
        + "Sharpe distribution. Overlapping regions mean the strategies "
        + "are not statistically separable on historical means alone — "
        + "the more overlap, the less reliable past-average ranking is "
        + "for forward selection."
      }
      theme={theme}
    >
      <ResponsiveContainer width="100%" height={320}>
        <AreaChart data={data}
                    margin={{ top: 12, right: 20, bottom: 12, left: 4 }}>
          <CartesianGrid stroke={theme.gridStroke} strokeDasharray="2 2" />
          <XAxis dataKey="sharpe" type="number"
                  domain={['dataMin', 'dataMax']}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  stroke={theme.textSecondary}
                  tick={theme.axisTick}
                  label={{ value: 'Sharpe ratio',
                            position: 'insideBottom',
                            offset: -6,
                            fill: theme.textSecondary,
                            fontSize: 11 }} />
          <YAxis stroke={theme.textSecondary}
                  tick={theme.axisTick}
                  label={{ value: 'Density', angle: -90,
                            position: 'insideLeft',
                            fill: theme.textSecondary,
                            fontSize: 11 }} />
          <Tooltip
            contentStyle={theme.tooltipContentStyle}
            labelStyle={theme.tooltipLabelStyle}
            labelFormatter={(v: number) =>
              typeof v === 'number' ? `Sharpe ≈ ${v.toFixed(3)}` : ''}
            formatter={(v: number) =>
              [(v ?? 0).toFixed(3), 'density']} />
          {Object.keys(perStrategy).map((name) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stroke={theme.colorFor(name)}
              fill={theme.colorFor(name)}
              fillOpacity={0.18}
              strokeOpacity={0.85}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
      <p className="text-2xs text-muted mt-2 leading-relaxed">
        Overlap region — where multiple distributions share Sharpe
        values — is the empirical case for regime-conditional
        construction: static historical ranking cannot reliably
        order strategies on this sample.
      </p>
    </SectionCard>
  )
}


// ── Rolling excess return chart ───────────────────────────────────────────────

export function RollingExcessReturnChart(
  { data, theme = DARK_CHART_THEME }:
  { data: RollingExcess; theme?: ChartTheme },
) {
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
      sectionId="rolling-excess-return"
      infoKey="rolling_excess_return"
      theme={theme}
      subtitle={`${data.window_months}-month rolling total return of each strategy minus the BENCHMARK (100% equity) — computed from monthly returns as a trailing window over the full study period. Above zero is outperformance, below zero is underperformance.`}
      dataExplain={{ currentValue:
        `${data.window_months}-month rolling excess return vs the 100% `
        + `equity benchmark for ${data.strategies.length} strategies across `
        + `${data.points.length} months`
        + (breakX ? `; correlation regime break near ${breakX}.` : '.') }}
      exportButton={<TableExportButton tableId="rolling_excess_return" headers={headers} rows={exportRows} />}
    >
      <ResponsiveContainer width="100%" height={320}>
        {/* Axis labels use the same pattern as the Sensitivity chart:
            Y at insideLeft / angle -90 / textAnchor middle so it reads
            vertically along the axis; X at insideBottom / offset -5 so
            it sits below the year ticks. Margins bumped (bottom 4→32,
            left 12→24) to clear the tick text without crowding the
            plot area. */}
        <LineChart data={data.points} margin={{ top: 8, right: 16, bottom: 32, left: 24 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.gridStroke} />
          <XAxis dataKey="date" tick={theme.axisTick} minTickGap={56}
                 label={{ value: 'Date', position: 'insideBottom',
                          offset: -5, fill: theme.axisTick.fill,
                          fontSize: 11 }} />
          <YAxis
            tick={theme.axisTick}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            label={{ value: 'Excess Return', angle: -90,
                     position: 'insideLeft',
                     style: { textAnchor: 'middle' },
                     fill: theme.axisTick.fill, fontSize: 11 }}
          />
          <Tooltip
            contentStyle={theme.tooltipContentStyle}
            labelStyle={theme.tooltipLabelStyle}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {/* Outperformance / underperformance half-plane shading. */}
          <ReferenceArea y1={0} y2={ymax} fill={theme.positive} fillOpacity={0.05} />
          <ReferenceArea y1={ymin} y2={0} fill={theme.negative} fillOpacity={0.05} />
          <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={1.5} />
          {breakX && (
            <ReferenceLine x={breakX} stroke={theme.regimeBreak} strokeDasharray="4 4"
              label={{ value: 'Correlation Regime Break', fill: theme.regimeBreak, fontSize: 11,
                       position: 'insideTopRight' }} />
          )}
          {data.strategies.map((s, i) => (
            <Line key={s} type="monotone" dataKey={s} name={s}
                  stroke={theme.seriesColors[i % theme.seriesColors.length]}
                  strokeWidth={1.5} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </SectionCard>
  )
}

// ── 2. Rolling correlation chart ──────────────────────────────────────────────

export function RollingCorrelationChart(
  { data, theme = DARK_CHART_THEME }:
  { data: RollingCorrelation; theme?: ChartTheme },
) {
  // The vertical regime marker must land on an actual x value — snap it to
  // the first plotted month at or after the 2022 break.
  const breakX = data.points.find((p) => p.date >= data.regime_break)?.date
    ?? data.regime_break
  const avg = (x: number | null) => (x == null ? '—' : x.toFixed(2))

  return (
    <SectionCard
      title="Rolling Correlation — Equity vs Bonds"
      sectionId="rolling-correlation"
      tourId="rolling-correlation"
      infoKey="rolling_correlation_chart"
      theme={theme}
      subtitle={`${data.window_months}-month rolling Pearson correlation between monthly equity returns (S&P 500) and bond returns — IG (BND) and HY (BAMLHYH0A0HYM2TRIV). The 2022 hiking cycle is where equity-bond diversification broke down.`}
      dataExplain={{ currentValue:
        `Equity-IG rolling correlation: pre-2022 avg ${avg(data.pre_2022.equity_ig)}, `
        + `post-2022 avg ${avg(data.post_2022.equity_ig)}. Equity-HY: pre `
        + `${avg(data.pre_2022.equity_hy)}, post ${avg(data.post_2022.equity_hy)}. `
        + `${data.window_months}-month window; regime break ${data.regime_break}.` }}
    >
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data.points} margin={{ top: 8, right: 16, bottom: 32, left: 24 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.gridStroke} />
          <XAxis dataKey="date" tick={theme.axisTick}
                 minTickGap={48}
                 label={{ value: 'Date', position: 'insideBottom',
                          offset: -5, fill: theme.axisTick.fill,
                          fontSize: 11 }} />
          <YAxis domain={[-1, 1]} tick={theme.axisTick}
                 label={{ value: 'Correlation', angle: -90,
                          position: 'insideLeft',
                          style: { textAnchor: 'middle' },
                          fill: theme.axisTick.fill, fontSize: 11 }} />
          <Tooltip
            contentStyle={theme.tooltipContentStyle}
            labelStyle={theme.tooltipLabelStyle}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <ReferenceLine y={0} stroke="#475569" />
          <ReferenceLine x={breakX} stroke={theme.regimeBreak} strokeDasharray="4 4"
            label={{ value: 'Correlation Regime Break', fill: theme.regimeBreak, fontSize: 11,
                     position: 'insideTopRight' }} />
          {/* The two pair lines use fixed blue/amber hues; dark keeps the
              original literals, light routes through the theme palette so
              they stay distinguishable on white. */}
          <Line type="monotone" dataKey="equity_ig" name="Equity vs IG"
                stroke={theme.mode === 'light' ? theme.seriesColors[0] : '#3b82f6'}
                dot={false} strokeWidth={2} connectNulls />
          <Line type="monotone" dataKey="equity_hy" name="Equity vs HY"
                stroke={theme.mode === 'light' ? theme.seriesColors[3] : '#f59e0b'}
                dot={false} strokeWidth={2} connectNulls />
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
      sectionId="regime-conditional"
      tourId="regime-conditional"
      infoKey="regime_conditional_table"
      subtitle="Each strategy's monthly returns split at the 2022 regime break (boundary 2022-01-01) — pre- and post-period Sharpe and CAGR. Sorted by post-2022 Sharpe — which strategies held up once diversification stopped working."
      dataExplain={{ currentValue:
        'Sharpe and CAGR by strategy, split at the 2022 break — '
        + rows.map((r) =>
            `${r.strategy}: post-2022 Sharpe ${num(r.post_2022_sharpe)} `
            + `(pre ${num(r.pre_2022_sharpe)}), post-2022 CAGR `
            + `${pct(r.post_2022_cagr)}`).join('; ') }}
      exportButton={<TableExportButton tableId="regime_conditional" headers={headers} rows={exportRows} />}
    >
      <div className="overflow-x-auto">
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH sticky>Strategy</TH><TH right>Pre-2022 Sharpe</TH><TH right>Post-2022 Sharpe</TH>
          <TH right>Pre-2022 CAGR</TH><TH right>Post-2022 CAGR</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} className="border-b border-border/50 hover:bg-navy-800/40 transition-colors">
              <TD sticky>{r.strategy}</TD>
              <TD right mono>{num(r.pre_2022_sharpe)}</TD>
              <TD right mono>{num(r.post_2022_sharpe)}</TD>
              <TD right mono>{pct(r.pre_2022_cagr)}</TD>
              <TD right mono>{pct(r.post_2022_cagr)}</TD>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
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
      sectionId="drawdown"
      infoKey="drawdown_table"
      subtitle="Max peak-to-trough loss and months to a new equity high, computed from each strategy's monthly returns over the full study period. Sorted by max drawdown — deepest loss first."
      dataExplain={{ currentValue:
        'Max drawdown and recovery by strategy — '
        + rows.map((r) =>
            `${r.strategy}: ${pct(r.max_drawdown)}, recovery `
            + `${r.recovery_months == null
                ? 'not recovered' : `${r.recovery_months} months`}`).join('; ') }}
      exportButton={<TableExportButton tableId="drawdown_comparison" headers={headers} rows={exportRows} />}
    >
      <div className="overflow-x-auto">
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH sticky>Strategy</TH>
          <TH right>
            <ResponsiveHeader full="Max Drawdown" short="Max DD" />
          </TH>
          <TH right>
            <ResponsiveHeader full="Recovery (months)" short="Rec. (mo)" />
          </TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} className="border-b border-border/50 hover:bg-navy-800/40 transition-colors">
              <TD sticky>{r.strategy}</TD>
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
      </div>
    </SectionCard>
  )
}

// ── 5. Fama-French factor loadings table ──────────────────────────────────────

function FactorLoadingsTable(
  { rows, ffNote }: { rows: FactorRow[]; ffNote?: string | null },
) {
  const headers = ['Strategy', 'Alpha (annualized)', 'MKT-RF', 'SMB', 'HML', 'MOM', 'R-squared']
  const exportRows = rows.map((r) => [
    r.strategy, pct(r.alpha_annualized), num(r.mkt_rf), num(r.smb), num(r.hml),
    r.mom === null ? '—' : num(r.mom), num(r.r_squared),
  ])
  // A loading is rendered bold + with a * suffix when p < 0.05.
  const Beta = ({ v, sig }: { v: number; sig: boolean }) => (
    <span className={sig ? 'text-electric font-semibold' : ''}>
      {num(v)}{sig ? ' *' : ''}
    </span>
  )
  // A strategy whose history predates the momentum backfill falls back to a
  // three-factor fit — flag the table when any row did.
  const anyThreeFactor = rows.some((r) => r.model !== 'carhart_4factor')
  return (
    <SectionCard
      title="Carhart Four-Factor Loadings"
      sectionId="factor-loadings"
      tourId="factor-loadings"
      // Title-level InfoIcon restored May 22 2026 (Molly UAT Group 5).
      // The earlier comment claimed the per-column ExplainableText
      // wrappers were "enough", but ExplainableText only renders
      // chrome in Commentary mode — in Analyst and Present mode the
      // children render bare with no dotted underline, no info icon,
      // no hover affordance at all. The chart title therefore had NO
      // explainer entry point in two of three modes. The InfoIcon at
      // the title level covers the Carhart-four-factor model
      // explanation in every mode; the per-column ExplainableText
      // continues to provide per-metric depth in Commentary mode.
      infoKey="ff_factor_loadings"
      subtitle={
        'OLS regression of each strategy\'s monthly excess return on the '
        + 'Carhart four-factor model (MKT-RF, SMB, HML, MOM). * marks loadings '
        + 'significant at p < 0.05.'
        + (anyThreeFactor
          ? ' A dash in the MOM column marks a strategy whose history predates '
            + 'the momentum-factor data — those rows use a three-factor fit.'
          : '')
      }
      dataExplain={{ currentValue:
        'Carhart four-factor loadings by strategy — '
        + rows.map((r) =>
            `${r.strategy}: alpha ${pct(r.alpha_annualized)}, MKT-RF `
            + `${num(r.mkt_rf)}, SMB ${num(r.smb)}, HML ${num(r.hml)}, `
            + `MOM ${r.mom === null ? '—' : num(r.mom)}, `
            + `R² ${num(r.r_squared)}`).join('; ') }}
      exportButton={<TableExportButton tableId="factor_loadings" headers={headers} rows={exportRows} />}
    >
      <div className="overflow-x-auto">
      <table className="w-full">
        <thead><tr className="border-b border-border">
          <TH sticky>Strategy</TH>
          <TH right infoKey="ff_alpha" infoLabel="Carhart Alpha" term="alpha">Alpha (annualized)</TH>
          <TH right infoKey="ff_mkt_rf" infoLabel="Market Beta (MKT-RF)" term="mkt_rf">MKT-RF</TH>
          <TH right infoKey="ff_smb" infoLabel="SMB Factor Loading" term="smb">SMB</TH>
          <TH right infoKey="ff_hml" infoLabel="HML Factor Loading" term="hml">HML</TH>
          <TH right infoKey="ff_mom" infoLabel="Momentum Factor Loading" term="mom">MOM</TH>
          <TH right infoKey="ff_r2" infoLabel="R-squared" term="r_squared">R²</TH>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy} className="border-b border-border/50 hover:bg-navy-800/40 transition-colors">
              <TD sticky>{r.strategy}</TD>
              <TD right mono>
                <span className={r.alpha_significant ? 'text-electric font-semibold' : ''}>
                  {pct(r.alpha_annualized)}{r.alpha_significant ? ' *' : ''}
                </span>
              </TD>
              <TD right mono><Beta v={r.mkt_rf} sig={r.mkt_rf_significant} /></TD>
              <TD right mono><Beta v={r.smb} sig={r.smb_significant} /></TD>
              <TD right mono><Beta v={r.hml} sig={r.hml_significant} /></TD>
              <TD right mono>
                {r.mom === null
                  ? <span className="text-muted">—</span>
                  : <Beta v={r.mom} sig={r.mom_significant} />}
              </TD>
              <TD right mono>{num(r.r_squared)}</TD>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {ffNote && (
        <p className="text-2xs text-muted mt-2 leading-relaxed">{ffNote}</p>
      )}
    </SectionCard>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

// ── Cumulative total return chart ─────────────────────────────────────────────

// The dynamic strategies consume an initialisation lookback window before
// they produce a first return — window length is fixed by the backtester
// (OPTIMIZATION_WINDOW=36 months; momentum max lookback=12; regime
// window=3). Used only to attribute the shorter histories in the chart
// footnote — this is disclosure metadata, not strategy logic.
const LOOKBACK_WINDOWS: Record<string, number> = {
  MIN_VARIANCE: 36,
  BLACK_LITTERMAN: 36,
  MAX_SHARPE_ROLLING: 36,
  MOMENTUM_ROTATION: 12,
  REGIME_SWITCHING: 3,
}

export function CumulativeReturnChart(
  { data, theme = DARK_CHART_THEME }:
  { data: CumulativeReturns; theme?: ChartTheme },
) {
  const [logScale, setLogScale] = useState(false)

  // Snap the 2022 regime marker to the first plotted month at/after the break.
  const breakX = data.points.find((p) => p.date >= '2022-01-01')?.date

  // Shorter-series disclosure — strategies whose first return month is
  // later than the earliest series start. Each line already begins at 1.0
  // at its own first point (a leading null draws nothing); the markers and
  // footnote make the shorter history explicit rather than implied.
  const starts = data.start_dates ?? {}
  const startValues = Object.values(starts)
  const earliest = startValues.length
    ? startValues.reduce((a, b) => (a < b ? a : b))
    : undefined
  const shorter = earliest
    ? Object.entries(starts)
        .filter(([, d]) => d > earliest)
        .sort((a, b) => (a[1] < b[1] ? -1 : 1))
    : []
  const markerDates = [...new Set(shorter.map(([, d]) => d))]
  // Group the shorter strategies by lookback window for the footnote.
  const windowGroups = new Map<number, { names: string[]; date: string }>()
  for (const [name, d] of shorter) {
    const w = LOOKBACK_WINDOWS[name] ?? 0
    const g = windowGroups.get(w)
    if (g) g.names.push(name)
    else windowGroups.set(w, { names: [name], date: d })
  }
  const footnoteParts = [...windowGroups.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([w, g]) => {
      const verb = g.names.length > 1 ? 'start' : 'starts'
      const window = w > 0 ? ` (${w}-month initialisation window)` : ''
      return `${g.names.join(', ')} ${verb} ${monthLabel(g.date)}${window}`
    })

  const headers = ['Date', ...data.strategies]
  const exportRows = data.points.map((p) => [
    p.date, ...data.strategies.map((s) => p[s] ?? ''),
  ])

  // Terminal growth-of-$1 multiples from the last plotted month — the
  // headline figures the Data Explain reading is anchored to.
  const lastPoint = data.points[data.points.length - 1]
  const cumulativeSummary =
    `Growth of $1 over ${data.points.length} months for `
    + `${data.strategies.length} strategies. Terminal multiples: `
    + data.strategies.map((s) => {
        const v = lastPoint ? lastPoint[s] : undefined
        return `${s} ${typeof v === 'number' ? v.toFixed(2) : '—'}x`
      }).join(', ')

  return (
    <SectionCard
      title="Cumulative Total Return"
      sectionId="cumulative-returns"
      tourId="cumulative-return"
      infoKey="cumulative_return_chart"
      theme={theme}
      subtitle="Growth of $1 invested in each strategy over the full study period. The benchmark (100% equity) is the bold grey reference line. Use the scale button above to switch the Y axis between linear and logarithmic."
      dataExplain={{ currentValue: cumulativeSummary }}
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
        <LineChart data={data.points} margin={{ top: 8, right: 16, bottom: 32, left: 24 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.gridStroke} />
          <XAxis dataKey="date" tick={theme.axisTick} minTickGap={56}
                 label={{ value: 'Date', position: 'insideBottom',
                          offset: -5, fill: theme.axisTick.fill,
                          fontSize: 11 }} />
          <YAxis
            scale={logScale ? 'log' : 'linear'}
            domain={logScale ? ['auto', 'auto'] : [0, 'auto']}
            allowDataOverflow
            tick={theme.axisTick}
            tickFormatter={(v: number) => `${v.toFixed(1)}x`}
            label={{ value: 'Growth of $1', angle: -90,
                     position: 'insideLeft',
                     style: { textAnchor: 'middle' },
                     fill: theme.axisTick.fill, fontSize: 11 }}
          />
          <Tooltip
            contentStyle={theme.tooltipContentStyle}
            labelStyle={theme.tooltipLabelStyle}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {breakX && (
            <ReferenceLine x={breakX} stroke={theme.regimeBreak} strokeDasharray="4 4"
              label={{ value: 'Correlation Regime Break', fill: theme.regimeBreak, fontSize: 11,
                       position: 'insideTopRight' }} />
          )}
          {/* Subtle vertical tick at each dynamic strategy's start date. */}
          {markerDates.map((d) => (
            <ReferenceLine key={`start-${d}`} x={d}
              stroke={theme.axisTick.fill} strokeWidth={1}
              strokeDasharray="2 4" strokeOpacity={0.5} />
          ))}
          {data.strategies.map((s, i) => {
            const bench = isBenchmark(s)
            return (
              <Line
                key={s}
                type="monotone"
                dataKey={s}
                name={s}
                stroke={bench ? theme.benchmark : theme.seriesColors[i % theme.seriesColors.length]}
                strokeWidth={bench ? 2.5 : 1.5}
                dot={false}
                connectNulls
              />
            )
          })}
        </LineChart>
      </ResponsiveContainer>
      {footnoteParts.length > 0 && (
        <p className="text-2xs mt-2 leading-relaxed"
           style={{ color: theme.textSecondary }}>
          {'* '}{footnoteParts.join('. ')}. Growth-of-$1 is indexed to each
          strategy&apos;s own start date.
        </p>
      )}
    </SectionCard>
  )
}

// ── Sensitivity analysis ──────────────────────────────────────────────────────

function SensitivityChart(
  { s, theme = DARK_CHART_THEME }:
  { s: SensitivityStrategy; theme?: ChartTheme },
) {
  const light = theme.mode === 'light'
  return (
    <div
      className={light ? 'rounded p-3' : 'bg-navy-800 rounded p-3'}
      {...(light ? { style: { background: theme.background, border: `1px solid ${theme.border}` } } : {})}
    >
      <div
        className={`text-sm font-medium ${light ? '' : 'text-white'}`}
        {...(light ? { style: { color: theme.textPrimary } } : {})}
      >
        {s.strategy}
      </div>
      <div
        className={`text-2xs mb-2 ${light ? '' : 'text-muted'}`}
        {...(light ? { style: { color: theme.textSecondary } } : {})}
      >
        {s.parameter}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={s.points} margin={{ top: 8, right: 12, bottom: 4, left: 12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.gridStroke} />
          <XAxis dataKey="value" type="number" domain={['dataMin', 'dataMax']}
                 tick={{ fill: theme.axisTick.fill, fontSize: 10 }} />
          <YAxis tick={{ fill: theme.axisTick.fill, fontSize: 10 }}
                 tickFormatter={(v: number) => v.toFixed(2)}
                 label={{ value: 'Sharpe', angle: -90, position: 'insideLeft',
                          fill: theme.axisTick.fill, fontSize: 10 }} />
          <Tooltip
            contentStyle={theme.tooltipContentStyle}
            labelStyle={theme.tooltipLabelStyle}
          />
          <ReferenceLine x={s.current_value} stroke={theme.regimeBreak} strokeDasharray="4 4"
            label={{ value: 'current', fill: theme.regimeBreak, fontSize: 10,
                     position: 'top' }} />
          {/* Single fixed-blue series; dark keeps the original literal,
              light routes through the theme palette. */}
          <Line type="monotone" dataKey="sharpe"
                stroke={light ? theme.seriesColors[0] : '#3b82f6'}
                strokeWidth={2} dot={{ r: 3 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function SensitivityAnalysis(
  { theme = DARK_CHART_THEME }: { theme?: ChartTheme },
) {
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
      sectionId="sensitivity"
      infoKey="sensitivity_analysis"
      theme={theme}
      subtitle="How sensitive is each dynamic strategy's Sharpe ratio to its key parameter — a ±20% sweep around the current setting (~23 full backtests per strategy, monthly returns over the full study period). The vertical line marks the current setting."
      {...(strategies.length > 0
        ? { dataExplain: { currentValue:
            `Sharpe-ratio sensitivity to the key parameter for `
            + `${strategies.length} dynamic strategies — `
            + strategies.map((s) => `${s.strategy} (${s.parameter})`).join('; ') } }
        : {})}
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
          {strategies.map((s) => <SensitivityChart key={s.strategy} s={s} theme={theme} />)}
        </div>
      )}
    </SectionCard>
  )
}

// ── Strategy methodology panel ────────────────────────────────────────────────

function MetaField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-2xs uppercase tracking-wider text-muted">{label}</span>
      <p className="text-xs text-slate-300 leading-relaxed">{value}</p>
    </div>
  )
}

type MethodologyTab = 'methodology' | 'profile'

function StrategyMethodologyPanel({ rows }: { rows: StrategyMeta[] }) {
  const [openId, setOpenId] = useState<string | null>(null)
  // Item 9 — tab toggle inside the expanded accordion. 'methodology' is
  // the original signal_logic / economic_intuition / etc. content;
  // 'profile' is the PortfolioProfilePanel three-card layout. Reset to
  // 'methodology' when a different strategy is opened so the user
  // doesn't see the previous strategy's tab on the new one.
  const [tab, setTab] = useState<MethodologyTab>('methodology')
  const fmtWeights = (w: StrategyMeta['weights']): string =>
    w == null
      ? 'Optimized — weights are solved each rebalance, not fixed'
      : `Equity ${(w.equity * 100).toFixed(0)}% · IG ${(w.ig * 100).toFixed(0)}%`
        + ` · HY ${(w.hy * 100).toFixed(0)}%`

  return (
    <SectionCard
      title="Strategy Rules and Methodology"
      sectionId="methodology"
      subtitle="The construction logic of every strategy — and, for the dynamic strategies, the signal and the economic intuition behind it."
    >
      <div className="space-y-1.5">
        {rows.map((s) => {
          const open = openId === s.id
          return (
            <div key={s.id} className="border border-border rounded overflow-hidden">
              <button
                type="button"
                onClick={() => {
                  // Reset the tab to Methodology whenever a new
                  // strategy opens so the user doesn't carry the
                  // previous strategy's tab selection forward.
                  setTab('methodology')
                  setOpenId(open ? null : s.id)
                }}
                className="w-full flex items-center gap-2 px-3 py-2 min-h-[44px] hover:bg-navy-700 transition-colors"
              >
                {open
                  ? <ChevronDown className="w-4 h-4 text-muted shrink-0" />
                  : <ChevronRight className="w-4 h-4 text-muted shrink-0" />}
                <span className="text-white text-sm">{s.name}</span>
                <span className={`text-2xs px-1.5 py-0.5 rounded-full border ${
                  s.type === 'dynamic'
                    ? 'bg-electric/15 text-electric border-electric/30'
                    : 'bg-navy-700 text-muted border-border'
                }`}>
                  {s.type}
                </span>
              </button>
              {open && (
                <div className="border-t border-border">
                  {/* Item 9 — Methodology / Portfolio Profile tab toggle.
                      Methodology shows the existing signal_logic /
                      economic_intuition / etc. content; Portfolio
                      Profile shows the three-card AI-generated
                      characterisation panel. */}
                  <div className="flex gap-1 px-3 pt-2"
                       data-testid={`methodology-tabs-${s.id}`}>
                    {(['methodology', 'profile'] as MethodologyTab[]).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setTab(t)}
                        data-testid={`methodology-tab-${t}-${s.id}`}
                        className={`text-2xs px-2.5 py-1 rounded border transition-colors ${
                          tab === t
                            ? 'border-electric bg-electric/10 text-electric'
                            : 'border-border text-muted hover:text-white hover:border-border/80'
                        }`}>
                        {t === 'methodology' ? 'Methodology' : 'Portfolio Profile'}
                      </button>
                    ))}
                  </div>
                  <div className="px-3 py-2.5 space-y-2">
                    {tab === 'methodology' ? (
                      s.type === 'dynamic' ? (
                        <>
                          {s.signal_logic && <MetaField label="Signal logic" value={s.signal_logic} />}
                          {s.economic_intuition &&
                            <MetaField label="Economic intuition" value={s.economic_intuition} />}
                          <MetaField label="Rebalancing" value={s.rebalancing} />
                          {s.key_parameter && (
                            <MetaField
                              label="Key parameter"
                              value={`${s.key_parameter} — ${s.parameter_value ?? '—'}`}
                            />
                          )}
                          <MetaField label="Rationale" value={s.rationale} />
                        </>
                      ) : (
                        <>
                          <MetaField label="Weights" value={fmtWeights(s.weights)} />
                          <MetaField label="Rebalancing" value={s.rebalancing} />
                          <MetaField label="Construction rationale" value={s.rationale} />
                        </>
                      )
                    ) : (
                      <PortfolioProfilePanel
                        strategyId={s.id}
                        strategyName={s.name} />
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </SectionCard>
  )
}

export default function AcademicAnalytics() {
  const [data, setData] = useState<AnalyticsPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { status: dataStatus } = useDataStatus()

  // The Fama-French factors can end earlier than the market data — the
  // Carhart regression then covers a shorter window. Surface that as a
  // second study-period line and a factor-loadings footnote.
  const ffTable = tableOf(dataStatus, 'ff_factors_monthly')
  const mktTable = tableOf(dataStatus, 'market_data_monthly')
  const ffLagsMarket = !!(
    ffTable && mktTable && ffTable.max_date && mktTable.max_date
    && ffTable.max_date < mktTable.max_date
  )
  let ffNote: string | null = null
  if (ffLagsMarket && ffTable?.min_date && ffTable.max_date
      && mktTable?.max_date) {
    const missing = monthsBetween(ffTable.max_date, mktTable.max_date)
    const lag = missing.length
    ffNote = '* Carhart four-factor regression covers '
      + `${monthLabel(ffTable.min_date)} to ${monthLabel(ffTable.max_date)} `
      + `(${lag} month${lag === 1 ? '' : 's'} behind market data — `
      + `${missing.join(', ')} factors not yet posted by Ken French).`
  }

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
    // lg:pr-28 reserves ~112px of right-edge clearance on lg+ screens
    // for the FloatingSectionNav collapsed pill (anchored at right-0,
    // ~110px wide with the 'Sections' label). Without it the Sensitivity
    // Analysis 2-col chart grid extends under the nav tab on lg+, and
    // the rightmost SensitivityChart (e.g. Regime Switching) reads
    // half-covered by the floating control. The padding is lg-gated so
    // mobile / md layouts (where the nav is a bottom drawer, not a
    // right-edge pill) keep their normal full-width content.
    <div className="p-4 md:p-6 lg:pr-28 space-y-5">
      <FloatingSectionNav pageKey="academic-analytics" />
      <div>
        <h1 className="text-xl font-semibold text-white" data-tour="analytics-header">Academic Analytics</h1>
        <p className="text-sm text-muted mt-1">
          Cumulative return, summary statistics, the equity-bond correlation regime break,
          rolling excess return, regime-conditional performance, drawdowns, Carhart
          four-factor loadings, parameter sensitivity, and strategy methodology — the
          analytical backbone of the midpoint paper. Every table exports to CSV.
        </p>
        {data?.study_period && (
          <p className="text-xs text-muted mt-1">
            <span className="font-mono">
              {/* UAT 2026-05-24 (#117): wrap raw-ISO dates through
                 formatDate() so the platform-wide MM-DD-YYYY
                 convention (PR #120 commit bdbd702) applies here
                 too. Previously the Study period line was rendering
                 raw "2002-07-31 → 2026-04-30" which was the last
                 raw-ISO date pair on the Analytics page. */}
              Study period: {formatDate(data.study_period.start)}
              {' → '}{formatDate(data.study_period.end)}
              {' '}({data.study_period.n_months} months)
            </span>
            . Five strategies have shorter histories due to initialisation
            windows — see footnotes.
          </p>
        )}
        {ffLagsMarket && ffTable && (
          <p className="text-xs text-muted mt-0.5 font-mono">
            Factor model: {formatDate(ffTable.min_date)}
            {' → '}{formatDate(ffTable.max_date)}
            {' '}({ffTable.row_count} months)
          </p>
        )}
        <div className="mt-1"><DataCurrencyBar /></div>
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
          {data.bootstrap_ci_sharpe && data.bootstrap_ci_sharpe.length > 0 &&
            <BootstrapCITable rows={data.bootstrap_ci_sharpe} />}
          {data.bootstrap_ci_samples && data.bootstrap_ci_samples.length > 0 &&
            <BootstrapCIDensityChart rows={data.bootstrap_ci_samples} />}
          {data.rolling_correlation && data.rolling_correlation.points.length > 0 &&
            <RollingCorrelationChart data={data.rolling_correlation} />}
          {/* Strategy Correlations — pairwise Pearson across the ten
              strategies + benchmark, full / pre-2022 / post-2022. Sits
              after the rolling-correlation chart so the eye flows from
              equity-vs-bond regime break → full strategy correlation
              picture. Independently fetched (item 8 commit 3). */}
          <CorrelationHeatmap />
          <ChartCommentStrip
            chartId="correlation_heatmap"
            chartType="diversification_heatmap"
            chartData={null}
            accentColor={ACCENT}
          />
          {data.rolling_excess_return && data.rolling_excess_return.points.length > 0 &&
            <RollingExcessReturnChart data={data.rolling_excess_return} />}
          {data.regime_conditional && data.regime_conditional.length > 0 &&
            <RegimeConditionalTable rows={data.regime_conditional} />}
          {data.drawdown_comparison && data.drawdown_comparison.length > 0 &&
            <DrawdownComparisonTable rows={data.drawdown_comparison} />}
          {/* Drawdown DURATION (the time dimension) sits adjacent to
              DrawdownComparisonTable (the depth dimension) — same
              concept, complementary axes. Item 8 commit 4. */}
          <DrawdownDurationTable />
          <ChartCommentStrip
            chartId="drawdown_duration"
            chartType="diversification_table"
            chartData={null}
            accentColor={ACCENT}
          />
          {/* Tail risk (VaR / CVaR) follows the drawdown picture: same
              "how bad can it get" theme but parametrized at 95% / 99%
              confidence levels rather than worst observed. Item 8
              commit 4. */}
          <TailRiskTable />
          <ChartCommentStrip
            chartId="tail_risk"
            chartType="diversification_table"
            chartData={null}
            accentColor={ACCENT}
          />
          {/* Up / Down capture — the asymmetry view. Comes after the
              tail-risk picture so the reader has the worst-case framing
              before seeing the asymmetric capture profile. Item 8
              commit 4. */}
          <CaptureScatter />
          <ChartCommentStrip
            chartId="capture_ratios"
            chartType="diversification_scatter"
            chartData={null}
            accentColor={ACCENT}
          />
          {/* Crisis performance — historical stress test. Reads naturally
              after capture: capture is the average asymmetry, crisis
              performance is the worst-case asymmetry. Item 8 commit 5. */}
          <CrisisPerformanceTable />
          <ChartCommentStrip
            chartId="crisis_performance"
            chartType="diversification_table"
            chartData={null}
            accentColor={ACCENT}
          />
          {/* Marginal contribution to risk — flips the question from
              "what is each strategy's risk" to "how does each strategy
              contribute to the portfolio's risk". The bar chart's visual
              compactness pairs well with the table-heavy sections above.
              Item 8 commit 5. */}
          <RiskContributionBar />
          <ChartCommentStrip
            chartId="marginal_contribution_to_risk"
            chartType="diversification_bar"
            chartData={null}
            accentColor={ACCENT}
          />
          {/* Return distribution — distribution moments + normality test.
              Closes the diversification suite: every section above reads
              more carefully once the reader knows which strategies pass
              normality (and which don't). Item 8 commit 5. */}
          <DistributionTable />
          <ChartCommentStrip
            chartId="return_distribution"
            chartType="diversification_table"
            chartData={null}
            accentColor={ACCENT}
          />
          {data.factor_loadings && data.factor_loadings.length > 0 &&
            <FactorLoadingsTable rows={data.factor_loadings} ffNote={ffNote} />}
          <SensitivityAnalysis />
          {data.strategy_metadata && data.strategy_metadata.length > 0 &&
            <StrategyMethodologyPanel rows={data.strategy_metadata} />}
        </>
      )}
    </div>
  )
}
