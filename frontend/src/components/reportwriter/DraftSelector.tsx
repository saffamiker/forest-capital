/**
 * DraftSelector — custom dropdown adjacent to the Template selector
 * that lets Bob switch between saved generation drafts and delete
 * them inline.
 *
 * May 23 2026 — paired with the placeholder-failures hotfix, this
 * closes the loop on draft persistence: PR #104 widened the
 * pipeline-audit restore window to 14 days, but it only restored
 * the SINGLE most recent draft. The user reported they sometimes
 * want to go back to an earlier draft (e.g. revert a misguided
 * iteration that landed in the active row). This dropdown surfaces
 * every draft tied to the user's email, newest first.
 *
 * May 24 2026 — refactor from <select> to a custom popover dropdown
 * per user spec: "Add a trash icon next to each draft entry in the
 * Draft selector dropdown (except 'New draft (start fresh)'). On
 * click: confirm with type-DELETE dialog, then remove from list.
 * Users should not need to load a draft to delete it." The native
 * <select> element cannot carry icons inside options, so this is
 * a purpose-built dropdown using a popover panel.
 *
 * Backend:
 *   GET    /api/v1/reports/generations?limit=20&template_id=...
 *   DELETE /api/v1/reports/generations/{id}     (new, May 24)
 *
 * UX contract:
 *   - First option is ALWAYS "New draft" — leaves the editor
 *     unchanged (no destructive side-effect from opening the menu).
 *   - Picking a saved draft fires `onSelect(id)`; the parent
 *     decides what to do (re-fetch + hydrate editor).
 *   - Trash icon per saved draft → type-DELETE confirm → DELETE.
 *     If the deleted draft was the currently-loaded one, the
 *     selector calls onSelect(null) so the editor clears.
 *   - The dropdown auto-refreshes when the template changes so it
 *     reflects only drafts for the active template.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { Loader2, ChevronDown, Trash2, AlertCircle } from 'lucide-react'


export interface DraftPreview {
  id: number
  template_id: string
  flag_count: number
  word_count_total: number
  generated_at: string | null
  preview: string
}


interface DraftSelectorProps {
  templateId: string
  onSelect: (draftId: number | null) => void
  selectedDraftId: number | null
  refreshNonce?: number
}


function _formatTimestamp(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
      hour12: false,
    })
  } catch {
    return iso
  }
}


function _formatOptionLabel(d: DraftPreview): string {
  const ts = _formatTimestamp(d.generated_at)
  const wc = d.word_count_total > 0 ? ` · ${d.word_count_total}w` : ''
  const flags = d.flag_count > 0
    ? ` · ${d.flag_count} flag${d.flag_count === 1 ? '' : 's'}`
    : ''
  return `${ts}${wc}${flags}`
}


export default function DraftSelector({
  templateId, onSelect, selectedDraftId, refreshNonce = 0,
}: DraftSelectorProps) {
  const [drafts, setDrafts] = useState<DraftPreview[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  // May 24 2026 — type-DELETE confirmation flow lives in the
  // panel. confirmDeleteId is the draft id under the prompt;
  // null means no prompt open.
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)
  const [deleteText, setDeleteText] = useState('')
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await axios.get<{ drafts: DraftPreview[] }>(
        '/api/v1/reports/generations',
        { params: { template_id: templateId, limit: 20 } })
      setDrafts(res.data.drafts || [])
    } catch (err) {
      setDrafts([])
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail
        setError(typeof detail === 'string' ? detail : 'Failed to load drafts.')
      } else {
        setError('Failed to load drafts.')
      }
    } finally {
      setLoading(false)
    }
  }, [templateId])

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, refreshNonce])

  // Close the popover on outside click. The confirm prompt is
  // INSIDE the popover so this doesn't dismiss the prompt
  // mid-typing.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
        setConfirmDeleteId(null)
        setDeleteText('')
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const handlePick = useCallback((id: number | null) => {
    onSelect(id)
    setOpen(false)
    setConfirmDeleteId(null)
    setDeleteText('')
  }, [onSelect])

  const submitDelete = useCallback(async (id: number) => {
    setDeletingId(id)
    try {
      await axios.delete(`/api/v1/reports/generations/${id}`)
      // If the deleted draft is the currently-loaded one, clear
      // the editor by selecting "new draft".
      if (selectedDraftId === id) onSelect(null)
      setConfirmDeleteId(null)
      setDeleteText('')
      await refresh()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail || err.message)
        : (err as Error).message
      setError(String(msg))
    } finally {
      setDeletingId(null)
    }
  }, [refresh, selectedDraftId, onSelect])

  const currentLabel = (() => {
    if (selectedDraftId === null) return 'New draft (start fresh)'
    const found = drafts.find((d) => d.id === selectedDraftId)
    return found ? _formatOptionLabel(found) : `Draft #${selectedDraftId}`
  })()

  return (
    <div ref={rootRef} className="relative inline-flex items-center gap-1">
      <label
        htmlFor="draft-selector"
        className="text-text-secondary text-xs">
        Draft:
      </label>
      <button
        type="button"
        id="draft-selector"
        data-testid="draft-selector"
        onClick={() => setOpen((v) => !v)}
        disabled={loading}
        className={
          'inline-flex items-center gap-1.5 px-2 py-1.5 ' +
          'bg-navy-900 border border-navy-700 ' +
          'rounded text-white text-sm focus:outline-none ' +
          'focus:border-electric-blue disabled:opacity-60 ' +
          'min-w-[180px] justify-between'
        }>
        <span className="truncate max-w-[200px]">{currentLabel}</span>
        <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
      </button>
      {loading ? (
        <Loader2
          className="w-3.5 h-3.5 text-text-muted animate-spin"
          aria-label="Loading drafts" />
      ) : null}
      {error && !open ? (
        <span
          className="text-amber-400 text-xs"
          title={error}>
          (drafts unavailable)
        </span>
      ) : null}

      {open ? (
        <div
          data-testid="draft-selector-panel"
          className="absolute left-0 top-full mt-1 z-50
                     min-w-[260px] max-w-[360px]
                     bg-navy-900 border border-navy-700
                     rounded-lg shadow-2xl overflow-hidden">
          <ul className="max-h-72 overflow-y-auto py-1">
            <li>
              <button
                type="button"
                onClick={() => handlePick(null)}
                data-testid="draft-selector-new"
                className={
                  'w-full text-left px-3 py-2 text-xs text-white ' +
                  'hover:bg-navy-700 transition-colors ' +
                  (selectedDraftId === null
                    ? 'bg-electric-blue/10 border-l-2 border-electric-blue'
                    : '')
                }>
                New draft (start fresh)
              </button>
            </li>
            {drafts.map((d) => (
              <li key={d.id} className="border-t border-navy-800/40">
                <div className="flex items-stretch">
                  <button
                    type="button"
                    onClick={() => handlePick(d.id)}
                    data-testid={`draft-selector-pick-${d.id}`}
                    className={
                      'flex-1 text-left px-3 py-2 text-xs text-white ' +
                      'hover:bg-navy-700 transition-colors ' +
                      'truncate ' +
                      (selectedDraftId === d.id
                        ? 'bg-electric-blue/10 border-l-2 border-electric-blue'
                        : '')
                    }>
                    {_formatOptionLabel(d)}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      setConfirmDeleteId(d.id)
                      setDeleteText('')
                    }}
                    disabled={deletingId === d.id}
                    aria-label={`Delete draft ${d.id}`}
                    title="Delete this draft"
                    data-testid={`draft-selector-delete-${d.id}`}
                    className={
                      'px-2 text-text-muted hover:text-red-300 ' +
                      'hover:bg-red-500/10 transition-colors ' +
                      'disabled:opacity-40 disabled:cursor-not-allowed'
                    }>
                    {deletingId === d.id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
                {confirmDeleteId === d.id ? (
                  <div
                    data-testid={`draft-selector-delete-confirm-${d.id}`}
                    className="px-3 py-2 bg-red-950/40 border-t border-red-400/30
                               space-y-1.5">
                    <p className="text-2xs text-red-200 flex items-start gap-1">
                      <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
                      <span>
                        Permanently delete this draft? Type{' '}
                        <strong className="font-mono">DELETE</strong>
                        {' '}to confirm. Saved versions are removed too.
                      </span>
                    </p>
                    <input
                      type="text"
                      value={deleteText}
                      onChange={(e) => setDeleteText(e.target.value)}
                      data-testid={`draft-selector-delete-text-${d.id}`}
                      autoFocus
                      className="w-full text-2xs px-2 py-1 rounded
                                 bg-navy-950 border border-red-400/30
                                 text-white font-mono" />
                    <div className="flex gap-1">
                      <button
                        type="button"
                        disabled={
                          deleteText !== 'DELETE' || deletingId === d.id
                        }
                        onClick={() => submitDelete(d.id)}
                        data-testid={`draft-selector-delete-submit-${d.id}`}
                        className="text-2xs px-2 py-0.5 rounded
                                   bg-red-600 text-white font-medium
                                   hover:bg-red-500
                                   disabled:opacity-50 disabled:cursor-not-allowed">
                        {deletingId === d.id ? 'Deleting…' : 'Delete'}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setConfirmDeleteId(null)
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
              </li>
            ))}
            {drafts.length === 0 && !loading ? (
              <li className="px-3 py-2 text-2xs text-text-muted italic">
                No saved drafts yet for this template.
              </li>
            ) : null}
          </ul>
          {error && open ? (
            <p className="px-3 py-1.5 text-2xs text-amber-300
                          border-t border-navy-700 bg-amber-500/5">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}


// Exported for parent use — lets ReportWriter trigger a refresh
// after a successful Generate so the new draft appears in the
// dropdown without a page reload. The parent imports and calls
// the same axios endpoint; this just centralises the URL.
export const DRAFT_LIST_URL = '/api/v1/reports/generations'
