/**
 * DraftVersionSelector -- Concern 7k-vii + 7l-iii.
 *
 * The editor toolbar dropdown that lets the team switch between
 * draft versions for the current document_type. Each version
 * carries the label, created_at, and (when available) a summary
 * of which critic round produced it.
 *
 * Mounted in the editor toolbar. On change, navigates to the
 * selected draft id; the editor state hydrates from the
 * /api/v1/documents/drafts/{id} endpoint as usual.
 *
 * When only one version exists this renders nothing -- no UI
 * clutter when there's nothing to switch between.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import { Layers, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'


export interface DraftVersionSelectorProps {
  /** The draft type to list versions for. */
  documentType: string
  /** The currently open draft id. The dropdown highlights this. */
  currentDraftId: number
}


interface DraftListItem {
  id: number
  document_type: string
  title?: string | null
  created_at?: string | null
  is_current?: boolean
}


export default function DraftVersionSelector(
  { documentType, currentDraftId }: DraftVersionSelectorProps,
): React.ReactElement | null {
  const navigate = useNavigate()
  const [drafts, setDrafts] = useState<DraftListItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    axios.get<{ drafts: DraftListItem[] }>(
      '/api/v1/documents/drafts')
      .then((res) => {
        if (cancelled) return
        const filtered = (res.data.drafts ?? []).filter(
          (d) => d.document_type === documentType)
        // Sort newest-first so the most recent revision is at top.
        filtered.sort((a, b) => {
          const aT = a.created_at
            ? new Date(a.created_at).getTime() : 0
          const bT = b.created_at
            ? new Date(b.created_at).getTime() : 0
          return bT - aT
        })
        setDrafts(filtered)
      })
      .catch(() => { if (!cancelled) setDrafts([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [documentType])

  if (loading) {
    return (
      <span className="text-2xs text-muted flex items-center
                       gap-1">
        <Loader2 className="w-3 h-3 animate-spin" />
        Loading versions…
      </span>
    )
  }
  if (drafts.length < 2) return null

  return (
    <div
      className="flex items-center gap-1.5"
      data-testid="draft-version-selector">
      <Layers className="w-3.5 h-3.5 text-muted" />
      <label className="text-2xs text-muted">Version:</label>
      <select
        value={currentDraftId}
        onChange={(e) => navigate(`/editor/${e.target.value}`)}
        className="text-2xs bg-navy-800 border border-border
                   rounded px-1.5 py-0.5 text-slate-200
                   focus:outline-none focus:border-electric/60">
        {drafts.map((d) => (
          <option key={d.id} value={d.id}>
            {d.title || `Draft ${d.id}`}
            {d.created_at
              ? ` -- ${new Date(d.created_at).toLocaleDateString()}`
              : ''}
          </option>
        ))}
      </select>
    </div>
  )
}
