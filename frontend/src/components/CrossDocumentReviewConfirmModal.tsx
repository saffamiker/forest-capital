/**
 * CrossDocumentReviewConfirmModal -- June 22 2026.
 *
 * Confirmation gate on the QA Hub's "Run Cross-Document Review"
 * button. The cross-document pass fans out the council across ALL
 * FOUR deliverables (brief / deck / appendix / script) + an arbiter
 * synthesis -- a meaningfully expensive call. The modal makes the
 * user confirm the intent so a stray click on the QA Hub doesn't
 * trigger the full pass.
 *
 * The per-document review buttons (in each editor's Writing
 * Assistant panel) do NOT carry this confirmation -- they're
 * intentionally one-click. Only the cross-document trigger gates
 * here.
 *
 * Modal shape consistent with BriefWorkflowModal: fixed overlay,
 * click-outside dismisses, Esc dismisses, two action buttons.
 */
import { useEffect } from 'react'
import { X } from 'lucide-react'


export interface CrossDocumentReviewConfirmModalProps {
  open:    boolean
  onClose: () => void
  /** Called when the user confirms. The parent fires the actual
   *  /api/council/academic-review POST. Modal closes itself after
   *  calling onConfirm so the calling component never has to
   *  remember to close it. */
  onConfirm: () => void
}


export default function CrossDocumentReviewConfirmModal({
  open, onClose, onConfirm,
}: CrossDocumentReviewConfirmModalProps): React.ReactElement | null {
  // Esc dismisses. The modal is non-destructive so unconditional
  // dismiss is safe.
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  const handleRun = (): void => {
    onConfirm()
    onClose()
  }

  return (
    <div
      data-testid="cross-document-review-confirm-modal"
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 p-4"
      onClick={onClose}>
      <div
        className="card p-5 max-w-md w-full space-y-3"
        onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold text-white">
            Run Cross-Document Review?
          </h3>
          <button
            type="button"
            onClick={onClose}
            data-testid="cross-document-review-confirm-close"
            aria-label="Close"
            className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body -- verbatim copy from the operator spec. */}
        <p className="text-2xs text-slate-300 leading-relaxed">
          This runs a full council review across all four
          deliverables -- brief, deck, appendix, and presentation
          script -- and is expensive to run. It is most useful as
          a final check after all documents have been generated and
          edited. Individual document reviews are available from
          within each editor.
        </p>

        {/* Action buttons */}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            data-testid="cross-document-review-confirm-cancel"
            className="px-3 py-1.5 rounded text-xs font-medium
                       border border-border text-slate-200
                       hover:bg-navy-700/30 transition-colors">
            Cancel
          </button>
          <button
            type="button"
            onClick={handleRun}
            data-testid="cross-document-review-confirm-run"
            className="px-3 py-1.5 rounded text-xs font-medium
                       bg-warning text-navy-900 hover:bg-amber-400
                       transition-colors">
            Run Review
          </button>
        </div>
      </div>
    </div>
  )
}
