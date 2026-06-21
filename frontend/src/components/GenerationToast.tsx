/**
 * GenerationToast — a global toast announcing a document-generation job
 * that finished while the user was away from the Reports page.
 *
 * Mounted once in MainLayout. It reads the module-level generation-job
 * store (which keeps polling across navigation) and shows a persistent
 * toast for the most recent terminal job the user has not yet acted on.
 * On the Reports page the panel itself shows the result, so the toast
 * is suppressed there.
 */
import { useLocation, useNavigate } from 'react-router-dom'
import { CheckCircle, AlertCircle, X, PenLine } from 'lucide-react'

import {
  useGenerationJobs, dismissJob, isDismissed,
} from '../lib/generationJobs'

const TYPE_LABEL: Record<string, string> = {
  executive_brief: 'executive brief',
  presentation_deck: 'presentation deck',
}

export default function GenerationToast() {
  const jobs = useGenerationJobs()
  const location = useLocation()
  const navigate = useNavigate()

  // The Reports panel shows the completion state inline — no toast there.
  if (location.pathname === '/reports') return null

  const toast = [...jobs].reverse().find(
    (j) => (j.status === 'complete' || j.status === 'failed')
      && !isDismissed(j.job_id))
  if (!toast) return null

  const label = TYPE_LABEL[toast.document_type] ?? 'document'
  const done = toast.status === 'complete'

  return (
    <div role="status"
      className="fixed bottom-4 right-4 z-[80] w-80 card p-3
                 border border-border shadow-lg"
      style={{ marginBottom: 'env(safe-area-inset-bottom)' }}>
      <div className="flex items-start gap-2">
        {done
          ? <CheckCircle className="w-4 h-4 text-success shrink-0 mt-0.5" />
          : <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />}
        <div className="flex-1 min-w-0">
          <p className="text-xs text-white">
            {done
              ? `Your ${label} is ready.`
              : `${label[0].toUpperCase()}${label.slice(1)} generation `
                + 'failed. Return to Reports to try again.'}
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
