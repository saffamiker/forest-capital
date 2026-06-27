/**
 * FocusBriefModal -- June 27 2026.
 *
 * Optional pre-review focus brief surfaced before Academic Review
 * fires. The user can name specific sections / known issues / areas
 * the council should prioritize -- the brief is injected at the
 * top of every agent's context block. The backend's instruction
 * (`Prioritize these areas in your review. Do not limit your
 * review to only these areas -- surface all issues found.`)
 * keeps the brief from being read as a scope reduction.
 *
 * Cross-document review flow (per the user spec):
 *   1. Click Run Academic Review
 *   2. CrossDocumentReviewConfirmModal (existing -- NOT replaced)
 *   3. User confirms
 *   4. FocusBriefModal (this component) -- Skip or Run Review
 *   5. Review fires with or without brief
 *
 * Per-document review flow (WritingAssistant):
 *   1. Click Run Academic Review
 *   2. FocusBriefModal -- Skip or Run Review
 *   3. Review fires
 *
 * Skip / Esc / click-outside ALL proceed without a brief; only the
 * Run Review button (with text in the textarea) submits with one.
 */
import { useEffect, useRef, useState } from 'react'
import { Sparkles, X, ArrowRight, SkipForward } from 'lucide-react'

const _MAX_CHARS = 1000

export interface FocusBriefModalProps {
  open: boolean
  /** Skip / Esc / click-outside all fire onSkip with null. */
  onSkip:    () => void
  /** Run Review fires onSubmit with the trimmed brief text. The
   *  caller decides whether to coerce empty-text to null or send as
   *  empty -- this component sends ONLY trimmed non-empty text. */
  onSubmit:  (brief: string) => void
}


export default function FocusBriefModal(
  { open, onSkip, onSubmit }: FocusBriefModalProps,
): React.ReactElement | null {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)

  // Reset content on every open so a previous brief doesn't bleed
  // into a fresh review run.
  useEffect(() => {
    if (open) {
      setText('')
      // Focus the textarea once the modal lays out.
      const t = setTimeout(() => {
        textareaRef.current?.focus()
      }, 0)
      return () => clearTimeout(t)
    }
    return undefined
  }, [open])

  // Esc dismisses (== Skip). Enter does NOT submit because the
  // textarea is multi-line and Shift+Enter / plain Enter both feed
  // newlines into the brief.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onSkip()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onSkip])

  if (!open) return null

  const handleOverlayClick = (
    e: React.MouseEvent<HTMLDivElement>,
  ): void => {
    if (e.target === overlayRef.current) onSkip()
  }

  const trimmed = text.trim()
  const remaining = _MAX_CHARS - text.length
  const canSubmit = trimmed.length > 0
  const submit = (): void => {
    if (!canSubmit) return
    onSubmit(trimmed)
  }

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      data-testid="focus-brief-modal"
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 backdrop-blur-sm p-4">
      <div className="card max-w-lg w-full p-5 relative">
        <button
          type="button"
          onClick={onSkip}
          aria-label="Close"
          data-testid="focus-brief-modal-close"
          className="absolute top-3 right-3 text-muted hover:text-white">
          <X className="w-4 h-4" />
        </button>
        <div className="flex items-start gap-3">
          <Sparkles
            className="w-5 h-5 text-electric shrink-0 mt-0.5"
            aria-hidden="true" />
          <div className="flex-1 space-y-2">
            <h3 className="text-white font-semibold text-sm">
              Focus the Council Review (optional)
            </h3>
            <p className="text-2xs text-muted leading-relaxed">
              Point the council at specific sections, known
              issues, or areas you want prioritized. The brief
              directs attention -- the council still surfaces
              every issue it finds.
            </p>
            <label
              htmlFor="focus-brief-textarea"
              className="block text-2xs font-semibold text-slate-300">
              Areas of focus or known issues
            </label>
            <textarea
              id="focus-brief-textarea"
              ref={textareaRef}
              data-testid="focus-brief-textarea"
              rows={5}
              maxLength={_MAX_CHARS}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={
                'e.g. Check Section B Table B.1 for the excess '
                + 'return vs benchmark column. Verify all '
                + 'drawdown figures match the analytical '
                + 'appendix.'}
              className="w-full rounded border border-border
                         bg-navy-900/40 text-xs text-white
                         placeholder:text-muted px-2.5 py-2
                         focus:outline-none focus:ring-2
                         focus:ring-electric/60
                         resize-y min-h-24" />
            <div className="flex items-center justify-between">
              <span
                data-testid="focus-brief-counter"
                className={
                  remaining < 50
                    ? 'text-2xs text-warning'
                    : 'text-2xs text-muted'}>
                {remaining} chars remaining
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-4">
          <button
            type="button"
            onClick={onSkip}
            data-testid="focus-brief-modal-skip"
            className="px-3 py-1.5 rounded text-xs border
                       border-border text-muted hover:text-white
                       hover:bg-navy-700 flex items-center gap-1.5">
            <SkipForward className="w-3 h-3" />
            Skip
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            data-testid="focus-brief-modal-submit"
            className="px-3 py-1.5 rounded text-xs font-semibold
                       bg-electric text-white hover:bg-blue-500
                       disabled:opacity-50
                       disabled:cursor-not-allowed
                       flex items-center gap-1.5">
            <ArrowRight className="w-3 h-3" />
            Run Review
          </button>
        </div>
      </div>
    </div>
  )
}
