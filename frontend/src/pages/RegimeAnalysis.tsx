/**
 * RegimeAnalysis — six Sprint 6 charts focused on how strategies behave
 * under different market regimes, including the central 2022 correlation
 * breakdown finding. Reads strategy + regime metadata from stores; both
 * are session-cached so navigation between screens does not re-fetch.
 *
 * Commentary-mode chrome mirrors StatisticalEvidence: a banner up top
 * and a ChartCommentStrip after every chart. The strips render in all
 * three modes (Sources line always visible) but only show the narrative
 * body in Commentary and Present mode.
 */
import { useEffect } from 'react'
import { useChartsStore } from '../stores/chartsStore'
import { useRegimeStore } from '../stores/regimeStore'
import { useGlossaryStore } from '../stores/glossaryStore'
import RegimeTimeline from '../components/charts/RegimeTimeline'
import RegimeConditionalPerformance from '../components/charts/RegimeConditionalPerformance'
import CorrelationBreakdownChart from '../components/charts/CorrelationBreakdownChart'
import FactorExposureHeatmap from '../components/charts/FactorExposureHeatmap'
import PerformanceAttributionWaterfall from '../components/charts/PerformanceAttributionWaterfall'
import RegimeTransitionMatrix from '../components/charts/RegimeTransitionMatrix'
import ChartCommentStrip from '../components/ChartCommentStrip'
import LearnModeBanner from '../components/LearnModeBanner'
import DataCurrencyBar from '../components/DataCurrencyBar'
import FloatingSectionNav from '../components/FloatingSectionNav'

// Purple accent — Regime Analysis is the regime/macro screen.
const ACCENT = '#7c3aed'

export default function RegimeAnalysis() {
  const { data: chartData, load: loadCharts, loading } = useChartsStore()
  const { regime, load: loadRegime } = useRegimeStore()
  const loadTerms = useGlossaryStore((s) => s.loadTerms)

  useEffect(() => {
    void loadCharts()
    void loadRegime()
    void loadTerms()
  }, [loadCharts, loadRegime, loadTerms])

  const initialLoad = loading && !chartData

  return (
    <div className="p-4 md:p-6 space-y-5">
      <FloatingSectionNav pageKey="regime-analysis" />
      <div>
        <h1 className="text-xl font-semibold text-white">Regime Analysis</h1>
        <p className="text-sm text-muted mt-1">
          Performance across bull, bear, and transition environments — and
          the 2022 equity-bond correlation breakdown that drove this project.
        </p>
        <div className="mt-1"><DataCurrencyBar /></div>
      </div>

      <LearnModeBanner />

      {initialLoad ? (
        <div className="card p-8 text-center text-muted text-sm">Loading…</div>
      ) : (
        <>
          <div
            data-section-id="regime-timeline"
            data-section-label="Regime Timeline">
            <RegimeTimeline timeline={chartData?.regime_timeline ?? []} />
            <ChartCommentStrip
              chartId="regime_timeline"
              chartType="timeline_band"
              chartData={chartData?.regime_timeline}
              accentColor={ACCENT}
            />
          </div>

          <div
            data-section-id="correlation-breakdown"
            data-section-label="Correlation Breakdown">
            <CorrelationBreakdownChart
              correlation={chartData?.correlation_breakdown ?? []}
              pre2022={regime?.pre_2022_avg_correlation ?? null}
              post2022={regime?.post_2022_avg_correlation ?? null}
            />
            <ChartCommentStrip
              chartId="correlation_breakdown_chart"
              chartType="line_rolling_correlation"
              chartData={chartData?.correlation_breakdown}
              accentColor={ACCENT}
            />
          </div>

          <div
            data-section-id="regime-conditional"
            data-section-label="Regime-Conditional Performance">
            <RegimeConditionalPerformance regimeConditional={chartData?.regime_conditional ?? {}} />
            <ChartCommentStrip
              chartId="regime_conditional_performance"
              chartType="bar_by_regime"
              chartData={chartData?.regime_conditional}
              accentColor={ACCENT}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
            <div
              data-section-id="factor-exposure"
              data-section-label="Factor Exposure">
              <FactorExposureHeatmap factorLoadings={chartData?.factor_loadings ?? {}} />
              <ChartCommentStrip
                chartId="factor_exposure_heatmap"
                chartType="heatmap_ff_loadings"
                chartData={chartData?.factor_loadings}
                accentColor={ACCENT}
              />
            </div>
            <div
              data-section-id="transition-matrix"
              data-section-label="Regime Transition Matrix">
              <RegimeTransitionMatrix matrix={chartData?.transition_matrix ?? ({} as never)} />
              <ChartCommentStrip
                chartId="regime_transition_matrix"
                chartType="matrix_3x3"
                chartData={chartData?.transition_matrix}
                accentColor={ACCENT}
              />
            </div>
          </div>

          <div
            data-section-id="attribution"
            data-section-label="Performance Attribution">
            <PerformanceAttributionWaterfall attribution={chartData?.attribution ?? {}} />
            <ChartCommentStrip
              chartId="performance_attribution_waterfall"
              chartType="waterfall_brinson"
              chartData={chartData?.attribution}
              accentColor={ACCENT}
            />
          </div>
        </>
      )}
    </div>
  )
}
