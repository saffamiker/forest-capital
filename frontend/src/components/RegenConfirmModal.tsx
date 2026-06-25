/**
 * RegenConfirmModal -- June 24 2026.
 *
 * Generic team-replacement regeneration warning shown when any
 * team member clicks Regenerate on a document tile that already
 * has an is_current=true draft. Used for the deck, analytical
 * appendix, and presentation script -- the executive brief has
 * its own brief-specific modal (BriefRegenConfirmModal) that
 * merges this warning with the downstream story-plan clear
 * warning into a single combined message.
 *
 * Body verbatim per spec:
 *
 *   "This will replace the current draft for the whole team. The
 *    existing draft will be archived and no longer shown as
 *    current. All team members will see the new version once
 *    generation completes."
 *
 * Modal shape mirrors BriefRegenConfirmModal: fixed overlay,
 * click-outside dismisses, Esc dismisses, single Cancel + primary
 * action pair.
 */
import { useEffect, useRef } from 'react'
import { AlertTriangle, X } from 'lucide-react'


export interface RegenConfirmModalProps {
  open:        boolean
  /** Human-readable document name -- 'Final Presentation Deck',
   *  'Analytical Appendix', 'Presentation Script'. Title becomes
   *  'Regenerate <name>?'. */
  documentName: string
  onCancel:  () => void
  onConfirm: () => void
}


export default function RegenConfirmModal(
  {
    open, documentName, onCancel, onConfirm,
  }: RegenConfirmModalProps,
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
      data-testid="regen-confirm-modal"
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="regen-confirm-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <AlertTriangle
            className="w-5 h-5 text-warning shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-2">
            <h3 className="text-white font-semibold text-sm">
              Regenerate {documentName}?
            </h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              This will replace the current draft for the whole
              team. The existing draft will be archived and no
              longer shown as current. All team members will see
              the new version once generation completes.
            </p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            data-testid="regen-confirm-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-muted hover:text-white hover:bg-navy-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="regen-confirm-modal-confirm"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-electric text-white hover:bg-blue-500">
            Regenerate
          </button>
        </div>
      </div>
    </div>
  )
}
