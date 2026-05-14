/**
 * frontend/src/components/LearnModeToggle.tsx
 *
 * Sub-toggle that lives inside Commentary mode. Switches metric labels
 * between "technical" (e.g. "Sharpe ratio") and "plain English" (e.g.
 * "return per unit of risk"). Renders nothing outside Commentary mode.
 *
 * The label-swap is opt-in. Most analysts prefer the technical labels;
 * the plain-English mode exists for board members and Forest Capital
 * stakeholders who want maximum comprehension over precision. Pages
 * read `useLabelMode()` to decide which label set to render.
 */
import { useEffect } from 'react'
import { create } from 'zustand'
import { ToggleLeft, ToggleRight } from 'lucide-react'
import { useUI } from '../context/UIContext'

// Standalone Zustand store for label mode. Kept separate from
// glossaryStore so it doesn't trigger a re-render every time a new
// explanation lands in the glossary.
interface LabelState {
  plainEnglish: boolean
  toggle: () => void
  setPlainEnglish: (v: boolean) => void
}

export const useLabelMode = create<LabelState>((set) => ({
  plainEnglish: false,
  toggle: () => set((s) => ({ plainEnglish: !s.plainEnglish })),
  setPlainEnglish: (v) => set({ plainEnglish: v }),
}))

const SESSION_KEY = 'fc_label_mode_plain'

export default function LearnModeToggle() {
  const { mode } = useUI()
  const { plainEnglish, toggle, setPlainEnglish } = useLabelMode()

  // Persist preference within the session — same pattern as UIContext.
  // sessionStorage so a fresh login resets to technical labels (the
  // safer default for any analyst-facing surface).
  useEffect(() => {
    const stored = sessionStorage.getItem(SESSION_KEY)
    if (stored === 'true') setPlainEnglish(true)
  }, [setPlainEnglish])

  useEffect(() => {
    sessionStorage.setItem(SESSION_KEY, plainEnglish ? 'true' : 'false')
  }, [plainEnglish])

  if (mode !== 'commentary') return null

  const Icon = plainEnglish ? ToggleRight : ToggleLeft

  return (
    <button
      type="button"
      onClick={toggle}
      className="flex items-center gap-1.5 px-2 py-1 rounded border border-border text-2xs text-muted hover:text-white hover:border-electric/40 transition-colors"
      aria-pressed={plainEnglish}
      aria-label="Toggle plain-English labels"
      data-testid="learn-mode-toggle"
    >
      <Icon className={`w-3.5 h-3.5 ${plainEnglish ? 'text-electric' : 'text-muted'}`} />
      <span>{plainEnglish ? 'Plain English' : 'Technical'}</span>
    </button>
  )
}
