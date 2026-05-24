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
import axios from 'axios'
import { AlertCircle, ChevronDown, ChevronRight, Clock,
         Loader2, RotateCcw, Save, Trash2, Star, StarOff } from 'lucide-react'


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
  // May 24 2026 P5 — Final Submission marker. Optional for
  // back-compat with pre-migration-040 cached rows.
  is_final_submission?: boolean | null
  final_submission_at?: string | null
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
  // May 24 2026 — Delete UX state. `confirmDeleteVer` holds the
  // version_number under the type-DELETE prompt; null means the
  // confirm dialog is closed. `confirmDeleteAll` is the bulk-delete
  // confirm state.
  const [confirmDeleteVer, setConfirmDeleteVer] = useState<number | null>(null)
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false)
  const [deleteText, setDeleteText] = useState('')
  // May 24 2026 P5 — surfaced when the user deletes the Final-marked
  // version (or all versions including a Final-marked one). The
  // backend drops the row + the marker; the frontend shows this
  // warning so Bob doesn't proceed to Defense Prep against a stale
  // canonical reference.
  const [finalCleared, setFinalCleared] = useState(false)

  // Hotfix May 23 2026: switched from raw fetch() to axios so the
  // session token (X-API-Key on axios.defaults.headers.common) is
  // attached to every request. Without this every list/save/restore
  // call was 401-ing because fetch() doesn't inherit axios defaults.
  const fetchVersions = useCallback(async () => {
    if (generationId === null) return
    setLoading(true)
    setError(null)
    try {
      const res = await axios.get<VersionListResponse>(
        `/api/v1/reports/generations/${generationId}/versions`)
      setVersions(res.data.versions ?? [])
      setRevision(res.data.paper_revision ?? null)
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
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
      await axios.post(
        `/api/v1/reports/generations/${generationId}/versions`,
        { label: saveLabel || null, source: 'manual' })
      setSaveLabel('')
      setShowSaveForm(false)
      await fetchVersions()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
    } finally {
      setBusyVer(null)
    }
  }, [generationId, saveLabel, fetchVersions])

  const submitMarkFinal = useCallback(async (versionNumber: number) => {
    if (generationId === null) return
    setBusyVer(versionNumber)
    setError(null)
    try {
      await axios.post(
        `/api/v1/reports/generations/${generationId}`
        + `/versions/${versionNumber}/mark-final`)
      await fetchVersions()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
    } finally {
      setBusyVer(null)
    }
  }, [generationId, fetchVersions])

  const submitUnmarkFinal = useCallback(async () => {
    if (generationId === null) return
    setBusyVer(-3)
    setError(null)
    try {
      await axios.delete(
        `/api/v1/reports/generations/${generationId}`
        + `/versions/final-marker`)
      await fetchVersions()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
    } finally {
      setBusyVer(null)
    }
  }, [generationId, fetchVersions])

  const submitDelete = useCallback(async (versionNumber: number) => {
    if (generationId === null) return
    setBusyVer(versionNumber)
    setError(null)
    // Capture whether we're about to drop the Final marker — the
    // warning banner reads "your Final Submission was deleted" if
    // so. Done BEFORE the delete so the row is still in versions[].
    const wasFinal = versions
      .find((v) => v.version_number === versionNumber)
      ?.is_final_submission === true
    try {
      await axios.delete(
        `/api/v1/reports/generations/${generationId}`
        + `/versions/${versionNumber}`)
      setConfirmDeleteVer(null)
      setDeleteText('')
      if (wasFinal) setFinalCleared(true)
      await fetchVersions()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
    } finally {
      setBusyVer(null)
    }
  }, [generationId, fetchVersions, versions])

  const submitDeleteAll = useCallback(async () => {
    if (generationId === null) return
    setBusyVer(-2)
    setError(null)
    const hadFinal = versions.some((v) => v.is_final_submission === true)
    try {
      await axios.delete(
        `/api/v1/reports/generations/${generationId}/versions`)
      setConfirmDeleteAll(false)
      setDeleteText('')
      if (hadFinal) setFinalCleared(true)
      await fetchVersions()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
    } finally {
      setBusyVer(null)
    }
  }, [generationId, fetchVersions, versions])

  const submitRestore = useCallback(async (versionNumber: number) => {
    if (generationId === null) return
    if (!window.confirm(
      `Restore v${versionNumber} as the current paper? `
      + `This creates a new version entry — the older versions are `
      + `preserved.`)) return
    setBusyVer(versionNumber)
    setError(null)
    try {
      await axios.post(
        `/api/v1/reports/generations/${generationId}`
        + `/versions/${versionNumber}/restore`)
      await fetchVersions()
      onRestored?.()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      setError(String(msg))
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

      {finalCleared ? (
        <div
          data-testid="version-final-cleared-warning"
          className="text-2xs text-amber-200 flex items-start gap-1.5
                     px-2 py-1.5 rounded
                     border border-amber-400/40 bg-amber-500/10">
          <AlertCircle className="w-3 h-3 mt-0.5 shrink-0 text-amber-300" />
          <div className="space-y-0.5 leading-snug">
            <p className="font-medium">
              Your Final Submission version has been deleted.
            </p>
            <p>
              Please mark a new version before running Thesis Defense
              Prep or downloading. Otherwise the canonical reference
              falls back to "most recent draft", which moves every
              time you edit.
            </p>
            <button
              type="button"
              onClick={() => setFinalCleared(false)}
              className="text-2xs underline text-amber-300/80
                         hover:text-amber-100">
              Dismiss
            </button>
          </div>
        </div>
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
                  className={`border rounded p-2 space-y-1 ${
                    v.is_final_submission
                      ? 'border-amber-400/60 bg-amber-500/5'
                      : 'border-navy-700 bg-navy-800/40'
                  }`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-text-primary
                                    flex items-center gap-1">
                        v{v.version_number}
                        {v.is_final_submission ? (
                          <span
                            data-testid={`version-final-badge-${v.version_number}`}
                            className="inline-flex items-center gap-0.5 px-1.5 py-0.5
                                       rounded text-2xs font-medium
                                       bg-amber-500/20 text-amber-200
                                       border border-amber-400/40">
                            <Star className="w-2.5 h-2.5 fill-amber-300 text-amber-300" />
                            Final Submission
                          </span>
                        ) : null}
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
                    {v.is_final_submission ? (
                      <button
                        type="button"
                        disabled={busyVer === -3}
                        onClick={submitUnmarkFinal}
                        data-testid={`version-unmark-final-${v.version_number}`}
                        title="Clear the Final Submission marker"
                        className="text-2xs inline-flex items-center gap-1
                                   px-1.5 py-0.5 rounded
                                   border border-amber-400/40
                                   text-amber-200
                                   hover:bg-amber-500/15
                                   disabled:opacity-50 disabled:cursor-not-allowed">
                        <StarOff className="w-2.5 h-2.5" />
                        {busyVer === -3 ? 'Clearing…' : 'Unmark Final'}
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyVer === v.version_number}
                        onClick={() => submitMarkFinal(v.version_number)}
                        data-testid={`version-mark-final-${v.version_number}`}
                        title="Mark this version as the Final Submission. Defense Prep and Citation Adjudication will reference it."
                        className="text-2xs inline-flex items-center gap-1
                                   px-1.5 py-0.5 rounded
                                   border border-amber-400/40
                                   text-amber-200
                                   hover:bg-amber-500/15
                                   disabled:opacity-50 disabled:cursor-not-allowed">
                        <Star className="w-2.5 h-2.5" />
                        {busyVer === v.version_number ? 'Marking…' : 'Mark as Final'}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busyVer === v.version_number}
                      onClick={() => {
                        setConfirmDeleteVer(v.version_number)
                        setDeleteText('')
                      }}
                      data-testid={`version-delete-${v.version_number}`}
                      title="Delete this saved version"
                      className="text-2xs inline-flex items-center gap-1
                                 px-1.5 py-0.5 rounded
                                 border border-red-400/40
                                 text-red-300
                                 hover:bg-red-500/15
                                 disabled:opacity-50 disabled:cursor-not-allowed">
                      <Trash2 className="w-2.5 h-2.5" />
                      Delete
                    </button>
                    {v.flag_count > 0 ? (
                      <span className="text-2xs text-amber-300">
                        {v.flag_count} flag{v.flag_count === 1 ? '' : 's'}
                      </span>
                    ) : null}
                  </div>

                  {confirmDeleteVer === v.version_number ? (
                    <div
                      data-testid={`version-delete-confirm-${v.version_number}`}
                      className="space-y-1 mt-1 p-1.5 rounded
                                 bg-red-950/40 border border-red-400/30">
                      <p className="text-2xs text-red-200">
                        Permanently delete v{v.version_number}? Type
                        <strong className="font-mono"> DELETE </strong>
                        to confirm.
                      </p>
                      <input
                        type="text"
                        value={deleteText}
                        onChange={(e) => setDeleteText(e.target.value)}
                        data-testid={`version-delete-text-${v.version_number}`}
                        autoFocus
                        className="w-full text-2xs px-2 py-1 rounded
                                   bg-navy-950 border border-red-400/30
                                   text-text-primary font-mono" />
                      <div className="flex gap-1">
                        <button
                          type="button"
                          disabled={
                            deleteText !== 'DELETE'
                            || busyVer === v.version_number
                          }
                          onClick={() => submitDelete(v.version_number)}
                          data-testid={`version-delete-submit-${v.version_number}`}
                          className="text-2xs px-2 py-0.5 rounded
                                     bg-red-600 text-white font-medium
                                     hover:bg-red-500
                                     disabled:opacity-50 disabled:cursor-not-allowed">
                          {busyVer === v.version_number
                            ? 'Deleting…' : 'Delete'}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setConfirmDeleteVer(null)
                            setDeleteText('')
                          }}
                          className="text-2xs px-2 py-0.5 rounded
                                     border border-navy-600 text-text-secondary
                                     hover:bg-navy-700">
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : null}

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

          {versions.length > 0 ? (
            <div className="pt-2 mt-2 border-t border-navy-700">
              {confirmDeleteAll ? (
                <div
                  data-testid="version-delete-all-confirm"
                  className="space-y-1 p-1.5 rounded
                             bg-red-950/40 border border-red-400/30">
                  <p className="text-2xs text-red-200">
                    Permanently delete <strong>all {versions.length}</strong>
                    {' '}versions for this draft? Type
                    <strong className="font-mono"> DELETE </strong>
                    to confirm. This cannot be undone.
                  </p>
                  <input
                    type="text"
                    value={deleteText}
                    onChange={(e) => setDeleteText(e.target.value)}
                    data-testid="version-delete-all-text"
                    autoFocus
                    className="w-full text-2xs px-2 py-1 rounded
                               bg-navy-950 border border-red-400/30
                               text-text-primary font-mono" />
                  <div className="flex gap-1">
                    <button
                      type="button"
                      disabled={deleteText !== 'DELETE' || busyVer === -2}
                      onClick={submitDeleteAll}
                      data-testid="version-delete-all-submit"
                      className="text-2xs px-2 py-0.5 rounded
                                 bg-red-600 text-white font-medium
                                 hover:bg-red-500
                                 disabled:opacity-50 disabled:cursor-not-allowed">
                      {busyVer === -2 ? 'Deleting…' : 'Delete all drafts'}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setConfirmDeleteAll(false)
                        setDeleteText('')
                      }}
                      className="text-2xs px-2 py-0.5 rounded
                                 border border-navy-600 text-text-secondary
                                 hover:bg-navy-700">
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setConfirmDeleteAll(true)
                    setDeleteText('')
                  }}
                  data-testid="version-delete-all"
                  className="text-2xs inline-flex items-center gap-1
                             px-2 py-1 rounded
                             border border-red-400/30 text-red-300
                             hover:bg-red-500/10">
                  <Trash2 className="w-3 h-3" />
                  Delete all drafts
                </button>
              )}
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
