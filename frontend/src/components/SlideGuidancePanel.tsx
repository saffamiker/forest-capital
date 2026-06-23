/**
 * SlideGuidancePanel.tsx -- June 22 2026.
 *
 * Per-user slide guidance upload for the presentation deck.
 *
 * Molly downloads the default template (JSON), opens it in any text
 * editor, edits string values only, saves, and uploads via this
 * panel. The deck generation pipeline reads her active guidance row
 * at generation time and overlays per-slide title / so_what /
 * max_bullets / bullet_guidance / speaker_note_directive on top of
 * the SLIDE_SPECIFICATIONS defaults.
 *
 * Validation is rigid -- partial uploads, type mismatches, length
 * overruns, and version mismatches all return 422 with the exact
 * field path that failed. The error message is rendered inline so
 * Molly can fix the file and re-upload without leaving the page.
 *
 * Three actions:
 *   - Download template       -- GET /template -> file save
 *   - Upload guidance         -- POST file -> persist
 *   - Reset to defaults       -- DELETE -> revert
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  AlertCircle, ChevronDown, ChevronRight, Download, Loader2,
  RotateCcw, Upload, FileText,
} from 'lucide-react'


interface ActiveGuidanceResponse {
  active:          boolean
  uploaded_at?:    string
  version?:        number
  generated_from?: string
  guidance?:       Record<string, unknown>
}


function todayIso(): string {
  const d = new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}


function downloadJson(payload: unknown, filename: string): void {
  const blob = new Blob(
    [JSON.stringify(payload, null, 2)],
    { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}


export default function SlideGuidancePanel(): React.ReactElement {
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState<ActiveGuidanceResponse | null>(
    null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const [downloadBusy, setDownloadBusy] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const fetchActive = async (): Promise<void> => {
    setLoading(true)
    try {
      const res = await axios.get<ActiveGuidanceResponse>(
        '/api/v1/deck/slide-guidance')
      setActive(res.data)
    } catch (err) {
      // Fail-open: show "Using default guidance" rather than an
      // alarming error -- the GET returning anything other than
      // 200 means "no active row" effectively.
      setActive({ active: false })
      if (axios.isAxiosError(err)) {
        // eslint-disable-next-line no-console
        console.warn('slide guidance fetch failed:', err.message)
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (expanded && active === null) {
      void fetchActive()
    }
  }, [expanded, active])

  const handleDownloadTemplate = async (): Promise<void> => {
    setDownloadBusy(true)
    try {
      const res = await axios.get(
        '/api/v1/deck/slide-guidance/template')
      const filename = (
        `slide-guidance-template-v${res.data.version}-`
        + `${todayIso()}.json`)
      downloadJson(res.data, filename)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'failed to download template'
      setUploadError(String(msg))
    } finally {
      setDownloadBusy(false)
    }
  }

  const handleDownloadCurrent = (): void => {
    if (!active?.active || !active.guidance) return
    const v = active.version || 1
    const filename = (
      `slide-guidance-current-v${v}-${todayIso()}.json`)
    downloadJson(active.guidance, filename)
  }

  const handleUploadClick = (): void => {
    setUploadError(null)
    fileInputRef.current?.click()
  }

  const handleFileChange = async (
    e: React.ChangeEvent<HTMLInputElement>,
  ): Promise<void> => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadBusy(true)
    setUploadError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      await axios.post(
        '/api/v1/deck/slide-guidance', form,
        {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        })
      // Refresh state so the panel reflects the newly active
      // guidance.
      await fetchActive()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'failed to upload guidance'
      setUploadError(String(msg))
    } finally {
      setUploadBusy(false)
      // Clear the input so the same file can be re-uploaded
      // after editing.
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const handleReset = async (): Promise<void> => {
    setResetBusy(true)
    setUploadError(null)
    try {
      await axios.delete('/api/v1/deck/slide-guidance')
      await fetchActive()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'failed to clear guidance'
      setUploadError(String(msg))
    } finally {
      setResetBusy(false)
    }
  }

  return (
    <section
      data-section-id="slide-guidance"
      data-section-label="Slide Guidance"
      className="card"
      data-testid="slide-guidance-panel">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        data-testid="slide-guidance-toggle"
        className="w-full flex items-center justify-between gap-3
                   px-4 py-3 hover:bg-navy-700/30 transition-colors
                   rounded">
        <div className="flex items-center gap-2 min-w-0">
          {expanded
            ? <ChevronDown className="w-4 h-4 text-muted shrink-0" />
            : <ChevronRight className="w-4 h-4 text-muted shrink-0" />}
          <FileText className="w-4 h-4 text-electric shrink-0" />
          <h2 className="text-white font-semibold text-sm">
            Slide Guidance
          </h2>
          {active && active.active && (
            <span
              data-testid="slide-guidance-active-chip"
              className="text-2xs text-success uppercase
                         tracking-wide font-medium ml-1">
              custom guidance active
            </span>
          )}
          {active && !active.active && (
            <span
              className="text-2xs text-muted uppercase
                         tracking-wide font-medium ml-1">
              using default guidance
            </span>
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-2 border-t border-border">
          {loading && (
            <div className="flex items-center justify-center gap-2
                            py-4 text-muted text-xs">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading slide guidance…
            </div>
          )}
          {!loading && active && (
            <>
              <p className="text-xs text-slate-300 mb-3">
                Per-slide guidance overlays the hardcoded
                defaults at deck generation time. Download
                the template, edit the string values only,
                upload back. Non-overridable fields (numeric
                anchors, charts, substitution tokens) are
                preserved verbatim.
              </p>
              <div className="mb-4 text-xs">
                <div className="text-muted mb-1">
                  Current state:
                </div>
                {active.active ? (
                  <div className="text-slate-300">
                    <div data-testid="slide-guidance-status-active">
                      Custom guidance v{active.version} uploaded{' '}
                      {active.uploaded_at && (
                        <span>
                          {new Date(active.uploaded_at)
                            .toLocaleString()}
                        </span>
                      )}
                    </div>
                    <div className="text-2xs text-muted mt-0.5">
                      generated_from: {active.generated_from}
                    </div>
                  </div>
                ) : (
                  <div
                    className="text-slate-300"
                    data-testid="slide-guidance-status-default">
                    Using the hardcoded defaults from
                    SLIDE_SPECIFICATIONS. No custom guidance
                    uploaded.
                  </div>
                )}
              </div>
              {uploadError && (
                <div
                  data-testid="slide-guidance-error"
                  className="flex items-start gap-2 px-3 py-2 rounded
                             border border-danger/30 bg-danger/5
                             text-danger text-xs mb-3 font-mono
                             whitespace-pre-wrap break-words">
                  <AlertCircle
                    className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>{uploadError}</span>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleDownloadTemplate}
                  disabled={downloadBusy}
                  data-testid="slide-guidance-download-template"
                  className="flex items-center gap-1.5 px-3 py-1.5
                             rounded border border-border
                             hover:bg-navy-700/30 text-xs
                             text-slate-200 disabled:opacity-50
                             disabled:cursor-wait">
                  {downloadBusy
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Download className="w-3 h-3" />}
                  Download template
                </button>
                {active.active && (
                  <button
                    type="button"
                    onClick={handleDownloadCurrent}
                    data-testid="slide-guidance-download-current"
                    className="flex items-center gap-1.5 px-3 py-1.5
                               rounded border border-border
                               hover:bg-navy-700/30 text-xs
                               text-slate-200">
                    <Download className="w-3 h-3" />
                    Download current guidance
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleUploadClick}
                  disabled={uploadBusy}
                  data-testid="slide-guidance-upload"
                  className="flex items-center gap-1.5 px-3 py-1.5
                             rounded border border-electric/40
                             bg-electric/10 hover:bg-electric/20
                             text-xs text-electric
                             disabled:opacity-50
                             disabled:cursor-wait">
                  {uploadBusy
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Upload className="w-3 h-3" />}
                  Upload guidance
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="application/json,.json"
                  onChange={handleFileChange}
                  data-testid="slide-guidance-file-input"
                  className="hidden" />
                {active.active && (
                  <button
                    type="button"
                    onClick={handleReset}
                    disabled={resetBusy}
                    data-testid="slide-guidance-reset"
                    className="flex items-center gap-1.5 px-3 py-1.5
                               rounded border border-warning/40
                               hover:bg-warning/10 text-xs
                               text-warning disabled:opacity-50
                               disabled:cursor-wait">
                    {resetBusy
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <RotateCcw className="w-3 h-3" />}
                    Reset to defaults
                  </button>
                )}
              </div>
              <p className="text-2xs text-muted mt-3">
                Validation rules: all 12 slides must be present,
                all values must be strings, no extra fields,
                version must match the current template. The
                error message names the exact field path that
                failed.
              </p>
            </>
          )}
        </div>
      )}
    </section>
  )
}
