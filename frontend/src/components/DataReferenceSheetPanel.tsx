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
  CheckCircle2, XCircle,
} from 'lucide-react'

import PostRefreshVerificationPanel
  from './PostRefreshVerificationPanel'


interface LockedProvenance {
  lock_date:     string
  dataset_end:   string
  method:        string
  defended:      string
  locked_value:  string
}

interface DataReferenceEntry {
  token:               string
  label:               string
  value:               string
  source:              string
  is_locked:           boolean
  last_verified:       string
  document_locations:  string[]
  provenance?:         LockedProvenance | null
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


// June 22 2026 -- validation shape, fetched in parallel from
// /api/v1/export/data-reference-sheet/validate. The summary bar
// at the top of the panel reads from `summary`; each row's
// status pill / expanded diff reads from a Map<token,
// ValidationResult> built from `results`.
type ValidationStatus = 'pass' | 'fail' | 'warning' | 'skipped'

interface ValidationResult {
  token:            string
  label:            string
  reference_value:  string | null
  source_value:     string | null
  source_endpoint:  string
  status:           ValidationStatus
  delta:            string | null
  note:             string | null
  cache_freshness:  string | null
}

interface ValidationSummary {
  total:    number
  passed:   number
  failed:   number
  warning:  number
  skipped:  number
}

interface ValidationResponse {
  data_hash:     string
  validated_at:  string
  summary:       ValidationSummary
  results:       ValidationResult[]
  error?:        string
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


// Lock icon with a hover-popover surfacing the locked
// constant's provenance block (lock_date / dataset_end /
// method / defended / locked_value). When provenance is null
// (rare -- means the catalog source string isn't in
// LOCKED_CONSTANT_PROVENANCE, which the backend test
// guards against) we render a plain lock with no tooltip.
function LockWithProvenance({
  provenance,
}: {
  provenance: LockedProvenance | null
}): React.ReactElement {
  const [open, setOpen] = useState(false)
  // Native title attribute provides a plain-text fallback for
  // screen readers + non-hover devices. The styled popover
  // adds the formatted multi-line view.
  const titleText = provenance
    ? (
        `Locked: ${provenance.lock_date} data lock\n`
        + `Source: ${provenance.locked_value}\n`
        + `Method: ${provenance.method}\n`
        + `Defended: ${provenance.defended}`)
    : 'locked at submission'
  return (
    <span
      data-testid="locked-provenance-icon"
      className="relative inline-block cursor-help"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}>
      <Lock
        className="w-3 h-3 text-warning inline"
        aria-label="locked at submission"
        {...(titleText ? { title: titleText } : {})} />
      {provenance && open && (
        <span
          data-testid="locked-provenance-popover"
          className="absolute left-4 top-0 z-30 w-72 p-3
                     rounded border border-border
                     bg-navy-900 text-2xs text-slate-300
                     shadow-lg whitespace-normal
                     normal-case font-sans tracking-normal">
          <div className="text-warning font-semibold mb-2
                          flex items-center gap-1">
            <Lock className="w-3 h-3" />
            Locked constant
          </div>
          <div className="space-y-1">
            <div>
              <span className="text-muted">Locked:</span>{' '}
              {provenance.lock_date} data lock
            </div>
            <div>
              <span className="text-muted">Value:</span>{' '}
              <span className="font-mono text-white">
                {provenance.locked_value}
              </span>
            </div>
            <div>
              <span className="text-muted">Dataset end:</span>{' '}
              {provenance.dataset_end}
            </div>
            <div>
              <span className="text-muted">Method:</span>{' '}
              {provenance.method}
            </div>
            <div>
              <span className="text-muted">Defended:</span>{' '}
              {provenance.defended}
            </div>
          </div>
        </span>
      )}
    </span>
  )
}


// Status pill rendered next to every reference-sheet row. Shape
// matches the legend in the summary bar: green check / red X /
// amber warning / grey lock / spinner while loading.
function ValidationPill({
  result, loading,
}: {
  result: ValidationResult | undefined
  loading: boolean
}): React.ReactElement {
  if (!result) {
    if (loading) {
      return (
        <Loader2
          data-testid="validation-pill-loading"
          className="w-3 h-3 text-muted animate-spin inline" />
      )
    }
    return <span className="text-2xs text-muted">—</span>
  }
  const title = (
    result.note
    || result.delta
    || (result.cache_freshness
        ? 'source row written '
          + new Date(result.cache_freshness).toLocaleString()
        : ''))
  if (result.status === 'pass') {
    return (
      <CheckCircle2
        data-testid="validation-pill-pass"
        className="w-3.5 h-3.5 text-success inline"
        aria-label="pass"
        {...(title ? { title } : {})} />
    )
  }
  if (result.status === 'fail') {
    return (
      <XCircle
        data-testid="validation-pill-fail"
        className="w-3.5 h-3.5 text-danger inline"
        aria-label="fail"
        {...(title ? { title } : {})} />
    )
  }
  if (result.status === 'warning') {
    return (
      <AlertCircle
        data-testid="validation-pill-warning"
        className="w-3.5 h-3.5 text-warning inline"
        aria-label="warning"
        {...(title ? { title } : {})} />
    )
  }
  // skipped -- locked constant or no validator
  return (
    <Lock
      data-testid="validation-pill-skipped"
      className="w-3 h-3 text-muted inline"
      aria-label="skipped"
      {...(title ? { title } : {})} />
  )
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
  // June 22 2026 -- cross-reference validation. Fired in parallel
  // with the sheet fetch on first expand; rendered as a summary
  // bar at the top of the panel + per-row status pills. The two
  // fetches are decoupled -- token values render the moment the
  // sheet fetch returns, validation results overlay when ready.
  const [validation, setValidation] =
    useState<ValidationResponse | null>(null)
  const [validationLoading, setValidationLoading] = useState(false)
  // June 27 2026 -- triggerKey for the post-refresh verification
  // panel. Bumped by the "Verify submission data" button so the
  // panel fires its endpoint without requiring a light refresh.
  const [verifyTrigger, setVerifyTrigger] = useState<number>(0)
  const [validationError, setValidationError] = useState<
    string | null>(null)

  // O(1) lookup from token -> validation result; built once when
  // validation arrives so the per-row render doesn't scan the
  // 153-item list each pass.
  const validationByToken: Map<string, ValidationResult> = (() => {
    const m = new Map<string, ValidationResult>()
    if (!validation) return m
    for (const r of validation.results) m.set(r.token, r)
    return m
  })()

  const fetchBoth = async () => {
    if ((data || loading) && (validation || validationLoading)) return
    if (!data && !loading) {
      setLoading(true)
      setError(null)
      void axios
        .get<DataReferenceResponse>(
          '/api/v1/export/data-reference-sheet')
        .then((res) => {
          setData(res.data)
          // Default-expand every category that has entries.
          const open: Record<string, boolean> = {}
          for (const key of CATEGORY_ORDER) {
            if (res.data.categories[key]) open[key] = true
          }
          setOpenCategories(open)
        })
        .catch((err) => {
          const msg = axios.isAxiosError(err)
            ? (err.response?.data?.detail ?? err.message)
            : 'Failed to load data reference sheet'
          setError(String(msg))
        })
        .finally(() => setLoading(false))
    }
    if (!validation && !validationLoading) {
      setValidationLoading(true)
      setValidationError(null)
      void axios
        .get<ValidationResponse>(
          '/api/v1/export/data-reference-sheet/validate')
        .then((res) => setValidation(res.data))
        .catch((err) => {
          const msg = axios.isAxiosError(err)
            ? (err.response?.data?.detail ?? err.message)
            : 'Failed to validate data reference sheet'
          setValidationError(String(msg))
        })
        .finally(() => setValidationLoading(false))
    }
  }

  const handleToggle = () => {
    if (!expanded) void fetchBoth()
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
                // June 27 2026 -- manual "Verify submission data"
                // button. Bumps the verify trigger key so the
                // mounted PostRefreshVerificationPanel re-fires
                // the verifier endpoint without requiring a full
                // light refresh first. Counter increment used as
                // a stable changing key (toString() avoids the
                // verifier-panel re-render on stale equality).
                setVerifyTrigger(verifyTrigger + 1)
              }}
              data-testid="data-reference-verify-button"
              className="flex items-center gap-1.5 text-2xs
                         text-warning bg-warning/10
                         border border-warning/30 px-2 py-1
                         rounded hover:bg-warning/20
                         uppercase tracking-wide font-medium
                         transition-colors">
              <AlertCircle className="w-3 h-3" />
              Verify submission data
            </button>
          )}
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
          {/* June 27 2026 -- manual verification panel. Mounted
              alongside the data so the "Verify submission data"
              header button fires the verifier endpoint without
              triggering a light refresh. Renders null until the
              user clicks the button (verifyTrigger > 0). */}
          {verifyTrigger > 0 ? (
            <div className="mb-3">
              <PostRefreshVerificationPanel
                triggerKey={verifyTrigger} />
            </div>
          ) : null}
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
              <div className="text-2xs text-muted mb-2
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
              {/* Validation summary bar. Renders after the
                  /validate endpoint returns; shows skeleton +
                  spinner while loading. Independent from the
                  sheet fetch -- value rows below render the
                  moment the sheet endpoint returns regardless
                  of whether validation has completed. */}
              <div
                data-testid="data-reference-validation-summary"
                className="px-3 py-2 mb-4 rounded
                           border border-border bg-navy-700/30
                           text-2xs flex items-center
                           gap-4 flex-wrap">
                {validationLoading && (
                  <span className="flex items-center gap-1.5
                                   text-muted">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Validating values against source caches…
                  </span>
                )}
                {validationError && (
                  <span className="flex items-center gap-1.5
                                   text-danger">
                    <AlertCircle className="w-3 h-3" />
                    Validation unavailable: {validationError}
                  </span>
                )}
                {validation && !validationError && (
                  <>
                    <span className="text-muted">
                      Cross-reference:
                    </span>
                    <span className="flex items-center gap-1
                                     text-success">
                      <CheckCircle2 className="w-3 h-3" />
                      {validation.summary.passed} passed
                    </span>
                    <span className="flex items-center gap-1
                                     text-danger">
                      <XCircle className="w-3 h-3" />
                      {validation.summary.failed} failed
                    </span>
                    {validation.summary.warning > 0 && (
                      <span className="flex items-center gap-1
                                       text-warning">
                        <AlertCircle className="w-3 h-3" />
                        {validation.summary.warning} warning
                      </span>
                    )}
                    <span className="flex items-center gap-1
                                     text-muted">
                      <Lock className="w-3 h-3" />
                      {validation.summary.skipped} skipped
                    </span>
                    <span className="text-muted ml-auto">
                      Validated {new Date(
                        validation.validated_at).toLocaleString()}
                    </span>
                  </>
                )}
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
                              <th className="py-1.5 pr-3 font-medium
                                            text-center w-12">
                                Check
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {cat.entries.map((entry) => {
                              const v = validationByToken
                                .get(entry.token)
                              const showExpandedFail = (
                                v && v.status === 'fail')
                              const rowClass = showExpandedFail
                                ? ('border-b border-danger/40 '
                                   + 'bg-danger/5')
                                : 'border-b border-border/30'
                              return (
                                <>
                                  <tr
                                    key={entry.token}
                                    className={rowClass}
                                    data-testid={
                                      'data-ref-row-'
                                      + entry.token.replace(
                                        /[{}]/g, '')}>
                                    <td className="py-1.5 pr-3
                                                  align-top">
                                      {entry.is_locked
                                        ? <LockWithProvenance
                                            provenance={
                                              entry.provenance
                                              ?? null} />
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
                                                  font-mono
                                                  text-right
                                                  whitespace-nowrap
                                                  align-top">
                                      {entry.value}
                                    </td>
                                    <td className="py-1.5 pr-3
                                                  text-muted
                                                  font-mono text-2xs
                                                  hidden md:table-cell
                                                  align-top">
                                      {entry.source}
                                    </td>
                                    <td className="py-1.5 pr-3
                                                  text-muted
                                                  text-2xs
                                                  hidden lg:table-cell
                                                  align-top">
                                      {entry.last_verified}
                                    </td>
                                    <td className="py-1.5 pr-3
                                                  text-muted
                                                  text-2xs
                                                  hidden lg:table-cell
                                                  align-top">
                                      {entry.document_locations
                                        .join('; ')}
                                    </td>
                                    <td className="py-1.5 pr-3
                                                  text-center
                                                  align-top">
                                      <ValidationPill
                                        result={v}
                                        loading={validationLoading}
                                      />
                                    </td>
                                  </tr>
                                  {showExpandedFail && v && (
                                    <tr
                                      key={entry.token + '-fail'}
                                      className="border-b
                                                border-danger/40
                                                bg-danger/5">
                                      <td colSpan={7}
                                          className="px-3 pb-2
                                                    pt-0 text-2xs">
                                        <div className="text-danger
                                                       font-semibold
                                                       mb-1">
                                          Validation failed
                                        </div>
                                        <div className="grid
                                                       grid-cols-2
                                                       md:grid-cols-4
                                                       gap-x-4
                                                       gap-y-1
                                                       text-slate-300">
                                          <div>
                                            <span className="text-muted">
                                              Reference:
                                            </span>{' '}
                                            <span className="font-mono
                                                            text-white">
                                              {v.reference_value
                                                ?? '—'}
                                            </span>
                                          </div>
                                          <div>
                                            <span className="text-muted">
                                              Source:
                                            </span>{' '}
                                            <span className="font-mono
                                                            text-white">
                                              {v.source_value
                                                ?? '—'}
                                            </span>
                                          </div>
                                          {v.delta && (
                                            <div className="md:col-span-2">
                                              <span className="text-muted">
                                                Delta:
                                              </span>{' '}
                                              <span className="font-mono
                                                              text-danger">
                                                {v.delta}
                                              </span>
                                            </div>
                                          )}
                                          <div className="md:col-span-4
                                                         text-muted
                                                         font-mono">
                                            {v.source_endpoint}
                                          </div>
                                          {v.cache_freshness && (
                                            <div className="md:col-span-4
                                                           text-muted">
                                              Source row written{' '}
                                              {new Date(
                                                v.cache_freshness)
                                                .toLocaleString()}
                                            </div>
                                          )}
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </>
                              )
                            })}
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
