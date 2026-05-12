import { AlertTriangle, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { RegimeData } from '../types/api'

interface RegimeStyleConfig {
  label: string
  color: string
  bg: string
  border: string
  Icon: LucideIcon
}

const REGIME_CONFIG: Record<string, RegimeStyleConfig> = {
  BULL:       { label: 'BULL',       color: 'text-success', bg: 'bg-success/10', border: 'border-success/20', Icon: TrendingUp },
  BEAR:       { label: 'BEAR',       color: 'text-danger',  bg: 'bg-danger/10',  border: 'border-danger/20',  Icon: TrendingDown },
  TRANSITION: { label: 'TRANSITION', color: 'text-warning', bg: 'bg-warning/10', border: 'border-warning/20', Icon: Minus },
  UNCERTAIN:  { label: 'UNCERTAIN',  color: 'text-warning', bg: 'bg-warning/10', border: 'border-warning/20', Icon: AlertTriangle },
}

function MetricPill({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 bg-navy-700 border border-border rounded">
      <span className="text-muted text-2xs tracking-wide uppercase">{label}</span>
      <span className={`text-white text-xs ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}

export default function RegimeIndicator({ regime }: { regime: RegimeData }) {
  const {
    threshold_regime = 'BULL',
    hmm_regime = 0,
    hmm_probabilities = [0.82, 0.12, 0.06],
    regimes_agree = true,
    vix_level = 14.3,
    yield_curve_slope = 0.42,
    credit_spread = 3.21,
    as_of = '2024-12-31',
  } = regime

  const displayRegime = regimes_agree ? threshold_regime : 'UNCERTAIN'
  const cfg = REGIME_CONFIG[displayRegime] ?? REGIME_CONFIG['BULL']!
  const { Icon } = cfg

  const hmm_labels = ['BULL', 'BEAR', 'TRANSITION']
  const dominantHmmLabel = hmm_labels[hmm_regime] ?? 'BULL'
  const hmmProb = hmm_probabilities[hmm_regime] ?? 0

  return (
    <div className={`border-b border-border ${cfg.bg} px-6 py-2.5`}>
      <div className="max-w-screen-xl mx-auto flex items-center gap-4 flex-wrap">
        {/* Regime badge */}
        <div className={`flex items-center gap-2 px-3 py-1 rounded border ${cfg.border} ${cfg.bg}`}>
          <Icon className={`w-3.5 h-3.5 ${cfg.color}`} />
          <span className={`font-mono font-semibold text-sm tracking-widest ${cfg.color}`}>
            {displayRegime}
          </span>
        </div>

        {/* Uncertainty warning */}
        {!regimes_agree && (
          <div className="flex items-center gap-1.5 text-warning text-xs">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>HMM and threshold signals disagree — regime classified as UNCERTAIN</span>
          </div>
        )}

        {/* Market metrics */}
        <div className="flex items-center gap-2 flex-wrap ml-auto">
          <MetricPill label="VIX" value={vix_level.toFixed(1)} />
          <MetricPill label="10Y−2Y" value={`${yield_curve_slope >= 0 ? '+' : ''}${yield_curve_slope.toFixed(2)}%`} />
          <MetricPill label="HY Spread" value={`${credit_spread.toFixed(2)}%`} />
          <MetricPill label="Threshold" value={threshold_regime} mono={false} />
          <MetricPill label="HMM" value={`${dominantHmmLabel} (${(hmmProb * 100).toFixed(0)}%)`} />
          <span className="text-muted text-2xs font-mono ml-1">as of {as_of}</span>
        </div>
      </div>
    </div>
  )
}
