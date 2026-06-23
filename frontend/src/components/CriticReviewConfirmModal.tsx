/**
 * CriticReviewConfirmModal -- June 23 2026, Concern 7.
 *
 * Confirmation gate for the full-package adversarial critic review.
 * Per-document critic review has no modal (one-click, just like
 * Concern 3's per-doc academic review). Modal shape follows
 * BriefRegenConfirmModal / CrossDocumentReviewConfirmModal:
 * fixed overlay, click-outside + Esc dismiss, single Cancel + run
 * pair.
 */
import { useEffect, useRef } from 'react'
import { AlertOctagon, X } from 'lucide-react'


export interface CriticReviewConfirmModalProps {
  open:      boolean
  onCancel:  () => void
  onConfirm: () => void
}


export default function CriticReviewConfirmModal(
  { open, onCancel, onConfirm }: CriticReviewConfirmModalProps,
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
      data-testid="critic-review-confirm-modal"
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="critic-review-confirm-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <AlertOctagon
            className="w-5 h-5 text-danger shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-2">
            <h3 className="text-white font-semibold text-sm">
              Run Adversarial Critic Review?
            </h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              Gemini and Grok will independently review all four
              deliverables for methodological, factual, and logical
              errors. This is resource-intensive. Run after all
              documents are finalized.
            </p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            data-testid="critic-review-confirm-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-muted hover:text-white hover:bg-navy-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="critic-review-confirm-modal-run"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-danger text-white hover:bg-rose-500">
            Run Review
          </button>
        </div>
      </div>
    </div>
  )
}
