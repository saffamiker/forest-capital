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
  /** June 27 2026 -- replaces the legacy auditFindings:string[]
   *  prop with a simple unresolved-finding count. When > 0 the
   *  modal renders the spec line:
   *    "This document has N unresolved review findings.
   *     Regenerating will clear them."
   *  When 0 / undefined the warning line is omitted. Source: the
   *  audit_warnings.flag_counts.total field from the current
   *  draft row (populated by tools.document_audit). */
  findingsCount?: number | undefined
  /** June 27 2026 -- true when the current draft's updated_at
   *  diverges from created_at by more than 60s (the same
   *  threshold TileMetadataBlock uses). Surfaces the spec line:
   *    "Your manual edits to this document will be lost."
   *  False / undefined omits the warning. Source: derived at
   *  the call site from the drafts API timestamps. */
  hasManualEdits?: boolean | undefined
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
//
// June 25 2026 (FIX): appendix regen no longer cascades to
// executive_brief -- brief is UPSTREAM of appendix, not downstream.
// The previous entry stranded the brief tile with no is_current=true
// draft after every appendix regen.
const _CASCADE_TYPES: Record<string, string[]> = {
  executive_brief: [
    'analytical_appendix', 'presentation_deck',
    'presentation_script',
  ],
  analytical_appendix: [
    'presentation_deck', 'presentation_script',
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
    open, documentName, onCancel, onConfirm, findingsCount,
    hasManualEdits, sourceDocumentType,
  }: RegenConfirmModalProps,
) {
  const findingsN = findingsCount ?? 0
  const cancelRef = useRef<HTMLButtonElement | null>(null)
  const cascadeTypes = (
    sourceDocumentType
      ? (_CASCADE_TYPES[sourceDocumentType] ?? [])
      : [])
  const cascadeLabels = cascadeTypes.map(
    (t) => _DOC_LABELS[t] ?? t)
  const overlayRef = useRef<HTMLDivElement | null>(null)

  // June 27 2026 -- Cancel-as-default keyboard contract:
  //   * Escape cancels (unchanged).
  //   * Enter ALSO cancels (was a no-op; the spec wants Enter
  //     and Esc to both back out so a stray Enter never fires
  //     the destructive action). Confirmation requires an
  //     explicit click / tab + Space on the Regenerate button.
  // The Cancel button also gets autoFocus on open so the
  // default focus target is Cancel, not Confirm.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' || e.key === 'Enter') {
        // Block Enter from triggering whichever button has focus
        // (in case the user tabbed to Regenerate); cancel always.
        e.preventDefault()
        onCancel()
      }
    }
    document.addEventListener('keydown', onKey)
    // autoFocus on the Cancel button so Tab order starts on the
    // safe action and a no-op keystroke can't confirm.
    cancelRef.current?.focus()
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
              {`Regenerate ${documentName}?`}
            </h3>
            {findingsN > 0 && (
              <p
                data-testid="regen-confirm-findings-warning"
                className="text-xs text-amber-200 leading-relaxed
                           rounded border border-warning/40
                           bg-warning/10 p-2.5">
                This document has {findingsN} unresolved review
                finding{findingsN === 1 ? '' : 's'}.
                Regenerating will clear {' '}
                {findingsN === 1 ? 'it' : 'them'}.
              </p>
            )}
            {hasManualEdits && (
              // June 27 2026 -- per spec: warn the user that any
              // manual edits to the draft (made via the in-platform
              // editor since generation) will be lost on regen.
              // Detected at the call site via updated_at > created_at
              // + 60s tolerance (the same threshold TileMetadataBlock
              // uses).
              <p
                data-testid="regen-confirm-manual-edits-warning"
                className="text-xs text-red-200 leading-relaxed
                           rounded border border-red-500/50
                           bg-red-500/10 p-2.5 font-semibold">
                Your manual edits to this document will be lost.
              </p>
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
        {/* June 27 2026 button contract per spec:
              * Cancel is leftmost, autoFocused on open so the
                default keyboard target is the safe action.
              * Regenerate is styled DESTRUCTIVE (red) to signal
                that confirming destroys the current draft.
              * Enter / Escape both cancel (handled by the
                keydown listener above) -- pressing Enter while
                Cancel has focus also cancels via the button's
                native click semantics. */}
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            type="button"
            ref={cancelRef}
            onClick={onCancel}
            autoFocus
            data-testid="regen-confirm-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border border-border
                       text-white bg-navy-700 hover:bg-navy-600
                       focus:outline-none focus:ring-2
                       focus:ring-electric/60 font-semibold">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="regen-confirm-modal-confirm"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-red-600 text-white hover:bg-red-500
                       focus:outline-none focus:ring-2
                       focus:ring-red-400/70">
            Regenerate
          </button>
        </div>
      </div>
    </div>
  )
}
