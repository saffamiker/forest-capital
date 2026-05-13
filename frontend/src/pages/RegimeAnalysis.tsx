/**
 * RegimeAnalysis — six Sprint 6 charts focused on how strategies behave
 * under different market regimes, including the central 2022 correlation
 * breakdown finding. Reads strategy + regime metadata from stores; both
 * are session-cached so navigation between screens does not re-fetch.
 */
import { useEffect } from 'react'
import { useChartsStore } from '../stores/chartsStore'
import { useRegimeStore } from '../stores/regimeStore'
import RegimeTimeline from '../components/charts/RegimeTimeline'
import RegimeConditionalPerformance from '../components/charts/RegimeConditionalPerformance'
import CorrelationBreakdownChart from '../components/charts/CorrelationBreakdownChart'
import FactorExposureHeatmap from '../components/charts/FactorExposureHeatmap'
import PerformanceAttributionWaterfall from '../components/charts/PerformanceAttributionWaterfall'
import RegimeTransitionMatrix from '../components/charts/RegimeTransitionMatrix'

export default function RegimeAnalysis() {
  const { data: chartData, load: loadCharts, loading } = useChartsStore()
  const { regime, load: loadRegime } = useRegimeStore()

  useEffect(() => {
    void loadCharts()
    void loadRegime()
  }, [loadCharts, loadRegime])

  const initialLoad = loading && !chartData

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Regime Analysis</h1>
        <p className="text-sm text-muted mt-1">
          Performance across bull, bear, and transition environments — and
          the 2022 equity-bond correlation breakdown that drove this project.
        </p>
      </div>

      {initialLoad ? (
        <div className="card p-8 text-center text-muted text-sm">Loading…</div>
      ) : (
        <>
          <RegimeTimeline timeline={chartData?.regime_timeline ?? []} />
          <CorrelationBreakdownChart
            correlation={chartData?.correlation_breakdown ?? []}
            pre2022={regime?.pre_2022_avg_correlation ?? null}
            post2022={regime?.post_2022_avg_correlation ?? null}
          />
          <RegimeConditionalPerformance regimeConditional={chartData?.regime_conditional ?? {}} />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <FactorExposureHeatmap factorLoadings={chartData?.factor_loadings ?? {}} />
            <RegimeTransitionMatrix matrix={chartData?.transition_matrix ?? ({} as never)} />
          </div>
          <PerformanceAttributionWaterfall attribution={chartData?.attribution ?? {}} />
        </>
      )}
    </div>
  )
}
