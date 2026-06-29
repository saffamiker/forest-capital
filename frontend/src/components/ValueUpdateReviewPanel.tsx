/**
 * ValueUpdateReviewPanel.tsx -- June 28 2026.
 *
 * Split-panel review mode for the dual-mode token storage
 * pipeline (PR-DM-Lite). Reads token_value nodes directly from
 * the upgraded content_json via
 * GET /api/v1/data/review-pending-updates/{draft_id} and lets
 * the operator selectively apply updates via
 * POST /api/v1/data/apply-updates/{draft_id}.
 *
 * Layout:
 *   Header -- doc-type tabs (Brief / Appendix / Deck / Script)
 *   Left  -- read-only reference: token | current | cache | match
 *   Right -- selectable updates with apply button
 *
 * Phase 1 ships the table-based right panel without the
 * cross-panel hover linking + freeze-boundary divider on the
 * left (those land in PR-DM-Rich together with the rich
 * NodeView). The data shape is identical so the linking layer
 * is a pure additive renderer change.
 */
import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import {
  CheckCircle, AlertCircle, AlertTriangle, Loader2, RefreshCw,
  Lock,
} from 'lucide-react'


type DocType =
  | 'executive_brief'
  | 'analytical_appendix'
  | 'presentation_deck'
  | 'presentation_script'


const DOC_LABEL: Record<DocType, string> = {
  executive_brief:     'Brief',
  analytical_appendix: 'Appendix',
  presentation_deck:   'Deck',
  presentation_script: 'Script',
}


interface ReviewEntry {
  token:          string
  current_value:  string | null
  cache_value:    string | null
  match:          boolean
  overridden:     boolean
  last_updated:   string | null
  data_hash:      string | null
}


interface ReviewResponse {
  draft_id:       number
  migration_run:  boolean
  effective_hash?: string
  entries:        ReviewEntry[]
  total?:         number
  matched?:       number
  mismatched?:    number
  overridden?:    number
  message?:       string
}


interface DraftRow {
  id:            number
  document_type: string
  is_current?:   boolean
}


export interface ValueUpdateReviewPanelProps {
  /** When supplied, triggers an immediate fetch + scrolls to
   *  this draft's tab. Pass the timestamp of the most recent
   *  successful light refresh to auto-fire. */
  triggerKey: string | number | null
}


export default function ValueUpdateReviewPanel(
  { triggerKey }: ValueUpdateReviewPanelProps,
): React.ReactElement | null {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<DraftRow[]>([])
  const [activeDocType, setActiveDocType]
    = useState<DocType>('executive_brief')
  const [reviewData, setReviewData]
    = useState<Record<number, ReviewResponse>>({})
  const [selected, setSelected]
    = useState<Record<string, boolean>>({})
  const [applyResult, setApplyResult]
    = useState<{ count: number; doctype: DocType } | null>(null)

  // Active draft id derived from drafts + tab.
  const activeDraftId = drafts.find(
    (d) => d.document_type === activeDocType
      && d.is_current !== false)?.id ?? null

  // Fetch review summary for the active draft.
  const loadReview = useCallback(async (
    draftId: number,
  ): Promise<void> => {
    if (!draftId) return
    setBusy(true)
    setError(null)
    try {
      const res = await axios.get<ReviewResponse>(
        `/api/v1/data/review-pending-updates/${draftId}`)
      setReviewData((prev) => (
        { ...prev, [draftId]: res.data }))
      // Pre-check mismatched entries (auto-update default).
      const next: Record<string, boolean> = {}
      for (const e of res.data.entries) {
        if (!e.match && !e.overridden) {
          next[`${draftId}::${e.token}`] = true
        }
      }
      setSelected((prev) => ({ ...prev, ...next }))
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Review fetch failed'
      setError(
        typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setBusy(false)
    }
  }, [])

  // On mount + triggerKey change: re-fetch drafts list + the
  // active draft's review summary.
  useEffect(() => {
    if (triggerKey === null) return
    let cancelled = false
    void (async () => {
      try {
        const res = await axios.get<{ drafts: DraftRow[] }>(
          '/api/v1/documents/drafts')
        if (cancelled) return
        setDrafts(res.data?.drafts ?? [])
      } catch {
        if (!cancelled) setDrafts([])
      }
    })()
    return () => { cancelled = true }
  }, [triggerKey])

  // When activeDocType or drafts change, fetch the active
  // draft's review.
  useEffect(() => {
    if (activeDraftId !== null
        && !reviewData[activeDraftId]) {
      void loadReview(activeDraftId)
    }
  }, [activeDraftId, reviewData, loadReview])

  const handleApply = async (): Promise<void> => {
    if (!activeDraftId) return
    const tokens: string[] = []
    for (const [key, checked] of Object.entries(selected)) {
      if (!checked) continue
      const [drId, token] = key.split('::')
      if (parseInt(drId, 10) === activeDraftId) {
        tokens.push(token)
      }
    }
    if (tokens.length === 0) return
    setBusy(true)
    try {
      const res = await axios.post<{ updates_count: number }>(
        `/api/v1/data/apply-updates/${activeDraftId}`,
        { tokens })
      setApplyResult({
        count: res.data.updates_count,
        doctype: activeDocType,
      })
      // Re-fetch to refresh table.
      await loadReview(activeDraftId)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Apply failed'
      setError(
        typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setBusy(false)
    }
  }

  if (triggerKey === null) return null

  const activeReview = activeDraftId
    ? reviewData[activeDraftId] : null

  return (
    <section
      data-testid="value-update-review-panel"
      className="card p-4 space-y-3">
      <div>
        <h2 className="text-white font-semibold text-sm flex
                       items-center gap-1.5">
          <RefreshCw className="w-4 h-4 text-electric" />
          Value Update Review
        </h2>
        <p className="text-xs text-muted mt-1">
          Review proposed updates from the fresh cache before
          applying. Only selected mismatches get written to the
          draft. Overridden values are skipped.
        </p>
      </div>

      {/* Doc-type tabs */}
      <div
        data-testid="value-update-review-tabs"
        className="flex items-center gap-1 border-b
                   border-border/40">
        {(Object.keys(DOC_LABEL) as DocType[]).map((dt) => {
          const exists = drafts.some(
            (d) => d.document_type === dt
              && d.is_current !== false)
          const active = dt === activeDocType
          return (
            <button
              key={dt}
              type="button"
              onClick={() => setActiveDocType(dt)}
              disabled={!exists}
              data-testid={`review-tab-${dt}`}
              className={(
                'px-3 py-1.5 text-xs font-medium '
                + 'transition-colors border-b-2 -mb-px '
                + (active
                  ? 'border-electric text-electric'
                  : (exists
                    ? 'border-transparent text-muted '
                      + 'hover:text-slate-200'
                    : 'border-transparent text-muted/40 '
                      + 'cursor-not-allowed')))}>
              {DOC_LABEL[dt]}
            </button>
          )
        })}
      </div>

      {error && (
        <div
          data-testid="value-update-review-error"
          className="rounded border border-danger/40 bg-danger/5
                     p-2.5 text-2xs text-danger
                     flex items-start gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {busy && (
        <div className="flex items-center gap-2 py-2 text-2xs
                        text-muted">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading review summary...
        </div>
      )}

      {activeReview && !activeReview.migration_run && (
        <div
          data-testid="value-update-review-not-upgraded"
          className="rounded border border-warning/40
                     bg-warning/5 p-2.5 text-2xs text-warning
                     flex items-start gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            {activeReview.message
              || 'Draft not yet upgraded to dual-mode token storage.'}
          </span>
        </div>
      )}

      {activeReview && activeReview.migration_run && (
        <>
          <div
            data-testid="value-update-review-summary"
            className="rounded border border-slate-500/40
                       bg-slate-700/20 p-2 text-2xs text-slate-200
                       flex items-center justify-between">
            <span>
              {activeReview.matched || 0} match | {' '}
              {activeReview.mismatched || 0} mismatch | {' '}
              {activeReview.overridden || 0} overridden
              {activeReview.effective_hash ? (
                <span className="text-muted ml-2">
                  ({activeReview.effective_hash.slice(0, 8)})
                </span>
              ) : null}
            </span>
            <button
              type="button"
              onClick={() => { void handleApply() }}
              disabled={busy
                || (activeReview.mismatched || 0) === 0}
              data-testid="value-update-review-apply-button"
              className="px-2 py-1 text-2xs font-medium
                         bg-electric/15 border border-electric/40
                         text-electric rounded
                         hover:bg-electric/25
                         disabled:opacity-50
                         disabled:cursor-not-allowed">
              Apply selected updates
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-2xs text-muted uppercase
                              tracking-wide text-left">
                  <th className="py-1.5 pr-3 w-8"></th>
                  <th className="py-1.5 pr-3 font-medium">
                    Token
                  </th>
                  <th className="py-1.5 pr-3 font-medium
                                text-right">
                    Draft
                  </th>
                  <th className="py-1.5 pr-3 font-medium
                                text-right">
                    Cache
                  </th>
                  <th className="py-1.5 pr-3 font-medium
                                text-center w-16">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {activeReview.entries.map((e) => {
                  const key = `${activeDraftId}::${e.token}`
                  const isSel = selected[key] || false
                  const disabled = (
                    e.match || e.overridden)
                  return (
                    <tr
                      key={e.token}
                      data-testid={`review-row-${
                        e.token.replace(/[{}]/g, '')}`}
                      className="border-b border-border/30">
                      <td className="py-1.5 pr-3 align-top">
                        <input
                          type="checkbox"
                          checked={isSel}
                          disabled={disabled}
                          onChange={(ev) => setSelected((p) => (
                            { ...p, [key]: ev.target.checked }))}
                          data-testid={`review-checkbox-${
                            e.token.replace(/[{}]/g, '')}`} />
                      </td>
                      <td className="py-1.5 pr-3 align-top
                                    font-mono text-2xs
                                    text-electric">
                        {e.token}
                        {e.overridden ? (
                          <Lock
                            className="w-3 h-3 inline ml-1
                                       text-warning" />
                        ) : null}
                      </td>
                      <td className="py-1.5 pr-3 font-mono
                                    text-right align-top
                                    whitespace-nowrap">
                        {e.current_value ?? '—'}
                      </td>
                      <td className="py-1.5 pr-3 font-mono
                                    text-right align-top
                                    whitespace-nowrap">
                        {e.cache_value ?? '—'}
                      </td>
                      <td className="py-1.5 pr-3 align-top
                                    text-center">
                        {e.match
                          ? <CheckCircle
                              className="w-3.5 h-3.5
                                         text-success inline"
                              aria-label="match" />
                          : <AlertTriangle
                              className="w-3.5 h-3.5
                                         text-warning inline"
                              aria-label="mismatch" />}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {applyResult && (
            <div
              data-testid="value-update-review-apply-result"
              className="rounded border border-success/30
                         bg-success/5 p-2.5 text-2xs text-success
                         flex items-start gap-1.5">
              <CheckCircle className="w-3.5 h-3.5 shrink-0
                                     mt-0.5" />
              <span>
                {applyResult.count} value(s) updated in{' '}
                {DOC_LABEL[applyResult.doctype]}.
              </span>
            </div>
          )}
        </>
      )}
    </section>
  )
}
