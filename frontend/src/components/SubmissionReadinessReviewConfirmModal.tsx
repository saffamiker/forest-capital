/**
 * SubmissionReadinessReviewConfirmModal -- June 23 2026.
 *
 * Confirmation gate before the Submission Readiness Review fires.
 * Same shape as BriefRegenConfirmModal /
 * CrossDocumentReviewConfirmModal: fixed overlay, click-outside +
 * Esc dismiss, single Cancel + primary action pair.
 *
 * The Submission Readiness Review is resource-intensive (data
 * cross-reference + full cross-document academic review) so a
 * stray click should not silently kick off both passes.
 */
import { useEffect, useRef } from 'react'
import { ShieldCheck, X } from 'lucide-react'


export interface SubmissionReadinessReviewConfirmModalProps {
  open:      boolean
  onCancel:  () => void
  onConfirm: () => void
}


export default function SubmissionReadinessReviewConfirmModal(
  {
    open, onCancel, onConfirm,
  }: SubmissionReadinessReviewConfirmModalProps,
) {
  const overlayRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>): void => {
    if (e.target === overlayRef.current) onCancel()
  }

  return (
    <div
      ref={overlayRef}
      data-testid="submission-readiness-confirm-modal"
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="submission-readiness-confirm-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <ShieldCheck
            className="w-5 h-5 text-success shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-2">
            <h3 className="text-white font-semibold text-sm">
              Run Submission Readiness Review?
            </h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              This runs a full data cross-reference and
              cross-document council review across all four
              deliverables. It is resource-intensive and should
              only be run after all documents have been generated,
              reviewed, and edited.
            </p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            data-testid="submission-readiness-confirm-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-muted hover:text-white hover:bg-navy-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="submission-readiness-confirm-modal-run"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-success text-navy-900 hover:bg-green-400">
            Run Review
          </button>
        </div>
      </div>
    </div>
  )
}
