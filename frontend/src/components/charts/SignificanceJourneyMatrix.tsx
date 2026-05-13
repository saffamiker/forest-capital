/**
 * SignificanceJourneyMatrix — pass/fail visual for the five Tier 1 gates
 * per strategy. Rows: 10 strategies. Columns: t-test, FDR, DSR, OOS, CV.
 * Reads gates directly from StrategyResult so it works offline of the
 * chart-data endpoint — useful when /api/v1/charts/data is cold.
 */
import type { StrategyResult } from '../../types/strategies'
import { prettyName } from '../../lib/strategyColors'

interface Gate {
  label: string
  pass: (s: StrategyResult) => boolean
  fmt: (s: StrategyResult) => string
}

// Thresholds match backend/tools/backtester.py run_all_strategies gate logic.
const GATES: Gate[] = [
  {
    label: 't-test',
    pass: (s) => (s.p_value_ttest ?? 1) < 0.005,
    fmt: (s) => `p=${(s.p_value_ttest ?? 1).toFixed(4)}`,
  },
  {
    label: 'FDR',
    pass: (s) => (s.p_value_corrected ?? 1) < 0.005,
    fmt: (s) => `q=${(s.p_value_corrected ?? 1).toFixed(4)}`,
  },
  {
    label: 'DSR',
    pass: (s) => (s.dsr_p_value ?? 1) < 0.005,
    fmt: (s) => `p=${(s.dsr_p_value ?? 1).toFixed(4)}`,
  },
  {
    label: 'OOS',
    pass: (s) => (s.oos_p_value ?? 1) < 0.050,
    fmt: (s) => `p=${(s.oos_p_value ?? 1).toFixed(3)}`,
  },
  {
    label: 'CV',
    pass: (s) => (s.cv_stability_score ?? 0) >= 0.60,
    fmt: (s) => `score=${(s.cv_stability_score ?? 0).toFixed(2)}`,
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
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted text-2xs uppercase tracking-wide">
              <th className="text-left py-2 pr-3">Strategy</th>
              {GATES.map((g) => (
                <th key={g.label} className="px-2 py-2">{g.label}</th>
              ))}
              <th className="px-2 py-2 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.strategy_name} className="border-t border-border/50">
                <td className="py-1.5 pr-3 text-white font-mono">{prettyName(s.strategy_name)}</td>
                {GATES.map((g) => {
                  const pass = g.pass(s)
                  return (
                    <td
                      key={g.label}
                      className="px-2 py-1.5 text-center"
                      title={g.fmt(s)}
                    >
                      <span
                        className={`inline-block w-4 h-4 rounded-sm ${
                          pass ? 'bg-success/30 border border-success/60' : 'bg-danger/20 border border-danger/40'
                        }`}
                        aria-label={`${g.label} ${pass ? 'pass' : 'fail'}`}
                      />
                    </td>
                  )
                })}
                <td className="px-2 py-1.5 text-right font-mono text-white">
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
