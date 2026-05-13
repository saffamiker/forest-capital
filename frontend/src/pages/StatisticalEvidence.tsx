/**
 * StatisticalEvidence — six Sprint 6 charts that establish the academic
 * credibility of every result on the Dashboard. Reads strategy data from
 * strategiesStore (already loaded by the Dashboard) and aux chart data
 * from chartsStore. Both stores cache for the session — navigating away
 * and back is instant.
 */
import { useEffect } from 'react'
import { useStrategiesStore } from '../stores/strategiesStore'
import { useChartsStore } from '../stores/chartsStore'
import SignificanceJourneyMatrix from '../components/charts/SignificanceJourneyMatrix'
import CPCVSharpePlot from '../components/charts/CPCVSharpePlot'
import CVStabilityRadar from '../components/charts/CVStabilityRadar'
import ProbabilisticSharpeChart from '../components/charts/ProbabilisticSharpeChart'
import MultipleComparisonTable from '../components/charts/MultipleComparisonTable'
import WalkForwardChart from '../components/charts/WalkForwardChart'

export default function StatisticalEvidence() {
  const { strategies, load: loadStrategies, loading: strategiesLoading } = useStrategiesStore()
  const { data: chartData, load: loadCharts, loading: chartsLoading } = useChartsStore()

  useEffect(() => {
    void loadStrategies()
    void loadCharts()
  }, [loadStrategies, loadCharts])

  const initialLoad = (strategiesLoading || chartsLoading) && strategies.length === 0

  return (
    <div className="p-4 md:p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Statistical Evidence</h1>
        <p className="text-sm text-muted mt-1">
          Tier 1 gates, CPCV distributions, FDR correction, and walk-forward
          out-of-sample performance across all 10 strategies.
        </p>
      </div>

      {initialLoad ? (
        <div className="card p-8 text-center text-muted text-sm">Loading…</div>
      ) : (
        <>
          <SignificanceJourneyMatrix strategies={strategies} />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <CPCVSharpePlot cpcv={chartData?.cpcv ?? {}} />
            <ProbabilisticSharpeChart strategies={strategies} />
          </div>
          <CVStabilityRadar radar={chartData?.cv_radar ?? {}} />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <MultipleComparisonTable strategies={strategies} />
            <WalkForwardChart walkForward={chartData?.walk_forward ?? {}} />
          </div>
        </>
      )}
    </div>
  )
}
