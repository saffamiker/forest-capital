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
import { Info, X } from 'lucide-react'
import { getTooltip } from '../constants/explainerTooltips'

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
        className="inline-flex items-center text-muted hover:text-electric
                   transition-colors ml-1 align-middle"
      >
        <Info className={dim} />
      </button>

      {/* Lightweight hover tooltip — static content, no API call. */}
      {hovered && (
        <span
          role="tooltip"
          className={`absolute z-50 left-1/2 -translate-x-1/2 w-60
                      rounded-md border border-border bg-navy-800 px-2.5 py-2
                      text-2xs leading-relaxed text-slate-200 shadow-lg
                      ${above ? 'bottom-full mb-1.5' : 'top-full mt-1.5'}`}
        >
          {tooltip}
        </span>
      )}

      {/* Click panel — commit 3 replaces this inline shell with the live
          ExplainerPanel (streamed agent explanation). Until then it shows
          the static text expanded so the click is never a dead end. */}
      {panelOpen && (
        <span
          role="dialog"
          className="absolute z-50 left-0 top-full mt-1.5 w-72 rounded-md
                     border border-border bg-navy-800 p-3 shadow-lg"
        >
          <span className="flex items-start justify-between gap-2">
            <span className="text-xs font-semibold text-white">{metricLabel}</span>
            <button
              type="button"
              onClick={() => setPanelOpen(false)}
              aria-label="Close"
              className="text-muted hover:text-white"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </span>
          {currentValue && (
            <span className="block text-2xs text-muted font-mono mt-1">
              Current value: {currentValue}
            </span>
          )}
          <span className="block text-2xs text-slate-300 leading-relaxed mt-1.5">
            {tooltip}
          </span>
        </span>
      )}
    </span>
  )
}
