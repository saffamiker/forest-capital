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
}

export default function TeamGate({
  children,
  tooltip = 'Available to project team',
  showDisabled = true,
}: TeamGateProps) {
  const isTeam = useIsTeamMember()

  if (isTeam) return <>{children}</>
  if (!showDisabled) return null

  return (
    <span
      className="relative inline-flex items-center gap-1 cursor-not-allowed
                 align-middle"
      title={tooltip}
      aria-disabled="true"
    >
      {/* pointer-events:none makes the wrapped control genuinely inert,
          so the muted look is matched by inert behaviour. */}
      <span className="opacity-40 pointer-events-none select-none">
        {children}
      </span>
      <Lock className="w-3 h-3 text-muted shrink-0" aria-hidden="true" />
    </span>
  )
}
