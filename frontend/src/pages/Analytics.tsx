/**
 * Analytics (route /analytics) — the analytical evidence behind the
 * recommendation, one click deeper than the Investment Outlook front
 * door. This is the MERGE of the former dashboard (strategy performance
 * tables, regime banner, efficient frontier, stress tests, drawdown) and
 * the academic analytics (cumulative returns, rolling correlation,
 * Carhart factor loadings, regime-conditional table, sensitivity) into a
 * single combined surface.
 *
 * The merge is a composition, not a rewrite: each existing page keeps its
 * own data fetching, loading / empty / error states, and the data-tour
 * anchors the site tour relies on. The dashboard's three Investment
 * Outlook cards were lifted out (they now live on the / page), so what
 * remains here is purely the technical detail.
 */
import Dashboard from '../components/Dashboard'
import AcademicAnalytics from './AcademicAnalytics'

export default function Analytics() {
  return (
    <div className="space-y-0">
      <Dashboard />
      <AcademicAnalytics />
    </div>
  )
}
