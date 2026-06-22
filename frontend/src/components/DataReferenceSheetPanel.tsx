/**
 * DataReferenceSheetPanel.tsx -- June 22 2026.
 *
 * The "Data Reference Sheet" panel on the Reports page. The
 * submission cross-reference tool the team uses to verify
 * every value in the brief / deck / appendix against the
 * canonical strategy cache.
 *
 * Reads /api/v1/export/data-reference-sheet lazily (on
 * expand). Renders one collapsible section per category;
 * every row carries token, label, value, source, locked/live
 * badge, last-verified timestamp, and the document
 * locations where the value appears.
 *
 * The header chip shows the STRATEGY hash (the value the
 * substitution table is built from) with a tooltip
 * explaining how it differs from the PLATFORM FINGERPRINT
 * that the document footers carry -- the two are produced by
 * different functions over different inputs and intentionally
 * differ.
 *
 * "Download CSV" button exports the full flat table as
 * forest-capital-data-reference-{data_hash}-{date}.csv so
 * the filename itself is traceable to the data state.
 */
import { useState } from 'react'
import axios from 'axios'
import {
  ChevronDown, ChevronRight, AlertCircle, Loader2,
  Lock, Activity, Download, Info,
} from 'lucide-react'


interface DataReferenceEntry {
  token:               string
  label:               string
  value:               string
  source:              string
  is_locked:           boolean
  last_verified:       string
  document_locations:  string[]
}

interface DataReferenceCategory {
  label:    string
  entries:  DataReferenceEntry[]
}

interface DataReferenceResponse {
  data_hash:             string
  platform_fingerprint:  string
  generated_at:          string
  categories:            Record<string, DataReferenceCategory>
}


// Display order. Matches the catalog declaration in
// tools/data_reference_catalog.CATALOG.
const CATEGORY_ORDER: string[] = [
  'study_period',
  'oos_window',
  'full_period_performance',
  'pre_post_2022',
  'drawdown_recovery',
  'correlation',
  'live_regime',
  'cost_sensitivity',
  'play_by_play',
  'tail_risk',
  'per_strategy_appendix',
  'factor_loadings',
]


function todayIso(): string {
  // Local date in YYYY-MM-DD for the CSV filename.
  const d = new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}


function csvEscape(value: string): string {
  // RFC 4180-ish: quote when commas / quotes / newlines present;
  // double up embedded quotes.
  if (
    value.includes(',') || value.includes('"')
    || value.includes('\n') || value.includes('\r')
  ) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}


function downloadCsv(data: DataReferenceResponse): void {
  const lines: string[] = [
    [
      'category', 'token', 'label', 'value', 'source',
      'is_locked', 'last_verified', 'document_locations',
    ].join(','),
  ]
  for (const key of CATEGORY_ORDER) {
    const cat = data.categories[key]
    if (!cat) continue
    for (const entry of cat.entries) {
      lines.push([
        csvEscape(key),
        csvEscape(entry.token),
        csvEscape(entry.label),
        csvEscape(entry.value),
        csvEscape(entry.source),
        entry.is_locked ? 'true' : 'false',
        csvEscape(entry.last_verified),
        csvEscape(entry.document_locations.join('; ')),
      ].join(','))
    }
  }
  const blob = new Blob([lines.join('\n')], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = (
    `forest-capital-data-reference-${data.data_hash}-`
    + `${todayIso()}.csv`)
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}


export default function DataReferenceSheetPanel() {
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<DataReferenceResponse | null>(
    null)
  const [error, setError] = useState<string | null>(null)
  const [openCategories, setOpenCategories] = useState<
    Record<string, boolean>>({})
  const [hashTooltipOpen, setHashTooltipOpen] = useState(false)

  const fetchSheet = async () => {
    if (data || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await axios.get<DataReferenceResponse>(
        '/api/v1/export/data-reference-sheet')
      setData(res.data)
      // Default-expand every category that has entries.
      const open: Record<string, boolean> = {}
      for (const key of CATEGORY_ORDER) {
        if (res.data.categories[key]) open[key] = true
      }
      setOpenCategories(open)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load data reference sheet'
      setError(String(msg))
    } finally {
      setLoading(false)
    }
  }

  const handleToggle = () => {
    if (!expanded) void fetchSheet()
    setExpanded(!expanded)
  }

  const toggleCategory = (key: string) => {
    setOpenCategories({
      ...openCategories,
      [key]: !openCategories[key],
    })
  }

  return (
    <section
      data-section-id="data-reference-sheet"
      data-section-label="Data Reference Sheet"
      className="card"
      data-testid="data-reference-sheet-panel">
      <button
        type="button"
        onClick={handleToggle}
        data-testid="data-reference-sheet-toggle"
        className="w-full flex items-center justify-between gap-3
                   px-4 py-3 hover:bg-navy-700/30 transition-colors
                   rounded">
        <div className="flex items-center gap-2 min-w-0">
          {expanded
            ? <ChevronDown className="w-4 h-4 text-muted shrink-0" />
            : <ChevronRight className="w-4 h-4 text-muted shrink-0" />}
          <Info className="w-4 h-4 text-electric shrink-0" />
          <h2 className="text-white font-semibold text-sm">
            Data Reference Sheet
          </h2>
          {data && (
            <span
              data-testid="data-reference-strategy-hash"
              className="text-2xs text-muted uppercase tracking-wide
                         font-mono ml-1 cursor-help relative"
              onMouseEnter={() => setHashTooltipOpen(true)}
              onMouseLeave={() => setHashTooltipOpen(false)}>
              hash {data.data_hash}
              {hashTooltipOpen && (
                <span
                  className="absolute left-0 top-full mt-1
                             w-80 z-20 p-3 rounded
                             border border-border bg-navy-900
                             text-2xs text-slate-300 normal-case
                             font-sans tracking-normal shadow-lg
                             whitespace-normal">
                  <strong className="text-white block mb-1">
                    Strategy hash vs platform fingerprint
                  </strong>
                  This reference sheet locks to the STRATEGY
                  hash <span className="font-mono text-electric">
                  {data.data_hash}</span> -- the value the
                  substitution table is built from. The brief and
                  appendix footers display the PLATFORM FINGERPRINT
                  <span className="font-mono text-electric">
                  {' '}{data.platform_fingerprint
                  ? data.platform_fingerprint.slice(0, 8)
                  : '(unavailable)'}
                  </span>{' '} instead, computed by
                  current_data_hash() over market data table state.
                  Two different functions, same data state.
                </span>
              )}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {data && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                downloadCsv(data)
              }}
              data-testid="data-reference-csv-button"
              className="flex items-center gap-1.5 text-2xs
                         text-electric bg-electric/10
                         border border-electric/30 px-2 py-1
                         rounded hover:bg-electric/20
                         uppercase tracking-wide font-medium
                         transition-colors">
              <Download className="w-3 h-3" />
              Download CSV
            </button>
          )}
        </div>
      </button>

      {!expanded && (
        <p className="text-2xs text-muted px-4 pb-3 pl-10">
          Cross-reference every value in the submission documents
          against the cache. Click to expand, then verify each
          row matches its counterpart in the brief / deck /
          appendix.
        </p>
      )}

      {expanded && (
        <div className="px-4 pb-4 pt-2 border-t border-border">
          {loading && (
            <div className="flex items-center justify-center gap-2
                            py-4 text-muted text-xs">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading data reference sheet…
            </div>
          )}
          {error && (
            <div className="flex items-start gap-2 px-3 py-2 rounded
                            border border-danger/30 bg-danger/5
                            text-danger text-xs mb-3">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
          {!loading && !error && data && (
            <>
              {data.generated_at && (
                <div className="text-2xs text-muted mb-3
                                flex items-center gap-1.5">
                  Generated {new Date(data.generated_at)
                    .toLocaleString()}
                </div>
              )}
              {/* Legend: locked vs live. */}
              <div className="text-2xs text-muted mb-4
                              flex items-center gap-4 flex-wrap">
                <span className="flex items-center gap-1">
                  <Lock className="w-3 h-3 text-warning" />
                  Locked constant (academic_deck.py)
                </span>
                <span className="flex items-center gap-1">
                  <Activity className="w-3 h-3 text-success" />
                  Live cache read (could update)
                </span>
              </div>
              {CATEGORY_ORDER.map((key) => {
                const cat = data.categories[key]
                if (!cat || cat.entries.length === 0) return null
                const isOpen = openCategories[key] !== false
                return (
                  <div key={key} className="mb-3">
                    <button
                      type="button"
                      onClick={() => toggleCategory(key)}
                      className="w-full flex items-center gap-2
                                 py-1.5 hover:bg-navy-700/30
                                 transition-colors rounded text-left">
                      {isOpen
                        ? <ChevronDown
                            className="w-3.5 h-3.5 text-muted" />
                        : <ChevronRight
                            className="w-3.5 h-3.5 text-muted" />}
                      <h3 className="text-xs text-white
                                     uppercase tracking-wide
                                     font-semibold">
                        {cat.label}
                      </h3>
                      <span className="text-2xs text-muted">
                        ({cat.entries.length} rows)
                      </span>
                    </button>
                    {isOpen && (
                      <div className="overflow-x-auto mt-1">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-2xs text-muted
                                          uppercase tracking-wide
                                          text-left">
                              <th className="py-1.5 pr-3 font-medium
                                            w-8"></th>
                              <th className="py-1.5 pr-3 font-medium">
                                Token / label
                              </th>
                              <th className="py-1.5 pr-3 font-medium
                                            text-right">
                                Value
                              </th>
                              <th className="py-1.5 pr-3 font-medium
                                            hidden md:table-cell">
                                Source
                              </th>
                              <th className="py-1.5 pr-3 font-medium
                                            hidden lg:table-cell">
                                Last verified
                              </th>
                              <th className="py-1.5 pr-3 font-medium
                                            hidden lg:table-cell">
                                Appears in
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {cat.entries.map((entry) => (
                              <tr
                                key={entry.token}
                                className="border-b border-border/30">
                                <td className="py-1.5 pr-3
                                              align-top">
                                  {entry.is_locked
                                    ? <Lock
                                        className="w-3 h-3
                                                   text-warning
                                                   inline" />
                                    : <Activity
                                        className="w-3 h-3
                                                   text-success
                                                   inline" />}
                                </td>
                                <td className="py-1.5 pr-3
                                              align-top">
                                  <div className="font-mono
                                                 text-2xs
                                                 text-electric">
                                    {entry.token}
                                  </div>
                                  <div className="text-slate-300
                                                 mt-0.5">
                                    {entry.label}
                                  </div>
                                </td>
                                <td className="py-1.5 pr-3
                                              text-white
                                              font-mono text-right
                                              whitespace-nowrap
                                              align-top">
                                  {entry.value}
                                </td>
                                <td className="py-1.5 pr-3 text-muted
                                              font-mono text-2xs
                                              hidden md:table-cell
                                              align-top">
                                  {entry.source}
                                </td>
                                <td className="py-1.5 pr-3 text-muted
                                              text-2xs
                                              hidden lg:table-cell
                                              align-top">
                                  {entry.last_verified}
                                </td>
                                <td className="py-1.5 pr-3 text-muted
                                              text-2xs
                                              hidden lg:table-cell
                                              align-top">
                                  {entry.document_locations
                                    .join('; ')}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}
    </section>
  )
}
