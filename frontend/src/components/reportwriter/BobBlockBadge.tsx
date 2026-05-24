/**
 * frontend/src/components/reportwriter/BobBlockBadge.tsx
 *
 * Highlighted, interactive [BOB] callout block.
 *
 * May 23 2026 (Item 1 — [BOB] pre-population) — the BOB kind now
 * receives PRE-POPULATED draft content from the academic writer.
 * The badge renders the draft as editable text by default (not a
 * blank textarea), with a toolbar offering:
 *
 *   [Mark as reviewed]    — accept the (possibly edited) draft.
 *                           Calls /resolve-bob with the current
 *                           textarea content. Replaces the [BOB]
 *                           marker in paper_md.
 *   [Rephrase]            — calls /iterate with action='rephrase';
 *                           on accept replaces the textarea content
 *                           with the rewritten version.
 *   [Expand]              — calls /iterate with action='expand'.
 *                           On accept replaces the textarea content.
 *   [Accept draft as-is]  — same as Mark as reviewed but always
 *                           passes the ORIGINAL pre-populated content
 *                           verbatim (no edits Bob may have made
 *                           between selecting the block and clicking).
 *
 * The OTHER marker kinds (DATA REQUIRED, CITATION REQUIRED, DATA
 * MISMATCH, UNVERIFIED NUMBER, CITATION UNVERIFIED) keep the
 * original collapsed-pill default — those are missing-data flags,
 * not draft prompts.
 */
import { useMemo, useState } from 'react'
import {
  AlertCircle, Check, Edit3, Loader2, Wand2, Maximize2, X, Trash2,
} from 'lucide-react'

import type { BobBlock } from '../../lib/bobBlocks'

interface IterationResponse {
  original: string
  rewritten: string
  word_delta: number
  new_unverified_numbers: number[]
  new_unverified_citations: string[]
}

interface Props {
  block: BobBlock
  onResolve: (marker: string, replacement: string) => Promise<void>
  /** May 24 2026 RW5 full spec — explicit Reject action. Removes
   *  the [BOB] marker from paper_md without inserting any
   *  replacement text. The block disappears entirely; bobCount
   *  drops. Distinct from Accept (which keeps the agent draft)
   *  and Edit-then-Accept (which keeps Bob's revised version). */
  onReject?: ((marker: string) => Promise<void>) | undefined
  onIterate?:
    | ((action: 'rephrase' | 'expand', selection: string) =>
        Promise<IterationResponse>)
    | undefined
  disabled?: boolean | undefined
}


const NON_BOB_LABEL: Record<string, string> = {
  'DATA REQUIRED':       'Missing data',
  'CITATION REQUIRED':   'Missing citation',
  'DATA MISMATCH':       'Data mismatch',
  'UNVERIFIED NUMBER':   'Unverified number',
  'CITATION UNVERIFIED': 'Citation unverified',
}


export default function BobBlockBadge({
  block, onResolve, onReject, onIterate, disabled,
}: Props) {
  // Route by kind. The five non-BOB kinds keep the collapsed
  // pill UX — they're missing-data flags, not draft prompts.
  if (block.kind !== 'BOB') {
    return (
      <NonBobBadge
        block={block}
        onResolve={onResolve}
        disabled={disabled}
      />
    )
  }
  return (
    <BobDraftBadge
      block={block}
      onResolve={onResolve}
      onReject={onReject}
      onIterate={onIterate}
      disabled={disabled}
    />
  )
}


// ── BOB pre-populated draft badge ──────────────────────────────────────────


function BobDraftBadge({
  block, onResolve, onReject, onIterate, disabled,
}: Props) {
  // The draft is the block's description — the agent's pre-
  // populated paragraph. Bob edits it in place; Mark as reviewed
  // sends the current value to /resolve-bob.
  const [draft, setDraft] = useState(block.description)
  const [submitting, setSubmitting] = useState(false)
  const [iterating, setIterating] =
    useState<'rephrase' | 'expand' | null>(null)
  const [rejecting, setRejecting] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // May 24 2026 RW5 full spec — Edit mode toggle. The default
  // surface treats the draft as accept-as-is + iterate-buttons;
  // clicking Edit reveals the textarea for inline edits. Keeps
  // the three actions (Accept / Edit / Reject) prominent at the
  // top of the block; the textarea is one click away.
  const [editing, setEditing] = useState(false)

  const wordCount = useMemo(
    () => draft.trim() ? draft.trim().split(/\s+/).length : 0,
    [draft],
  )

  const handleMarkReviewed = async (content: string) => {
    if (!content.trim()) {
      setErr('Draft is empty — type your replacement first.')
      return
    }
    setSubmitting(true)
    setErr(null)
    try {
      await onResolve(block.marker, content.trim())
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Save failed.'
      setErr(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const handleReject = async () => {
    if (!onReject) {
      // Defensive — the parent should always wire onReject for
      // [BOB] blocks. If not, fall back to resolving with an
      // empty replacement (the marker disappears either way).
      setSubmitting(true)
      setErr(null)
      try {
        await onResolve(block.marker, '')
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : 'Reject failed.')
      } finally {
        setSubmitting(false)
      }
      return
    }
    setRejecting(true)
    setErr(null)
    try {
      await onReject(block.marker)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Reject failed.')
    } finally {
      setRejecting(false)
    }
  }

  const handleIterate = async (action: 'rephrase' | 'expand') => {
    if (!onIterate) {
      setErr(
        'AI iteration unavailable — edit manually or click Mark as reviewed.')
      return
    }
    if (!draft.trim()) {
      setErr('Draft is empty.')
      return
    }
    setIterating(action)
    setErr(null)
    try {
      const res = await onIterate(action, draft)
      if (res.rewritten && res.rewritten.trim()) {
        setDraft(res.rewritten)
      }
      if ((res.new_unverified_numbers?.length ?? 0) > 0) {
        setErr(
          `Warning: iteration introduced ${res.new_unverified_numbers.length} ` +
          `unverified number(s). Review before marking as reviewed.`)
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Iteration failed.')
    } finally {
      setIterating(null)
    }
  }

  return (
    <div
      data-testid="bob-draft-badge"
      className={
        'block w-full my-3 bg-amber-500/10 ' +
        'border border-amber-500/40 border-l-4 border-l-amber-500 ' +
        'rounded p-3'
      }>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <Edit3 className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <span className="text-amber-200 font-semibold text-sm">
            Review and personalise
          </span>
        </div>
        <span
          data-testid="bob-draft-word-count"
          className="text-amber-100/60 text-2xs whitespace-nowrap">
          {wordCount} words
        </span>
      </div>
      <p className="text-amber-100/80 text-2xs mb-2 italic">
        This draft was generated from your data. Edit to reflect
        your own analysis and voice, then click Mark as reviewed.
      </p>
      {/* May 24 2026 RW5 full spec — three primary actions are
          always visible at the top of the block. The textarea +
          iterate buttons are revealed only when Edit is clicked,
          keeping the Accept / Edit / Reject decision the clearest
          surface. */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <button
          type="button"
          onClick={() => handleMarkReviewed(block.description)}
          disabled={submitting || iterating !== null
                    || rejecting || disabled}
          data-testid="bob-accept"
          title="Accept the agent draft verbatim and remove the block"
          className={
            'inline-flex items-center gap-1.5 px-3 py-1 ' +
            'bg-green-500 hover:bg-green-400 disabled:bg-green-700 ' +
            'text-navy-950 text-xs font-semibold rounded transition-colors'
          }>
          <Check className="w-3 h-3" />
          Accept
        </button>
        <button
          type="button"
          onClick={() => setEditing((v) => !v)}
          disabled={submitting || iterating !== null
                    || rejecting || disabled}
          data-testid="bob-edit-toggle"
          title="Edit the agent draft inline, then confirm"
          className={
            'inline-flex items-center gap-1.5 px-3 py-1 ' +
            'bg-amber-500 hover:bg-amber-400 disabled:bg-amber-700 ' +
            'text-navy-950 text-xs font-semibold rounded transition-colors'
          }>
          <Edit3 className="w-3 h-3" />
          {editing ? 'Done editing' : 'Edit'}
        </button>
        <button
          type="button"
          onClick={() => { void handleReject() }}
          disabled={submitting || iterating !== null
                    || rejecting || disabled}
          data-testid="bob-reject"
          title="Discard the block — no replacement text is inserted"
          className={
            'inline-flex items-center gap-1.5 px-3 py-1 ' +
            'bg-red-500 hover:bg-red-400 disabled:bg-red-700 ' +
            'text-navy-950 text-xs font-semibold rounded transition-colors'
          }>
          {rejecting ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Trash2 className="w-3 h-3" />
          )}
          {rejecting ? 'Rejecting…' : 'Reject'}
        </button>
      </div>
      {/* The textarea + iterate row only renders in Edit mode. */}
      {editing ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.max(4, Math.min(12, draft.split('\n').length + 1))}
            data-testid="bob-draft-textarea"
            disabled={submitting || iterating !== null || rejecting}
            className={
              'w-full px-2 py-1.5 bg-navy-950 border border-amber-500/30 ' +
              'rounded text-white text-sm leading-relaxed ' +
              'placeholder-amber-100/40 focus:outline-none ' +
              'focus:border-amber-400 disabled:opacity-60'
            }
          />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <button
              type="button"
              onClick={() => handleMarkReviewed(draft)}
              disabled={submitting || iterating !== null
                        || rejecting || disabled}
              data-testid="bob-mark-reviewed"
              className={
                'inline-flex items-center gap-1.5 px-3 py-1 ' +
                'bg-amber-500 hover:bg-amber-400 disabled:bg-amber-700 ' +
                'text-navy-950 text-xs font-semibold rounded transition-colors'
              }>
              <Check className="w-3 h-3" />
              {submitting ? 'Saving…' : 'Confirm edits'}
            </button>
            <button
              type="button"
              onClick={() => { void handleIterate('rephrase') }}
              disabled={submitting || iterating !== null
                        || rejecting || disabled || !onIterate}
              data-testid="bob-rephrase"
              title={onIterate
                ? 'Rewrite in your voice (same length, same numbers)'
                : 'AI iteration unavailable in this context'}
              className={
                'inline-flex items-center gap-1.5 px-2.5 py-1 ' +
                'bg-navy-800 hover:bg-navy-700 border border-amber-500/20 ' +
                'disabled:bg-navy-900 disabled:text-text-muted ' +
                'text-amber-100/90 text-xs rounded transition-colors'
              }>
              {iterating === 'rephrase' ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Wand2 className="w-3 h-3" />
              )}
              Rephrase in my voice
            </button>
            <button
              type="button"
              onClick={() => { void handleIterate('expand') }}
              disabled={submitting || iterating !== null
                        || rejecting || disabled || !onIterate}
              data-testid="bob-expand"
              title={onIterate
                ? 'Add one more sentence of detail'
                : 'AI iteration unavailable in this context'}
              className={
                'inline-flex items-center gap-1.5 px-2.5 py-1 ' +
                'bg-navy-800 hover:bg-navy-700 border border-amber-500/20 ' +
                'disabled:bg-navy-900 disabled:text-text-muted ' +
                'text-amber-100/90 text-xs rounded transition-colors'
              }>
              {iterating === 'expand' ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Maximize2 className="w-3 h-3" />
              )}
              Expand
            </button>
          </div>
        </>
      ) : (
        // Read-only preview of the agent's draft so Bob can read
        // before deciding. Same content the textarea would show in
        // Edit mode.
        <p
          data-testid="bob-draft-preview"
          className="text-white text-sm leading-relaxed
                     bg-navy-950/60 border border-amber-500/20 rounded
                     px-2 py-1.5 mb-1 whitespace-pre-wrap">
          {block.description}
        </p>
      )}
      {err ? (
        <p
          data-testid="bob-draft-error"
          className="text-red-400 text-xs mt-1.5">
          {err}
        </p>
      ) : null}
    </div>
  )
}


// ── Non-BOB collapsed pill (legacy behaviour preserved) ────────────────────


function NonBobBadge({
  block, onResolve, disabled,
}: Props) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const label = NON_BOB_LABEL[block.kind] ?? block.kind

  const handleDone = async () => {
    if (!text.trim()) {
      setErr('Enter your replacement text first.')
      return
    }
    setSubmitting(true)
    setErr(null)
    try {
      await onResolve(block.marker, text.trim())
      setOpen(false)
      setText('')
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Save failed.'
      setErr(msg)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        data-testid="bob-block-badge"
        className={
          'inline-flex items-center gap-1.5 px-2 py-0.5 ' +
          'bg-amber-500/15 border-l-2 border-amber-500 ' +
          'rounded-r text-amber-200 text-xs font-medium ' +
          'hover:bg-amber-500/25 cursor-pointer transition-colors ' +
          'align-baseline'
        }>
        <AlertCircle className="w-3 h-3" />
        <span>{label}</span>
        <span className="text-amber-100/70 italic truncate max-w-[28ch]">
          {block.description}
        </span>
      </button>
    )
  }

  return (
    <div
      className={
        'inline-block w-full my-2 bg-amber-500/10 ' +
        'border border-amber-500/40 border-l-4 border-l-amber-500 ' +
        'rounded p-3 align-baseline'
      }
      data-testid="bob-block-badge-open">
      <div className="flex items-center gap-2 mb-2">
        <AlertCircle className="w-4 h-4 text-amber-400" />
        <span className="text-amber-200 font-semibold text-sm">{label}</span>
      </div>
      <p className="text-amber-100/80 text-xs mb-2 italic">
        {block.description}
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type your replacement text…"
        rows={3}
        className={
          'w-full px-2 py-1.5 bg-navy-950 border border-amber-500/30 ' +
          'rounded text-white text-sm placeholder-amber-100/40 ' +
          'focus:outline-none focus:border-amber-400'
        }
      />
      {err ? (
        <p className="text-red-400 text-xs mt-1.5">{err}</p>
      ) : null}
      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          onClick={handleDone}
          disabled={submitting || disabled}
          data-testid="bob-block-done"
          className={
            'inline-flex items-center gap-1.5 px-3 py-1 ' +
            'bg-amber-500 hover:bg-amber-400 disabled:bg-amber-700 ' +
            'text-navy-950 text-xs font-medium rounded transition-colors'
          }>
          <Check className="w-3 h-3" />
          {submitting ? 'Saving…' : 'Done'}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setErr(null) }}
          className="text-amber-100/60 hover:text-amber-100 text-xs">
          <X className="w-3 h-3 inline" /> Cancel
        </button>
      </div>
    </div>
  )
}
