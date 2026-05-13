/**
 * RegimeAnalysis — placeholder screen scaffolded in Sprint 5 addendum.
 *
 * Sprint 5 spec calls for six charts here (RegimeConditionalPerformance,
 * RegimeTimeline, CorrelationBreakdownChart, FactorExposureHeatmap,
 * PerformanceAttributionWaterfall, RegimeTransitionMatrix). This file
 * establishes the route and nav target so the link works; charts ship
 * as separate component PRs without re-touching routing each time.
 */
export default function RegimeAnalysis() {
  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Regime Analysis</h1>
        <p className="text-sm text-muted mt-1">
          Performance across bull, bear, high-volatility, and rising-rate
          environments — including the central 2022 equity-bond correlation
          breakdown finding.
        </p>
      </div>

      <div className="card p-4">
        <div className="text-2xs text-muted uppercase tracking-wide mb-2">
          Charts pending Sprint 5 completion
        </div>
        <ul className="text-sm text-muted space-y-1 list-disc list-inside">
          <li>Regime Conditional Performance — strategies by regime</li>
          <li>Regime Timeline — HMM and threshold classifications 2000–2024</li>
          <li>Correlation Breakdown Chart — rolling equity-bond correlation</li>
          <li>Factor Exposure Heatmap — Fama-French loadings per strategy</li>
          <li>Performance Attribution Waterfall — Brinson-Hood-Beebower</li>
          <li>Regime Transition Matrix — bull/bear/transition probabilities</li>
        </ul>
      </div>
    </div>
  )
}
