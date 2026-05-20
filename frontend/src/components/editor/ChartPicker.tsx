/**
 * ChartPicker — the editor's right panel when adding a chart to a slide.
 *
 * It replaces the Writing Assistant panel while open. Charts come from
 * GET /api/v1/charts/available (the server-renderable platform charts),
 * grouped by category, each shown as a live thumbnail rendered by the
 * GET /api/v1/charts/render endpoint. Clicking a chart adds it to the
 * current slide and closes the picker.
 *
 * The category strings on the API response are short kebab-style keys
 * ("regime", "performance", ...). The picker maps them to human display
 * labels in CATEGORY_LABELS — the API field stays compact for routing.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import { X, Loader2, BarChart3 } from 'lucide-react'

interface AvailableChart {
  key: string
  label: string
  description: string
  category: string
}

interface Props {
  onSelect: (chartKey: string) => void
  onClose: () => void
}

// Display labels for the AVAILABLE_CHARTS category keys. An unknown
// category falls through to the raw value uppercased — the categories on
// the API are the source of truth; this map is purely cosmetic.
const CATEGORY_LABELS: Record<string, string> = {
  regime:       'Regime Analysis',
  factors:      'Factors',
  performance:  'Performance',
  risk:         'Risk',
  significance: 'Significance',
  activity:     'Activity',
}

function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key.toUpperCase()
}

export default function ChartPicker({ onSelect, onClose }: Props) {
  const [charts, setCharts] = useState<AvailableChart[]>([])
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const res = await axios.get<AvailableChart[]>('/api/v1/charts/available')
        if (!cancelled) { setCharts(res.data ?? []); setPhase('ready') }
      } catch {
        if (!cancelled) setPhase('error')
      }
    })()
    return () => { cancelled = true }
  }, [])

  // One group per category, in first-seen order.
  const groups: { category: string; items: AvailableChart[] }[] = []
  for (const c of charts) {
    let g = groups.find((x) => x.category === c.category)
    if (!g) { g = { category: c.category, items: [] }; groups.push(g) }
    g.items.push(c)
  }

  return (
    <div className="h-full overflow-y-auto p-3" data-testid="chart-picker">
      <div className="flex items-center justify-between mb-3">
        <span className="text-2xs text-muted uppercase tracking-wide
                         flex items-center gap-1">
          <BarChart3 className="w-3 h-3" /> Add a chart
        </span>
        <button type="button" onClick={onClose} aria-label="Close chart picker"
          className="text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
      </div>

      {phase === 'loading' && (
        <div className="text-2xs text-muted flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading charts…
        </div>
      )}
      {phase === 'error' && (
        <p className="text-2xs text-danger">
          Could not load the chart list — please retry.
        </p>
      )}

      {phase === 'ready' && groups.map((g) => (
        <div key={g.category} className="mb-5"
             data-testid={`chart-picker-group-${g.category}`}>
          <div className="text-2xs text-electric uppercase tracking-wider
                          mb-1.5 pb-1 border-b border-border/60 font-semibold">
            {categoryLabel(g.category)}
          </div>
          <div className="space-y-2">
            {g.items.map((c) => (
              <button key={c.key} type="button" onClick={() => onSelect(c.key)}
                data-testid={`chart-picker-item-${c.key}`}
                className="w-full text-left card p-2 hover:border-electric/50
                           border border-border transition-colors">
                <ChartThumb chartKey={c.key} label={c.label} />
                <div className="text-xs text-white mt-1.5">{c.label}</div>
                <div className="text-2xs text-muted mt-0.5 leading-snug">
                  {c.description}
                </div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/** A 200x120 live thumbnail rendered by the chart render endpoint. */
function ChartThumb({ chartKey, label }: { chartKey: string; label: string }) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    let url: string | null = null
    void (async () => {
      try {
        const res = await axios.get(
          `/api/v1/charts/render/${chartKey}`,
          { params: { width: 200, height: 120, theme: 'light' },
            responseType: 'blob' })
        if (cancelled) return
        url = URL.createObjectURL(res.data as Blob)
        setSrc(url)
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [chartKey])

  if (failed) {
    return (
      <div className="h-[120px] rounded bg-navy-800 flex items-center
                      justify-center text-2xs text-muted">
        Preview unavailable
      </div>
    )
  }
  if (!src) {
    return (
      <div className="h-[120px] rounded bg-navy-800 flex items-center
                      justify-center">
        <Loader2 className="w-4 h-4 animate-spin text-muted" />
      </div>
    )
  }
  return (
    <img src={src} alt={`${label} preview`}
      className="w-full h-[120px] object-cover rounded bg-white" />
  )
}
