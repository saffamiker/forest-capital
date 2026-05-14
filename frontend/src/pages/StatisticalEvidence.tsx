/**
 * StatisticalEvidence — six Sprint 6 charts that establish the academic
 * credibility of every result on the Dashboard. Reads strategy data from
 * strategiesStore (already loaded by the Dashboard) and aux chart data
 * from chartsStore. Both stores cache for the session — navigating away
 * and back is instant.
 *
 * Commentary-mode chrome: each chart is followed by a ChartCommentStrip
 * that renders nothing in Analyst mode (except its always-on Sources
 * line), reveals AI-generated narrative in Commentary mode, and
 * auto-expands the highlighted charts in Present mode. The LearnModeBanner
 * at the top is the only chrome that's exclusive to Commentary mode.
 */
import { useEffect } from 'react'
import { useStrategiesStore } from '../stores/strategiesStore'
import { useChartsStore } from '../stores/chartsStore'
import { useGlossaryStore } from '../stores/glossaryStore'
import SignificanceJourneyMatrix from '../components/charts/SignificanceJourneyMatrix'
import CPCVSharpePlot from '../components/charts/CPCVSharpePlot'
import CVStabilityRadar from '../components/charts/CVStabilityRadar'
import ProbabilisticSharpeChart from '../components/charts/ProbabilisticSharpeChart'
import MultipleComparisonTable from '../components/charts/MultipleComparisonTable'
import WalkForwardChart from '../components/charts/WalkForwardChart'
import ChartCommentStrip from '../components/ChartCommentStrip'
import LearnModeBanner from '../components/LearnModeBanner'

// Teal accent — Statistical Evidence is the academic-rigour screen.
const ACCENT = '#0d9488'

export default function StatisticalEvidence() {
  const { strategies, load: loadStrategies, loading: strategiesLoading } = useStrategiesStore()
  const { data: chartData, load: loadCharts, loading: chartsLoading } = useChartsStore()
  const loadTerms = useGlossaryStore((s) => s.loadTerms)

  useEffect(() => {
    void loadStrategies()
    void loadCharts()
    void loadTerms()
  }, [loadStrategies, loadCharts, loadTerms])

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

      <LearnModeBanner />

      {initialLoad ? (
        <div className="card p-8 text-center text-muted text-sm">Loading…</div>
      ) : (
        <>
          <div>
            <SignificanceJourneyMatrix strategies={strategies} />
            <ChartCommentStrip
              chartId="significance_journey_matrix"
              chartType="matrix_pass_fail"
              chartData={strategies}
              accentColor={ACCENT}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <div>
              <CPCVSharpePlot cpcv={chartData?.cpcv ?? {}} />
              <ChartCommentStrip
                chartId="cpcv_sharpe_distribution"
                chartType="box_plot"
                chartData={chartData?.cpcv}
                accentColor={ACCENT}
              />
            </div>
            <div>
              <ProbabilisticSharpeChart strategies={strategies} />
              <ChartCommentStrip
                chartId="probabilistic_sharpe_chart"
                chartType="error_bars"
                chartData={strategies}
                accentColor={ACCENT}
              />
            </div>
          </div>

          <div>
            <CVStabilityRadar radar={chartData?.cv_radar ?? {}} />
            <ChartCommentStrip
              chartId="cv_stability_radar"
              chartType="radar_small_multiples"
              chartData={chartData?.cv_radar}
              accentColor={ACCENT}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <div>
              <MultipleComparisonTable strategies={strategies} />
              <ChartCommentStrip
                chartId="multiple_comparison_table"
                chartType="table_fdr"
                chartData={strategies}
                accentColor={ACCENT}
              />
            </div>
            <div>
              <WalkForwardChart walkForward={chartData?.walk_forward ?? {}} />
              <ChartCommentStrip
                chartId="walk_forward_chart"
                chartType="line_rolling_oos"
                chartData={chartData?.walk_forward}
                accentColor={ACCENT}
              />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
