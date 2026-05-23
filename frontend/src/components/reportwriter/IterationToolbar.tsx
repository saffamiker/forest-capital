/**
 * frontend/src/components/reportwriter/IterationToolbar.tsx
 *
 * Floating AI iteration toolbar — appears above the inline editor
 * when Bob has text selected. Four actions: Rephrase, Tighten,
 * Expand, Ask. Each POSTs to /iterate with the selection; the
 * response carries the rewritten text + a diff of new unverified
 * numbers / citations the iteration introduced (so the UI can warn
 * before Bob accepts).
 */
import { useState } from 'react'
import { Wand2, Minimize2, Maximize2, MessageSquare, Loader2 } from 'lucide-react'

export type IterationAction = 'rephrase' | 'tighten' | 'expand' | 'ask'

interface IterationResponse {
  original: string
  rewritten: string
  word_delta: number
  new_unverified_numbers: number[]
  new_unverified_citations: string[]
}

interface Props {
  selectedText: string
  onRun: (
    action: IterationAction, instruction?: string,
  ) => Promise<IterationResponse>
  onAccept: (rewritten: string) => void
  disabled?: boolean | undefined
}

export default function IterationToolbar({
  selectedText, onRun, onAccept, disabled,
}: Props) {
  const [pending, setPending] = useState<IterationAction | null>(null)
  const [proposal, setProposal] = useState<IterationResponse | null>(null)
  const [askPrompt, setAskPrompt] = useState('')
  const [askOpen, setAskOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handle = async (action: IterationAction, instruction?: string) => {
    setPending(action)
    setErr(null)
    try {
      const res = await onRun(action, instruction)
      setProposal(res)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Iteration failed.')
    } finally {
      setPending(null)
    }
  }

  const empty = !selectedText.trim()

  if (proposal !== null) {
    const warnings: string[] = []
    if (proposal.new_unverified_numbers.length > 0) {
      warnings.push(
        `Introduced ${proposal.new_unverified_numbers.length} ` +
        `unverified number(s): ${proposal.new_unverified_numbers.join(', ')}`)
    }
    if (proposal.new_unverified_citations.length > 0) {
      warnings.push(
        `Introduced ${proposal.new_unverified_citations.length} ` +
        `unverified citation(s): ${proposal.new_unverified_citations.join(', ')}`)
    }
    return (
      <div
        data-testid="iteration-proposal"
        className={
          'p-3 bg-navy-900 border border-electric-blue/40 rounded ' +
          'border-l-4 border-l-electric-blue'
        }>
        <div className="flex items-center gap-2 mb-2">
          <Wand2 className="w-4 h-4 text-electric-blue" />
          <span className="text-white font-medium text-sm">
            Proposed rewrite
          </span>
          {proposal.word_delta !== 0 ? (
            <span className="text-text-secondary text-xs">
              ({proposal.word_delta > 0 ? '+' : ''}{proposal.word_delta} words)
            </span>
          ) : null}
        </div>
        {warnings.length > 0 ? (
          <div className="mb-2 p-2 bg-amber-500/10 border border-amber-500/40 rounded">
            {warnings.map((w) => (
              <p key={w} className="text-amber-200 text-xs">⚠ {w}</p>
            ))}
            <p className="text-amber-100/70 text-2xs mt-1 italic">
              Accept anyway? These will be flagged on the next final check.
            </p>
          </div>
        ) : null}
        <pre className={
          'text-text-secondary text-xs mb-2 whitespace-pre-wrap ' +
          'font-sans bg-navy-950 p-2 rounded max-h-48 overflow-y-auto'
        }>
          {proposal.rewritten}
        </pre>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => { onAccept(proposal.rewritten); setProposal(null) }}
            className={
              'px-3 py-1 bg-electric-blue hover:bg-electric-blue/80 ' +
              'text-white text-xs font-medium rounded'
            }>
            Accept
          </button>
          <button
            type="button"
            onClick={() => setProposal(null)}
            className="px-3 py-1 text-text-secondary hover:text-white text-xs">
            Dismiss
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1 flex-wrap">
        <ToolbarButton
          icon={Wand2}
          label="Rephrase"
          disabled={empty || disabled || !!pending}
          loading={pending === 'rephrase'}
          onClick={() => handle('rephrase')}
        />
        <ToolbarButton
          icon={Minimize2}
          label="Tighten"
          disabled={empty || disabled || !!pending}
          loading={pending === 'tighten'}
          onClick={() => handle('tighten')}
        />
        <ToolbarButton
          icon={Maximize2}
          label="Expand"
          disabled={empty || disabled || !!pending}
          loading={pending === 'expand'}
          onClick={() => handle('expand')}
        />
        <ToolbarButton
          icon={MessageSquare}
          label="Ask the writer"
          disabled={empty || disabled || !!pending}
          loading={pending === 'ask'}
          onClick={() => setAskOpen((v) => !v)}
        />
        {empty ? (
          <span className="text-text-muted text-xs italic ml-2">
            Select text in the editor to enable
          </span>
        ) : null}
      </div>
      {askOpen ? (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={askPrompt}
            onChange={(e) => setAskPrompt(e.target.value)}
            placeholder="e.g. Make this paragraph less passive"
            className={
              'flex-1 px-2 py-1 bg-navy-950 border border-navy-700 ' +
              'rounded text-white text-xs placeholder-text-muted ' +
              'focus:outline-none focus:border-electric-blue'
            }
            data-testid="iteration-ask-input"
          />
          <button
            type="button"
            disabled={!askPrompt.trim() || !!pending}
            onClick={() => {
              if (askPrompt.trim()) {
                handle('ask', askPrompt.trim())
                setAskOpen(false)
                setAskPrompt('')
              }
            }}
            className={
              'px-3 py-1 bg-electric-blue hover:bg-electric-blue/80 ' +
              'disabled:bg-navy-700 disabled:text-text-muted ' +
              'text-white text-xs font-medium rounded'
            }>
            Run
          </button>
        </div>
      ) : null}
      {err ? (
        <p className="text-red-400 text-xs">{err}</p>
      ) : null}
    </div>
  )
}


interface ToolbarButtonProps {
  icon: typeof Wand2
  label: string
  disabled: boolean
  loading: boolean
  onClick: () => void
}

function ToolbarButton({
  icon: Icon, label, disabled, loading, onClick,
}: ToolbarButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={`iteration-${label.toLowerCase().replace(/\s/g, '-')}`}
      className={
        'inline-flex items-center gap-1.5 px-2.5 py-1 ' +
        'bg-navy-800 hover:bg-navy-700 ' +
        'disabled:bg-navy-900 disabled:text-text-muted ' +
        'border border-navy-700 rounded ' +
        'text-text-secondary text-xs font-medium transition-colors'
      }>
      {loading ? (
        <Loader2 className="w-3 h-3 animate-spin" />
      ) : (
        <Icon className="w-3 h-3" />
      )}
      {label}
    </button>
  )
}
