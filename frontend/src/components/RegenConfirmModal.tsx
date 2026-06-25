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
  /** June 25 2026 -- when supplied (and non-empty), the modal
   *  swaps to 'Regeneration Warning' framing and renders the
   *  list of unresolved critical audit-finding check names. Max
   *  5 surfaced; the rest collapse under '… and N more'. Empty
   *  array or undefined leaves the legacy 'Regenerate <doc>?'
   *  framing unchanged. */
  auditFindings?: string[] | undefined
  /** June 25 2026 -- the source document_type being regenerated.
   *  Drives the cascade-impact callout that lists which other
   *  current drafts will be marked stale per _REGEN_CASCADE:
   *    executive_brief / analytical_appendix -> all other 3
   *    presentation_deck -> presentation_script
   *    presentation_script -> nothing (no callout)
   *  Omitted = no cascade callout (legacy callers stay the same).
   */
  sourceDocumentType?: string | undefined
}


// Mirrors backend tools/editor_drafts._REGEN_CASCADE. Kept in sync
// manually; the cascade is short and rarely changes.
const _CASCADE_TYPES: Record<string, string[]> = {
  executive_brief: [
    'analytical_appendix', 'presentation_deck',
    'presentation_script',
  ],
  analytical_appendix: [
    'executive_brief', 'presentation_deck',
    'presentation_script',
  ],
  presentation_deck: ['presentation_script'],
  presentation_script: [],
  midpoint_paper: [],
}

const _DOC_LABELS: Record<string, string> = {
  executive_brief: 'Executive Brief',
  analytical_appendix: 'Analytical Appendix',
  presentation_deck: 'Presentation Deck',
  presentation_script: 'Presentation Script',
  midpoint_paper: 'Midpoint Paper',
}


export default function RegenConfirmModal(
  {
    open, documentName, onCancel, onConfirm, auditFindings,
    sourceDocumentType,
  }: RegenConfirmModalProps,
) {
  const cascadeTypes = (
    sourceDocumentType
      ? (_CASCADE_TYPES[sourceDocumentType] ?? [])
      : [])
  const cascadeLabels = cascadeTypes.map(
    (t) => _DOC_LABELS[t] ?? t)
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
              {auditFindings && auditFindings.length > 0
                ? 'Regeneration Warning'
                : `Regenerate ${documentName}?`}
            </h3>
            {auditFindings && auditFindings.length > 0 && (
              <div
                data-testid="regen-confirm-audit-block"
                className="text-xs text-amber-100/90 leading-relaxed
                           rounded border border-warning/40
                           bg-warning/5 p-2.5 space-y-1.5">
                <p>
                  {auditFindings.length} audit finding
                  {auditFindings.length === 1 ? ' is' : 's are'}{' '}
                  unresolved from a previous audit run.
                  Regenerating will auto-resolve {' '}
                  {auditFindings.length === 1 ? 'it' : 'them'}{' '}
                  and create a fresh draft.
                </p>
                <ul className="list-disc list-inside text-2xs
                               text-amber-100/85 space-y-0.5">
                  {auditFindings.slice(0, 5).map((f, i) => (
                    <li
                      key={i}
                      data-testid={`regen-confirm-audit-finding-${i}`}>
                      {f}
                    </li>
                  ))}
                  {auditFindings.length > 5 && (
                    <li className="text-amber-100/60 italic">
                      … and {auditFindings.length - 5} more
                    </li>
                  )}
                </ul>
              </div>
            )}
            <p className="text-xs text-slate-300 leading-relaxed">
              This will replace the current draft for the whole
              team. The existing draft will be archived in version
              history (preserved, not lost) and no longer shown as
              current. All team members will see the new version
              once generation completes.
            </p>
            {cascadeLabels.length > 0 && (
              <div
                data-testid="regen-confirm-cascade-block"
                className="text-xs text-amber-100/90 leading-relaxed
                           rounded border border-warning/30
                           bg-warning/5 p-2.5 space-y-1.5">
                <p>
                  Regenerating the {documentName} will also mark
                  the following current
                  document{cascadeLabels.length === 1 ? '' : 's'}{' '}
                  as outdated — they will need to be regenerated
                  after this one completes:
                </p>
                <ul className="list-disc list-inside text-2xs
                               text-amber-100/85 space-y-0.5">
                  {cascadeLabels.map((label, i) => (
                    <li
                      key={i}
                      data-testid={`regen-confirm-cascade-${i}`}>
                      {label}
                    </li>
                  ))}
                </ul>
              </div>
            )}
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
            {auditFindings && auditFindings.length > 0
              ? 'Regenerate Anyway'
              : 'Regenerate'}
          </button>
        </div>
      </div>
    </div>
  )
}
