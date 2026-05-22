/**
 * PortfolioProfilePanel — the three-card per-strategy profile.
 *
 * Item 9 Commit 3 (May 22 2026). Reads from the
 * useCharacterisationsStore Zustand store (one fetch per session;
 * shared with the Dashboard behavioural_tag in Commit 4). The three
 * cards:
 *
 *   Card 1 "How it's built"
 *     - construction_summary paragraph
 *     - Stat row: Holdings · Turnover · Concentration · Rebalances
 *
 *   Card 2 "Performance conditions"
 *     - Two columns: Tends to outperform / Tends to underperform
 *       Each carries an up/down indicator and the AI-generated
 *       when-clause for that side.
 *     - regime_sensitivity in muted text below
 *
 *   Card 3 "Role in the portfolio"
 *     - diversification_role paragraph
 *     - primary_risk_factor as a badge linking to the Factor
 *       Loadings chart on the Analytics page.
 *
 * Empty state: when the characterisation is not yet computed (cold
 * deploy that has not seen a strategy_cache write yet), the panel
 * renders a single muted card explaining the auto-refresh will
 * populate it after the next ingestion. The agent layer reads from
 * the DB directly, so a missing frontend cache row does NOT mean the
 * council / academic-review prompts also miss the context.
 */
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  TrendingUp, TrendingDown, AlertCircle,
} from 'lucide-react'
import {
  useCharacterisationsStore,
} from '../stores/strategyCharacterisationsStore'
import type {
  StrategyCharacterisation,
} from '../stores/strategyCharacterisationsStore'


interface PortfolioProfilePanelProps {
  /** The strategy_id from STRATEGY_METADATA — e.g. "BENCHMARK",
   *  "VOL_TARGETING". Maps directly to the characterisation row's
   *  strategy_id key. */
  strategyId: string
  /** Optional display name override — falls back to a humanised
   *  version of strategyId when omitted. */
  strategyName?: string
}


function fmtNumber(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}


function humanise(strategyId: string): string {
  return strategyId.replace(/_/g, ' ')
}


function CardShell({
  title, accent = '#3b82f6', children, testId,
}: {
  title: string
  accent?: string
  children: React.ReactNode
  testId: string
}) {
  return (
    <div className="card p-5"
         style={{ borderLeft: `3px solid ${accent}` }}
         data-testid={testId}>
      <h3 className="text-sm font-semibold text-white mb-3">{title}</h3>
      {children}
    </div>
  )
}


function StatTile({
  label, value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="flex flex-col">
      <span className="text-2xs uppercase tracking-wider text-muted mb-0.5">
        {label}
      </span>
      <span className="font-mono text-sm text-white font-semibold">{value}</span>
    </div>
  )
}


function HowItsBuiltCard({
  row,
}: {
  row: StrategyCharacterisation
}) {
  const pc = row.portfolio_characteristics
  return (
    <CardShell title="How it's built" accent="#3b82f6"
               testId="profile-card-how-its-built">
      <p className="text-xs text-slate-200 leading-relaxed mb-4"
         data-testid="profile-construction-summary">
        {row.construction_summary || '—'}
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3
                      border-t border-border">
        <StatTile
          label="Holdings"
          value={pc.avg_holdings !== null
            ? `${fmtNumber(pc.avg_holdings, 1)} avg`
            : '—'} />
        <StatTile
          label="Turnover"
          value={pc.avg_turnover_pct !== null
            ? `${fmtNumber(pc.avg_turnover_pct, 1)}%`
            : '—'} />
        {/* Concentration label notes "largest" because the universe is
            three assets — top-5 is trivially 100%, so we surface the
            average max-weight as the meaningful concentration figure. */}
        <StatTile
          label="Largest holding"
          value={pc.avg_concentration !== null
            ? `${fmtNumber(pc.avg_concentration, 0)}% avg`
            : '—'} />
        <StatTile
          label="Rebalances"
          value={pc.rebalance_frequency || '—'} />
      </div>
    </CardShell>
  )
}


function PerformanceConditionsCard({
  row,
}: {
  row: StrategyCharacterisation
}) {
  const bp = row.behavioural_profile
  return (
    <CardShell title="Performance conditions" accent="#0d9488"
               testId="profile-card-performance-conditions">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div data-testid="profile-outperforms-column">
          <div className="flex items-center gap-1.5 mb-1.5">
            <TrendingUp className="w-4 h-4 text-positive" />
            <span className="text-2xs uppercase tracking-wider
                              text-positive font-semibold">
              Tends to outperform
            </span>
          </div>
          <p className="text-xs text-slate-200 leading-relaxed">
            {bp.outperforms_when || '—'}
          </p>
        </div>
        <div data-testid="profile-underperforms-column">
          <div className="flex items-center gap-1.5 mb-1.5">
            <TrendingDown className="w-4 h-4 text-warning" />
            <span className="text-2xs uppercase tracking-wider
                              text-warning font-semibold">
              Tends to underperform
            </span>
          </div>
          <p className="text-xs text-slate-200 leading-relaxed">
            {bp.underperforms_when || '—'}
          </p>
        </div>
      </div>
      {row.regime_sensitivity && (
        <p className="text-2xs text-muted italic mt-4 pt-3
                       border-t border-border leading-relaxed"
           data-testid="profile-regime-sensitivity">
          {row.regime_sensitivity}
        </p>
      )}
    </CardShell>
  )
}


function RoleInPortfolioCard({
  row,
}: {
  row: StrategyCharacterisation
}) {
  const bp = row.behavioural_profile
  return (
    <CardShell title="Role in the portfolio" accent="#8b5cf6"
               testId="profile-card-role">
      <p className="text-xs text-slate-200 leading-relaxed mb-4"
         data-testid="profile-diversification-role">
        {bp.diversification_role || '—'}
      </p>
      <div className="pt-3 border-t border-border">
        <div className="text-2xs uppercase tracking-wider text-muted mb-1.5">
          Primary risk factor
        </div>
        {/* Factor badge links to the Factor Loadings table on the
            Analytics page (the same surface in the StrategyMethodology
            screen carries the regression table). The hash takes the
            user straight to the section. */}
        <Link
          to="/analytics#factor-loadings"
          className="inline-flex items-center gap-1.5 px-2.5 py-1
                      rounded-full border border-purple-400/40
                      bg-purple-500/10 text-purple-200 text-xs
                      hover:bg-purple-500/20 hover:border-purple-400/60
                      transition-colors"
          data-testid="profile-primary-risk-factor"
        >
          <span>{bp.primary_risk_factor || 'Market exposure'}</span>
          <span className="text-2xs opacity-70">→ Factor Loadings</span>
        </Link>
      </div>
    </CardShell>
  )
}


export function PortfolioProfilePanel({
  strategyId, strategyName,
}: PortfolioProfilePanelProps) {
  const byId = useCharacterisationsStore((s) => s.byId)
  const loading = useCharacterisationsStore((s) => s.loading)
  const loaded = useCharacterisationsStore((s) => s.loaded)
  const available = useCharacterisationsStore((s) => s.available)
  const load = useCharacterisationsStore((s) => s.load)

  useEffect(() => { void load() }, [load])

  const displayName = strategyName ?? humanise(strategyId)
  const row = byId[strategyId]

  if (loading && !loaded) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="portfolio-profile-loading">
        <div className="flex items-center gap-2 text-muted text-sm">
          Loading {displayName} portfolio profile…
        </div>
      </div>
    )
  }

  if (!available || !row) {
    return (
      <div className="card p-5"
           style={{ borderLeft: '3px solid #3b82f6' }}
           data-testid="portfolio-profile-empty">
        <h3 className="text-sm font-semibold text-white mb-2 flex items-center gap-1.5">
          <AlertCircle className="w-4 h-4 text-warning" />
          Portfolio Profile not yet computed
        </h3>
        <p className="text-xs text-muted leading-relaxed">
          The {displayName} portfolio profile will be generated
          automatically after the next strategy cache refresh. The
          agent layer reads characterisation data directly from the
          database, so council, explainer and academic-review prompts
          may still surface this context once the refresh completes.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-3" data-testid="portfolio-profile-panel">
      <HowItsBuiltCard row={row} />
      <PerformanceConditionsCard row={row} />
      <RoleInPortfolioCard row={row} />
    </div>
  )
}
