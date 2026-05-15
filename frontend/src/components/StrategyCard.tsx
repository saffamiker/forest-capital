import { CheckCircle, XCircle, ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import type { StrategyResult, StressResults } from '../types/strategies'
import ExplainableText from './ExplainableText'

function pct(v: number | undefined, decimals = 1) {
  return v != null ? `${(v * 100).toFixed(decimals)}%` : '—'
}

function fmt(v: number | undefined, decimals = 2) {
  return typeof v === 'number' ? v.toFixed(decimals) : '—'
}

function pFmt(p: number | undefined) {
  if (p == null) return '—'
  if (p >= 0.01) return p.toFixed(3)
  return p.toFixed(4)
}

interface SignificanceBadgeProps {
  label: string
  pValue: number | undefined
  threshold?: number
}

function SignificanceBadge({ label, pValue, threshold = 0.005 }: SignificanceBadgeProps) {
  const pass = pValue != null && pValue <= threshold
  return (
    <div className="flex items-center justify-between py-1 border-b border-border/50 last:border-0">
      <span className="text-muted text-2xs">{label}</span>
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-2xs text-slate-400">p={pFmt(pValue)}</span>
        {pass ? (
          <span className="badge-pass">PASS</span>
        ) : (
          <span className="badge-fail">FAIL</span>
        )}
      </div>
    </div>
  )
}

function StabilityGauge({ score }: { score: number | undefined }) {
  if (score == null) {
    return <span className="font-mono text-xs text-muted">—</span>
  }
  const pctVal = Math.round(score * 100)
  const color = score >= 0.75 ? '#22c55e' : score >= 0.60 ? '#f59e0b' : '#ef4444'
  const passes = score >= 0.60
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-navy-700 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pctVal}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono text-xs" style={{ color }}>{score.toFixed(2)}</span>
      {passes ? (
        <CheckCircle className="w-3 h-3 text-success" />
      ) : (
        <XCircle className="w-3 h-3 text-danger" />
      )}
    </div>
  )
}

function StressBar({ results }: { results: StressResults }) {
  const scenarios = (Object.entries(results) as [string, unknown][]).filter(([k]) => k !== 'note') as [
    string,
    { vs_benchmark: number; return: number; max_dd: number }
  ][]
  const vals = scenarios.map(([, v]) => v.vs_benchmark)
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const range = max - min || 1

  return (
    <div className="space-y-1">
      {scenarios.map(([name, v]) => {
        const bar = ((v.vs_benchmark - min) / range) * 100
        const color = v.vs_benchmark > 0 ? '#22c55e' : v.vs_benchmark > -0.05 ? '#f59e0b' : '#ef4444'
        return (
          <div key={name} className="flex items-center gap-2">
            <span className="text-muted text-2xs w-20 shrink-0">{name.replace(/_/g, ' ')}</span>
            <div className="flex-1 h-1 bg-navy-700 rounded-full overflow-hidden">
              <div className="h-full rounded-full" style={{ width: `${bar}%`, backgroundColor: color }} />
            </div>
            <span className="font-mono text-2xs w-12 text-right" style={{ color }}>
              {v.vs_benchmark >= 0 ? '+' : ''}{pct(v.vs_benchmark)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

interface StrategyCardProps {
  strategy: StrategyResult
  onAskCouncil?: (strategyName: string) => void
}

export default function StrategyCard({ strategy, onAskCouncil }: StrategyCardProps) {
  const [expanded, setExpanded] = useState(false)
  const s = strategy

  const typeBadge = s.strategy_type === 'dynamic'
    ? 'bg-electric/10 text-electric border-electric/20'
    : 'bg-navy-600 text-slate-300 border-border'

  const sigColor = s.is_significant ? 'text-success' : (s.tier1_gates_passed ?? 0) >= 3 ? 'text-warning' : 'text-danger'

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-border">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-white font-semibold text-sm tracking-wide">
                {s.strategy_name.replace(/_/g, ' ')}
              </h3>
              <span className={`text-2xs px-1.5 py-0.5 rounded border font-medium ${typeBadge}`}>
                {(s.strategy_type ?? 'static').toUpperCase()}
              </span>
            </div>
            <p className={`text-2xs mt-0.5 font-mono ${sigColor}`}>
              {s.tier1_gates_passed ?? '?'}/5 Tier 1 gates
              {s.is_significant && ' · ✓ SIGNIFICANT'}
            </p>
          </div>
          {s.is_significant && (
            <div className="badge-pass shrink-0">SIGNIFICANT</div>
          )}
        </div>
      </div>

      {/* Core metrics — labels wrapped in ExplainableText with stable
          term IDs that line up with the dashboard table headers, so a
          glossary lookup hits the same entry whether the user clicks
          the Sharpe label here or in the Dashboard table. */}
      <div className="px-4 py-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide">
            <ExplainableText term="sharpe_ratio">Sharpe Ratio</ExplainableText>
          </div>
          <div className="font-mono text-white text-sm font-semibold">{fmt(s.sharpe_ratio)}</div>
          <div className="text-2xs text-muted font-mono">
            [{fmt(s.sharpe_ci_95?.[0])} – {fmt(s.sharpe_ci_95?.[1])}] 95% CI
          </div>
        </div>
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide">
            <ExplainableText term="cagr">CAGR</ExplainableText>
          </div>
          <div className="font-mono text-white text-sm font-semibold">{pct(s.cagr)}</div>
          <div className="text-2xs text-muted font-mono">OOS: {pct(s.oos_cagr)}</div>
        </div>
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide">
            <ExplainableText term="max_drawdown">Max Drawdown</ExplainableText>
          </div>
          <div className="font-mono text-danger text-sm font-semibold">{pct(s.max_drawdown)}</div>
          <div className="text-2xs text-muted font-mono">{s.drawdown_duration_days}d duration</div>
        </div>
        <div>
          <div className="text-2xs text-muted uppercase tracking-wide">
            <ExplainableText term="volatility">Volatility</ExplainableText>
          </div>
          <div className="font-mono text-white text-sm font-semibold">{pct(s.volatility)}</div>
          <div className="text-2xs text-muted font-mono">β={fmt(s.beta)}</div>
        </div>
      </div>

      {/* CV Stability */}
      <div className="px-4 pb-3">
        <div className="text-2xs text-muted uppercase tracking-wide mb-1">
          <ExplainableText term="cv_score">CV Stability Score</ExplainableText>
        </div>
        <StabilityGauge score={s.cv_stability_score} />
      </div>

      {/* Expand toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2 border-t border-border text-muted hover:text-white hover:bg-navy-700 transition-colors text-xs"
      >
        <span>{expanded ? 'Less detail' : 'More detail'}</span>
        {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-border pt-3">
          {/* Significance tests */}
          <div>
            <div className="section-header mb-2">
              <ExplainableText term="tier1_gates">Tier 1 Significance Tests (p &lt; 0.005)</ExplainableText>
            </div>
            <SignificanceBadge label="Paired t-test"         pValue={s.p_value_ttest} />
            <SignificanceBadge label="Jobson-Korkie Sharpe"  pValue={s.p_value_sharpe_jk} />
            <SignificanceBadge label="Alpha (Newey-West)"    pValue={s.p_value_alpha} />
            <SignificanceBadge label="FDR corrected"         pValue={s.p_value_corrected} />
            <SignificanceBadge label="OOS walk-forward"      pValue={s.oos_p_value} />
          </div>

          {/* DSR / PSR — labels carry stable term IDs so the glossary
              tooltip explains each Lopez de Prado metric. */}
          <div>
            <div className="section-header mb-2">Lopez de Prado Metrics</div>
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: 'Deflated SR',      term: 'dsr',      value: fmt(s.deflated_sharpe_ratio) },
                { label: 'DSR p-value',      term: 'dsr',      value: pFmt(s.dsr_p_value) },
                { label: 'Probabilistic SR', term: 'sharpe_ratio', value: fmt(s.probabilistic_sharpe_ratio) },
                { label: 'SPA p-value',      term: 'p_fdr',    value: pFmt(s.spa_p_value) },
              ].map(({ label, term, value }) => (
                <div key={label} className="bg-navy-700 rounded p-2">
                  <div className="text-2xs text-muted">
                    <ExplainableText term={term}>{label}</ExplainableText>
                  </div>
                  <div className="font-mono text-white text-xs mt-0.5">{value}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Stress tests */}
          {s.stress_results && (
            <div>
              <div className="section-header mb-2">Stress Tests — vs Benchmark (directional only)</div>
              <StressBar results={s.stress_results} />
              {s.stress_results.note && (
                <p className="text-muted text-2xs mt-1.5 italic">{s.stress_results.note}</p>
              )}
            </div>
          )}

          {/* Economic significance */}
          <div className="bg-navy-700 rounded p-3">
            <div className="section-header mb-1.5">Economic Significance</div>
            <div className="flex items-center justify-between">
              <span className="text-muted text-xs">Alpha after costs</span>
              <span className={`font-mono text-xs ${(s.alpha_after_costs_bps ?? 0) >= 50 ? 'text-success' : 'text-warning'}`}>
                {s.alpha_after_costs_bps ?? '—'}bps {(s.alpha_after_costs_bps ?? 0) >= 50 ? '✓' : '< 50bps threshold'}
              </span>
            </div>
          </div>

          {/* Significance summary */}
          <div className="bg-navy-700 rounded p-3">
            <div className="section-header mb-1">Significance Summary</div>
            <p className="text-slate-300 text-xs">{s.significance_summary}</p>
          </div>
        </div>
      )}

      {/* Ask council */}
      <div className="px-4 pb-3 mt-1">
        <button
          onClick={() => onAskCouncil?.(s.strategy_name)}
          className="w-full text-xs text-electric border border-electric/20 rounded py-1.5 hover:bg-electric/10 transition-colors"
        >
          Ask the Council about {s.strategy_name.replace(/_/g, ' ')} →
        </button>
      </div>
    </div>
  )
}
