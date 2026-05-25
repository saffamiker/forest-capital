/**
 * Step1ExportButtons — CSV / JSON export of the staged findings list.
 *
 * Sits next to "View details" on the Step 1 pipeline row. Exports the
 * findings payload the Stage Findings backend just returned — no
 * round-trip, no backend change. The CSV is the same flat-table shape
 * the academic-export package uses; the JSON is the unmodified
 * findings array so a tool consuming it sees every field (evidence
 * lines, surprise flag, surprise reason, implication).
 *
 * Driven by the citation R&D workflow: the exported findings feed
 * manual search-query experiments before the patterns get encoded as
 * Step 2 sourcing prompt templates. CSV is for Excel inspection; JSON
 * is for piping into a script.
 *
 * The button group mirrors the styling of StepDetailToggle so the
 * three controls (View details / CSV / JSON) read as one row of step-
 * level actions.
 */
import { Download } from 'lucide-react'
import { csvBlob } from '../../lib/csv'
import { trackExport } from '../../lib/activityLogger'

// One finding row as the backend emits it (see
// backend/tools/analytical_findings._finding_template). evidence is a
// list of pre-rendered evidence strings — already contains the
// supporting metrics (Sharpe, p-value, CAGR) the user asked to surface.
// The strategy name lives in `title` (e.g. "REGIME_SWITCHING beats
// benchmark on post-2022 Sharpe").
export interface StagedFinding {
  title?: string
  finding?: string
  evidence?: string[]
  implication?: string
  nugget_strength?: string
  surprise?: boolean
  surprise_reason?: string | null
  [k: string]: unknown
}

// CSV header columns — exported so the test pins the column order
// (the citation R&D workflow consumes the CSV downstream; a silent
// reorder would break parser scripts).
export const STAGE_FINDINGS_CSV_HEADERS = [
  '#', 'title', 'nugget_strength', 'surprise',
  'finding', 'implication', 'evidence', 'surprise_reason',
] as const

// Pure mapper — exported so the test can verify CSV cell contents
// without touching the DOM. The CSV row layout is the contract a
// downstream tool will parse against; keep it stable.
export function stageFindingsToCsvRows(
  findings: StagedFinding[],
): (string | number | boolean | null | undefined)[][] {
  return findings.map((f, i) => [
    i + 1,
    f.title ?? '',
    f.nugget_strength ?? 'LOW',
    f.surprise === true ? 'true' : 'false',
    f.finding ?? '',
    f.implication ?? '',
    Array.isArray(f.evidence) ? f.evidence.join(' | ') : '',
    f.surprise_reason ?? '',
  ])
}

// Pure mapper — preserves the full findings array verbatim so a
// downstream tool sees every field exactly as the backend emitted.
export function stageFindingsToJsonString(
  findings: StagedFinding[],
): string {
  return JSON.stringify(findings, null, 2)
}

function timestamp(): string {
  return new Date().toISOString().slice(0, 10).replace(/-/g, '')
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

interface Step1ExportButtonsProps {
  findings: StagedFinding[]
}

export function Step1ExportButtons({ findings }: Step1ExportButtonsProps) {
  const ts = timestamp()

  const downloadCsv = () => {
    triggerDownload(
      csvBlob([...STAGE_FINDINGS_CSV_HEADERS], stageFindingsToCsvRows(findings)),
      `stage_findings_${ts}.csv`,
    )
    trackExport('stage_findings', { format: 'csv', rows: findings.length })
  }

  const downloadJson = () => {
    const blob = new Blob(
      [stageFindingsToJsonString(findings)],
      { type: 'application/json' },
    )
    triggerDownload(blob, `stage_findings_${ts}.json`)
    trackExport('stage_findings', {
      format: 'json', rows: findings.length,
    })
  }

  // Disabled state — Step 1 may have completed with strategy_count
  // populated but findings empty (the test-env path). In that case
  // hide both buttons rather than offer a download that would produce
  // an empty file.
  if (findings.length === 0) return null

  return (
    <div
      className="inline-flex items-center gap-2 pl-2 ml-2 border-l border-navy-800"
      data-testid="step1-export-buttons"
    >
      <button
        type="button"
        onClick={downloadCsv}
        data-testid="step1-export-csv"
        aria-label="Export staged findings as CSV"
        className={
          'inline-flex items-center gap-1 px-2 py-0.5 ' +
          'text-2xs text-electric-blue hover:text-electric-blue/80'
        }
      >
        <Download className="w-3 h-3" />
        CSV
      </button>
      <button
        type="button"
        onClick={downloadJson}
        data-testid="step1-export-json"
        aria-label="Export staged findings as JSON"
        className={
          'inline-flex items-center gap-1 px-2 py-0.5 ' +
          'text-2xs text-electric-blue hover:text-electric-blue/80'
        }
      >
        <Download className="w-3 h-3" />
        JSON
      </button>
    </div>
  )
}
