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
import { createPortal } from 'react-dom'
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
  // Viewport-coordinate position computed on hover-open so the tooltip
  // can be rendered through a portal at document.body — escaping any
  // ancestor with overflow:hidden (the Dashboard strategy table is the
  // canonical offender; UAT feedback #2/#6 flagged columns P(FDR)
  // through Tier 1 clipping). Includes a flip-above signal when there
  // isn't room below the icon.
  const [pos, setPos] = useState<{ top: number; left: number;
                                    above: boolean } | null>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const iconRef = useRef<HTMLButtonElement>(null)

  // A mis-keyed icon shows nothing rather than an empty tooltip.
  if (!tooltip) return null

  const dim = size === 'md' ? 'w-3.5 h-3.5' : 'w-3 h-3'

  const onEnter = () => {
    hoverTimer.current = setTimeout(() => {
      const rect = iconRef.current?.getBoundingClientRect()
      if (!rect) return
      // Flip above when there isn't ~96px of room below the icon.
      const above = rect.bottom + 96 > window.innerHeight
      // Tooltip width = w-60 (240px). Centre horizontally on the icon,
      // but clamp to a 12px margin so a tooltip near the viewport edge
      // doesn't extend past it.
      const iconCentre = rect.left + rect.width / 2
      const tipWidth = 240
      const margin = 12
      const minLeft = margin
      const maxLeft = window.innerWidth - tipWidth - margin
      const desiredLeft = iconCentre - tipWidth / 2
      const left = Math.max(minLeft, Math.min(maxLeft, desiredLeft))
      // Vertical: anchor to top of icon (for above flip) or bottom.
      const top = above ? rect.top - 6 : rect.bottom + 6
      setPos({ top, left, above })
      setHovered(true)
    }, HOVER_DELAY_MS)
  }
  const onLeave = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    setHovered(false)
    setPos(null)
  }

  return (
    // While the tooltip is open, the wrapper is elevated to z-[60] so
    // it creates its own stacking context above the table's strongest
    // sticky cell (the Strategy column header is z-20 on the Dashboard
    // strategy table). Without this, the absolute tooltip at z-50
    // lives inside the thead's z-10 stacking context, and a sibling
    // <th> at z-20 (the sticky-left Strategy header) renders OVER it
    // when the w-60 tooltip extends sideways. Click-to-open is at
    // higher z still (ExplainerPanel uses z-[60]/[61]); the
    // setHovered(false) on click clears this elevation before the
    // panel mounts, so the panel correctly covers everything.
    <span className={`relative inline-flex items-center
                      ${hovered || panelOpen ? 'z-[60]' : ''}`}>
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

      {/* Lightweight hover tooltip — static content, no API call.
          Rendered through a portal at document.body with fixed
          positioning so the tooltip is not subject to ANY ancestor
          overflow:hidden (the Dashboard strategy table card and its
          overflow-x-auto inner wrapper both used to clip tooltips on
          the right-hand metric columns). The pos calculation in
          onEnter handles flip-above and edge clamping. */}
      {hovered && pos && createPortal(
        <span
          role="tooltip"
          style={{
            position: 'fixed',
            top: pos.above ? undefined : pos.top,
            bottom: pos.above
              ? window.innerHeight - pos.top : undefined,
            left: pos.left,
            // Width caps at 240px desktop, shrinks to fit viewports
            // narrower than 264px (240 + 24 margin) so the tooltip
            // never extends past the viewport edge on iPhone SE.
            width: 'min(240px, calc(100vw - 24px))',
            maxWidth: 'min(240px, calc(100vw - 24px))',
            // Vertical cap: 60vh on tall viewports, 320px on short
            // ones. Without this, a long tooltip placed above the
            // icon (flip-above branch) can extend past the top of
            // the viewport and clip its first lines. Excess content
            // scrolls within the tooltip via overflow-y-auto.
            // UAT feedback flagged P (FDR) → Tier 1 column tooltips
            // clipping at 375px width; the cap makes the wrap +
            // scroll behaviour predictable across viewports.
            maxHeight: 'min(60vh, 320px)',
            overflowY: 'auto',
          }}
          className="z-[80] card px-3 py-2
                      text-2xs leading-relaxed text-slate-200 shadow-card
                      break-words [overflow-wrap:anywhere]
                      whitespace-normal"
        >
          {tooltip}
        </span>,
        document.body,
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
