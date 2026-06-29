/**
 * CrossDocumentReviewConfirmModal -- June 22 2026.
 *
 * Confirmation gate that fires BEFORE the QA Hub's cross-document
 * Academic Review POST. The cross-document pass is expensive (eight
 * personas across four deliverables, ~90s wall time) and is most
 * useful as a final check after all documents are generated and
 * edited. A stray click on the QA Hub button should not silently
 * trigger the run; spec calls for an explicit confirm.
 *
 * Per-document review buttons (in each editor's Writing Assistant)
 * do NOT have this gate -- they are quick checks the user runs
 * frequently while editing.
 *
 * Modal shape mirrors BriefRegenConfirmModal / BriefWorkflowModal:
 * fixed overlay, click-outside dismisses, Esc dismisses.
 */
import { useEffect, useRef } from 'react'
import { GraduationCap, X } from 'lucide-react'


export interface CrossDocumentReviewConfirmModalProps {
  open:      boolean
  onCancel:  () => void
  onConfirm: () => void
}


export default function CrossDocumentReviewConfirmModal(
  {
    open, onCancel, onConfirm,
  }: CrossDocumentReviewConfirmModalProps,
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
      data-testid="cross-document-review-confirm-modal"
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="cross-document-review-confirm-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <GraduationCap
            className="w-5 h-5 text-warning shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-2">
            <h3 className="text-white font-semibold text-sm">
              Run Cross-Document Review?
            </h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              This runs a full council review across all four
              deliverables -- brief, deck, appendix, and presentation
              script. It is resource-intensive and most useful as a
              final check after all documents have been generated
              and edited. Individual document reviews are available
              from within each editor.
            </p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            data-testid="cross-document-review-confirm-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-muted hover:text-white hover:bg-navy-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="cross-document-review-confirm-modal-run"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-warning text-navy-900 hover:bg-amber-400">
            Run Review
          </button>
        </div>
      </div>
    </div>
  )
}
