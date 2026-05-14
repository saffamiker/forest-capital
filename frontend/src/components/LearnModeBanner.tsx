/**
 * frontend/src/components/LearnModeBanner.tsx
 *
 * Top-of-page banner shown only in Commentary mode. Quietly tells the
 * user what's different about the screen they're on — hover for a
 * definition, click for the full explanation, click a chart strip for
 * the analyst note.
 *
 * Self-contained: reads `mode` from useUI and renders nothing in
 * Analyst or Present mode. Pages just drop `<LearnModeBanner />` at
 * the top and forget about it.
 */
import { MessageSquare } from 'lucide-react'
import { useUI } from '../context/UIContext'

export default function LearnModeBanner() {
  const { mode } = useUI()
  if (mode !== 'commentary') return null

  return (
    <div
      className="flex items-start gap-2.5 px-4 py-2.5 rounded border bg-electric/5 border-electric/30 text-electric"
      role="status"
      data-testid="learn-mode-banner"
    >
      <MessageSquare className="w-3.5 h-3.5 mt-0.5 shrink-0" />
      <div className="text-xs leading-relaxed">
        <span className="font-semibold uppercase tracking-wide text-2xs">Commentary mode</span>
        <span className="ml-2 text-cbd5e1">
          Hover any underlined metric for a one-line definition, click to expand
          with a session-specific explanation, or click a chart strip for the
          analyst note. Switch to Analyst mode for the clean dashboard view.
        </span>
      </div>
    </div>
  )
}
