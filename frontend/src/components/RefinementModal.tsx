/**
 * RefinementModal -- June 27 2026.
 *
 * Multi-round iterative refinement of a fix proposal's text before
 * the surgical patch executes against the document. Each refinement
 * is a cheap targeted Sonnet call against
 * /api/v1/apply-fix/refine -- the document is NEVER touched until
 * the user clicks "Apply This Fix" in this modal.
 *
 * UX (per spec):
 *   Header:                "Refine Fix Proposal"
 *   Proposal display:      shows the CURRENT proposed fix text
 *                          (original on first open, updates after
 *                          each successful refinement round)
 *   Refinement input:      textarea "What should be adjusted?"
 *                          500 chars max + live counter
 *   Three buttons:
 *     Cancel         -- closes modal, discards all refinements,
 *                       original proposal unchanged
 *     Refine         -- POST /apply-fix/refine; on success updates
 *                       the proposal panel + clears input
 *     Apply This Fix -- fires onApply(currentProposalText) so the
 *                       parent runs the surgical patch with the
 *                       (possibly refined) text
 *   Round counter:         "Round 1", "Round 2", ... visible at all
 *                          times; increments per successful refine
 *   Refinement history:    collapsible thread of (note, result)
 *                          tuples; one row per round
 *   Esc / click-outside:   == Cancel
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  Sparkles, X, Send, Loader2, Check, ChevronDown, ChevronRight,
} from 'lucide-react'

const _NOTE_MAX_CHARS = 500


export interface RefinementHistoryEntry {
  round: number
  note: string
  resultProposalText: string
}


export interface RefinementModalProps {
  open: boolean
  /** The original fix proposal text. Restored if the user cancels. */
  originalProposalText: string
  fixProposalId:        number
  documentType:         string
  sectionName:          string | null
  /** Cancel closes the modal, discards all refinements; the parent
   *  treats the original proposal as unchanged. */
  onCancel:             () => void
  /** Apply This Fix passes the CURRENT working proposal text (the
   *  original OR the most recently refined version) so the parent
   *  runs the surgical patch with it. */
  onApply:              (proposalText: string) => void
}


export default function RefinementModal(
  {
    open, originalProposalText, fixProposalId, documentType,
    sectionName, onCancel, onApply,
  }: RefinementModalProps,
): React.ReactElement | null {
  // Working proposal text -- starts as original, updates after each
  // successful refine. Reset to original on every open so a previous
  // refinement session doesn't bleed into a fresh one.
  const [workingText, setWorkingText] = useState(originalProposalText)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<RefinementHistoryEntry[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const noteRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (open) {
      setWorkingText(originalProposalText)
      setNote('')
      setError(null)
      setHistory([])
      setHistoryOpen(false)
      // Auto-focus the refinement note so the user can start typing.
      const t = setTimeout(() => noteRef.current?.focus(), 0)
      return () => clearTimeout(t)
    }
    return undefined
  }, [open, originalProposalText])

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  const handleOverlayClick = (
    e: React.MouseEvent<HTMLDivElement>,
  ): void => {
    if (e.target === overlayRef.current) onCancel()
  }

  const trimmedNote = note.trim()
  const remaining = _NOTE_MAX_CHARS - note.length
  const canRefine = trimmedNote.length > 0 && !busy
  const round = history.length + 1
  const previousRound = history.length

  const handleRefine = async (): Promise<void> => {
    if (!canRefine) return
    setBusy(true)
    setError(null)
    try {
      const res = await axios.post<{
        refined_proposal_text: string
      }>(
        '/api/v1/apply-fix/refine',
        {
          fix_proposal_id:        fixProposalId,
          current_proposal_text:  workingText,
          refinement_note:        trimmedNote,
          document_type:          documentType,
          section_name:           sectionName,
          refinement_round:       round,
        })
      const refined = res.data?.refined_proposal_text?.trim() || ''
      if (!refined) {
        setError(
          'Refinement returned an empty response. Try again or '
          + 'adjust the note.')
        return
      }
      setHistory((h) => [
        ...h,
        {
          round,
          note: trimmedNote,
          resultProposalText: refined,
        },
      ])
      setWorkingText(refined)
      setNote('')
      noteRef.current?.focus()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (typeof err.response?.data?.detail === 'string'
          ? err.response.data.detail
          : err.message)
        : 'Refinement failed.'
      setError(String(msg))
    } finally {
      setBusy(false)
    }
  }

  const handleApply = (): void => {
    if (busy) return
    onApply(workingText)
  }

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      data-testid="refinement-modal"
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-2xl w-full p-5 relative
                      max-h-[90vh] overflow-y-auto">
        <button
          type="button"
          onClick={onCancel}
          aria-label="Close"
          data-testid="refinement-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <Sparkles className="w-5 h-5 text-electric shrink-0 mt-0.5" />
          <div className="flex-1 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-white font-semibold text-sm">
                Refine Fix Proposal
              </h3>
              <span
                data-testid="refinement-modal-round"
                className="text-2xs uppercase tracking-wide
                           text-muted">
                Round {round}
                {previousRound > 0 && (
                  <span className="ml-1 text-electric/80">
                    ({previousRound} refined)
                  </span>
                )}
              </span>
            </div>

            {/* Current proposal display panel. */}
            <div>
              <div className="text-2xs uppercase tracking-wide
                              text-muted mb-1">
                Current proposed fix
                {previousRound > 0 && (
                  <span className="text-electric/80 normal-case
                                   tracking-normal ml-1">
                    (refined)
                  </span>
                )}
              </div>
              <pre
                data-testid="refinement-modal-current-proposal"
                className="text-xs text-slate-200
                           whitespace-pre-wrap
                           bg-navy-900/40 border border-border
                           rounded p-2.5 max-h-40 overflow-y-auto
                           font-sans">
                {workingText}
              </pre>
            </div>

            {/* Refinement note input. */}
            <div>
              <label
                htmlFor="refinement-note-textarea"
                className="block text-2xs font-semibold
                           text-slate-300 mb-1">
                What should be adjusted?
              </label>
              <textarea
                id="refinement-note-textarea"
                ref={noteRef}
                data-testid="refinement-modal-note"
                rows={3}
                maxLength={_NOTE_MAX_CHARS}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={
                  'e.g. Mention that the figure should match '
                  + 'Table B.1 specifically. Or: tighten the tone.'}
                className="w-full rounded border border-border
                           bg-navy-900/40 text-xs text-white
                           placeholder:text-muted px-2.5 py-2
                           focus:outline-none focus:ring-2
                           focus:ring-electric/60 resize-y
                           min-h-16" />
              <div className="flex items-center justify-between">
                <span
                  data-testid="refinement-modal-counter"
                  className={
                    remaining < 50
                      ? 'text-2xs text-warning'
                      : 'text-2xs text-muted'}>
                  {remaining} chars remaining
                </span>
              </div>
            </div>

            {/* Refinement history (collapsible). */}
            {history.length > 0 && (
              <div>
                <button type="button"
                  onClick={() => setHistoryOpen((v) => !v)}
                  data-testid="refinement-modal-history-toggle"
                  className="text-2xs text-electric hover:text-electric/80
                             flex items-center gap-1">
                  {historyOpen
                    ? <ChevronDown className="w-3 h-3" />
                    : <ChevronRight className="w-3 h-3" />}
                  Refinement history ({history.length} round
                  {history.length === 1 ? '' : 's'})
                </button>
                {historyOpen && (
                  <div
                    data-testid="refinement-modal-history"
                    className="mt-2 space-y-2">
                    {history.map((h) => (
                      <div
                        key={h.round}
                        data-testid={
                          `refinement-modal-history-row-${h.round}`}
                        className="rounded border border-border
                                   bg-navy-900/30 p-2 space-y-1">
                        <div className="text-2xs uppercase
                                        tracking-wide text-muted">
                          Round {h.round}
                        </div>
                        <div className="text-2xs">
                          <span className="text-muted">Note:</span>
                          {' '}
                          <span className="text-slate-300">
                            {h.note}
                          </span>
                        </div>
                        <div className="text-2xs">
                          <span className="text-muted">
                            Result:
                          </span>
                          <pre className="text-slate-200
                                          whitespace-pre-wrap
                                          font-sans mt-1
                                          bg-navy-900/40 rounded
                                          p-1.5">
                            {h.resultProposalText}
                          </pre>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {error && (
              <div
                data-testid="refinement-modal-error"
                className="text-2xs text-danger leading-relaxed
                           rounded border border-danger/40
                           bg-danger/5 p-2">
                {error}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between gap-2
                        mt-5">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            data-testid="refinement-modal-cancel"
            className="px-3 py-1.5 rounded text-xs border
                       border-border text-muted hover:text-white
                       hover:bg-navy-700 disabled:opacity-50">
            Cancel
          </button>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => { void handleRefine() }}
              disabled={!canRefine}
              data-testid="refinement-modal-refine"
              className="px-3 py-1.5 rounded text-xs font-semibold
                         border border-electric/50 text-electric
                         hover:bg-electric/10 disabled:opacity-50
                         disabled:cursor-not-allowed
                         flex items-center gap-1.5">
              {busy
                ? <><Loader2 className="w-3 h-3 animate-spin" />
                    Refining…</>
                : <><Send className="w-3 h-3" /> Refine</>}
            </button>
            <button
              type="button"
              onClick={handleApply}
              disabled={busy}
              data-testid="refinement-modal-apply"
              className="px-3 py-1.5 rounded text-xs font-semibold
                         bg-warning text-navy-900
                         hover:bg-amber-400 disabled:opacity-50
                         flex items-center gap-1.5">
              <Check className="w-3 h-3" />
              Apply This Fix
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
