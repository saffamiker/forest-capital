/**
 * TeamGate — wraps an action element so it is fully usable for project
 * team members and gated for everyone else.
 *
 *   Team member        → children render normally.
 *   Non-team, showDisabled (default) → children render muted and
 *       non-interactive, with a small lock icon and a tooltip naming it
 *       a team feature. A clear-but-quiet signal, not an error state.
 *   Non-team, !showDisabled → nothing renders (the feature is hidden).
 *
 * The disabled state sets pointer-events:none on the children, so a
 * wrapped button or input cannot be clicked or focused — the visual
 * muting and the lock icon are matched by genuinely inert markup.
 *
 * `block` selects the wrapper geometry: inline (default) for buttons and
 * toggles — the lock sits beside the control; block for full-width
 * cards and panels — the wrapper keeps the child's width and the lock
 * floats in the top-right corner.
 */
import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import { useIsTeamMember } from '../hooks/useIsTeamMember'

interface TeamGateProps {
  children: ReactNode
  /** Tooltip shown on the gated element for non-team users. */
  tooltip?: string
  /** When false, the element is hidden entirely for non-team users
   *  instead of shown disabled. Default true. */
  showDisabled?: boolean
  /** Block geometry — for full-width cards/panels. Default inline. */
  block?: boolean
}

export default function TeamGate({
  children,
  tooltip = 'Available to project team',
  showDisabled = true,
  block = false,
}: TeamGateProps) {
  const isTeam = useIsTeamMember()

  if (isTeam) return <>{children}</>
  if (!showDisabled) return null

  if (block) {
    // Full-width: keep the child's geometry, float the lock in the corner.
    return (
      <div
        className="relative cursor-not-allowed"
        title={tooltip}
        aria-disabled="true"
      >
        <div className="opacity-40 pointer-events-none select-none">
          {children}
        </div>
        <span
          className="absolute top-2 right-2 inline-flex items-center gap-1
                     rounded bg-navy-900/80 px-1.5 py-0.5 text-2xs text-muted"
          aria-hidden="true"
        >
          <Lock className="w-3 h-3" />
        </span>
      </div>
    )
  }

  // Inline: the lock sits beside the muted, inert control.
  return (
    <span
      className="relative inline-flex items-center gap-1 cursor-not-allowed
                 align-middle"
      title={tooltip}
      aria-disabled="true"
    >
      <span className="opacity-40 pointer-events-none select-none inline-flex">
        {children}
      </span>
      <Lock className="w-3 h-3 text-muted shrink-0" aria-hidden="true" />
    </span>
  )
}
