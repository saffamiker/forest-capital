/**
 * SignificanceJourneyMatrix — five Tier 1 gates per strategy, scannable
 * at a glance. Every cell shows the actual metric value (p=0.049, q=0.066,
 * 0.65) rather than just a pass/fail tile, so the audience never has to
 * hover to read a number — the matrix is presentation-ready in print too.
 *
 * Reads gates directly from StrategyResult so it works offline of the
 * chart-data endpoint — useful when /api/v1/charts/data is cold.
 */
import type { StrategyResult } from '../../types/strategies'
import { prettyName, tooltipLine } from '../../lib/strategyColors'
import StrategyTypeBadge from '../StrategyTypeBadge'
import ExplainableText from '../ExplainableText'

interface Gate {
  label: string
  metricName: string                           // for tooltip standardisation
  // Glossary term ID. Wrapping the column header in ExplainableText
  // gives the audience a click-to-explain affordance for each Tier 1
  // gate without crowding the cells. IDs are stable so the Explainer
  // Agent prompt can emit them.
  term: string
  pass: (s: StrategyResult) => boolean
  // Returns the value to render inside the cell — no "p=" prefix needed
  // when the column header already says "T-TEST", so each gate decides
  // its own formatting. Bold-cased, sub-second to read.
  cellText: (s: StrategyResult) => string
}

// Thresholds match backend/tools/backtester.py run_all_strategies gate logic.
const GATES: Gate[] = [
  {
    label: 'T-TEST',
    metricName: 'Full-period p-value',
    term: 'tier1_t_test',
    pass: (s) => (s.p_value_ttest ?? 1) < 0.005,
    cellText: (s) => `p=${(s.p_value_ttest ?? 1).toFixed(3)}`,
  },
  {
    label: 'FDR',
    metricName: 'FDR-corrected q-value',
    term: 'tier1_fdr_correction',
    pass: (s) => (s.p_value_corrected ?? 1) < 0.005,
    cellText: (s) => `q=${(s.p_value_corrected ?? 1).toFixed(3)}`,
  },
  {
    label: 'DSR',
    metricName: 'Deflated Sharpe p-value',
    term: 'tier1_dsr',
    pass: (s) => (s.dsr_p_value ?? 1) < 0.005,
    cellText: (s) => `p=${(s.dsr_p_value ?? 1).toFixed(3)}`,
  },
  {
    label: 'OOS',
    metricName: 'Out-of-sample p-value',
    term: 'tier1_oos',
    pass: (s) => (s.oos_p_value ?? 1) < 0.050,
    cellText: (s) => `p=${(s.oos_p_value ?? 1).toFixed(3)}`,
  },
  {
    label: 'CV',
    metricName: 'CV stability score',
    term: 'tier1_cv',
    pass: (s) => (s.cv_stability_score ?? 0) >= 0.60,
    cellText: (s) => `${(s.cv_stability_score ?? 0).toFixed(2)}`,
  },
]

interface Props {
  strategies: StrategyResult[]
}

export default function SignificanceJourneyMatrix({ strategies }: Props) {
  const sorted = [...strategies].sort(
    (a, b) => (b.tier1_gates_passed ?? 0) - (a.tier1_gates_passed ?? 0),
  )

  return (
    <div className="card p-4" data-testid="significance-journey-matrix">
      <div className="mb-3">
        <h3 className="text-white font-semibold text-sm">Significance Journey Matrix</h3>
        <p className="text-muted text-xs mt-0.5">
          Five Tier 1 gates per strategy — all five must pass for is_significant
        </p>
      </div>

      <div className="overflow-x-auto">
        {/* Compact table: 11px font, 4px row padding. Numbers live inside
            each cell so the matrix is scannable in print and screenshot. */}
        <table className="w-full text-2xs">
          <thead>
            <tr className="text-muted uppercase tracking-wide">
              <th className="text-left py-1.5 pr-3">Strategy</th>
              {GATES.map((g) => (
                <th key={g.label} className="px-1.5 py-1.5 text-center w-20">
                  <ExplainableText term={g.term}>{g.label}</ExplainableText>
                </th>
              ))}
              <th className="px-1.5 py-1.5 text-right w-12">Total</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.strategy_name} className="border-t border-border/40">
                <td className="py-1 pr-3 align-middle">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-white font-mono text-xs">{prettyName(s.strategy_name)}</span>
                    <StrategyTypeBadge strategy={s.strategy_name} />
                  </div>
                </td>
                {GATES.map((g) => {
                  const pass = g.pass(s)
                  return (
                    <td
                      key={g.label}
                      className="px-1 py-1 text-center"
                      title={tooltipLine(s.strategy_name, g.metricName, g.cellText(s).replace(/^[pq]=/, ''))}
                    >
                      <span
                        className={`inline-block w-full max-w-[5rem] px-1.5 py-0.5 rounded font-mono text-white text-2xs border ${
                          pass
                            ? 'bg-success/30 border-success/60'
                            : 'bg-danger/20 border-danger/40'
                        }`}
                      >
                        {g.cellText(s)}
                      </span>
                    </td>
                  )
                })}
                <td className="px-1.5 py-1 text-right font-mono text-white">
                  {s.tier1_gates_passed ?? 0}/5
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
