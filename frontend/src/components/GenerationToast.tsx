/**
 * GenerationToast — a global toast announcing a document-generation job
 * that finished while the user was away from the Reports page.
 *
 * Mounted once in MainLayout. It reads the module-level generation-job
 * store (which keeps polling across navigation) and shows a persistent
 * toast for the most recent terminal job the user has not yet acted on.
 * On the Reports page the panel itself shows the result, so the toast
 * is suppressed there.
 *
 * June 27 2026 (BUG 2) -- the failed-state branch now ALSO checks
 * whether a current draft exists for the failed job's document_type
 * via the shared currentDraftPresence store. When a usable draft is
 * present we suppress the red AlertCircle + "Generation failed"
 * chrome and show a neutral message + Open in Editor:
 *   "Previous generation attempt unavailable -- your current draft
 *    is still available below."
 * Reserve the red error for cases where there is genuinely no
 * usable draft (cold caches / first-ever generation failure).
 */
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertCircle, X, PenLine, Info } from 'lucide-react'

import {
  useGenerationJobs, dismissJob, isDismissed,
} from '../lib/generationJobs'
import {
  hasCurrentDraft, currentDraftId, refreshFromDraftsList,
  useCurrentDraftPresence,
} from '../lib/currentDraftPresence'

const TYPE_LABEL: Record<string, string> = {
  executive_brief: 'executive brief',
  presentation_deck: 'presentation deck',
  analytical_appendix: 'analytical appendix',
  presentation_script: 'presentation script',
}

export default function GenerationToast() {
  const jobs = useGenerationJobs()
  const location = useLocation()
  const navigate = useNavigate()
  // Subscribe to the draft-presence store so the toast re-renders
  // when DocumentGenerationPanel refreshes the drafts list (e.g.
  // immediately after a generation completes). Without the
  // subscription the toast would show a stale "no draft" state
  // until the user navigated and re-mounted the component.
  useCurrentDraftPresence()

  // June 27 2026 (BUG 2) -- lazy-fetch the drafts list on first
  // mount when the cache is cold so users who never visit Reports
  // still get the toast suppression. refreshFromDraftsList is
  // throttled internally (30s TTL); calling it on every render is
  // safe but we only need it once per mount.
  useEffect(() => {
    void refreshFromDraftsList()
  }, [])

  // The Reports panel shows the completion state inline — no toast there.
  if (location.pathname === '/reports') return null

  const toast = [...jobs].reverse().find(
    (j) => (j.status === 'complete' || j.status === 'failed')
      && !isDismissed(j.job_id))
  if (!toast) return null

  const label = TYPE_LABEL[toast.document_type] ?? 'document'
  const done = toast.status === 'complete'
  // BUG 2 -- when the most-recent terminal job FAILED but a current
  // draft is still available for this doc_type, demote the chrome
  // from red to a neutral 'previous attempt unavailable' note +
  // Open in Editor.
  const failed = toast.status === 'failed'
  const draftRescue = failed && hasCurrentDraft(toast.document_type)
  const rescueDraftId = draftRescue
    ? currentDraftId(toast.document_type)
    : null

  return (
    <div role="status"
      className="fixed bottom-4 right-4 z-[80] w-80 card p-3
                 border border-border shadow-lg"
      style={{ marginBottom: 'env(safe-area-inset-bottom)' }}>
      <div className="flex items-start gap-2">
        {done && (
          <CheckCircle
            className="w-4 h-4 text-success shrink-0 mt-0.5" />
        )}
        {failed && !draftRescue && (
          <AlertCircle
            data-testid="generation-toast-failed-icon"
            className="w-4 h-4 text-danger shrink-0 mt-0.5" />
        )}
        {draftRescue && (
          <Info
            data-testid="generation-toast-rescue-icon"
            className="w-4 h-4 text-muted shrink-0 mt-0.5" />
        )}
        <div className="flex-1 min-w-0">
          <p
            data-testid={
              done ? 'generation-toast-complete-msg'
                : draftRescue ? 'generation-toast-rescue-msg'
                  : 'generation-toast-failed-msg'}
            className={
              done ? 'text-xs text-white'
                : draftRescue ? 'text-xs text-slate-300'
                  : 'text-xs text-white'}>
            {done
              ? `Your ${label} is ready.`
              : (draftRescue
                  ? (
                    `Previous ${label} generation attempt `
                    + 'unavailable -- your current draft is '
                    + 'still available below.')
                  : (
                    `${label[0].toUpperCase()}${label.slice(1)} `
                    + 'generation failed. Return to Reports to '
                    + 'try again.'))}
          </p>
          <div className="flex items-center gap-2 mt-2">
            {done && toast.draft_id != null ? (
              <button type="button"
                onClick={() => {
                  dismissJob(toast.job_id)
                  navigate(`/editor/${toast.draft_id}`)
                }}
                className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                           bg-electric text-white hover:bg-blue-500">
                <PenLine className="w-3 h-3" /> Open in Editor
              </button>
            ) : draftRescue && rescueDraftId != null ? (
              <button type="button"
                onClick={() => {
                  dismissJob(toast.job_id)
                  navigate(`/editor/${rescueDraftId}`)
                }}
                data-testid="generation-toast-rescue-open-editor"
                className="flex items-center gap-1 text-2xs px-2 py-1 rounded
                           bg-electric text-white hover:bg-blue-500">
                <PenLine className="w-3 h-3" /> Open in Editor
              </button>
            ) : (
              <button type="button"
                onClick={() => {
                  dismissJob(toast.job_id)
                  navigate('/reports')
                }}
                className="text-2xs px-2 py-1 rounded border
                           border-electric/40 text-electric
                           hover:bg-electric/10">
                Go to Reports
              </button>
            )}
          </div>
        </div>
        <button type="button" aria-label="Dismiss"
          onClick={() => dismissJob(toast.job_id)}
          className="text-muted hover:text-white shrink-0">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}
