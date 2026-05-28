/**
 * Investment Outlook — the front door (route /). The executive landing
 * page shown first to the CIO, the investment committee, Dr. Panttser,
 * and the presentation audience. It is the past/present/future arc:
 *
 *   present  CIO Live Recommendation (the current signal)
 *   future   Forward Confidence Projection (the projected path)
 *   past     Council Performance Record preview (the track record)
 *
 * Clean and bold by design: it carries only these three components, each
 * self-fetching with its own loading / empty / error state. The
 * analytical evidence behind the recommendation is one click deeper at
 * /analytics.
 */
import CIORecommendationCard from '../components/CIORecommendationCard'
import ForwardConfidenceChart from '../components/ForwardConfidenceChart'
import PerformanceRecordLink from '../components/PerformanceRecordLink'

export default function InvestmentOutlook() {
  return (
    <div className="space-y-0">
      <header className="px-4 md:px-6 pt-5 pb-1">
        <h1 className="text-xl md:text-2xl font-bold text-text tracking-tight">
          Investment Outlook
        </h1>
        <p className="text-xs md:text-sm text-muted mt-1">
          Current signal, forward view, and track record. The full
          analytical evidence is on the Analytics page.
        </p>
      </header>
      <CIORecommendationCard />
      <ForwardConfidenceChart />
      <PerformanceRecordLink />
    </div>
  )
}
