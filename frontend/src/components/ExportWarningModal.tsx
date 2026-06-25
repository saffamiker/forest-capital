/**
 * ExportWarningModal -- June 25 2026.
 *
 * Pre-export soft block that surfaces in the editor toolbar's
 * Export DOCX flow when the draft is stale (hash mismatch) and/or
 * the academic review has not been run. Never hard-blocks: the
 * primary action is Cancel; "Export Anyway" always proceeds.
 *
 * Modal shape matches the other confirm modals in the app:
 * fixed overlay, click-outside dismisses, Esc dismisses.
 *
 * Body text is chosen by the (hashMismatch, missingReview) tuple:
 *   (true, true)   -> both warnings combined
 *   (true, false)  -> hash-only warning
 *   (false, true)  -> review-only warning
 *   (false, false) -> never shown; the caller skips the modal
 */
import { useEffect, useRef } from 'react'
import { AlertTriangle, X } from 'lucide-react'


export interface ExportWarningModalProps {
  open:          boolean
  hashMismatch:  boolean
  missingReview: boolean
  /** Document label used in the review-warning sentence. */
  documentLabel: string
  onCancel:      () => void
  onConfirm:     () => void
}


function modalCopy(
  hashMismatch: boolean,
  missingReview: boolean,
  documentLabel: string,
): { title: string; body: React.ReactNode } {
  if (hashMismatch && missingReview) {
    return {
      title: 'Export with Warnings',
      body: (
        <>
          <p>
            This draft has two issues that should be resolved before
            submission:
          </p>
          <ol className="list-decimal list-outside pl-5
                          space-y-1 mt-2">
            <li>
              <strong>Data hash mismatch</strong> — the draft was
              generated against an older dataset. Run a Light
              Refresh to update, then regenerate if any figures
              changed.
            </li>
            <li>
              <strong>Academic review not run</strong> — click
              Review {documentLabel} in the editor before
              exporting.
            </li>
          </ol>
          <p className="mt-2 text-2xs text-muted italic">
            You can still export now, but the submission may
            contain outdated figures or unreviewed content.
          </p>
        </>
      ),
    }
  }
  if (hashMismatch) {
    return {
      title: 'Export with Data Warning',
      body: (
        <>
          <p>
            This draft was generated against an older dataset
            (hash mismatch). Run a Light Refresh to update the
            analytics cache. If any figures changed, regenerate
            before submitting.
          </p>
          <p className="mt-2 text-2xs text-muted italic">
            You can still export now.
          </p>
        </>
      ),
    }
  }
  // missingReview only
  return {
    title: 'Export Without Review',
    body: (
      <>
        <p>
          The academic review has not been run against this draft.
          Click Review {documentLabel} in the editor to run it
          before exporting.
        </p>
        <p className="mt-2 text-2xs text-muted italic">
          You can still export now.
        </p>
      </>
    ),
  }
}


export default function ExportWarningModal(
  {
    open, hashMismatch, missingReview, documentLabel,
    onCancel, onConfirm,
  }: ExportWarningModalProps,
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
  if (!hashMismatch && !missingReview) return null

  const { title, body } = modalCopy(
    hashMismatch, missingReview, documentLabel)

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>): void => {
    if (e.target === overlayRef.current) onCancel()
  }

  return (
    <div
      ref={overlayRef}
      data-testid="export-warning-modal"
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="export-warning-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <AlertTriangle
            className="w-5 h-5 text-warning shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-1 text-xs text-slate-300
                          leading-relaxed">
            <h3 className="text-white font-semibold text-sm">
              {title}
            </h3>
            {body}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onCancel}
            data-testid="export-warning-modal-cancel"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-electric text-white hover:bg-blue-500">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="export-warning-modal-confirm"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-muted hover:text-white hover:bg-navy-700">
            Export Anyway
          </button>
        </div>
      </div>
    </div>
  )
}
