/**
 * DataExplainButton — the "✨ Explain this data" affordance.
 *
 * A drop-in button placed on the strategy detail subscreen and on every
 * Analytics chart. It opens a DataExplainPanel drawer that streams a
 * contextual explanation of the specific values currently on screen.
 *
 * This is the deliberate counterpart to the ⓘ InfoIcon:
 *   ⓘ InfoIcon       → "what does this metric mean?"
 *   ✨ Data Explain   → "what do these specific values mean?"
 */
import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import DataExplainPanel from './DataExplainPanel'

interface DataExplainButtonProps {
  /** Metric / chart / strategy name sent to the explainer. */
  metric: string
  /** Compact summary of the on-screen values, injected into the prompt. */
  currentValue?: string
  /** Free-text framing hint passed to the explainer. */
  context?: string
  /** Extra classes for layout in the host component. */
  className?: string
}

export default function DataExplainButton({
  metric, currentValue, context, className = '',
}: DataExplainButtonProps) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs
                    border border-electric/30 bg-electric/5 text-electric
                    hover:bg-electric/15 transition-colors ${className}`}
      >
        <Sparkles className="w-3 h-3" />
        Explain this data
      </button>
      {open && (
        <DataExplainPanel
          metric={metric}
          {...(currentValue !== undefined ? { currentValue } : {})}
          {...(context !== undefined ? { context } : {})}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}
