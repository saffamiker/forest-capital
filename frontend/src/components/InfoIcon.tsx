/**
 * InfoIcon — a small ⓘ affordance placed inline after a chart title,
 * table column header, or metric label.
 *
 * Two interaction levels:
 *   HOVER  — after 300ms, a lightweight tooltip with the pre-written
 *            static text from explainerTooltips.ts (no API call).
 *   CLICK  — opens the ExplainerPanel, which calls the live explainer
 *            agent for a data-anchored explanation.
 *
 * The icon sits only in the label/title area — never over a chart
 * canvas — so it never intercepts chart interactions. It renders
 * nothing when its tooltipKey has no static entry, so a mis-keyed
 * icon fails silent rather than showing an empty tooltip.
 */
import { useRef, useState } from 'react'
import { Info } from 'lucide-react'
import { getTooltip } from '../constants/explainerTooltips'
import ExplainerPanel from './ExplainerPanel'

interface InfoIconProps {
  /** Key into explainerTooltips.ts — supplies the static hover text. */
  tooltipKey: string
  /** Human-readable metric/chart name, sent to the explainer on click. */
  metricLabel: string
  /** Current on-screen value, injected into the explainer prompt. Omit
   *  for column headers, where the icon explains the metric in general. */
  currentValue?: string
  /** Icon size — 'sm' (default) for table headers, 'md' for chart titles. */
  size?: 'sm' | 'md'
}

const HOVER_DELAY_MS = 300

export default function InfoIcon({
  tooltipKey, metricLabel, currentValue, size = 'sm',
}: InfoIconProps) {
  const tooltip = getTooltip(tooltipKey)
  const [hovered, setHovered] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  // Tooltip flips above the icon when it sits low in the viewport.
  const [above, setAbove] = useState(false)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const iconRef = useRef<HTMLButtonElement>(null)

  // A mis-keyed icon shows nothing rather than an empty tooltip.
  if (!tooltip) return null

  const dim = size === 'md' ? 'w-3.5 h-3.5' : 'w-3 h-3'

  const onEnter = () => {
    hoverTimer.current = setTimeout(() => {
      const rect = iconRef.current?.getBoundingClientRect()
      // Flip above when there isn't ~96px of room below the icon.
      setAbove(!!rect && rect.bottom + 96 > window.innerHeight)
      setHovered(true)
    }, HOVER_DELAY_MS)
  }
  const onLeave = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    setHovered(false)
  }

  return (
    <span className="relative inline-flex items-center">
      <button
        ref={iconRef}
        type="button"
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        onClick={() => { setHovered(false); setPanelOpen(true) }}
        aria-label={`Explain ${metricLabel}`}
        /* 44px tap target on mobile (the icon itself stays small and
           centred); reset to the natural inline size from sm: up. */
        className="inline-flex items-center justify-center
                   min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0
                   text-muted hover:text-electric
                   transition-colors ml-1 align-middle"
      >
        <Info className={dim} />
      </button>

      {/* Lightweight hover tooltip — static content, no API call. */}
      {hovered && (
        <span
          role="tooltip"
          className={`absolute z-50 left-1/2 -translate-x-1/2 w-60
                      card px-3 py-2
                      text-2xs leading-relaxed text-slate-200 shadow-card
                      ${above ? 'bottom-full mb-1.5' : 'top-full mt-1.5'}`}
        >
          {tooltip}
        </span>
      )}

      {/* Click — the live, streamed explainer drawer. currentValue is
          spread so it is omitted (not passed as undefined) when absent —
          exactOptionalPropertyTypes forbids an explicit undefined. */}
      {panelOpen && (
        <ExplainerPanel
          metricLabel={metricLabel}
          {...(currentValue !== undefined ? { currentValue } : {})}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </span>
  )
}
