/**
 * AuditExportButton -- Concern 7m-iii + 7m-iv.
 *
 * Two-mode export button for the adversarial review audit trail.
 *
 *   mode="dropdown"
 *     For the Submission Readiness Review panel. Opens a dropdown
 *     with four choices: Full Package + each of the three editor
 *     deliverable types. Each option hits
 *     POST /api/v1/documents/audit-export/docx with the chosen
 *     scope and triggers a file download.
 *
 *   mode="single"
 *     For the per-document editor (below the critic findings
 *     panel). Single button that exports just the current doc's
 *     audit trail.
 *
 * Both modes require team membership -- non-team users see a
 * disabled button with a tooltip (the backend gates on
 * require_team_member; this is the frontend mirror).
 */
import { useState } from 'react'
import axios from 'axios'
import {
  ChevronDown, Download, Loader2,
} from 'lucide-react'


export type AuditExportDocType =
  | 'full_package'
  | 'executive_brief'
  | 'presentation_deck'
  | 'analytical_appendix'
  | 'presentation_script'

const DOC_LABELS: Record<AuditExportDocType, string> = {
  full_package:        'Full Package Audit (DOCX)',
  executive_brief:     'Executive Brief Audit (DOCX)',
  presentation_deck:   'Presentation Deck Audit (DOCX)',
  analytical_appendix: 'Analytical Appendix Audit (DOCX)',
  presentation_script: 'Presentation Script Audit (DOCX)',
}


export interface AuditExportButtonProps {
  mode: 'dropdown' | 'single'
  /** Required when mode='single'. Ignored when mode='dropdown'. */
  documentType?: AuditExportDocType
  /** True when the team member gate evaluated true -- the button
   *  renders enabled. False = disabled with tooltip. */
  isTeam?: boolean
}


export default function AuditExportButton(
  { mode, documentType, isTeam = true }: AuditExportButtonProps,
): React.ReactElement {
  const [busy, setBusy] = useState<AuditExportDocType | null>(null)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const exportOne = async (
    docType: AuditExportDocType,
  ): Promise<void> => {
    setBusy(docType)
    setError(null)
    try {
      const res = await axios.post(
        '/api/v1/documents/audit-export/docx',
        null,
        {
          params: { document_type: docType },
          responseType: 'blob',
        })
      // Trigger file download via blob URL.
      const blob = new Blob(
        [res.data as ArrayBuffer],
        {
          type: 'application/vnd.openxmlformats-officedocument'
            + '.wordprocessingml.document',
        })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const today = new Date().toISOString().slice(0, 10)
      a.download = (
        `forest_capital_audit_trail_${docType}_${today}.docx`)
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Audit export failed.'
      setError(String(msg))
    } finally {
      setBusy(null)
      setOpen(false)
    }
  }

  if (mode === 'single') {
    const dt = documentType ?? 'full_package'
    return (
      <div data-testid="audit-export-button-single">
        <button
          type="button"
          onClick={() => { void exportOne(dt) }}
          disabled={busy !== null || !isTeam}
          title={isTeam
            ? undefined
            : 'Audit export is available to the project team'}
          className="flex items-center gap-1.5 text-2xs
                     px-3 py-1.5 rounded border border-border
                     text-muted hover:text-white
                     hover:bg-navy-700
                     disabled:opacity-50
                     disabled:cursor-not-allowed">
          {busy
            ? <><Loader2 className="w-3 h-3 animate-spin" />
                Exporting…</>
            : <><Download className="w-3 h-3" />
                Export Audit Trail</>}
        </button>
        {error && (
          <p className="text-2xs text-danger mt-1">{error}</p>
        )}
      </div>
    )
  }

  // dropdown mode -- four choices for SubmissionReadinessReview.
  return (
    <div className="relative inline-block"
      data-testid="audit-export-button-dropdown">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={busy !== null || !isTeam}
        title={isTeam
          ? undefined
          : 'Audit export is available to the project team'}
        className="flex items-center gap-1.5 text-xs px-3 py-1.5
                   rounded border border-border text-muted
                   hover:text-white hover:bg-navy-700
                   disabled:opacity-50
                   disabled:cursor-not-allowed">
        {busy
          ? <><Loader2 className="w-3 h-3 animate-spin" />
              Exporting…</>
          : <><Download className="w-3 h-3" />
              Export Audit Trail
              <ChevronDown className="w-3 h-3" /></>}
      </button>
      {open && (
        <div
          className="absolute right-0 mt-1 z-30 rounded border
                     border-border bg-navy-800 shadow-lg
                     min-w-[18rem] py-1"
          data-testid="audit-export-dropdown-menu">
          {(Object.entries(DOC_LABELS) as
            [AuditExportDocType, string][]).map(
            ([dt, label]) => (
              <button
                key={dt}
                type="button"
                onClick={() => { void exportOne(dt) }}
                disabled={busy !== null}
                className="w-full text-left text-xs px-3 py-1.5
                            text-slate-200 hover:bg-navy-700
                            disabled:opacity-50">
                {label}
              </button>
            ))}
        </div>
      )}
      {error && (
        <p className="text-2xs text-danger mt-1">{error}</p>
      )}
    </div>
  )
}
