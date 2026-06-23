/**
 * BriefRegenConfirmModal -- June 23 2026.
 *
 * Confirmation gate that fires BEFORE the Executive Brief regen POST
 * when /api/v1/story-plans/exists reports that downstream plans
 * (deck / appendix / script) currently exist. The spec replaces the
 * old generic window.confirm for brief regens so the user sees the
 * concrete consequence: regenerating the brief automatically clears
 * the deck / appendix / script story plans server-side
 * (_generate_brief_document does this at the top), so those
 * documents will need to be regenerated to stay consistent with the
 * new brief narrative.
 *
 * If the pre-flight check returns exists=false, the modal is
 * skipped entirely and Generate fires immediately.
 *
 * Modal shape mirrors BriefWorkflowModal / CrossDocumentReview
 * confirm modal: fixed overlay, click-outside dismisses, Esc
 * dismisses, single Cancel + primary action pair.
 */
import { useEffect, useRef } from 'react'
import { AlertTriangle, X } from 'lucide-react'


export interface BriefRegenConfirmModalProps {
  open:      boolean
  onCancel:  () => void
  onConfirm: () => void
}


export default function BriefRegenConfirmModal(
  { open, onCancel, onConfirm }: BriefRegenConfirmModalProps,
) {
  const overlayRef = useRef<HTMLDivElement | null>(null)

  // Esc dismisses; matches the pattern other modals in the app use.
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
    // Only dismiss when the click hit the overlay itself, not the
    // modal body that bubbles up to it.
    if (e.target === overlayRef.current) onCancel()
  }

  return (
    <div
      ref={overlayRef}
      data-testid="brief-regen-confirm-modal"
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="brief-regen-confirm-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <AlertTriangle
            className="w-5 h-5 text-warning shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-2">
            <h3 className="text-white font-semibold text-sm">
              Regenerate Executive Brief?
            </h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              Generating a new brief will clear the story plans for
              the deck, appendix, and presentation script. Those
              documents will need to be regenerated afterward to
              stay consistent with the new brief narrative.
            </p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            data-testid="brief-regen-confirm-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-muted hover:text-white hover:bg-navy-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="brief-regen-confirm-modal-confirm"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-electric text-white hover:bg-blue-500">
            Regenerate Brief
          </button>
        </div>
      </div>
    </div>
  )
}
