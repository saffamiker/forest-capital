/**
 * VersionHistoryPanel — the version-control surface for the report
 * paper_md.
 *
 * Item 2 (May 23 2026 — collaborative editing + version control).
 *
 * Every save to paper_md creates an append-only snapshot in
 * report_paper_versions. This panel:
 *
 *   - GETs the version list on mount and after any save
 *   - lets the reviewer save a NAMED snapshot of the current paper
 *     (the "Save Version" button)
 *   - lets the reviewer preview any prior version (popover with
 *     truncated paper_md head)
 *   - lets the reviewer RESTORE a prior version (creates a new
 *     snapshot rather than overwriting history)
 *
 * The panel does NOT modify the editor's paper_md directly — it
 * fires `onRestored` after a successful restore so the parent can
 * re-fetch the generation and update the editor textarea.
 *
 * Concurrent-edit detection (the PATCH paper-md 409 path) is
 * handled in the parent ReportWriter — this panel just shows the
 * version history.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, ChevronDown, ChevronRight, Clock,
         Loader2, RotateCcw, Save } from 'lucide-react'


export interface PaperVersion {
  id: number
  version_number: number
  paper_md: string
  flag_count: number
  word_counts: Record<string, unknown>
  saved_by_email: string | null
  saved_at: string | null
  label: string | null
  source: string
  restored_from_version: number | null
}


export interface VersionListResponse {
  generation_id: number
  paper_revision: number | null
  versions: PaperVersion[]
  version_count: number
}


const SOURCE_LABEL: Record<string, string> = {
  manual:           'Saved manually',
  auto_edit:        'Inline edit',
  auto_iterate:     'AI iteration',
  auto_resolve_bob: '[BOB] resolved',
  restore:          'Restored',
}


export interface VersionHistoryPanelProps {
  generationId: number | null
  /** Fires after a successful restore so the parent can re-fetch
   *  the generation and update the editor. */
  onRestored?: () => void
}


export default function VersionHistoryPanel({
  generationId, onRestored,
}: VersionHistoryPanelProps) {
  const [versions, setVersions] = useState<PaperVersion[]>([])
  const [revision, setRevision] = useState<number | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [open,     setOpen]     = useState(true)
  const [busyVer,  setBusyVer]  = useState<number | null>(null)
  const [saveLabel, setSaveLabel] = useState('')
  const [showSaveForm, setShowSaveForm] = useState(false)
  const [previewVer, setPreviewVer] = useState<number | null>(null)

  const fetchVersions = useCallback(async () => {
    if (generationId === null) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `/api/v1/reports/generations/${generationId}/versions`,
        { credentials: 'include' })
      if (!res.ok) throw new Error(`Versions fetch returned ${res.status}`)
      const data = await res.json() as VersionListResponse
      setVersions(data.versions ?? [])
      setRevision(data.paper_revision ?? null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [generationId])

  useEffect(() => { void fetchVersions() }, [fetchVersions])

  const submitSave = useCallback(async () => {
    if (generationId === null) return
    setBusyVer(-1)
    setError(null)
    try {
      const res = await fetch(
        `/api/v1/reports/generations/${generationId}/versions`,
        {
          method:      'POST',
          credentials: 'include',
          headers:     { 'Content-Type': 'application/json' },
          body:        JSON.stringify({
            label:  saveLabel || null,
            source: 'manual',
          }),
        })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Save failed (${res.status})`)
      }
      setSaveLabel('')
      setShowSaveForm(false)
      await fetchVersions()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyVer(null)
    }
  }, [generationId, saveLabel, fetchVersions])

  const submitRestore = useCallback(async (versionNumber: number) => {
    if (generationId === null) return
    if (!window.confirm(
      `Restore v${versionNumber} as the current paper? `
      + `This creates a new version entry — the older versions are `
      + `preserved.`)) return
    setBusyVer(versionNumber)
    setError(null)
    try {
      const res = await fetch(
        `/api/v1/reports/generations/${generationId}`
        + `/versions/${versionNumber}/restore`,
        { method: 'POST', credentials: 'include' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Restore failed (${res.status})`)
      }
      await fetchVersions()
      onRestored?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyVer(null)
    }
  }, [generationId, fetchVersions, onRestored])

  if (generationId === null) return null

  return (
    <section
      data-testid="version-history-panel"
      className="bg-navy-900 border border-navy-700 rounded p-3 space-y-2">
      <header className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 text-sm font-semibold
                     text-text-primary hover:text-electric-blue
                     transition-colors">
          {open ? <ChevronDown className="w-4 h-4" />
                : <ChevronRight className="w-4 h-4" />}
          Version History
          <span className="text-2xs px-1.5 py-0.5 rounded
                           bg-navy-700 text-text-muted">
            {versions.length} version{versions.length === 1 ? '' : 's'}
          </span>
          {revision !== null ? (
            <span className="text-2xs text-text-muted">
              rev {revision}
            </span>
          ) : null}
        </button>
        {loading ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin text-text-muted" />
        ) : null}
      </header>

      {error ? (
        <p className="text-xs text-red-400 flex items-start gap-1">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          {error}
        </p>
      ) : null}

      {open ? (
        <>
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="version-save-toggle"
              onClick={() => setShowSaveForm(!showSaveForm)}
              className="text-2xs inline-flex items-center gap-1
                         px-2 py-1 rounded border border-navy-600
                         text-text-secondary hover:bg-navy-800">
              <Save className="w-3 h-3" />
              {showSaveForm ? 'Cancel' : 'Save Version'}
            </button>
          </div>

          {showSaveForm ? (
            <div className="space-y-1 pt-1">
              <input
                type="text"
                placeholder="Label (optional, e.g. pre-Molly-edit)"
                value={saveLabel}
                onChange={(e) => setSaveLabel(e.target.value)}
                data-testid="version-save-label"
                className="w-full text-2xs px-2 py-1 rounded
                           bg-navy-800 border border-navy-700
                           text-text-primary placeholder:text-text-muted" />
              <button
                type="button"
                disabled={busyVer === -1}
                onClick={submitSave}
                data-testid="version-save-submit"
                className="text-2xs px-2 py-1 rounded
                           bg-electric-blue text-navy-950 font-medium
                           hover:bg-electric-blue/90 disabled:opacity-50">
                {busyVer === -1 ? 'Saving…' : 'Create snapshot'}
              </button>
            </div>
          ) : null}

          {versions.length === 0 ? (
            <p className="text-xs text-text-muted italic">
              No versions yet — versions appear when you save the paper
              or run an AI iteration.
            </p>
          ) : (
            <ol className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
              {versions.map((v) => (
                <li
                  key={v.id}
                  data-testid={`version-row-${v.version_number}`}
                  className="border border-navy-700 rounded p-2
                             bg-navy-800/40 space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-text-primary
                                    flex items-center gap-1">
                        v{v.version_number}
                        {v.label ? (
                          <span className="text-text-secondary
                                           font-normal">
                            — {v.label}
                          </span>
                        ) : null}
                      </p>
                      <p className="text-2xs text-text-muted
                                    flex items-center gap-1 flex-wrap">
                        <Clock className="w-2.5 h-2.5" />
                        {v.saved_at
                          ? new Date(v.saved_at).toLocaleString()
                          : '—'}
                        <span className="text-text-muted">·</span>
                        <span>{SOURCE_LABEL[v.source] ?? v.source}</span>
                        {v.saved_by_email ? (
                          <>
                            <span className="text-text-muted">·</span>
                            <span>{v.saved_by_email}</span>
                          </>
                        ) : null}
                        {v.restored_from_version ? (
                          <>
                            <span className="text-text-muted">·</span>
                            <span>from v{v.restored_from_version}</span>
                          </>
                        ) : null}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setPreviewVer(
                        previewVer === v.version_number
                          ? null : v.version_number)}
                      data-testid={`version-preview-${v.version_number}`}
                      className="text-2xs px-1.5 py-0.5 rounded
                                 border border-navy-600 text-text-secondary
                                 hover:bg-navy-700">
                      {previewVer === v.version_number ? 'Hide' : 'Preview'}
                    </button>
                    <button
                      type="button"
                      disabled={busyVer === v.version_number}
                      onClick={() => submitRestore(v.version_number)}
                      data-testid={`version-restore-${v.version_number}`}
                      className="text-2xs inline-flex items-center gap-1
                                 px-1.5 py-0.5 rounded
                                 border border-electric-blue/40
                                 text-electric-blue
                                 hover:bg-electric-blue/15
                                 disabled:opacity-50 disabled:cursor-not-allowed">
                      <RotateCcw className="w-2.5 h-2.5" />
                      {busyVer === v.version_number ? 'Restoring…' : 'Restore'}
                    </button>
                    {v.flag_count > 0 ? (
                      <span className="text-2xs text-amber-300">
                        {v.flag_count} flag{v.flag_count === 1 ? '' : 's'}
                      </span>
                    ) : null}
                  </div>

                  {previewVer === v.version_number ? (
                    <pre
                      data-testid={`version-preview-body-${v.version_number}`}
                      className="text-2xs text-text-secondary font-mono
                                 max-h-32 overflow-y-auto p-1.5 rounded
                                 bg-navy-950 border border-navy-700
                                 whitespace-pre-wrap break-words">
                      {v.paper_md.slice(0, 600)}
                      {v.paper_md.length > 600 ? '…' : ''}
                    </pre>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </>
      ) : null}
    </section>
  )
}
