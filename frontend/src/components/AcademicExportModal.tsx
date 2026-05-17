/**
 * AcademicExportModal — assembles the academic export package.
 *
 * On open it runs a five-step pipeline and shows progress:
 *   1. Capture the analytics charts as light-mode PNGs.
 *   2. Export the analytics tables as CSV.
 *   3. (Chart descriptions — generated server-side.)
 *   4. POST everything to /api/v1/export/package, which assembles a ZIP.
 *   5. Download the ZIP.
 *
 * The charts are rendered OFF-SCREEN with LIGHT_CHART_THEME (white
 * background, darkened series colours) so the captured PNGs are suitable
 * for printing and embedding in a Word document — the live dark UI is
 * never touched. html2canvas captures each off-screen node at 2×.
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { X, Check, Loader2, Download, AlertTriangle } from 'lucide-react'
import {
  CumulativeReturnChart, RollingCorrelationChart, RollingExcessReturnChart,
  SensitivityAnalysis,
} from '../pages/AcademicAnalytics'
import type { AnalyticsPayload } from '../pages/AcademicAnalytics'
import EfficientFrontier from './EfficientFrontier'
import TeamActivityCharts from './TeamActivityCharts'
import { LIGHT_CHART_THEME } from '../lib/exportTheme'
import { captureElement, placeholderImage } from '../utils/chartCapture'
import { csvBlob } from '../lib/csv'
import type { EfficientFrontierData } from '../types/api'
import type { ActivityEvent, ActivitySummary } from '../types/activity'
import { trackFeature } from '../lib/activityLogger'

const STEP_LABELS = [
  'Capturing charts in light mode',
  'Exporting tables as CSV',
  'Generating chart descriptions',
  'Assembling ZIP package',
  'Downloading the package',
]

// Chart slot → numbered export filename (no extension).
const CHART_SLOTS: { slug: string; name: string; label: string }[] = [
  { slug: 'cumulative_returns',    name: '01_cumulative_returns',    label: 'Cumulative returns' },
  { slug: 'rolling_correlation',   name: '02_rolling_correlation',   label: 'Rolling correlation' },
  { slug: 'rolling_excess_return', name: '03_rolling_excess_return', label: 'Rolling excess return' },
  { slug: 'efficient_frontier',    name: '04_efficient_frontier',    label: 'Efficient frontier' },
  { slug: 'sensitivity_analysis',  name: '05_sensitivity_analysis',  label: 'Sensitivity analysis' },
  { slug: 'team_activity',         name: '06_team_activity',         label: 'Team activity' },
]

type StepState = 'pending' | 'active' | 'done'

/** A generic table → CSV: headers from the first row's keys. */
function objectsCsvBlob(rows: readonly unknown[] | undefined): Blob | null {
  if (!rows || !rows.length) return null
  const recs = rows as Record<string, unknown>[]
  const headers = Object.keys(recs[0])
  return csvBlob(headers, recs.map((r) => headers.map((h) => r[h] as never)))
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))

export default function AcademicExportModal({ onClose }: { onClose: () => void }) {
  const [steps, setSteps] = useState<StepState[]>(
    () => STEP_LABELS.map((_, i) => (i === 0 ? 'active' : 'pending')),
  )
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  // Data for the off-screen charts.
  const [analytics, setAnalytics] = useState<AnalyticsPayload | null>(null)
  const [frontier, setFrontier] = useState<EfficientFrontierData | null>(null)
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [summary, setSummary] = useState<ActivitySummary | null>(null)
  const [chartsReady, setChartsReady] = useState(false)

  // One ref per off-screen chart slot.
  const refs = useRef<Record<string, HTMLDivElement | null>>({})
  const setRef = (slug: string) => (el: HTMLDivElement | null) => { refs.current[slug] = el }

  const setStep = (i: number, s: StepState) =>
    setSteps((prev) => prev.map((v, idx) => (idx === i ? s : v)))

  useEffect(() => {
    let cancelled = false

    async function run() {
      try {
        // ── Fetch every data source the charts and tables need. ──────────
        const [aRes, fRes, tRes, sRes] = await Promise.allSettled([
          axios.get<AnalyticsPayload>('/api/v1/analytics/academic'),
          axios.post<{ efficient_frontier: EfficientFrontierData }>(
            '/api/optimize/weights', { method: 'MAX_SHARPE' }),
          axios.get<{ events: ActivityEvent[] }>('/api/v1/activity/team'),
          axios.get<ActivitySummary>('/api/v1/activity/summary'),
        ])
        if (cancelled) return
        const a = aRes.status === 'fulfilled' ? aRes.value.data : null
        setAnalytics(a)
        if (fRes.status === 'fulfilled') setFrontier(fRes.value.data.efficient_frontier)
        if (tRes.status === 'fulfilled') setEvents(tRes.value.data.events ?? [])
        if (sRes.status === 'fulfilled') setSummary(sRes.value.data)
        setChartsReady(true)

        // Let the off-screen recharts charts mount, fetch (sensitivity),
        // and finish their entry animation before rasterising.
        await delay(2200)
        if (cancelled) return

        // ── Step 1 — capture each chart, placeholder on failure. ─────────
        const chartFiles: { name: string; blob: Blob }[] = []
        for (const slot of CHART_SLOTS) {
          const node = refs.current[slot.slug]
          try {
            if (!node) throw new Error('not rendered')
            chartFiles.push({ name: `${slot.name}.png`, blob: await captureElement(node) })
          } catch {
            // One failed chart never fails the package.
            chartFiles.push({ name: `${slot.name}.png`, blob: placeholderImage(slot.label) })
          }
        }
        setStep(0, 'done'); setStep(1, 'active')

        // ── Step 2 — tables as CSV (no duplicated export logic — csv.ts). ─
        const tableFiles: { name: string; blob: Blob }[] = []
        const addTable = (name: string, rows: readonly unknown[] | undefined) => {
          const b = objectsCsvBlob(rows)
          if (b) tableFiles.push({ name, blob: b })
        }
        addTable('01_summary_statistics.csv', a?.summary_statistics)
        addTable('02_regime_conditional_performance.csv', a?.regime_conditional)
        addTable('03_drawdown_comparison.csv', a?.drawdown_comparison)
        addTable('04_factor_loadings.csv', a?.factor_loadings)
        setStep(1, 'done'); setStep(2, 'active')

        // Step 3 — chart descriptions are generated server-side; visual only.
        await delay(150)
        setStep(2, 'done'); setStep(3, 'active')

        // ── Step 4 — POST the multipart package; backend assembles a ZIP. ─
        const form = new FormData()
        for (const c of chartFiles) form.append('charts', c.blob, c.name)
        for (const t of tableFiles) form.append('tables', t.blob, t.name)
        form.append('metadata', JSON.stringify({
          study_period_start: a?.study_period?.start ?? '—',
          study_period_end: a?.study_period?.end ?? '—',
          n_months: a?.study_period?.n_months ?? '—',
          generated_at: new Date().toISOString(),
        }))
        const zipRes = await axios.post('/api/v1/export/package', form, {
          responseType: 'blob',
        })
        if (cancelled) return
        setStep(3, 'done'); setStep(4, 'active')

        // ── Step 5 — download. ───────────────────────────────────────────
        const today = new Date().toISOString().slice(0, 10)
        triggerDownload(zipRes.data as Blob, `forest_capital_academic_export_${today}.zip`)
        trackFeature('academic_export_package')
        setStep(4, 'done')
        setDone(true)
      } catch (err) {
        if (cancelled) return
        const msg = axios.isAxiosError(err)
          ? (err.response?.data?.detail ?? err.message)
          : 'Export failed'
        setError(String(msg))
      }
    }

    void run()
    return () => { cancelled = true }
  }, [])

  return (
    <>
      {/* Off-screen light-mode chart sheet — rendered but never visible.
          html2canvas rasterises these nodes; the live UI is untouched. */}
      {chartsReady && (
        <div
          aria-hidden="true"
          style={{ position: 'fixed', left: -10000, top: 0, width: 900,
                   background: '#FFFFFF' }}
        >
          <div ref={setRef('cumulative_returns')}>
            {analytics?.cumulative_returns && (
              <CumulativeReturnChart data={analytics.cumulative_returns} theme={LIGHT_CHART_THEME} />
            )}
          </div>
          <div ref={setRef('rolling_correlation')}>
            {analytics?.rolling_correlation && (
              <RollingCorrelationChart data={analytics.rolling_correlation} theme={LIGHT_CHART_THEME} />
            )}
          </div>
          <div ref={setRef('rolling_excess_return')}>
            {analytics?.rolling_excess_return && (
              <RollingExcessReturnChart data={analytics.rolling_excess_return} theme={LIGHT_CHART_THEME} />
            )}
          </div>
          <div ref={setRef('efficient_frontier')}>
            {frontier && <EfficientFrontier data={frontier} theme={LIGHT_CHART_THEME} />}
          </div>
          <div ref={setRef('sensitivity_analysis')}>
            <SensitivityAnalysis theme={LIGHT_CHART_THEME} />
          </div>
          <div ref={setRef('team_activity')}>
            <TeamActivityCharts events={events} summary={summary} presentMode theme={LIGHT_CHART_THEME} />
          </div>
        </div>
      )}

      {/* Progress modal */}
      <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
           role="presentation" onClick={done || error ? onClose : undefined}>
        <div
          role="dialog"
          aria-label="Academic export package"
          onClick={(e) => e.stopPropagation()}
          className="w-full max-w-md rounded-lg border border-border bg-navy-800 shadow-2xl"
        >
          <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
            <div>
              <h2 className="text-white font-semibold text-sm">Academic Export Package</h2>
              <p className="text-2xs text-muted mt-0.5">
                Light-mode charts and CSV tables, packaged for paper submission
              </p>
            </div>
            {(done || error) && (
              <button type="button" onClick={onClose} aria-label="Close"
                      className="text-muted hover:text-white shrink-0">
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          <div className="px-5 py-4 space-y-2.5">
            {error ? (
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                <div>
                  <div className="text-danger font-semibold text-sm">Export failed</div>
                  <p className="text-slate-300 text-sm mt-1">{error}</p>
                </div>
              </div>
            ) : (
              STEP_LABELS.map((label, i) => (
                <div key={label} className="flex items-center gap-2.5 text-sm">
                  {steps[i] === 'done' ? (
                    <Check className="w-4 h-4 text-success shrink-0" />
                  ) : steps[i] === 'active' ? (
                    <Loader2 className="w-4 h-4 text-electric shrink-0 animate-spin" />
                  ) : (
                    <div className="w-4 h-4 rounded-full border border-border shrink-0" />
                  )}
                  <span className={steps[i] === 'pending' ? 'text-muted' : 'text-white'}>
                    {label}
                  </span>
                </div>
              ))
            )}
            {done && (
              <div className="flex items-center gap-2 text-success text-sm pt-1">
                <Download className="w-4 h-4" />
                Package downloaded.
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
