/**
 * DraftSelector — dropdown adjacent to the Template selector that
 * lets Bob switch between his saved generation drafts instead of
 * starting fresh every login.
 *
 * May 23 2026 — paired with the placeholder-failures hotfix, this
 * closes the loop on draft persistence: PR #104 widened the
 * pipeline-audit restore window to 14 days, but it only restored
 * the SINGLE most recent draft. The user reported they sometimes
 * want to go back to an earlier draft (e.g. revert a misguided
 * iteration that landed in the active row). This dropdown surfaces
 * every draft tied to the user's email, newest first.
 *
 * Backend: GET /api/v1/reports/generations?limit=20&template_id=...
 * Returns the slim preview shape (id, generated_at, flag_count,
 * word_count_total, preview). Full paper_md fetched on selection
 * via the existing GET /api/v1/reports/generations/{id}.
 *
 * UX contract:
 *   - First option is ALWAYS "New draft" — leaves the editor
 *     unchanged (no destructive side-effect from opening the menu).
 *   - Picking a saved draft fires `onSelect(id)`; the parent
 *     decides what to do (re-fetch + hydrate editor).
 *   - The dropdown auto-refreshes when the template changes so it
 *     reflects only drafts for the active template.
 *
 * Auth is enforced server-side (team-member only). The dropdown
 * simply renders whatever the API returns; a viewer (no permission)
 * would see an empty list, which is the correct degradation.
 */
import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { Loader2 } from 'lucide-react'


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
  // Fires when the user picks a saved draft. Parent fetches the
  // full generation and hydrates the editor. Null fires on "New
  // draft" so the parent can clear the editor state.
  onSelect: (draftId: number | null) => void
  // The currently-loaded generation id. Renders as the selected
  // option so the dropdown reflects what the editor is showing.
  // Null means a fresh pipeline (no draft loaded yet).
  selectedDraftId: number | null
  // Parent bumps this after a Generate or Restore lands so the
  // dropdown re-fetches and the new draft shows up without a page
  // reload. Defaults to 0; any change triggers a refresh.
  refreshNonce?: number
}


function _formatTimestamp(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    // Short form for the dropdown — date + 24h time, no seconds.
    // "May 23 14:23" reads at a glance and fits in the option width.
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
  const flags = d.flag_count > 0 ? ` · ${d.flag_count} flag${d.flag_count === 1 ? '' : 's'}` : ''
  return `${ts}${wc}${flags}`
}


export default function DraftSelector({
  templateId, onSelect, selectedDraftId, refreshNonce = 0,
}: DraftSelectorProps) {
  const [drafts, setDrafts] = useState<DraftPreview[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await axios.get<{ drafts: DraftPreview[] }>(
        '/api/v1/reports/generations',
        { params: { template_id: templateId, limit: 20 } })
      setDrafts(res.data.drafts || [])
    } catch (err) {
      // Fail-open — render an empty list with a small error hint.
      // The selector is additive: failure here doesn't block the
      // user from running a fresh pipeline.
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

  // Refresh on mount AND whenever the active template changes so
  // the list always reflects the current template's drafts. The
  // refreshNonce dep lets the parent force a re-fetch after a
  // Generate or Restore without a page reload.
  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, refreshNonce])

  const handleChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value
    if (val === 'new') {
      onSelect(null)
      return
    }
    const id = parseInt(val, 10)
    if (Number.isFinite(id)) {
      onSelect(id)
    }
  }, [onSelect])

  return (
    <>
      <label
        htmlFor="draft-selector"
        className="text-text-secondary text-xs">
        Draft:
      </label>
      <select
        id="draft-selector"
        data-testid="draft-selector"
        value={selectedDraftId === null ? 'new' : String(selectedDraftId)}
        onChange={handleChange}
        disabled={loading}
        className={
          'px-2 py-1.5 bg-navy-900 border border-navy-700 ' +
          'rounded text-white text-sm focus:outline-none ' +
          'focus:border-electric-blue disabled:opacity-60'
        }>
        <option value="new">New draft (start fresh)</option>
        {drafts.map((d) => (
          <option key={d.id} value={String(d.id)}>
            {_formatOptionLabel(d)}
          </option>
        ))}
      </select>
      {loading ? (
        <Loader2
          className="w-3.5 h-3.5 text-text-muted animate-spin"
          aria-label="Loading drafts" />
      ) : null}
      {error ? (
        <span
          className="text-amber-400 text-xs"
          title={error}>
          (drafts unavailable)
        </span>
      ) : null}
    </>
  )
}


// Exported for parent use — lets ReportWriter trigger a refresh
// after a successful Generate so the new draft appears in the
// dropdown without a page reload. The parent imports and calls
// the same axios endpoint; this just centralises the URL.
export const DRAFT_LIST_URL = '/api/v1/reports/generations'
