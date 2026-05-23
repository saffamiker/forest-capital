/**
 * frontend/src/components/reportwriter/BobBlockBadge.tsx
 *
 * Highlighted, interactive [BOB] callout block. Renders inline with
 * the preview prose; clicking the [Done] button POSTs the resolved
 * text to /resolve-bob and the parent updates paper_md.
 *
 * Visual style follows the amber-accent callout pattern used
 * elsewhere in the system (academic export modals, document review).
 */
import { useState } from 'react'
import { AlertCircle, Check, X } from 'lucide-react'

import type { BobBlock } from '../../lib/bobBlocks'

interface Props {
  block: BobBlock
  onResolve: (marker: string, replacement: string) => Promise<void>
  disabled?: boolean | undefined
}

const KIND_LABEL: Record<string, string> = {
  'BOB':                'Your input needed',
  'DATA REQUIRED':      'Missing data',
  'CITATION REQUIRED':  'Missing citation',
  'DATA MISMATCH':      'Data mismatch',
  'UNVERIFIED NUMBER':  'Unverified number',
  'CITATION UNVERIFIED': 'Citation unverified',
}

export default function BobBlockBadge({ block, onResolve, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const label = KIND_LABEL[block.kind] ?? block.kind

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
