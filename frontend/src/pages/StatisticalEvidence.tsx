/**
 * StatisticalEvidence — placeholder screen scaffolded in Sprint 5 addendum.
 *
 * Sprint 5 spec calls for six charts here (SignificanceJourneyMatrix,
 * CPCVSharpePlot, CVStabilityRadar, ProbabilisticSharpeChart,
 * MultipleComparisonTable, WalkForwardChart). This file establishes the
 * route and nav target so the link works; the charts arrive as separate
 * component PRs without churning routing each time.
 */
export default function StatisticalEvidence() {
  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Statistical Evidence</h1>
        <p className="text-sm text-muted mt-1">
          Tier 1 gates, CPCV distributions, FDR correction, and walk-forward
          out-of-sample performance across all 10 strategies.
        </p>
      </div>

      <div className="card p-4">
        <div className="text-2xs text-muted uppercase tracking-wide mb-2">
          Charts pending Sprint 5 completion
        </div>
        <ul className="text-sm text-muted space-y-1 list-disc list-inside">
          <li>Significance Journey Matrix — 5 Tier 1 gates per strategy</li>
          <li>CPCV Sharpe Distribution — box plots across CPCV paths</li>
          <li>CV Stability Radar — six-axis robustness profile</li>
          <li>Probabilistic Sharpe Chart — point estimates with 95% CIs</li>
          <li>Multiple Comparison Table — raw vs FDR-corrected p-values</li>
          <li>Walk-Forward Chart — rolling OOS Sharpe by window</li>
        </ul>
      </div>
    </div>
  )
}
